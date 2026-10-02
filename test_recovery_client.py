import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from device_identity import Identity
from platform_client import Platform, CredentialRejected

class RecoveryClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'identity.json'
        self.identity=Identity(self.path); self.identity.save('Court A')
        self.client=Platform(self.identity,'https://example.test/api-next'); self.client.revoked=True
        self.old=self.identity.snapshot()['credential']; self.device_id=self.identity.public()['id']
        self.status=dict(id=self.device_id,name='Court A',metadataRevision=1,lifecycle='REGISTERED',owner=None,revision=8)
    def tearDown(self): self.tmp.cleanup()
    def test_explicit_admin_code_replaces_secret_and_preserves_id_name_setup(self):
        self.identity.update(setupComplete=True)
        self.client.request=Mock(return_value=self.status)
        result=self.client.recover_credential('000012345678')
        args=self.client.request.call_args
        self.assertEqual(args.args[:2],('POST','/recoveries')); self.assertTrue(args.kwargs['anonymous'])
        self.assertEqual(args.args[2]['recoveryPin'],'000012345678')
        self.assertEqual(args.args[2]['id'],self.device_id)
        self.assertNotEqual(self.identity.snapshot()['credential'],self.old)
        self.assertEqual(self.identity.snapshot()['credential'],args.args[2]['credential'])
        self.assertEqual(self.identity.public()['id'],self.device_id)
        self.assertEqual(self.identity.public()['name'],'Court A'); self.assertTrue(self.identity.public()['setupComplete'])
        self.assertNotIn('pendingCredential',self.identity.snapshot())
        self.assertFalse(result['recoveryRequired']); self.assertFalse(result['selectionAvailable'])
        self.assertNotIn(self.identity.snapshot()['credential'],json.dumps(result))
        self.assertNotIn('000012345678',self.path.read_text())
    def test_lost_response_keeps_pending_secret_and_restart_confirms_server_commit(self):
        accepted={}
        def lost(method,path,body=None,**kwargs):
            accepted['credential']=body['credential']; raise OSError('response lost after commit')
        self.client.request=lost
        with self.assertRaises(OSError): self.client.recover_credential('000012345678')
        self.assertEqual(self.identity.snapshot()['credential'],self.old)
        restarted_identity=Identity(self.path); restarted=Platform(restarted_identity,'https://example.test/api-next')
        def confirm(method,path,body=None,**kwargs):
            self.assertEqual((method,path),('GET','/status'))
            self.assertEqual(kwargs['credential'],accepted['credential']); return self.status
        restarted.request=confirm; restarted.ensure_registration()
        self.assertEqual(restarted_identity.snapshot()['credential'],accepted['credential'])
        self.assertEqual(restarted_identity.public()['id'],self.device_id)
        self.assertFalse(restarted.public()['recoveryRequired'])
    def test_invalid_code_retains_original_secret_and_retry_reuses_pending_candidate(self):
        self.client.request=Mock(side_effect=ValueError('Recovery code invalid'))
        with self.assertRaises(ValueError): self.client.recover_credential('000012345678')
        pending=self.identity.snapshot()['pendingCredential']
        self.assertEqual(self.identity.snapshot()['credential'],self.old)
        self.client.request=Mock(side_effect=[CredentialRejected('Candidate not accepted'),self.status])
        self.client.recover_credential('999912345678')
        self.assertEqual(self.client.request.call_args.args[2]['credential'],pending)
        self.assertEqual(self.identity.snapshot()['credential'],pending)
    def test_invalid_server_response_never_overwrites_identity(self):
        self.client.request=Mock(side_effect=[self.status|dict(id='wrong-device')])
        with self.assertRaisesRegex(ValueError,'Invalid recovery response'): self.client.recover_credential('000012345678')
        for invalid in [self.status|dict(metadataRevision=None),self.status|dict(metadataRevision=True)]:
            self.client.request=Mock(side_effect=[CredentialRejected('Candidate not accepted'),invalid])
            with self.assertRaisesRegex(ValueError,'Invalid recovery response'): self.client.recover_credential('000012345678')
        self.assertEqual(self.identity.snapshot()['credential'],self.old)
        self.assertTrue(self.client.public()['recoveryRequired'])
    def test_failed_local_commit_can_be_completed_after_restart(self):
        self.client.request=Mock(return_value=self.status)
        with patch.object(self.identity,'complete_credential_recovery',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError): self.client.recover_credential('000012345678')
        pending=self.identity.snapshot()['pendingCredential']
        self.assertEqual(self.identity.snapshot()['credential'],self.old)
        restored=Identity(self.path); client=Platform(restored,'https://example.test/api-next'); client.request=Mock(return_value=self.status)
        self.assertTrue(client.resolve_pending_recovery())
        self.assertEqual(restored.snapshot()['credential'],pending)
    def test_active_credentials_and_bad_code_format_cannot_start_recovery(self):
        self.client.request=Mock(); self.client.revoked=False
        for code in ('12345678','letters','0000 1234 5678'):
            with self.assertRaises(ValueError): self.client.recover_credential(code)
        with self.assertRaisesRegex(ValueError,'still active'): self.client.recover_credential('000012345678')
        self.client.request.assert_not_called(); self.assertNotIn('pendingCredential',self.identity.snapshot())
    def test_candidate_not_accepted_never_resurrects_old_revoked_secret(self):
        self.identity.prepare_credential_recovery()
        self.client.request=Mock(side_effect=CredentialRejected('Candidate not accepted'))
        with self.assertRaises(CredentialRejected): self.client.ensure_registration()
        self.assertEqual(self.identity.snapshot()['credential'],self.old)
        self.assertFalse(self.client.selection_available())
    def test_pending_state_is_private_and_atomic(self):
        first=self.identity.prepare_credential_recovery()
        self.assertEqual(self.identity.prepare_credential_recovery(),first)
        self.assertNotIn(first,json.dumps(self.identity.public()))
        self.assertNotIn(self.old,json.dumps(self.identity.public()))
        with patch('device_identity.os.replace',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError): self.identity.complete_credential_recovery(first)
        self.assertEqual(Identity(self.path).snapshot()['credential'],self.old)
        self.assertEqual(Identity(self.path).snapshot()['pendingCredential'],first)

if __name__=='__main__': unittest.main()
