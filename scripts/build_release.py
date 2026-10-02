"""Build a reproducible Pi application bundle with pinned offline wheels."""
import argparse
from email.parser import BytesParser
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile

from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

MAX_ARTIFACT = 256 * 1024 * 1024
CUSTOM_FIELDS = ('schemaVersion', 'releaseId', 'version', 'sequence', 'commitSha',
                 'hardware', 'architecture', 'python', 'os', 'protocolMin',
                 'protocolMax', 'stateFormat', 'rollbackCompatible', 'notes', 'unpackedBytes')


def release_identity(tag):
    match = re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', tag)
    if not match:
        raise ValueError('Release tag must be vMAJOR.MINOR.PATCH')
    major, minor, patch = map(int, match.groups())
    if major > 999 or minor >= 1000 or patch >= 1000:
        raise ValueError('Release components exceed monotonic sequence bounds')
    return tag[1:], major * 1_000_000 + minor * 1000 + patch


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def wheel_lock(wheels):
    """All dependency installation comes from these exact wheel bytes."""
    rows, distributions = [], set()
    files = sorted(Path(wheels).iterdir())
    if not files or len(files) > 128:
        raise ValueError('Supply 1 to 128 dependency wheels')
    for path in files:
        if path.is_symlink() or not path.is_file() or path.suffix != '.whl' or path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('Wheel directory must contain only bounded regular .whl files')
        name, version, _, tags = parse_wheel_filename(path.name)
        platforms = {t.platform for t in tags}
        if any(p != 'any' and not re.fullmatch(r'(linux|manylinux2014|manylinux_[0-9]+_[0-9]+)_aarch64', p) for p in platforms):
            raise ValueError('Dependencies must target aarch64 Linux or be portable')
        for platform in platforms:
            if platform.startswith('manylinux_') and tuple(map(int, platform.split('_')[1:3])) > (2, 41):
                raise ValueError('Dependency requires a newer libc than Raspberry Pi OS Trixie')
        supported = set(cpython_tags((3, 13), abis=['cp313'], platforms=list(platforms)))
        supported.update(compatible_tags((3, 13), interpreter='cp313', platforms=list(platforms)))
        if not tags & supported:
            raise ValueError('Dependency is incompatible with CPython 3.13')
        normalized = canonicalize_name(name)
        if normalized in distributions:
            raise ValueError('Duplicate distribution in wheelhouse')
        distributions.add(normalized)
        with zipfile.ZipFile(path) as wheel:
            metadata_paths = [p for p in wheel.namelist() if p.endswith('.dist-info/METADATA')]
            if len(metadata_paths) != 1:
                raise ValueError('Wheel must contain exactly one metadata document')
            info = wheel.getinfo(metadata_paths[0])
            if info.file_size > 512 * 1024:
                raise ValueError('Wheel metadata is too large')
            metadata = BytesParser().parsebytes(wheel.read(info))
            if canonicalize_name(metadata['Name'] or '') != normalized or str(version) != metadata['Version']:
                raise ValueError('Wheel filename and metadata disagree')
        rows.append(f'{normalized}=={version} --hash=sha256:{sha256(path.read_bytes())}')
    return ('\n'.join(rows) + '\n').encode(), files


def source_files(source):
    source = Path(source).resolve()
    names = subprocess.check_output(['git', '-C', str(source), 'ls-files', '--cached'], text=True).splitlines()
    result = {}
    for name in sorted(names):
        path = Path(name)
        include = (len(path.parts) == 1 and path.suffix in {'.py', '.html', '.js'}
                   and not path.name.startswith('test_')) or path.parts[0] in {'assets', 'fonts'}
        if not include:
            continue
        actual = source / path
        if actual.is_symlink() or not actual.is_file() or not actual.resolve().is_relative_to(source):
            raise ValueError('Source bundle may only contain regular repository files')
        result['app/' + path.as_posix()] = actual.read_bytes()
    if 'app/device_admin.py' not in result:
        raise ValueError('Application entry point must be tracked before publishing')
    return result


def build(source, wheels, output, tag, commit, notes='', epoch=0):
    version, sequence = release_identity(tag)
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('A full lowercase source commit SHA is required')
    if len(notes) > 8000:
        raise ValueError('Release notes exceed 8000 characters')
    source = Path(source).resolve()
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if head != commit:
        raise ValueError('Build must use the exact requested source revision')
    dirty = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=all'], text=True)
    if dirty.strip():
        raise ValueError('Commit source changes before creating a permanent release')
    payload = source_files(source)
    lock, wheel_paths = wheel_lock(wheels)
    payload['requirements.lock'] = lock
    for path in wheel_paths:
        payload['wheels/' + path.name] = path.read_bytes()
    manifest = dict(schemaVersion=1, releaseId=tag, version=version, sequence=sequence,
                    commitSha=commit, hardware='pi5', architecture='aarch64', python='3.13',
                    os='raspios-trixie', protocolMin=1, protocolMax=1, stateFormat=1,
                    rollbackCompatible=True, notes=notes, unpackedBytes=0,
                    files={name: sha256(data) for name, data in sorted(payload.items())})
    app_version = json.dumps({key: manifest[key] for key in CUSTOM_FIELDS if key not in {'notes', 'unpackedBytes'}}, sort_keys=True).encode() + b'\n'
    payload['app/release.json'] = app_version
    manifest['files']['app/release.json'] = sha256(app_version)
    if any(len(value) > 64 * 1024 * 1024 for value in payload.values()):
        raise ValueError('A bundle member exceeds the Pi extraction limit')
    expanded = sum(map(len, payload.values()))
    if expanded > MAX_ARTIFACT * 4:
        raise ValueError('Expanded application exceeds one GiB')
    for _ in range(10):
        data = json.dumps(manifest, sort_keys=True, indent=2).encode() + b'\n'
        size = expanded + len(data)
        if manifest['unpackedBytes'] == size:
            break
        manifest['unpackedBytes'] = size
    else:
        raise ValueError('Could not stabilize bundle size')
    payload['release.json'] = data
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'y-scores-{tag}-pi5-aarch64-py313.tar.gz'
    with archive.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=epoch) as zipped:
        # ARM wheel basenames can exceed USTAR's 100-byte name field. PAX preserves
        # these exact names without truncation and stays deterministic here.
        with tarfile.open(fileobj=zipped, mode='w', format=tarfile.PAX_FORMAT) as tar:
            for name, contents in sorted(payload.items()):
                item = tarfile.TarInfo(name)
                item.size = len(contents)
                item.mode = 0o644
                item.mtime = epoch
                tar.addfile(item, io.BytesIO(contents))
    if archive.stat().st_size > MAX_ARTIFACT:
        archive.unlink()
        raise ValueError('Release archive exceeds 256 MiB')
    (output / 'release.json').write_bytes(data)
    (output / 'SHA256SUMS').write_text(f'{sha256(archive.read_bytes())}  {archive.name}\n', encoding='utf-8')
    return archive, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--wheels', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--notes', type=Path)
    parser.add_argument('--epoch', type=int, default=0)
    args = parser.parse_args()
    archive, metadata = build(args.source, args.wheels, args.output, args.tag, args.commit,
                              args.notes.read_text(encoding='utf-8') if args.notes else '', args.epoch)
    print(json.dumps({'artifact': archive.name, 'releaseId': metadata['releaseId'], 'sha256': sha256(archive.read_bytes())}))


if __name__ == '__main__':
    main()
