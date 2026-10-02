"""Publish/refresh signed TUF metadata for exact immutable release artifacts."""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tarfile

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import Metadata, Root, Targets, Snapshot, Timestamp, MetaFile, TargetFile

try:
    from scripts.build_release import CUSTOM_FIELDS, MAX_ARTIFACT, release_identity
except ModuleNotFoundError:
    from build_release import CUSTOM_FIELDS, MAX_ARTIFACT, release_identity

MAX_METADATA = 5 * 1024 * 1024


def load_metadata(path, signed_type):
    data = Path(path).read_bytes()
    if len(data) > MAX_METADATA:
        raise ValueError('Metadata exceeds catalog bound')
    metadata = Metadata.from_bytes(data)
    if not isinstance(metadata.signed, signed_type):
        raise ValueError('Unexpected metadata type')
    return metadata


def publish(metadata_dir, root_path, signers, archive=None, release_json=None, now=None):
    now = now or datetime.now(timezone.utc)
    metadata_dir = Path(metadata_dir)
    root = load_metadata(root_path, Root)
    root.verify_delegate('root', root)
    if root.signed.expires <= now:
        raise ValueError('Offline root has expired; renew it through the root ceremony')
    if not root.signed.consistent_snapshot:
        raise ValueError('The repository requires consistent snapshots')
    for role in ('targets', 'snapshot', 'timestamp'):
        if role not in signers or signers[role].public_key.keyid not in root.signed.roles[role].keyids:
            raise ValueError('Online signing key is not authorized by the offline root')
        if root.signed.roles[role].threshold != 1:
            raise ValueError('This publisher requires one authorized signer per online role')
    metadata_dir.mkdir(parents=True, exist_ok=True)
    existing_root = metadata_dir / 'root.json'
    if existing_root.exists():
        old_root = load_metadata(existing_root, Root)
        if old_root.to_dict() != root.to_dict():
            old_root.verify_delegate('root', root)
            if root.signed.version != old_root.signed.version + 1:
                raise ValueError('Root renewal must advance exactly one version')
            if root.signed.keys != old_root.signed.keys or root.signed.roles != old_root.signed.roles:
                raise ValueError('Key rotation requires a separately reviewed trust ceremony')
    previous = {}
    for role, kind in (('targets', Targets), ('snapshot', Snapshot), ('timestamp', Timestamp)):
        path = metadata_dir / f'{role}.json'
        if path.exists():
            previous[role] = load_metadata(path, kind)
            root.verify_delegate(role, previous[role])
    if previous and len(previous) != 3:
        raise ValueError('Catalog is incomplete; restore its last valid commit')
    if previous:
        timestamp_snapshot = previous['timestamp'].signed.snapshot_meta
        timestamp_snapshot.verify_length_and_hashes((metadata_dir / 'snapshot.json').read_bytes())
        if timestamp_snapshot.version != previous['snapshot'].signed.version:
            raise ValueError('Timestamp/snapshot versions disagree')
        snapshot_targets = previous['snapshot'].signed.meta['targets.json']
        snapshot_targets.verify_length_and_hashes((metadata_dir / 'targets.json').read_bytes())
        if snapshot_targets.version != previous['targets'].signed.version:
            raise ValueError('Snapshot/targets versions disagree')
    targets = Metadata(Targets(version=previous['targets'].signed.version + 1 if previous else 1,
                               expires=now + timedelta(days=90),
                               targets=deepcopy(previous['targets'].signed.targets) if previous else {}))
    if archive is not None:
        archive = Path(archive)
        if archive.is_symlink() or archive.stat().st_size > MAX_ARTIFACT:
            raise ValueError('Release must be a bounded regular archive')
        data = Path(release_json).read_bytes()
        if len(data) > 512 * 1024:
            raise ValueError('Release description exceeds its bound')
        release = json.loads(data)
        version, sequence = release_identity(release['releaseId'])
        if release['version'] != version or release['sequence'] != sequence:
            raise ValueError('Release sequence/version disagree')
        if archive.name != f'y-scores-{release["releaseId"]}-pi5-aarch64-py313.tar.gz':
            raise ValueError('Artifact name must bind its exact release and platform')
        with tarfile.open(archive, 'r:gz') as bundle:
            item = bundle.getmember('release.json')
            if not item.isfile() or item.size != len(data) or bundle.extractfile(item).read() != data:
                raise ValueError('Permanent release description and artifact disagree')
        custom = {key: release[key] for key in CUSTOM_FIELDS}
        if custom['schemaVersion'] != 1 or custom['hardware'] != 'pi5' or custom['architecture'] != 'aarch64' or custom['python'] != '3.13' or custom['os'] != 'raspios-trixie':
            raise ValueError('Unsupported release platform')
        info = TargetFile.from_file(archive.name, str(archive), ['sha256'])
        info.unrecognized_fields['custom'] = custom
        old = targets.signed.targets.get(archive.name)
        if old is not None and old.to_dict() != info.to_dict():
            raise ValueError('Published target is immutable; create a new version')
        if old is None and any(t.custom['sequence'] >= sequence for t in targets.signed.targets.values()):
            raise ValueError('New releases must advance the monotonic sequence')
        if len(targets.signed.targets) >= 128 and old is None:
            raise ValueError('Catalog reached its retained release limit; perform reviewed retirement')
        targets.signed.targets[archive.name] = info
    targets.sign(signers['targets'])
    root.verify_delegate('targets', targets)
    target_bytes = targets.to_bytes()
    snapshot = Metadata(Snapshot(version=previous['snapshot'].signed.version + 1 if previous else 1,
                                 expires=now + timedelta(days=14),
                                 meta={'targets.json': MetaFile.from_data(targets.signed.version, target_bytes, ['sha256'])}))
    snapshot.sign(signers['snapshot'])
    root.verify_delegate('snapshot', snapshot)
    snapshot_bytes = snapshot.to_bytes()
    timestamp = Metadata(Timestamp(version=previous['timestamp'].signed.version + 1 if previous else 1,
                                   expires=now + timedelta(days=3),
                                   snapshot_meta=MetaFile.from_data(snapshot.signed.version, snapshot_bytes, ['sha256'])))
    timestamp.sign(signers['timestamp'])
    root.verify_delegate('timestamp', timestamp)
    outputs = {'root.json': root.to_bytes(), f'{root.signed.version}.root.json': root.to_bytes(),
               'targets.json': target_bytes, f'{targets.signed.version}.targets.json': target_bytes,
               'snapshot.json': snapshot_bytes, f'{snapshot.signed.version}.snapshot.json': snapshot_bytes,
               'timestamp.json': timestamp.to_bytes()}
    if any(len(value) > MAX_METADATA for value in outputs.values()):
        raise ValueError('Catalog metadata exceeds its published limit')
    # The containing Git commit is the publication boundary; preserve every numbered version.
    for name, value in outputs.items():
        path = metadata_dir / name
        if name[0].isdigit() and path.exists() and path.read_bytes() != value:
            raise ValueError('Versioned metadata is immutable')
    for name, value in outputs.items():
        (metadata_dir / name).write_bytes(value)
    return targets.signed.version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata-dir', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--artifact', type=Path)
    parser.add_argument('--release-json', type=Path)
    args = parser.parse_args()
    if bool(args.artifact) != bool(args.release_json):
        parser.error('Provide both artifact and release-json, or neither for refresh')
    signers = {}
    for role in ('targets', 'snapshot', 'timestamp'):
        pem = os.environ.get(f'TUF_{role.upper()}_KEY_PEM', '')
        if not pem:
            parser.error(f'Protected environment secret TUF_{role.upper()}_KEY_PEM is required')
        signers[role] = CryptoSigner(load_pem_private_key(pem.encode(), password=None))
    version = publish(args.metadata_dir, args.root, signers, args.artifact, args.release_json)
    print(f'Published catalog metadata version {version}; private key material was not written to disk.')


if __name__ == '__main__':
    main()
