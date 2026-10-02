"""Offline threshold-signed renewal of existing root trust, retaining role keys."""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import Metadata, Root


def renew(root_path, key_paths, output, now=None):
    root = Metadata.from_file(str(root_path))
    if not isinstance(root.signed, Root):
        raise ValueError('Expected offline root metadata')
    root.verify_delegate('root', root)
    candidate = Metadata(deepcopy(root.signed))
    candidate.signed.version += 1
    candidate.signed.expires = (now or datetime.now(timezone.utc)) + timedelta(days=365)
    for path in key_paths:
        signer = CryptoSigner(load_pem_private_key(Path(path).read_bytes(), password=None))
        if signer.public_key.keyid not in root.signed.roles['root'].keyids:
            raise ValueError('Signing key is not an existing offline root key')
        candidate.sign(signer, append=True)
    root.verify_delegate('root', candidate)
    candidate.verify_delegate('root', candidate)
    with Path(output).open('xb') as file:
        file.write(candidate.to_bytes())
    return candidate.signed.version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--key', action='append', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(f'Renewed public root version {renew(args.root, args.key, args.output)}. Private keys were not copied.')


if __name__ == '__main__':
    main()
