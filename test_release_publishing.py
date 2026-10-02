"""Exercise exact release bundles and real TUF refresh/download verification."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import Metadata
from tuf.ngclient import Updater, UpdaterConfig
from tuf.ngclient.fetcher import FetcherInterface
from tuf.api.exceptions import DownloadHTTPError, RepositoryError

from scripts.build_release import CUSTOM_FIELDS, build, release_identity, wheel_lock
from scripts.provision_update_trust import provision
from scripts.publish_update_metadata import publish
from scripts.renew_update_root import renew


class LocalFetcher(FetcherInterface):
    def __init__(self, metadata, artifacts):
        self.metadata = metadata
        self.artifacts = artifacts

    def _fetch(self, url):
        root = self.metadata if '/metadata/' in url else self.artifacts
        path = root / url.rsplit('/', 1)[-1]
        if not path.is_file():
            raise DownloadHTTPError('Fixture missing', 404)
        yield path.read_bytes()


class ReleasePublishingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        (self.source / 'device_admin.py').write_text('print("app")\n')
        (self.source / 'admin.html').write_text('<html></html>')
        (self.source / 'test_ignored.py').write_text('ignored')
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        self.git('add', '.')
        self.git('-c', 'user.name=Release Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'Fixture')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        self.wheels = self.base / 'wheels'
        self.wheels.mkdir()
        self.wheel('demo-1.0-py3-none-any.whl')
        self.artifacts = self.base / 'artifacts'
        self.archive, self.release = build(self.source, self.wheels, self.artifacts, 'v1.0.0', self.commit, epoch=123)
        self.root = provision(self.base / 'keys')
        self.signers = {role: CryptoSigner(load_pem_private_key((self.root.parent / (role + '.pem')).read_bytes(), password=None))
                        for role in ('targets', 'snapshot', 'timestamp')}
        self.catalog = self.base / 'metadata'

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.source), *args], text=True)

    def wheel(self, name, metadata='Name: demo\nVersion: 1.0\n'):
        with zipfile.ZipFile(self.wheels / name, 'w') as wheel:
            wheel.writestr('demo-1.0.dist-info/METADATA', metadata)

    def publish(self, **kwargs):
        return publish(self.catalog, self.root, self.signers, self.archive, self.artifacts / 'release.json', **kwargs)

    def client(self):
        cache = self.base / 'client'
        cache.mkdir(exist_ok=True)
        if not (cache / 'root.json').exists():
            (cache / 'root.json').write_bytes(self.root.read_bytes())
        return Updater(str(cache), 'https://fixture.invalid/metadata/',
                       target_dir=str(self.base / 'downloads'),
                       target_base_url='https://fixture.invalid/targets/',
                       fetcher=LocalFetcher(self.catalog, self.artifacts),
                       config=UpdaterConfig(prefix_targets_with_hash=False), bootstrap=None)

    def test_bundle_is_reproducible_and_self_consistent(self):
        other, manifest = build(self.source, self.wheels, self.base / 'other', 'v1.0.0', self.commit, epoch=123)
        self.assertEqual(other.read_bytes(), self.archive.read_bytes())
        with tarfile.open(self.archive) as bundle:
            members = bundle.getmembers()
            self.assertEqual(sum(m.size for m in members), manifest['unpackedBytes'])
            self.assertTrue(all(m.isfile() and m.mode == 0o644 for m in members))
            self.assertNotIn('app/test_ignored.py', bundle.getnames())
            for name, expected in manifest['files'].items():
                self.assertEqual(hashlib.sha256(bundle.extractfile(name).read()).hexdigest(), expected)
            self.assertEqual(bundle.extractfile('release.json').read(), (self.artifacts / 'release.json').read_bytes())

    def test_real_publisher_bundle_matches_pi_extraction_contract(self):
        from update_core import unpack, manifest
        destination = self.base / 'pi-candidate'
        custom = {key: self.release[key] for key in CUSTOM_FIELDS}
        self.assertEqual(unpack(self.archive, destination, custom['unpackedBytes']), custom['unpackedBytes'])
        self.assertEqual(manifest(destination, custom), self.release)

    def test_long_arm_wheel_names_roundtrip_through_pi_extraction(self):
        from update_core import unpack, manifest
        name = 'adafruit_blinka_raspberry_pi5_piomatter-1.0.0-cp313-cp313-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl'
        self.wheel(name, 'Name: Adafruit-Blinka-Raspberry-Pi5-Piomatter\nVersion: 1.0.0\n')
        archive, release = build(self.source, self.wheels, self.base / 'long-wheels', 'v1.0.0', self.commit, epoch=123)
        duplicate, _ = build(self.source, self.wheels, self.base / 'long-wheels-repeat', 'v1.0.0', self.commit, epoch=123)
        self.assertEqual(archive.read_bytes(), duplicate.read_bytes())
        destination = self.base / 'long-wheel-candidate'
        self.assertEqual(unpack(archive, destination, release['unpackedBytes']), release['unpackedBytes'])
        self.assertTrue((destination / 'wheels' / name).is_file())
        self.assertEqual(manifest(destination, {key: release[key] for key in CUSTOM_FIELDS}), release)

    def test_dirty_source_wrong_commit_and_invalid_version_rejected(self):
        with self.assertRaises(ValueError):
            build(self.source, self.wheels, self.artifacts, 'v1.0.0', '0' * 40)
        (self.source / 'untracked.py').write_text('unexpected')
        with self.assertRaises(ValueError):
            build(self.source, self.wheels, self.artifacts, 'v1.0.0', self.commit)
        for tag in ('v01.0.0', 'v1.1000.0', 'v1.0.0-rc1', 'v1000.0.0'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release_identity(tag)

    def test_wrong_platform_and_metadata_wheels_rejected(self):
        for name in ('demo-1.0-cp313-cp313-win_amd64.whl', 'demo-1.0-cp313-cp313-manylinux_2_42_aarch64.whl',
                     'demo-1.0-cp314-cp314-linux_aarch64.whl'):
            with self.subTest(name=name):
                self.wheel(name)
                with self.assertRaises(ValueError):
                    wheel_lock(self.wheels)
                (self.wheels / name).unlink()
        self.wheel('demo-1.0-py3-none-any.whl', 'Name: other\nVersion: 1.0\n')
        with self.assertRaises(ValueError):
            wheel_lock(self.wheels)

    def test_real_tuf_client_refreshes_and_downloads_exact_bytes(self):
        self.assertEqual(self.publish(), 1)
        client = self.client()
        client.refresh()
        target = client.get_targetinfo(self.archive.name)
        self.assertEqual(target.custom['commitSha'], self.commit)
        downloaded = Path(client.download_target(target))
        self.assertEqual(downloaded.read_bytes(), self.archive.read_bytes())
        self.assertEqual(publish(self.catalog, self.root, self.signers), 2)
        client = self.client()
        client.refresh()
        self.assertEqual(client.get_targetinfo(self.archive.name).hashes, target.hashes)
        self.assertTrue((self.catalog / '1.targets.json').exists())
        self.assertTrue((self.catalog / '2.targets.json').exists())

    def test_tuf_rejects_corrupted_artifact_and_unsigned_catalog(self):
        self.publish()
        client = self.client()
        client.refresh()
        target = client.get_targetinfo(self.archive.name)
        self.archive.write_bytes(b'corrupted')
        with self.assertRaises(RepositoryError):
            client.download_target(target)
        timestamp = json.loads((self.catalog / 'timestamp.json').read_bytes())
        timestamp['signed']['version'] += 1
        (self.catalog / 'timestamp.json').write_text(json.dumps(timestamp))
        with self.assertRaises(RepositoryError):
            self.client().refresh()

    def test_publisher_rejects_wrong_signer_and_inconsistent_catalog(self):
        self.publish()
        bad = dict(self.signers, targets=CryptoSigner.generate_ed25519())
        with self.assertRaises(ValueError):
            publish(self.catalog, self.root, bad)
        (self.catalog / 'snapshot.json').write_bytes((self.catalog / 'targets.json').read_bytes())
        with self.assertRaises(ValueError):
            publish(self.catalog, self.root, self.signers)

    def test_immutable_target_and_sequence_rollback_rejected(self):
        self.publish()
        changed, _ = build(self.source, self.wheels, self.artifacts, 'v1.0.0', self.commit, notes='changed')
        with self.assertRaises(ValueError):
            self.publish()
        old, _ = build(self.source, self.wheels, self.artifacts, 'v0.9.0', self.commit)
        with self.assertRaises(ValueError):
            publish(self.catalog, self.root, self.signers, old, self.artifacts / 'release.json')

    def test_expired_root_and_existing_key_directory_rejected(self):
        with self.assertRaises(ValueError):
            self.publish(now=datetime.now(timezone.utc) + timedelta(days=366))
        with self.assertRaises(ValueError):
            provision(self.root.parent)

    def test_offline_threshold_root_renewal_is_verified_by_real_client(self):
        self.publish()
        self.client().refresh()
        renewed = self.base / 'renewed-root.json'
        keys = [self.root.parent / f'root-{i}.pem' for i in (1, 2)]
        with self.assertRaises(RepositoryError):
            renew(self.root, keys[:1], renewed)
        self.assertEqual(renew(self.root, keys, renewed), 2)
        publish(self.catalog, renewed, self.signers)
        self.assertTrue((self.catalog / '1.root.json').exists())
        self.assertTrue((self.catalog / '2.root.json').exists())
        self.client().refresh()
        root = Metadata.from_file(str(self.base / 'client' / 'root.json'))
        self.assertEqual(root.signed.version, 2)


if __name__ == '__main__':
    unittest.main()
