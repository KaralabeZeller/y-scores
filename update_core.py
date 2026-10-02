"""Verified application-only releases and power-failure-safe activation."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import time
import zipfile

MAX_ARCHIVE=268435456
MAX_UNPACKED=1073741824
TERMINAL={'SUCCEEDED','ROLLED_BACK','FAILED','CANCELLED','EXPIRED'}

class UpdateError(ValueError):
    def __init__(self,reason): super().__init__(reason); self.reason=reason

def write_json(path,value,mode=0o600):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,mode)
    with os.fdopen(fd,'w',encoding='utf-8') as output:
        json.dump(value,output,sort_keys=True); output.flush(); os.fsync(output.fileno())
    os.replace(temporary,path)
    if os.name!='nt':
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)

def future(value):
    try: return datetime.fromisoformat(value.replace('Z','+00:00'))>datetime.now(timezone.utc)
    except (ValueError,TypeError,AttributeError): return False

def version(root,fallback='development'):
    try:
        value=json.loads((Path(root)/'release.json').read_text())['version']
        return value if isinstance(value,str) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',value) else fallback
    except (OSError,ValueError,KeyError): return fallback

def installed_sequence(root):
    try:
        result=json.loads((Path(root)/'release.json').read_text()).get('sequence',0)
        return result if type(result) is int and result>=0 else 0
    except (OSError,ValueError): return 0

def unpack(archive,destination,maximum):
    if not 0<maximum<=MAX_UNPACKED: raise UpdateError('COMPATIBILITY_FAILED')
    destination=Path(destination); destination.mkdir(mode=0o755,parents=True,exist_ok=False)
    seen=set(); total=0
    with tarfile.open(archive,'r:gz') as source:
        for index,member in enumerate(source):
            name=member.name; path=PurePosixPath(name)
            if (index>=4096 or '\\' in name or ':' in name or path.is_absolute()
                    or not path.parts or str(path)!=name.rstrip('/') or any(part in ('..','') for part in path.parts) or name in seen
                    or not (member.isdir() or member.isfile()) or member.mode & 0o7000):
                raise UpdateError('TRUST_FAILED')
            if path.parts[0] not in ('app','wheels','release.json','requirements.lock'):
                raise UpdateError('TRUST_FAILED')
            if path.is_relative_to('app/.venv'): raise UpdateError('TRUST_FAILED')
            seen.add(name); target=destination.joinpath(*path.parts)
            if member.isdir(): target.mkdir(mode=0o755,parents=True,exist_ok=True); continue
            total+=member.size
            if member.size>67108864 or total>maximum: raise UpdateError('TRUST_FAILED')
            target.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
            with source.extractfile(member) as input_file, target.open('xb') as output:
                shutil.copyfileobj(input_file,output,1048576)
                output.flush(); os.fsync(output.fileno())
            target.chmod(0o644)
    return total

def manifest(root,expected):
    root=Path(root)
    try: release=json.loads((root/'release.json').read_text())
    except (OSError,ValueError): raise UpdateError('TRUST_FAILED') from None
    for key in ('version','sequence','hardware','architecture','python','os','protocolMin','protocolMax','stateFormat','rollbackCompatible','schemaVersion','releaseId','commitSha','unpackedBytes'):
        if release.get(key)!=expected.get(key): raise UpdateError('COMPATIBILITY_FAILED')
    files=release.get('files')
    if not isinstance(files,dict) or not files: raise UpdateError('TRUST_FAILED')
    actual={str(p.relative_to(root)).replace('\\','/') for p in root.rglob('*') if p.is_file() and p!=root/'release.json' and not p.is_relative_to(root/'app/.venv')}
    if actual!=set(files): raise UpdateError('TRUST_FAILED')
    for name,info in files.items():
        data=root.joinpath(*PurePosixPath(name).parts).read_bytes()
        digest=info.get('sha256') if isinstance(info,dict) else info
        if hashlib.sha256(data).hexdigest()!=digest: raise UpdateError('TRUST_FAILED')
        if isinstance(info,dict) and len(data)!=info.get('size',len(data)): raise UpdateError('TRUST_FAILED')
    if not (root/'app/device_admin.py').is_file() or not (root/'requirements.lock').is_file() or not list((root/'wheels').glob('*.whl')):
        raise UpdateError('TRUST_FAILED')
    # The publisher embeds the same installation identity in app/release.json.
    try: installed=json.loads((root/'app/release.json').read_text())
    except (OSError,ValueError): raise UpdateError('TRUST_FAILED') from None
    if any(installed.get(k)!=expected.get(k) for k in expected if k not in ('notes','unpackedBytes')): raise UpdateError('TRUST_FAILED')
    return release

def compatible(release,sequence=0,allow_downgrade=False):
    if (release.get('hardware')!='pi5' or release.get('architecture')!='aarch64' or release.get('python')!='3.13'
            or release.get('os')!='raspios-trixie' or release.get('stateFormat')!=1
            or release.get('rollbackCompatible') is not True
            or not release.get('protocolMin',99)<=1<=release.get('protocolMax',0)
            or type(release.get('sequence')) is not int or release['sequence']<0):
        raise UpdateError('COMPATIBILITY_FAILED')
    if release['sequence']<sequence and not allow_downgrade: raise UpdateError('VERSION_MISMATCH')

class Engine:
    def __init__(self,home,releases,current,system,clock=time.monotonic):
        self.home=Path(home); self.releases=Path(releases); self.current=Path(current)
        self.system=system; self.clock=clock; self.journal_path=self.home/'journal.json'
        self.journal=json.loads(self.journal_path.read_text()) if self.journal_path.exists() else {'state':'READY','highWaterSequence':installed_sequence(self.current.resolve())}
    def record(self,**changes):
        self.journal.update(changes); write_json(self.journal_path,self.journal)
    def managed(self,path):
        path=Path(path).resolve()
        if not path.is_relative_to(self.releases.resolve()) or path==self.releases.resolve() or not path.is_dir():
            raise UpdateError('ROLLBACK_FAILED')
        return path
    def swap(self,target):
        target=self.managed(target); temporary=self.current.with_name('current.next')
        temporary.unlink(missing_ok=True); temporary.symlink_to(target,target_is_directory=True)
        os.replace(temporary,self.current)
        if os.name!='nt':
            fd=os.open(self.current.parent,os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
    def recover(self):
        if self.journal.get('state') in ('STOPPING','ACTIVATING','HEALTH_CHECK','ROLLING_BACK'):
            if hasattr(self.system,'expected'): self.system.expected=self.journal.get('protected')
            self.rollback()
    def rollback(self):
        self.record(state='ROLLING_BACK',reason='HEALTH_FAILED')
        try:
            previous=self.managed(self.journal['previous'])
            self.system.stop(); self.swap(previous); self.system.start()
            if not self.system.healthy(previous): raise UpdateError('ROLLBACK_FAILED')
            self.record(state='ROLLED_BACK',reason='ROLLED_BACK',installedVersion=version(previous))
        except Exception:
            self.record(state='FAILED',reason='ROLLBACK_FAILED'); raise UpdateError('ROLLBACK_FAILED') from None
    def stage(self,job,archive,custom):
        compatible(custom,self.journal.get('highWaterSequence',0),job.get('allowDowngrade') is True)
        maximum=custom.get('unpackedBytes')
        if type(maximum) is not int or not 0<maximum<=MAX_UNPACKED: raise UpdateError('COMPATIBILITY_FAILED')
        if shutil.disk_usage(self.releases).free<maximum*3+archive.stat().st_size+134217728:
            raise UpdateError('INSTALL_FAILED')
        name='update-'+str(job['id'])
        if not re.fullmatch(r'update-[0-9a-fA-F-]{36}',name): raise UpdateError('TRUST_FAILED')
        candidate=self.releases/name
        if (candidate.exists() and self.journal.get('jobId')==job['id']
                and self.journal.get('releaseVersion')==custom['version']
                and self.journal.get('state') in ('READY','WAITING_FOR_IDLE')):
            # Existing candidate is root-owned and never writable by the app user.
            # Recheck every signed file; ignore only the locally built virtualenv.
            manifest(candidate,custom)
            return candidate/'app'
        if candidate.exists():
            # Only an interrupted candidate for this exact journalled job is recoverable.
            if self.journal.get('jobId')!=job['id'] or self.journal.get('candidate')!=str(candidate/'app'):
                raise UpdateError('INSTALL_FAILED')
            if candidate.is_symlink(): raise UpdateError('TRUST_FAILED')
            shutil.rmtree(candidate)
        self.record(jobId=job['id'],state='VERIFYING',candidate=str(candidate/'app'))
        try:
            if unpack(archive,candidate,maximum)!=maximum: raise UpdateError('TRUST_FAILED')
            manifest(candidate,custom)
        except UpdateError: raise
        except (OSError,tarfile.TarError,ValueError): raise UpdateError('INSTALL_FAILED') from None
        self.system.build(candidate)
        self.record(state='READY',releaseSequence=custom['sequence'],releaseVersion=custom['version'])
        return candidate/'app'
    def activate(self,candidate,authorize,notify=lambda:None):
        candidate=self.managed(candidate)
        previous=self.managed(self.current.resolve())
        # Last authorization is deliberately after staging/reservation and before stop.
        permission=authorize()
        if not permission or not future(permission.get('activationExpiresAt')): raise UpdateError('LEASE_EXPIRED')
        protected=self.system.protected() if hasattr(self.system,'protected') else None
        self.record(previous=str(previous),candidate=str(candidate),state='STOPPING',protected=protected)
        if hasattr(self.system,'expected'): self.system.expected=protected
        try:
            self.system.stop()
            if not future(permission.get('activationExpiresAt')): raise UpdateError('LEASE_EXPIRED')
            self.record(state='ACTIVATING'); self.swap(candidate); self.system.start()
            self.record(state='HEALTH_CHECK'); notify()
            if not self.system.healthy(candidate): raise UpdateError('HEALTH_FAILED')
            self.record(state='SUCCEEDED',reason='SUCCEEDED',installedVersion=version(candidate),
                        highWaterSequence=max(self.journal.get('highWaterSequence',0),self.journal['releaseSequence']))
            self.keep_previous(previous)
        except Exception:
            self.rollback()
    def keep_previous(self,previous):
        temporary=self.current.with_name('previous.next'); temporary.unlink(missing_ok=True)
        temporary.symlink_to(previous,target_is_directory=True)
        os.replace(temporary,self.current.with_name('previous'))
        if os.name!='nt':
            fd=os.open(self.current.parent,os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)

class System:
    def __init__(self,admin): self.admin=admin; self.expected=None
    def protected(self):
        state=self.admin.state
        try:
            identity=json.loads((state/'device-identity.json').read_text())
            data=json.dumps({k:identity[k] for k in ('id','credential')},sort_keys=True).encode()
            data+=(state/'admin-pin.txt').read_bytes()+Path('/etc/y-scores.env').read_bytes()
            return hashlib.sha256(data).hexdigest()
        except (OSError,ValueError,KeyError): raise UpdateError('HEALTH_FAILED') from None
    def command(self,args,timeout=120):
        try: subprocess.run(args,check=True,timeout=timeout,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except (OSError,subprocess.SubprocessError): raise UpdateError('INSTALL_FAILED') from None
    def stop(self): self.command(['/usr/bin/systemctl','stop','y-scores.service'],30)
    def start(self): self.command(['/usr/bin/systemctl','start','y-scores.service'],30)
    def build(self,root):
        app=root/'app'; interpreter=app/'.venv/bin/python'
        rows=(root/'requirements.lock').read_text().splitlines()
        if not rows or len(rows)>128 or any(not re.fullmatch(r'[a-zA-Z0-9_.-]+==[a-zA-Z0-9.!+_-]+ --hash=sha256:[0-9a-f]{64}',row) for row in rows):
            raise UpdateError('TRUST_FAILED')
        expanded=0
        for wheel in (root/'wheels').iterdir():
            if not wheel.is_file() or wheel.suffix!='.whl': raise UpdateError('TRUST_FAILED')
            try:
                with zipfile.ZipFile(wheel) as archive:
                    if len(archive.infolist())>10000: raise UpdateError('TRUST_FAILED')
                    for item in archive.infolist():
                        path=PurePosixPath(item.filename)
                        if not path.parts or path.is_absolute() or '..' in path.parts or '\\' in item.filename or ':' in item.filename or (item.external_attr>>16)&0o170000==0o120000:
                            raise UpdateError('TRUST_FAILED')
                        expanded+=item.file_size
                        if expanded>MAX_UNPACKED: raise UpdateError('INSTALL_FAILED')
            except (OSError,zipfile.BadZipFile): raise UpdateError('TRUST_FAILED') from None
        if shutil.disk_usage(root).free<expanded*2+134217728: raise UpdateError('INSTALL_FAILED')
        self.command(['/usr/bin/python3','-m','venv',str(app/'.venv')])
        self.command([str(interpreter),'-m','pip','--isolated','install','--no-deps','--no-index','--only-binary=:all:','--require-hashes',
                      '--no-compile','--find-links',str(root/'wheels'),'-r',str(root/'requirements.lock')],600)
        self.command(['/usr/bin/systemd-run','--quiet','--wait','--collect','--uid=yscores',
                      '--property=NoNewPrivileges=yes','--property=ProtectSystem=strict','--property=ProtectHome=yes',str(interpreter),'-c',
                      'import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from live_scoreboard import Renderer; from PIL import Image; Renderer(Path(sys.argv[1])/"fonts"); Image.open(Path(sys.argv[1])/"assets/logo-white.png").verify()',str(app)])
    def healthy(self,candidate):
        successes=0; end=time.monotonic()+90
        while time.monotonic()<end:
            try:
                state=self.admin.status()
                if (state.get('installedVersion')==version(candidate) and state.get('frame_age') is not None
                        and state['frame_age']<3 and not state.get('hardware_error') and not state.get('renderer_error')
                        and state.get('hardware_output_age') is not None and state['hardware_output_age']<3
                        and state.get('state_valid') is True and (self.expected is None or self.protected()==self.expected)):
                    successes+=1
                    if successes>=30: return True
                else: successes=0
            except Exception: successes=0
            time.sleep(2)
        return False
