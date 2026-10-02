import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import threading
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from device_model import DEFAULTS, validate_config, save_config, select_court
from device_admin import Auth, Server, Handler, Device, ROOT, existing_installation
from device_identity import Identity

A='11111111-1111-4111-8111-111111111111'
B='22222222-2222-4222-8222-222222222222'
class SettingsTests(unittest.TestCase):
    def test_invalid_settings_never_replace_saved_config(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'; save_config(path,DEFAULTS)
            original=path.read_text()
            for values in [dict(mode='court',court_id=''),dict(mode='oops'),dict(brightness=float('nan')),dict(match_id='oops'),dict(timezone='No/SuchZone')]:
                with self.subTest(values=values), self.assertRaises(ValueError): save_config(path,DEFAULTS|values)
                self.assertEqual(path.read_text(),original)
    def test_new_device_starts_on_logo_with_bundled_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            device=Device(Path(folder)/'config.json','https://example.invalid',ROOT/'fonts')
            image,brightness=device.render()
            self.assertEqual(device.settings()['mode'],'logo')
            self.assertEqual(device.settings()['match_id'],'')
            self.assertEqual(image.size,(192,64))
            self.assertIsNotNone(image.getbbox())
            self.assertEqual(brightness,.08)
    def test_pin_is_created_once_and_preserved_on_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'pin.txt'
            first=Auth(path); second=Auth(path)
            self.assertEqual(first.pin,second.pin)
            self.assertEqual(len(first.pin),6)
            self.assertTrue(first.pin.isdigit())
    def test_existing_pin_without_config_suppresses_first_run_disclosure(self):
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'config.json'; pin=Path(folder)/'pin.txt'
            self.assertFalse(existing_installation(config,pin))
            pin.write_text('123456')
            identity=Identity(Path(folder)/'identity.json',legacy=existing_installation(config,pin))
            self.assertTrue(identity.public()['setupComplete'])
            self.assertFalse(config.exists())
    def test_roundtrip_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'; config=DEFAULTS|dict(mode='court',court_id=A,tournament_id=B)
            save_config(path,config); self.assertEqual(validate_config(json.loads(path.read_text())),config)
    def test_court_prefers_paused_live_match_over_earlier_scheduled(self):
        matches=[dict(id='early',court=dict(id=A),status='SCHEDULED',matchTime='2026-01-01'),
                 dict(id='live',court=dict(id=A),status='in_progress',matchTime='2026-01-02'),
                 dict(id='other',court=dict(id=B),status='RUNNING',matchTime='2025')]
        selected,live=select_court(matches,A); self.assertEqual(selected['id'],'live'); self.assertTrue(live)
        selected,live=select_court(matches,A,finished={'live'}); self.assertEqual(selected['id'],'early'); self.assertFalse(live)
    def test_completed_court_has_no_upcoming_and_retains_current_live(self):
        matches=[dict(id='done',court=dict(id=A),status='finished')]
        self.assertEqual(select_court(matches,A),(None,False))
        matches += [dict(id=x,court=dict(id=A),status='PAUSED') for x in ('one','two')]
        self.assertEqual(select_court(matches,A,'two')[0]['id'],'two')

class AdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); path=Path(self.tmp.name)/'pin.txt'; path.write_text('123456')
        self.server=Server(('127.0.0.1',0),Handler); self.server.auth=Auth(path)
        self.server.device=Mock(); self.server.device.state.return_value={'config':DEFAULTS}
        self.server.device.apply.side_effect=lambda value: validate_config(value)
        self.server.identity=Identity(Path(self.tmp.name)/'device-identity.json')
        self.server.platform=Mock(); self.server.platform.public.return_value={'status':None,'claim':None}
        self.server.network=Mock(); self.server.network.status.return_value={'available':False,'state':'UNAVAILABLE'}
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True); self.worker.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.csrf=''
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join(); self.tmp.cleanup()
    def request(self,path,body=None,cookie=None,origin=None,marker=True):
        headers={'Content-Type':'application/json'}
        if marker: headers['X-Scoreboard-Request']='1'
        if self.csrf: headers['X-Scoreboard-CSRF']=self.csrf
        if cookie: headers['Cookie']=cookie
        if origin: headers['Origin']=origin
        return urlopen(Request(self.base+path,headers=headers,data=json.dumps(body).encode() if body is not None else None),timeout=3)
    def login(self):
        with self.request('/api/login',{'pin':'123456'}) as response:
            self.csrf=json.load(response)['csrfToken']
            return response.headers['Set-Cookie'].split(';')[0]
    def test_authenticated_mutations_require_csrf(self):
        cookie=self.login(); self.csrf=''
        with self.assertRaises(HTTPError) as error: self.request('/api/config',DEFAULTS,cookie)
        self.assertEqual(error.exception.code,403); error.exception.close()
        self.server.device.apply.assert_not_called()
    def test_return_to_platform_requires_pin_session_and_csrf(self):
        self.server.device.resume_platform.return_value={'local_takeover':False}
        with self.assertRaises(HTTPError) as error: self.request('/api/platform/resume',{})
        self.assertEqual(error.exception.code,401); error.exception.close()
        cookie=self.login(); token=self.csrf; self.csrf=''
        with self.assertRaises(HTTPError) as error: self.request('/api/platform/resume',{},cookie)
        self.assertEqual(error.exception.code,403); error.exception.close()
        self.server.device.resume_platform.assert_not_called()
        self.csrf=token
        with self.request('/api/platform/resume',{},cookie) as response:
            self.assertFalse(json.load(response)['local_takeover'])
        self.server.device.resume_platform.assert_called_once()
    def test_dns_rebinding_host_blocked(self):
        request=Request(self.base+'/api/login',data=json.dumps({'pin':'123456'}).encode(),headers={'Content-Type':'application/json','X-Scoreboard-Request':'1','Host':'attacker.example:'+str(self.server.server_port)})
        with self.assertRaises(HTTPError) as error: urlopen(request,timeout=3)
        self.assertEqual(error.exception.code,403); error.exception.close()
    def test_setup_returns_numeric_address_using_the_actual_bound_port(self):
        cookie=self.login()
        with self.request('/api/setup',cookie=cookie) as response:value=json.load(response)
        self.assertEqual(value['access']['urls'],[self.base+'/'])
        self.assertIsNone(value['access']['hostnameUrl'])
    def test_setup_status_never_exposes_device_secret_or_initial_admin_pin(self):
        cookie=self.login()
        with self.request('/api/setup',cookie=cookie) as response:
            raw=response.read().decode(); value=json.loads(raw)
        self.assertNotIn(self.server.identity.snapshot()['credential'],raw)
        self.assertNotIn('123456',raw)
        self.assertFalse(value['identity']['setupComplete'])
    def test_identity_locked_and_setup_completion_requires_name(self):
        cookie=self.login()
        with self.assertRaises(HTTPError) as error: self.request('/api/setup/complete',{},cookie)
        self.assertEqual(error.exception.code,400); error.exception.close()
        with self.request('/api/identity',{'name':'Court A','id':A},cookie) as response:
            self.assertEqual(json.load(response)['id'],A)
        with self.assertRaises(HTTPError) as error: self.request('/api/identity',{'name':'Court B','id':B},cookie)
        self.assertEqual(error.exception.code,400); error.exception.close()
        with self.request('/api/setup/complete',{},cookie) as response:
            self.assertTrue(json.load(response)['setupComplete'])
    def test_pairing_and_network_actions_require_local_session_and_csrf(self):
        cookie=self.login(); self.csrf=''
        self.server.network.reset_mock()
        for route in ('/api/platform/claim','/api/network/connect'):
            with self.assertRaises(HTTPError) as error: self.request(route,{},cookie)
            self.assertEqual(error.exception.code,403); error.exception.close()
        self.server.platform.create_claim.assert_not_called()
        self.server.network.request.assert_not_called()
    def test_admin_requires_pin_and_allows_authenticated_apply(self):
        with self.assertRaises(HTTPError) as error: self.request('/api/status')
        self.assertEqual(error.exception.code,401); error.exception.close()
        cookie=self.login()
        with self.request('/api/config',DEFAULTS|dict(mode='logo'),cookie) as response:
            self.assertEqual(json.load(response)['mode'],'logo')
        self.server.device.apply.assert_called_once()
    def test_cross_origin_configuration_blocked(self):
        cookie=self.login()
        with self.assertRaises(HTTPError) as error: self.request('/api/config',DEFAULTS,cookie,origin='https://unrelated.example')
        self.assertEqual(error.exception.code,403); error.exception.close(); self.server.device.apply.assert_not_called()
    def test_pin_rate_limit(self):
        for _ in range(5):
            with self.assertRaises(ValueError): self.server.auth.login('000000','test')
        with self.assertRaisesRegex(ValueError,'Too many'): self.server.auth.login('123456','test')
    def test_logout_invalidates_session(self):
        cookie=self.login()
        with self.request('/api/logout',{},cookie): pass
        with self.assertRaises(HTTPError) as error: self.request('/api/status',cookie=cookie)
        self.assertEqual(error.exception.code,401); error.exception.close()
    def test_catalog_endpoints_are_removed_without_public_requests(self):
        from device_admin import PlatformRequired
        cookie=self.login()
        self.server.device.require_platform.side_effect=PlatformRequired('Pair this device')
        for path in ('/api/catalog','/api/tournaments'):
            with self.assertRaises(HTTPError) as error: self.request(path,cookie=cookie)
            self.assertEqual(error.exception.code,404); error.exception.close()
        self.server.device.catalog.get.assert_not_called()
        self.server.device.catalog.schedule.assert_not_called()
    def test_manual_requires_pin_csrf_active_mode_and_current_revision(self):
        from manual_match import ManualMatch
        self.server.device.manual=ManualMatch(Path(self.tmp.name)/'manual.json')
        self.server.device.apply_lock=threading.Lock()
        self.server.device.settings.return_value=DEFAULTS|dict(mode='manual')
        command=dict(action='goal',team=0,delta=1,revision=0)
        with self.assertRaises(HTTPError) as error: self.request('/api/manual',command)
        self.assertEqual(error.exception.code,401); error.exception.close()
        cookie=self.login(); token=self.csrf; self.csrf=''
        with self.assertRaises(HTTPError) as error: self.request('/api/manual',command,cookie)
        self.assertEqual(error.exception.code,403); error.exception.close(); self.csrf=token
        with self.request('/api/manual',command,cookie) as response:
            self.assertEqual(json.load(response)['teams'][0]['score'],1)
        with self.assertRaises(HTTPError) as error: self.request('/api/manual',command,cookie)
        self.assertEqual(error.exception.code,409); error.exception.close()
        self.server.device.settings.return_value=DEFAULTS
        with self.assertRaises(HTTPError) as error: self.request('/api/manual',command|dict(revision=1),cookie)
        self.assertEqual(error.exception.code,400); error.exception.close()
        self.assertEqual(self.server.device.manual.snapshot()['teams'][0]['score'],1)
    def test_credential_recovery_requires_local_pin_and_csrf(self):
        request={'recoveryPin':'000012345678'}
        with self.assertRaises(HTTPError) as error: self.request('/api/platform/recover',request)
        self.assertEqual(error.exception.code,401); error.exception.close()
        cookie=self.login(); token=self.csrf; self.csrf=''
        with self.assertRaises(HTTPError) as error: self.request('/api/platform/recover',request,cookie)
        self.assertEqual(error.exception.code,403); error.exception.close()
        self.server.platform.recover_credential.assert_not_called(); self.csrf=token
        self.server.platform.recover_credential.return_value={'recoveryRequired':False,'claim':None}
        with self.request('/api/platform/recover',request,cookie) as response:
            self.assertFalse(json.load(response)['recoveryRequired'])
        self.server.platform.recover_credential.assert_called_once_with('000012345678')

if __name__=='__main__': unittest.main()
