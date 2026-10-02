from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from io import BytesIO
import tempfile
import unittest
from unittest.mock import Mock, MagicMock
import json
import time
from device_identity import Identity
from platform_client import Platform, NoRedirect


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.identity=Identity(Path(self.tmp.name)/'identity.json'); self.identity.save('Board')
        self.client=Platform(self.identity,'https://example.test/api-next')
        self.status=dict(id=self.identity.public()['id'],name='Board',metadataRevision=1,
                         owner=None,ownershipState='UNOWNED',lifecycle='REGISTERED',revision=0)
    def tearDown(self): self.tmp.cleanup()
    def test_heartbeat_conflict_reopens_same_session_without_replaying_sequence(self):
        self.identity.update(platformEnabled=True)
        session='22222222-2222-4222-8222-222222222222'
        status=self.status|dict(owner={'subject':'owner'},telemetrySession=session)
        client=self.client
        client.status=status; client.registered_revision=1
        client.telemetry_session=session; client.session_opened=True; client.report_sequence=12
        client.telemetry_provider=lambda:dict(effectiveMode='LOGO')
        reports=[]
        def open_request(request,timeout):
            path=request.full_url.rsplit('/',1)[-1]
            if path=='heartbeat':
                body=json.loads(request.data); reports.append(body['reportSequence'])
                if len(reports)==1 or body['reportSequence']<=12:
                    raise HTTPError(request.full_url,409,'Conflict',{},BytesIO(b'{}'))
            response=MagicMock(); response.status=200
            response.read.return_value=json.dumps({'state':'NONE'} if path=='claim' else status).encode()
            response.__enter__.return_value=response
            return response
        client.opener=Mock(); client.opener.open.side_effect=open_request
        with self.assertRaises(ValueError): client.tick()
        client.tick()
        self.assertEqual(reports,[13,14])
        self.assertTrue(client.session_opened)
        self.assertTrue(client.selection_available())
    def test_tls_default_and_redirect_credentials_never_forwarded(self):
        with self.assertRaises(ValueError): Platform(self.identity,'http://example.test')
        Platform(self.identity,'http://localhost:8080/api-next',allow_http=True)
        for endpoint in ['https://user:secret@example.test','https://example.test/?credential=x','https://example.test/#x']:
            with self.assertRaises(ValueError): Platform(self.identity,endpoint)
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://attacker.test'))
    def test_lost_bootstrap_response_reuses_persisted_credential(self):
        calls=[]
        def request(method,path,body=None,anonymous=False):
            calls.append(body)
            if len(calls)==1: raise OSError('connection lost after commit')
            return self.status
        self.client.request=request
        with self.assertRaises(OSError): self.client.ensure_registration()
        restarted=Platform(Identity(self.identity.path),'https://example.test/api-next'); restarted.request=request
        restarted.ensure_registration()
        self.assertEqual(calls[0]['credential'],calls[1]['credential'])
        self.assertEqual(calls[0]['id'],calls[1]['id'])
    def test_offline_rename_after_restart_puts_new_metadata(self):
        self.identity.save('Renamed offline')
        self.client.request=Mock(side_effect=[self.status,self.status|dict(name='Renamed offline',metadataRevision=2)])
        self.client.ensure_registration()
        self.assertEqual(self.client.request.call_args_list[1].args[:2],('PUT','/metadata'))
        self.assertEqual(self.client.registered_revision,2)
    def test_leading_zero_pin_is_ephemeral_and_consumption_clears_it(self):
        expiry=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()
        self.client.request=Mock(side_effect=[self.status,dict(pairingPin='00123456',expiresAt=expiry)])
        self.assertEqual(self.client.create_claim()['claim']['pairingPin'],'00123456')
        restarted=Platform(Identity(self.identity.path),'https://example.test/api-next')
        self.assertIsNone(restarted.public()['claim'])
        self.client.accept(self.status|dict(owner={'displayName':'Owner'},ownershipState='OWNED'))
        self.assertIsNone(self.client.public()['claim'])
        self.assertNotIn('00123456',self.identity.path.read_text())
    def test_expired_pin_cleared_and_no_cloud_display_claim(self):
        self.client.claim=dict(pairingPin='00123456',expiresAt='2020-01-01T00:00:00Z')
        self.assertIsNone(self.client.public()['claim'])
        self.assertFalse(self.client.public()['displayControlAvailable'])
    def test_lost_regenerated_claim_never_displays_invalid_old_pin(self):
        self.client.status=self.status; self.client.registered_revision=1
        self.client.claim=dict(pairingPin='00123456',expiresAt=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat())
        self.client.request=Mock(side_effect=OSError('response lost after replacement'))
        with self.assertRaises(OSError): self.client.create_claim()
        self.assertIsNone(self.client.public()['claim'])
    def test_bootstrap_conflict_stops_outbound_retries_and_preserves_identity(self):
        self.identity.update(platformEnabled=True)
        credential=self.identity.snapshot()['credential']; device_id=self.identity.public()['id']
        self.client.opener=Mock()
        self.client.opener.open.side_effect=HTTPError('https://example.test',409,'Conflict',{},BytesIO(b'{}'))
        with self.assertRaisesRegex(ValueError,'administrator for recovery'): self.client.tick()
        with self.assertRaisesRegex(ValueError,'administrator for recovery'): self.client.tick()
        self.client.opener.open.assert_called_once()
        self.assertEqual(self.identity.snapshot()['credential'],credential)
        self.assertEqual(self.identity.public()['id'],device_id)
        self.assertTrue(self.client.registration_blocked)
    def test_owned_claim_conflict_does_not_terminalize_registration_worker(self):
        self.client.status=self.status; self.client.registered_revision=1
        self.client.opener=Mock()
        self.client.opener.open.side_effect=HTTPError('https://example.test',409,'Conflict',{},BytesIO(b'{}'))
        with self.assertRaises(ValueError): self.client.create_claim()
        self.assertFalse(self.client.registration_blocked)
        self.client.ensure_registration()
    def test_selection_requires_owned_enabled_fresh_healthy_registration(self):
        self.client.accept(self.status)
        self.identity.update(platformEnabled=True)
        self.assertFalse(self.client.selection_available())
        owned=self.status|dict(owner={'subject':'owner'},ownershipState='OWNED')
        self.client.accept(owned); self.assertTrue(self.client.selection_available())
        self.client.error='Platform unavailable'; self.assertFalse(self.client.selection_available())
        self.client.accept(owned); self.client.received=time.monotonic()-36
        self.assertFalse(self.client.selection_available())
        self.client.accept(owned); self.identity.update(platformEnabled=False)
        self.assertFalse(self.client.selection_available())
        self.identity.update(platformEnabled=True); self.client.accept(self.status)
        self.assertFalse(self.client.selection_available())
        self.client.accept(owned|dict(lifecycle='REVOKED'))
        self.assertFalse(self.client.selection_available())
    def test_disconnection_takes_effect_when_remote_cancellation_fails(self):
        self.identity.update(platformEnabled=True)
        self.client.accept(self.status|dict(owner={'subject':'owner'}))
        self.client.cancel_claim=Mock(side_effect=OSError('offline'))
        self.client.disable()
        self.assertFalse(self.identity.public()['platformEnabled'])
        self.assertFalse(self.client.selection_available())


if __name__=='__main__': unittest.main()
