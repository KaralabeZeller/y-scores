"""Protected versioned identity. Independent of the legacy display config."""
import copy
import json
import os
from pathlib import Path
import secrets
import threading
from uuid import UUID, uuid4


def protected_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


class Identity:
    def __init__(self, path, legacy=False):
        self.path = Path(path)
        self.lock = threading.RLock()
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
            if self.data.get('schemaVersion') != 1:
                raise ValueError('Unsupported identity version; preserve state and recover manually')
            UUID(self.data['id'])
            if not isinstance(self.data.get('credential'), str) or len(self.data['credential']) != 64:
                raise ValueError('Invalid saved device credential; preserve identity and recover manually')
            bytes.fromhex(self.data['credential'])
            pending=self.data.get('pendingCredential')
            if pending is not None:
                if not isinstance(pending,str) or len(pending)!=64: raise ValueError('Invalid pending credential; preserve identity and recover manually')
                bytes.fromhex(pending)
        else:
            self.data = dict(schemaVersion=1, id=str(uuid4()), name='', locked=False,
                             metadataRevision=0, credential=secrets.token_hex(32),
                             setupComplete=bool(legacy), legacy=bool(legacy), platformEnabled=False)
            # Persist the credential before any outbound request, including failed bootstrap.
            protected_write(self.path, self.data)
    def public(self):
        with self.lock:
            return {k: copy.deepcopy(self.data[k]) for k in
                    ('schemaVersion','id','name','locked','metadataRevision','setupComplete','legacy','platformEnabled')}
    def snapshot(self):
        with self.lock: return copy.deepcopy(self.data)
    def save(self, name, device_id=None):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64:
            raise ValueError('Name must contain 1 to 64 characters')
        with self.lock:
            proposed = str(UUID(device_id)) if device_id is not None else self.data['id']
            if self.data['locked'] and proposed != self.data['id']:
                raise ValueError('Device ID is already locked')
            value = self.snapshot()
            if value['name'] != name.strip() or not value['locked']:
                value.update(id=proposed, name=name.strip(), locked=True,
                             metadataRevision=value['metadataRevision'] + 1)
            protected_write(self.path, value)
            self.data = value
            return self.public()
    def update(self, **changes):
        if set(changes) - {'setupComplete', 'platformEnabled'} or any(type(v) is not bool for v in changes.values()):
            raise ValueError('Invalid setup state')
        with self.lock:
            value = self.snapshot() | changes
            protected_write(self.path, value)
            self.data = value
            return self.public()
    def prepare_credential_recovery(self):
        with self.lock:
            if not self.data['locked']: raise ValueError('Save device identity first')
            value=self.snapshot()
            if not value.get('pendingCredential'):
                value['pendingCredential']=secrets.token_hex(32)
                protected_write(self.path,value)
                self.data=value
            return value['pendingCredential']
    def prepare_runtime_metadata(self, version, capabilities, advance=False):
        # Persist the upgrade revision before sending metadata. A lost response or
        # restart retries the same revision, never incrementing it repeatedly.
        marker=dict(softwareVersion=version,capabilities=list(capabilities))
        with self.lock:
            if self.data.get('runtimeMetadata')!=marker:
                value=self.snapshot(); value['runtimeMetadata']=marker
                if advance: value['metadataRevision']+=1
                protected_write(self.path,value); self.data=value
            return self.snapshot()
    def complete_credential_recovery(self,credential):
        with self.lock:
            if self.data.get('pendingCredential')!=credential: raise ValueError('Credential recovery state changed; retry recovery')
            value=self.snapshot()
            value.update(credential=credential,platformEnabled=True)
            value.pop('pendingCredential')
            protected_write(self.path,value)
            self.data=value
            return self.public()
