import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import threading
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from device_model import DEFAULTS, validate_config, save_config, select_court
from device_admin import Auth, Server, Handler, Device, ROOT

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
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True); self.worker.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join(); self.tmp.cleanup()
    def request(self,path,body=None,cookie=None,origin=None,marker=True):
        headers={'Content-Type':'application/json'}
        if marker: headers['X-Scoreboard-Request']='1'
        if cookie: headers['Cookie']=cookie
        if origin: headers['Origin']=origin
        return urlopen(Request(self.base+path,headers=headers,data=json.dumps(body).encode() if body is not None else None),timeout=3)
    def login(self):
        with self.request('/api/login',{'pin':'123456'}) as response:
            return response.headers['Set-Cookie'].split(';')[0]
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

if __name__=='__main__': unittest.main()
