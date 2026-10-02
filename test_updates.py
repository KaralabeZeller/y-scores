import hashlib
import copy
from contextlib import nullcontext
from io import BytesIO
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from update_core import Engine, System, UpdateError, compatible, manifest, unpack, write_json
from update_local import defer, deferred
from device_admin import Device, ROOT

CUSTOM=dict(schemaVersion=1,releaseId='v1.0.0',version='1.0.0',sequence=1000000,commitSha='a'*40,
            hardware='pi5',architecture='aarch64',python='3.13',os='raspios-trixie',protocolMin=1,protocolMax=1,
            stateFormat=1,rollbackCompatible=True,notes='Test',unpackedBytes=5000)

class PortableLink:
    """Windows runners cannot create symlinks; model only the link boundary there."""
    def __init__(self,target):self.target=target
    def resolve(self):return self.target
    def with_name(self,name):return PortableLink(self.target)
    def unlink(self,**kwargs):pass
    def symlink_to(self,target,**kwargs):self.target=target

def bundle(path,extra=None):
    files={'app/device_admin.py':b'pass\n','app/release.json':json.dumps(CUSTOM).encode(),
           'wheels/test.whl':b'wheel','requirements.lock':b'x==1 --hash=sha256:abcd\n'}
    release=CUSTOM|{'files':{k:hashlib.sha256(v).hexdigest() for k,v in files.items()}}
    files['release.json']=json.dumps(release).encode()
    with tarfile.open(path,'w:gz') as output:
        for name,data in files.items():
            item=tarfile.TarInfo(name);item.size=len(data);output.addfile(item,BytesIO(data))
        if extra: output.addfile(extra,BytesIO(b'x') if extra.isfile() else None)
    return sum(map(len,files.values()))

