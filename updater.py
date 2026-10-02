"""Fixed root-owned updater. Never loaded from the mutable application release."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor
from uuid import uuid4
from platform_client import Platform, NoRedirect
from update_core import Engine, System, UpdateError, compatible, future, version, write_json, MAX_ARCHIVE, installed_sequence
from update_local import deferred

class SavedIdentity:
    def __init__(self,path): self.path=Path(path)
    def snapshot(self):
        value=json.loads(self.path.read_text())
        if not value.get('locked') or not value.get('platformEnabled'): raise UpdateError('AUTHORIZATION_CHANGED')
        return value

class AuthFetcher:
    """Fetches TUF bytes from the configured API proxy, never follows redirects."""
    def __init__(self,api): self.api=api
    def fetch(self,url):
        from tuf.api.exceptions import DownloadHTTPError
        prefix=self.api.base+'/scoreboard-device/update-repository/'
        if not url.startswith(prefix) or not re.fullmatch(r'(metadata|targets)/[A-Za-z0-9._-]+',url[len(prefix):]):
            raise UpdateError('TRUST_FAILED')
        identity=self.api.identity.snapshot()
        request=Request(url,headers={'Authorization':'Scoreboard '+identity['credential'],'X-Scoreboard-Device-Id':identity['id']})
        # Target proxy verifies/spools the asset before sending response headers.
        timeout=135 if url[len(prefix):].startswith('targets/') else 15
        end=time.monotonic()+300
        try:
            with self.api.opener.open(request,timeout=timeout) as response:
                received=0
                while True:
                    chunk=response.read(65536)
                    if not chunk: return
                    received+=len(chunk)
                    if received>MAX_ARCHIVE or time.monotonic()>end: raise UpdateError('DOWNLOAD_FAILED')
                    yield chunk
        except HTTPError as error:
            code=error.code; error.close()
            raise DownloadHTTPError('Update proxy request failed',code) from None
    def download_file(self,url,max_length):
        # python-tuf expects FetcherInterface's bounded temporary-file helper.
        from tuf.ngclient.fetcher import FetcherInterface
        return FetcherInterface.download_file(self,url,max_length)
    def download_bytes(self,url,max_length):
        from tuf.ngclient.fetcher import FetcherInterface
        return FetcherInterface.download_bytes(self,url,max_length)

class Trust:
    def __init__(self,api,home,root): self.api=api; self.home=Path(home); self.root=Path(root)
    def download(self,release):
        from tuf.ngclient import Updater
        from tuf.ngclient.config import UpdaterConfig
        if not self.root.is_file() or not re.fullmatch(r'y-scores-v[0-9]+\.[0-9]+\.[0-9]+-pi5-aarch64-py313\.tar\.gz',release.get('targetPath','')): raise UpdateError('TRUST_FAILED')
        cache=self.home/'metadata'; downloads=self.home/'downloads'
        cache.mkdir(parents=True,exist_ok=True); downloads.mkdir(parents=True,exist_ok=True)
        base=self.api.base+'/scoreboard-device/update-repository/'
        updater=Updater(str(cache),base+'metadata/',str(downloads),base+'targets/',
                        fetcher=AuthFetcher(self.api),bootstrap=None if (cache/'root.json').exists() else self.root.read_bytes(),
                        config=UpdaterConfig(prefix_targets_with_hash=False,max_root_rotations=32,max_delegations=32,
                                             root_max_length=524288,targets_max_length=2097152,snapshot_max_length=2097152))
        try:
            updater.refresh(); info=updater.get_targetinfo(release['targetPath'])
            if info is None or not 0<info.length<=MAX_ARCHIVE or info.length!=release['size'] or info.hashes.get('sha256')!=release['sha256']:
                raise UpdateError('TRUST_FAILED')
            custom=info.custom
            for key in ('version','sequence','hardware','architecture','python','os','protocolMin','protocolMax','stateFormat','rollbackCompatible','commitSha'):
                if custom.get(key)!=release.get(key): raise UpdateError('COMPATIBILITY_FAILED')
            compatible(custom)
            destination=downloads/(release['sha256']+'.tar.gz')
            cached=updater.find_cached_target(info,filepath=str(destination))
            if not cached: updater.download_target(info,filepath=str(destination))
            return destination,custom
        except UpdateError: raise
        except Exception: raise UpdateError('TRUST_FAILED') from None

class Admin:
    def __init__(self,state,port):
        self.state=Path(state); self.base='http://127.0.0.1:'+str(port)
        self.opener=build_opener(NoRedirect(),HTTPCookieProcessor(CookieJar())); self.csrf=''
    def request(self,path,body=None):
        headers={'Content-Type':'application/json','X-Scoreboard-Request':'1','X-Scoreboard-CSRF':self.csrf}
        request=Request(self.base+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        with self.opener.open(request,timeout=5) as response:
            raw=response.read(65537)
            if len(raw)>65536: raise UpdateError('HEALTH_FAILED')
            return json.loads(raw)
    def login(self):
        result=self.request('/api/login',{'pin':(self.state/'admin-pin.txt').read_text().strip()})
        self.csrf=result['csrfToken']
    def status(self):
        try: return self.request('/api/status')
        except HTTPError as error:
            code=error.code; error.close()
            if code!=401: raise UpdateError('HEALTH_FAILED') from None
            self.login(); return self.request('/api/status')
    def reserve(self,now=False):
        self.status(); return self.request('/api/updates/reserve',{'now':now})
    def release(self):
        try: self.request('/api/updates/release',{})
        except Exception: pass

def host_compatible():
    try:
        model=Path('/proc/device-tree/model').read_text().rstrip('\0')
        os_release=Path('/etc/os-release').read_text()
        return ('Raspberry Pi 5' in model and platform.machine()=='aarch64' and platform.python_version_tuple()[:2]==('3','13')
                and re.search(r'^VERSION_CODENAME=["\']?trixie["\']?$',os_release,re.M))
    except OSError: return False

@contextmanager
def install_lock(path='/run/lock/y-scores-install.lock'):
    import fcntl
    with open(path,'a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try: yield
        finally: fcntl.flock(lock,fcntl.LOCK_UN)

class Daemon:
    def __init__(self,api,trust,engine,admin,state,enabled):
        self.api=api; self.trust=trust; self.engine=engine; self.admin=admin; self.state=Path(state)
        self.enabled=enabled; self.boot=str(uuid4()); self.lease=None; self.job=None
    def public(self):
        journal=self.engine.journal
        write_json(self.engine.home/'public.json',dict(enabled=bool(self.enabled),state=journal.get('state','READY'),
            progressPercent=journal.get('progressPercent',0),reason=journal.get('reason'),jobId=journal.get('jobId'),
            installedVersion=version(self.engine.current.resolve(),os.environ.get('Y_SCORES_VERSION','development')),
            installedSequence=installed_sequence(self.engine.current.resolve())),0o644)
    def report(self,state,progress,reason=None):
        sequence=self.engine.journal.get('reportSequence',0)+1
        body=dict(leaseId=self.lease['leaseId'],reportSequence=sequence,state=state,
                  progressPercent=progress,reason=reason,installedVersion=version(self.engine.current.resolve(),os.environ.get('Y_SCORES_VERSION','development')))
        self.engine.record(reportSequence=sequence,progressPercent=progress,reason=reason)
        self.job=self.api.request('POST','/updates/'+self.job['id']+'/progress',body)
        self.public()
    def acquire(self):
        self.lease=self.api.request('POST','/updates/'+self.job['id']+'/lease',
                       dict(updaterBootId=self.boot,expectedRevision=self.job['revision']))
        self.job=self.lease['job']
        sequence=self.engine.journal.get('reportSequence',0) if self.engine.journal.get('leaseId')==self.lease['leaseId'] else 0
        self.engine.record(jobId=self.job['id'],reportSequence=sequence,leaseId=self.lease['leaseId'])
    def authorize(self):
        if not future(self.lease['expiresAt']) or not future(self.job['expiresAt']): raise UpdateError('LEASE_EXPIRED')
        reservation=self.admin.reserve(self.job['mode']=='NOW')
        sequence=self.engine.journal.get('reportSequence',0)+1
        self.engine.record(reportSequence=sequence)
        body=dict(leaseId=self.lease['leaseId'],reportSequence=sequence,
            installedVersion=version(self.engine.current.resolve(),os.environ.get('Y_SCORES_VERSION','development')),
            busyReason=reservation.get('busyReason'),networkBusy=reservation.get('networkBusy',False))
        if reservation.get('busyReason') in ('LOCAL_EVENT','NETWORK_BUSY'): raise UpdateError(reservation['busyReason'])
        if reservation.get('busyReason') and self.job['mode']!='NOW': raise UpdateError(reservation['busyReason'])
        permission=self.api.request('POST','/updates/'+self.job['id']+'/activation',body)
        # A late local event deferral always wins over NOW too.
        if deferred(self.state/'update-deferral.json'): raise UpdateError('LOCAL_EVENT')
        return permission
    def tick(self):
        with install_lock(): self.engine.recover()
        self.public()
        if not self.enabled: return
        self.job=self.api.request('GET','/updates/current')['job']
        if not self.job or self.job['state'] in ('CANCELLED','EXPIRED','FAILED','SUCCEEDED','ROLLED_BACK'): return
        previous_job=self.engine.journal.get('jobId'); previous_state=self.engine.journal.get('state')
        self.acquire()
        if previous_job==self.job['id'] and previous_state in ('SUCCEEDED','ROLLED_BACK','FAILED'):
            if previous_state=='SUCCEEDED' and self.job['state']=='ACTIVATING': self.report('HEALTH_CHECK',90,None)
            self.report(previous_state,100,previous_state if previous_state in ('SUCCEEDED','ROLLED_BACK') else self.engine.journal.get('reason')); return
        if self.job['state'] in ('ACTIVATING','HEALTH_CHECK'):
            # The activation response can be lost before local STOPPING is journalled.
            # Current still is last good; do not start a second installation attempt.
            self.engine.record(state='ROLLED_BACK',reason='ROLLED_BACK')
            self.report('ROLLED_BACK',100,'ROLLED_BACK'); return
        with install_lock():
            try:
                if self.job['state']=='QUEUED': self.report('DOWNLOADING',5,'DOWNLOADING')
                archive,custom=self.trust.download(self.job['release'])
                if self.job['state']=='DOWNLOADING': self.report('VERIFYING',40,'VERIFYING')
                candidate=self.engine.stage(self.job,archive,custom)
                if self.job['state'] in ('VERIFYING','WAITING_FOR_IDLE'): self.report('READY',70,'READY')
                if deferred(self.state/'update-deferral.json'): raise UpdateError('LOCAL_EVENT')
                self.acquire() # renew after the potentially slow offline environment build
                def notify():
                    try: self.report('HEALTH_CHECK',90,None)
                    except Exception: pass # cloud failure cannot invalidate local readiness
                self.engine.activate(candidate,self.authorize,notify)
                self.report(self.engine.journal['state'],100,self.engine.journal.get('reason'))
            except UpdateError as error:
                if self.engine.journal.get('state') in ('SUCCEEDED','ROLLED_BACK'): raise
                if error.reason in ('MATCH_ACTIVE','LOCAL_EVENT','NETWORK_BUSY'):
                    self.engine.record(state='WAITING_FOR_IDLE'); self.report('WAITING_FOR_IDLE',70,error.reason)
                else:
                    self.engine.record(state='FAILED'); self.report('FAILED',0,error.reason)
            finally: self.admin.release(); self.public()
    def run(self):
        while True:
            try: self.tick()
            except Exception: self.public()
            time.sleep(15)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trust-root',type=Path,default=Path('/opt/y-scores/updater/root.json'))
    args=parser.parse_args(); os.umask(0o022); home=Path('/var/lib/y-scores-updater'); state=Path('/var/lib/y-scores')
    home.mkdir(mode=0o711,parents=True,exist_ok=True)
    api=Platform(SavedIdentity(state/'device-identity.json'),os.environ.get('Y_SCORES_PLATFORM_API','https://y-sports.xyz/api-next'))
    admin=Admin(state,int(os.environ.get('Y_SCORES_PORT','8080')))
    engine=Engine(home,'/opt/y-scores/releases','/opt/y-scores/current',System(admin))
    Daemon(api,Trust(api,home,args.trust_root),engine,admin,state,args.trust_root.is_file() and host_compatible()).run()

if __name__=='__main__': main()
