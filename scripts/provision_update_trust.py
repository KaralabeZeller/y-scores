"""Offline, operator-run creation of TUF trust; never invoked by CI or the Pi."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import Metadata, Root


def private_file(path, data):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as file:
        file.write(data)


def provision(output, now=None):
    output = Path(output)
    if output.exists():
        raise ValueError('Use a new protected directory; never overwrite a trust root')
    now = now or datetime.now(timezone.utc)
    output.mkdir(parents=True, mode=0o700)
    root = Metadata(Root(version=1, expires=now + timedelta(days=365), consistent_snapshot=True))
    root_signers = [CryptoSigner.generate_ed25519() for _ in range(3)]
    for index, signer in enumerate(root_signers, 1):
        root.signed.add_key(signer.public_key, 'root')
        private_file(output / f'root-{index}.pem', signer.private_bytes)
    root.signed.roles['root'].threshold = 2
    for role in ('targets', 'snapshot', 'timestamp'):
        signer = CryptoSigner.generate_ed25519()
        root.signed.add_key(signer.public_key, role)
        private_file(output / f'{role}.pem', signer.private_bytes)
    for signer in root_signers:
        root.sign(signer, append=True)
    root.verify_delegate('root', root)
    (output / 'root.json').write_bytes(root.to_bytes())
    (output / 'public-keys.json').write_text(json.dumps({key: value.to_dict() for key, value in root.signed.keys.items()}, indent=2), encoding='utf-8')
    return output / 'root.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path,
                        help='New directory OUTSIDE the repository, on protected storage')
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    if args.output.resolve().is_relative_to(repository):
        parser.error('Private signing material must be stored outside the repository')
    root = provision(args.output)
    print(f'Created public trust root: {root}')
    print('Keep root-*.pem offline. Put only online role PEMs in protected release environment secrets.')
    print('Provision root.json on each Pi through the trusted bootstrap installer.')
    print('On Windows, restrict the directory ACL before using the keys; POSIX modes alone do not restrict Windows ACLs.')


if __name__ == '__main__':
    main()