class UpdateTests(unittest.TestCase):
    def test_traversal_links_devices_duplicates_and_absolute_rejected(self):
        for name,kind in [('../escape',tarfile.REGTYPE),('/etc/passwd',tarfile.REGTYPE),('app/link',tarfile.SYMTYPE),('app/dev',tarfile.CHRTYPE),('app/device_admin.py',tarfile.REGTYPE),('app/.venv/sitecustomize.py',tarfile.REGTYPE),('.',tarfile.DIRTYPE)]:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);item=tarfile.TarInfo(name);item.type=kind;item.size=1 if item.isfile() else 0
                archive=root/'test.tgz';bundle(archive,item)
                with self.assertRaises((UpdateError,FileExistsError)): unpack(archive,root/'out',5000)
    def test_corruption_and_manifest_extra_file_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);archive=root/'test.tgz';bundle(archive);out=root/'out';unpack(archive,out,5000)
            manifest(out,CUSTOM)
            (out/'app/device_admin.py').write_text('corrupt')
            with self.assertRaises(UpdateError): manifest(out,CUSTOM)
    def test_version_replay_requires_explicit_downgrade(self):
        with self.assertRaises(UpdateError): compatible(CUSTOM,1000001)
        compatible(CUSTOM,1000001,True)
        with self.assertRaises(UpdateError): compatible(CUSTOM|{'architecture':'x86_64'})
    def engine(self,root):
        releases=root/'releases';old=releases/'old';new=releases/'new';old.mkdir(parents=True);new.mkdir()
        write_json(old/'release.json',{'version':'0.9.0','sequence':9000});write_json(new/'release.json',CUSTOM)
        current=root/'current'
        if os.name!='nt':current.symlink_to(old,target_is_directory=True)
        system=Mock();system.healthy.return_value=True;system.protected.return_value=None
        engine=Engine(root/'state',releases,current,system);engine.record(releaseSequence=1000000)
        if os.name=='nt':
            engine.current=PortableLink(old)
            engine.swap=lambda target:engine.current.symlink_to(engine.managed(target))
            engine.keep_previous=Mock()
        return engine,old,new,system
    def test_expired_authorization_never_stops_display(self):
        with tempfile.TemporaryDirectory() as folder:
            engine,old,new,system=self.engine(Path(folder))
            with self.assertRaises(UpdateError): engine.activate(new,lambda:{'activationExpiresAt':'2020-01-01T00:00:00Z'})
            system.stop.assert_not_called();self.assertEqual(engine.current.resolve(),old)
    def test_unpair_before_activation_never_stops_display(self):
        with tempfile.TemporaryDirectory() as folder:
            engine,old,new,system=self.engine(Path(folder))
            def rejected(): raise UpdateError('AUTHORIZATION_CHANGED')
            with self.assertRaises(UpdateError): engine.activate(new,rejected)
            system.stop.assert_not_called()
    def test_success_and_failed_health_rollback_keep_last_good(self):
        for healthy in (True,False):
            with self.subTest(healthy=healthy),tempfile.TemporaryDirectory() as folder:
                engine,old,new,system=self.engine(Path(folder));system.healthy.side_effect=[healthy,True]
                engine.activate(new,lambda:{'activationExpiresAt':'2099-01-01T00:00:00Z'})
                self.assertEqual(engine.current.resolve(),new if healthy else old)
                self.assertEqual(engine.journal['state'],'SUCCEEDED' if healthy else 'ROLLED_BACK')
                self.assertEqual(system.stop.call_count,1 if healthy else 2)
    def test_crash_recovery_is_durable_and_repeat_safe(self):
        for state in ('STOPPING','ACTIVATING','HEALTH_CHECK','ROLLING_BACK'):
            with self.subTest(state=state),tempfile.TemporaryDirectory() as folder:
                engine,old,new,system=self.engine(Path(folder));engine.swap(new);engine.record(previous=str(old),candidate=str(new),state=state)
                fresh=Engine(engine.home,engine.releases,Path(folder)/'current',system)
                if os.name=='nt':fresh.current=engine.current;fresh.swap=engine.swap
                fresh.recover();fresh.recover()
                self.assertEqual(fresh.current.resolve(),old);self.assertEqual(system.stop.call_count,1)
    def test_full_disk_leaves_current_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);engine,old,new,system=self.engine(root);archive=root/'test.tgz';bundle(archive)
            with patch('update_core.shutil.disk_usage',return_value=Mock(free=1)):
                with self.assertRaises(UpdateError):engine.stage({'id':str(uuid4())},archive,CUSTOM)
            self.assertEqual(engine.current.resolve(),old);system.build.assert_not_called()
    def test_operator_deferral_and_manual_paused_are_never_bypassed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);device=Device(root/'config.json','https://example.invalid',ROOT/'fonts');device.network_status={'state':'CONNECTED'}
            self.assertTrue(device.reserve_update()['reserved']);device.update_reserved_until=0
            device.apply({'mode':'manual'});self.assertFalse(device.manual.snapshot()['running'])
            self.assertEqual(device.reserve_update(True)['busyReason'],'LOCAL_EVENT')
            self.assertFalse(device.reserve_update(True)['reserved'])
            defer(root/'update-deferral.json',60);self.assertTrue(deferred(root/'update-deferral.json'))
            defer(root/'update-deferral.json',0);self.assertIsNone(deferred(root/'update-deferral.json'))
    def test_reservation_blocks_selection_and_settings_then_expires(self):
        with tempfile.TemporaryDirectory() as folder:
            device=Device(Path(folder)/'config.json','https://example.invalid',ROOT/'fonts');device.network_status={'state':'CONNECTED'}
            device.reserve_update()
            with self.assertRaises(ValueError): device.apply({'brightness':0.2})
            device.accept_control({'revision':1,'mode':'LOGO'},True);self.assertIsNone(device.control.desired)
            device.update_reserved_until=0;device.apply({'brightness':0.2})
    def test_network_change_never_bypassed_by_now(self):
        with tempfile.TemporaryDirectory() as folder:
            device=Device(Path(folder)/'config.json','https://example.invalid',ROOT/'fonts');device.network_status={'state':'CONNECTING'}
            self.assertEqual(device.reserve_update(True)['busyReason'],'NETWORK_BUSY')
            self.assertFalse(device.reserve_update(True)['reserved'])
    def test_requirement_urls_and_options_never_reach_pip(self):
        for row in ('x @ https://evil.invalid/x.whl','--find-links https://evil.invalid','-r /etc/passwd','x==1 --hash=sha256:bad'):
            with self.subTest(row=row),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);(root/'requirements.lock').write_text(row)
                system=System(Mock());system.command=Mock()
                with self.assertRaises(UpdateError):system.build(root)
                system.command.assert_not_called()

