from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock
from unittest.mock import patch
from network_helper import NetworkManager, Recovery, RECOVERY_PASSWORD_ALPHABET


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.now=0
        self.backend=Mock(admin_port=8080)
        self.backend.text=NetworkManager.text
        self.backend.connected.return_value=False; self.backend.hotspot_active.return_value=False
        self.backend.isolation_active.return_value=False
        self.backend.candidate_ready.return_value=True
        self.backend.profile.return_value='trial'; self.backend.checkpoint.return_value='/checkpoint/1'
        self.path=Path(self.tmp.name)/'network.json'
        self.recovery=Recovery(self.backend,self.path,lambda:self.now)
    def tearDown(self): self.tmp.cleanup()
    def test_no_local_network_starts_unique_protected_hotspot_after_grace(self):
        first=self.recovery.data['password']
        self.recovery.tick(); self.backend.activate.assert_not_called()
        self.now=89; self.recovery.tick(); self.backend.activate.assert_not_called()
        self.now=91; self.recovery.tick()
        self.assertEqual(self.recovery.state,'RECOVERY_HOTSPOT')
        self.backend.isolation.assert_called_with(True)
        call=self.backend.profile.call_args
        self.assertEqual(call.args[1],first); self.assertTrue(call.kwargs['hotspot'])
        self.assertRegex(first,r'^[A-Z3-9]{5}-[A-Z3-9]{5}$')
        self.assertTrue(set(first.replace('-',''))<=set(RECOVERY_PASSWORD_ALPHABET))
        restarted=Recovery(self.backend,self.path,lambda:self.now)
        self.assertEqual(restarted.data['password'],first)
        self.assertEqual(restarted.status()['recoveryUrl'],'http://192.168.4.1:8080/')
    def test_existing_long_password_and_profile_survive_the_readability_upgrade(self):
        saved=dict(schemaVersion=1,ssid='Y-Scores-Setup-ABCD',password='aBcD_1234-LongPasswordXYZ',hotspotProfile='existing-profile')
        self.path.write_text(json.dumps(saved))
        restarted=Recovery(self.backend,self.path,lambda:self.now)
        self.backend.reset_mock();restarted.hotspot()
        self.assertEqual(restarted.status()['recoveryPassword'],saved['password'])
        self.backend.profile.assert_not_called()
        self.backend.activate.assert_called_once_with('existing-profile')
    def test_cloud_outage_alone_does_not_start_hotspot(self):
        self.backend.connected.return_value=True; self.now=999
        self.recovery.tick(); self.backend.activate.assert_not_called()
        self.assertEqual(self.recovery.state,'CONNECTED')
    def test_trial_requires_candidate_confirmation_and_clears_journal(self):
        self.recovery._connect('Venue','strongpassword',False)
        self.assertEqual(self.recovery.state,'AWAITING_CONFIRMATION')
        self.assertNotIn('strongpassword',self.path.read_text())
        self.backend.candidate_ready.return_value=False
        with self.assertRaises(ValueError): self.recovery.confirm()
        self.backend.commit.assert_not_called()
        self.backend.candidate_ready.return_value=True; self.recovery.confirm()
        self.backend.commit.assert_called_once()
        self.assertNotIn('trialProfile',self.path.read_text())
    def test_no_confirmation_rolls_back_deletes_only_trial(self):
        self.recovery._connect('Venue','strongpassword',False); self.now=76
        self.recovery.tick()
        self.backend.rollback.assert_called_once_with('/checkpoint/1')
        self.backend.delete_trial.assert_called_once_with('trial')
        self.backend.commit.assert_not_called()
    def test_restart_removes_interrupted_trial(self):
        self.recovery._connect('Venue','strongpassword',False)
        restarted=Recovery(self.backend,self.path,lambda:self.now)
        self.backend.rollback.assert_called_once_with('/checkpoint/1')
        self.backend.delete_trial.assert_called_once_with('trial')
        self.assertIsNone(restarted.pending)
    def test_invalid_keyfile_input_never_changes_network(self):
        for ssid,password in [('Venue\n[ipv4]','strongpassword'),('Venue','password\n123'),('Venue','short')]:
            with self.assertRaises(ValueError): self.recovery.connect(ssid,password)
        self.backend.checkpoint.assert_not_called()
    def test_country_and_fixed_interface_validation(self):
        for country in (None,'00','H;','INVALID'):
            with self.assertRaises(ValueError): NetworkManager(country=country)
        with self.assertRaises(ValueError): NetworkManager(interface='wlan0;command',country='HU')
    def test_candidate_failure_cannot_be_masked_by_working_ethernet(self):
        self.backend.connected.return_value=True
        self.backend.candidate_ready.return_value=False
        self.recovery._connect('Venue','strongpassword',False)
        self.assertEqual(self.recovery.state,'RECOVERING')
        self.backend.rollback.assert_called_once()
        self.backend.commit.assert_not_called()
    def test_slow_wifi_activation_never_extends_nm_checkpoint_window(self):
        def activate(_): self.now=80
        self.backend.activate.side_effect=activate
        self.recovery._connect('Venue','strongpassword',False)
        self.assertEqual(self.recovery.pending['deadline'],110)
    def test_ap_idle_is_fifteen_minutes_and_status_poll_does_not_extend_it(self):
        self.backend.hotspot_active.return_value=True
        self.recovery.hotspot_since=0
        self.now=899; self.recovery.status(); self.recovery.tick()
        self.backend.deactivate.assert_not_called()
        self.now=900; self.recovery.status(); self.recovery.tick()
        self.backend.deactivate.assert_called_once_with(self.recovery.data['hotspotProfile'])
    def test_only_authenticated_activity_renews_ap_idle_window(self):
        self.backend.hotspot_active.return_value=True
        self.recovery.hotspot_since=0
        self.now=899; self.recovery.authenticated_activity()
        self.now=900; self.recovery.tick(); self.backend.deactivate.assert_not_called()
        self.now=1799; self.recovery.tick(); self.backend.deactivate.assert_called_once()
    def test_ap_checkpoint_keeps_isolation_until_commit(self):
        self.backend.hotspot_active.return_value=True
        self.backend.isolation_active.return_value=True
        self.backend.reset_mock()
        self.recovery._connect('Venue','strongpassword',False)
        calls=self.backend.method_calls
        self.assertNotIn(('isolation',(False,),{}),calls)
        self.assertLess(calls.index(('isolation',(True,),{})),calls.index(('checkpoint',(),{})))
        self.backend.hotspot_active.return_value=False
        self.recovery.confirm()
        calls=self.backend.method_calls
        self.assertLess(calls.index(('commit',('/checkpoint/1',),{})),calls.index(('isolation',(False,),{})))
    def test_helper_dies_pending_checkpoint_ap_restore_remains_isolated(self):
        self.backend.hotspot_active.return_value=True; self.backend.isolation_active.return_value=True
        self.recovery._connect('Venue','strongpassword',False)
        self.backend.reset_mock()
        # Simulate independent NM rollback while the helper was stopped.
        self.backend.rollback.side_effect=ValueError('checkpoint already expired')
        restarted=Recovery(self.backend,self.path,lambda:self.now)
        self.assertEqual(restarted.state,'RECOVERY_HOTSPOT')
        self.assertNotIn(('isolation',(False,),{}),self.backend.method_calls)
        self.assertEqual(self.backend.method_calls[0],('isolation',(True,),{}))
    def test_rollback_restores_previous_station_isolation_state(self):
        self.recovery._connect('Venue','strongpassword',False)
        self.backend.reset_mock(); self.now=76; self.recovery.tick()
        self.assertIn(('isolation',(False,),{}),self.backend.method_calls)
        self.assertLess(self.backend.method_calls.index(('rollback',('/checkpoint/1',),{})),
                        self.backend.method_calls.index(('isolation',(False,),{})))
    def test_existing_hotspot_is_isolated_during_startup(self):
        self.backend.hotspot_active.return_value=True; self.backend.reset_mock()
        restarted=Recovery(self.backend,self.path,lambda:self.now)
        self.assertEqual(restarted.state,'RECOVERY_HOTSPOT')
        self.backend.isolation.assert_called_with(True)
    def test_failed_candidate_restores_ap_without_removing_isolation(self):
        self.backend.hotspot_active.return_value=True; self.backend.isolation_active.return_value=True
        self.backend.activate.side_effect=ValueError('Wrong Wi-Fi password')
        self.backend.reset_mock(); self.recovery._connect('Venue','strongpassword',False)
        self.backend.rollback.assert_called_once()
        self.assertEqual(self.recovery.state,'RECOVERING')
        self.assertNotIn(('isolation',(False,),{}),self.backend.method_calls)
    def test_firewall_cleanup_failure_does_not_delete_confirmed_network(self):
        self.recovery._connect('Venue','strongpassword',False)
        def isolation(enabled):
            if not enabled: raise ValueError('Firewall cleanup failed')
        self.backend.isolation.side_effect=isolation
        with self.assertRaises(ValueError): self.recovery.confirm()
        self.assertIsNone(self.recovery.pending)
        self.assertNotIn('trialProfile',self.path.read_text())
        self.backend.delete_trial.assert_not_called()
    def test_checkpoint_firewall_only_accepts_dhcp_and_local_ipv4_admin(self):
        backend=NetworkManager(country='HU')
        with patch('network_helper.subprocess.run',side_effect=[Mock(returncode=1),Mock(returncode=0)]) as command:
            backend.isolation(True)
        rules=command.call_args.kwargs['input']
        self.assertIn('udp sport 67 udp dport 68 accept',rules)
        self.assertIn('fib daddr type local tcp dport 8080 accept',rules)
        self.assertNotIn('tcp dport 22 accept',rules)
        self.assertIn('iifname "wlan0" drop',rules)
        self.assertIn('oifname "wlan0" drop',rules)
    def test_root_helper_socket_group_does_not_require_chown_capability(self):
        root=Path(__file__).parent
        unit=(root/'scripts/y-scores-network.service').read_text()
        source=(root/'network_helper.py').read_text()
        self.assertIn('User=root\nGroup=yscores',unit)
        self.assertIn('RuntimeDirectoryMode=0750',unit)
        self.assertIn('CapabilityBoundingSet=CAP_NET_ADMIN',unit)
        self.assertNotIn('CAP_CHOWN',unit)
        self.assertNotIn('os.chown(',source)
        self.assertIn('socket.SO_PEERCRED',source)


if __name__=='__main__': unittest.main()
