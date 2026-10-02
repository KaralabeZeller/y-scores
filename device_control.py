"""Durable local takeover and applied cloud revision, separate from local config."""
import json
import os


class Control:
    def __init__(self, path, legacy_config=None):
        self.path=path
        self.value=json.loads(path.read_text()) if path.exists() else dict(
            localTakeover=bool(legacy_config and legacy_config.get('mode') in ('manual','blank','logo')),
            appliedRevision=None)
        self.desired=None; self.ack=None; self.authorized=False
        self.seen_revision=self.value.get('localSelectionRevision') or 0
    def save(self):
        temporary=self.path.with_suffix('.tmp')
        with temporary.open('w',encoding='utf-8') as output:
            os.chmod(temporary,0o600); json.dump(self.value,output)
            output.flush(); os.fsync(output.fileno())
        temporary.replace(self.path)
    def takeover(self, enabled):
        self.value['localTakeover']=enabled; self.authorized=False; self.save()
        self.ack=None
    def local_selection(self, takeover):
        self.value['localTakeover']=takeover
        if not takeover: self.value['localSelectionRevision']=self.seen_revision
        self.authorized=False; self.desired=None; self.ack=None; self.save()
    def resume(self):
        self.value['localSelectionRevision']=None
        self.takeover(False)
    def accept(self, desired, available):
        self.authorized=bool(available)
        self.desired=desired if available and isinstance(desired,dict) else None
        if self.desired and type(desired.get('revision')) is int:
            self.seen_revision=max(self.seen_revision,desired['revision'])
            # Revision zero is the server's unassigned default, not a cloud command.
            if desired['revision']==0 and desired.get('state')=='NONE': self.desired=None
            suppressed=self.value.get('localSelectionRevision')
            if not self.value['localTakeover'] and suppressed is not None and desired['revision']<=suppressed:
                self.desired=None
    def reject(self, revision, reason):
        self.ack=dict(revision=revision,status='REJECTED',reason=reason)
    def applied(self, revision):
        self.value['appliedRevision']=revision; self.save()
        self.ack=dict(revision=revision,status='APPLIED',reason=None)