class TrustTests(unittest.TestCase):
    def repository(self,folder):
        from securesystemslib.signer import CryptoSigner
        from tuf.api.metadata import Metadata,Root,Targets,Snapshot,Timestamp,TargetFile,MetaFile
        from updater import Trust
        root=Path(folder);expires=datetime.now(timezone.utc)+timedelta(days=1)
        signers={role:CryptoSigner.generate_ed25519() for role in ('root','targets','snapshot','timestamp')}
        self.root_signer=signers['root']
        trust=Metadata(Root(expires=expires,consistent_snapshot=True))
        for role,signer in signers.items():trust.signed.add_key(signer.public_key,role)
        trust.sign(signers['root']);rootfile=root/'root.json';rootfile.write_bytes(trust.to_bytes())
        archive=root/'y-scores-v1.0.0-pi5-aarch64-py313.tar.gz';bundle(archive)
        target=TargetFile.from_file(archive.name,str(archive),['sha256']);target.unrecognized_fields['custom']=CUSTOM
        targets=Metadata(Targets(expires=expires,targets={archive.name:target}));targets.sign(signers['targets']);tdata=targets.to_bytes()
        snapshot=Metadata(Snapshot(expires=expires,meta={'targets.json':MetaFile.from_data(1,tdata,['sha256'])}));snapshot.sign(signers['snapshot']);sdata=snapshot.to_bytes()
        timestamp=Metadata(Timestamp(expires=expires,snapshot_meta=MetaFile.from_data(1,sdata,['sha256'])));timestamp.sign(signers['timestamp'])
        data={'metadata/timestamp.json':timestamp.to_bytes(),'metadata/1.snapshot.json':sdata,'metadata/1.targets.json':tdata,'targets/'+archive.name:archive.read_bytes()}
        class Response(BytesIO):pass
        api=Mock();api.base='https://example.invalid/api';api.identity.snapshot.return_value={'id':str(uuid4()),'credential':'private-test'}
        def opened(request,**kwargs):
            prefix=api.base+'/scoreboard-device/update-repository/'
            self.assertTrue(request.full_url.startswith(prefix))
            self.assertEqual(request.get_header('Authorization'),'Scoreboard private-test')
            key=request.full_url[len(prefix):]
            self.assertEqual(kwargs['timeout'],135 if key.startswith('targets/') else 15)
            if key not in data:raise HTTPError(request.full_url,404,'Missing',{},None)
            return Response(data[key])
        api.opener.open.side_effect=opened
        release=CUSTOM|dict(targetPath=archive.name,size=target.length,sha256=target.hashes['sha256'])
        return Trust(api,root/'cache',rootfile),release,data
    def test_real_tuf_signatures_and_target_hash_before_extract(self):
        with tempfile.TemporaryDirectory() as folder:
            trust,release,data=self.repository(folder)
            archive,custom=trust.download(release);self.assertTrue(archive.is_file());self.assertEqual(custom,CUSTOM)
    def test_corrupt_target_and_unsigned_timestamp_rejected(self):
        for corrupt_target in (True,False):
            with self.subTest(target=corrupt_target),tempfile.TemporaryDirectory() as folder:
                trust,release,data=self.repository(folder)
                key='targets/'+release['targetPath'] if corrupt_target else 'metadata/timestamp.json'
                data[key]=data[key]+b'corrupt' if corrupt_target else b'{}'
                with self.assertRaises(UpdateError):trust.download(release)
    def test_tuf_replay_of_older_timestamp_rejected(self):
        from tuf.api.metadata import Metadata
        with tempfile.TemporaryDirectory() as folder:
            trust,release,data=self.repository(folder);trust.download(release)
            timestamp=Metadata.from_bytes(data['metadata/timestamp.json']);timestamp.signed.version=0
            data['metadata/timestamp.json']=timestamp.to_bytes()
            with self.assertRaises(UpdateError):trust.download(release)
    def test_cached_renewed_root_survives_missing_upstream_root(self):
        from tuf.api.metadata import Metadata
        from securesystemslib.signer import CryptoSigner
        with tempfile.TemporaryDirectory() as folder:
            trust,release,data=self.repository(folder)
            # Renew with a new root key, cross-signed by old and new trusted keys.
            old=Metadata.from_file(trust.root)
            signer=CryptoSigner.generate_ed25519()
            renewed=Metadata.from_bytes(old.to_bytes());renewed.signed.version=2
            old_id=next(iter(renewed.signed.roles['root'].keyids));renewed.signed.revoke_key(old_id,'root');renewed.signed.add_key(signer.public_key,'root')
            # The fixture signer is retained separately for this cross-signing test.
            renewed.sign(self.root_signer);renewed.sign(signer,append=True)
            data['metadata/2.root.json']=renewed.to_bytes();trust.download(release)
            del data['metadata/2.root.json'];trust.download(release)
            self.assertEqual(Metadata.from_file(trust.home/'metadata/root.json').signed.version,2)

class DaemonTests(unittest.TestCase):
    def fixture(self,folder,busy=None,lose_health=False):
        from updater import Daemon
        root=Path(folder);engine,old,new,system=UpdateTests().engine(root)
        job=dict(id=str(uuid4()),state='QUEUED',revision=0,mode='IDLE',expiresAt='2099-01-01T00:00:00Z',release=CUSTOM,ownershipRevision=1)
        sequence=-1;states=[];lost=False
        def request(method,path,body=None):
            nonlocal sequence,lost
            if path=='/updates/current':return {'job':copy.deepcopy(job)}
            if path.endswith('/lease'):return dict(job=copy.deepcopy(job),leaseId='lease',expiresAt='2099-01-01T00:00:00Z',ownershipRevision=1)
            self.assertGreater(body['reportSequence'],sequence)
            if path.endswith('/activation'):
                self.assertEqual(job['state'],'READY');sequence=body['reportSequence'];job.update(state='ACTIVATING',revision=job['revision']+1);states.append('ACTIVATING')
                return dict(job=copy.deepcopy(job),activationExpiresAt='2099-01-01T00:00:00Z')
            state=body['state']
            if state=='HEALTH_CHECK' and lose_health and not lost:lost=True;raise RuntimeError('Simulated temporary network loss')
            allowed={'QUEUED':{'DOWNLOADING'},'DOWNLOADING':{'VERIFYING'},'VERIFYING':{'READY'},'READY':{'WAITING_FOR_IDLE'},'WAITING_FOR_IDLE':{'READY'},'ACTIVATING':{'HEALTH_CHECK','ROLLED_BACK'},'HEALTH_CHECK':{'SUCCEEDED','ROLLED_BACK'}}
            if state not in allowed.get(job['state'],set()):raise RuntimeError('Server rejects skipped stage')
            sequence=body['reportSequence'];job.update(state=state,revision=job['revision']+1);states.append(state)
            return copy.deepcopy(job)
        api=Mock();api.request.side_effect=request
        trust=Mock();trust.download.return_value=(root/'unused.tar.gz',CUSTOM)
        def stage(*args):engine.record(state='READY',candidate=str(new));return new
        engine.stage=stage
        admin=Mock();admin.reserve.return_value=dict(reserved=busy is None,busyReason=busy,networkBusy=busy=='NETWORK_BUSY')
        daemon=Daemon(api,trust,engine,admin,root,True)
        return daemon,states,job,old,new
    def test_complete_outbound_job_stage_order(self):
        with tempfile.TemporaryDirectory() as folder,patch('updater.install_lock',side_effect=lambda:nullcontext()):
            daemon,states,job,old,new=self.fixture(folder);daemon.tick()
            self.assertEqual(states,['DOWNLOADING','VERIFYING','READY','ACTIVATING','HEALTH_CHECK','SUCCEEDED'])
            self.assertEqual(daemon.engine.current.resolve(),new);self.assertEqual(job['state'],'SUCCEEDED')
    def test_lost_health_report_preserves_success_then_resumes_in_order(self):
        with tempfile.TemporaryDirectory() as folder,patch('updater.install_lock',side_effect=lambda:nullcontext()):
            daemon,states,job,old,new=self.fixture(folder,lose_health=True)
            with self.assertRaises(RuntimeError):daemon.tick()
            self.assertEqual(daemon.engine.journal['state'],'SUCCEEDED');self.assertEqual(job['state'],'ACTIVATING')
            self.assertEqual(daemon.engine.current.resolve(),new)
            daemon.tick();self.assertEqual(states[-2:],['HEALTH_CHECK','SUCCEEDED'])
    def test_local_busy_waits_without_stopping_writer_then_resumes(self):
        with tempfile.TemporaryDirectory() as folder,patch('updater.install_lock',side_effect=lambda:nullcontext()):
            daemon,states,job,old,new=self.fixture(folder,busy='LOCAL_EVENT');daemon.tick()
            self.assertEqual(job['state'],'WAITING_FOR_IDLE');daemon.engine.system.stop.assert_not_called()
            daemon.admin.reserve.return_value=dict(reserved=True,busyReason=None,networkBusy=False)
            daemon.tick();self.assertEqual(job['state'],'SUCCEEDED')
            self.assertEqual(states,['DOWNLOADING','VERIFYING','READY','WAITING_FOR_IDLE','READY','ACTIVATING','HEALTH_CHECK','SUCCEEDED'])

if __name__=='__main__':unittest.main()
