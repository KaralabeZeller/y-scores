from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from device_admin import Device, PlatformRequired, ROOT
from device_model import DEFAULTS, save_config

class DeviceGatingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.device=Device(Path(self.tmp.name)/'config.json','https://example.test',ROOT/'fonts')
        self.device.catalog=Mock()
    def tearDown(self): self.tmp.cleanup()
    def test_all_platform_modes_rejected_without_network_reads(self):
        for mode in ('match','court','schedule'):
            with self.assertRaises(PlatformRequired):
                self.device.apply(DEFAULTS|dict(mode=mode,match_id='00000000-0000-0000-0000-000000000001',court_id='00000000-0000-0000-0000-000000000002'))
        self.device.catalog.get.assert_not_called(); self.device.catalog.schedule.assert_not_called()
    def test_lost_pairing_hides_old_match_immediately_and_stops_feed(self):
        self.device.config=DEFAULTS|dict(mode='match',match_id='00000000-0000-0000-0000-000000000001')
        feed=Mock(); self.device.feed=feed; self.device.active_id=self.device.config['match_id']
        image,_=self.device.render()
        self.assertEqual(image.size,(192,64)); feed.stop.set.assert_called_once(); feed.view.assert_not_called()
        state=self.device.state()
        self.assertEqual(state['effective_mode'],'logo'); self.assertIsNone(state['match']); self.assertIsNone(state['active_match_id'])
    def test_route_never_fetches_saved_platform_selection_while_unpaired(self):
        self.device.config=DEFAULTS|dict(mode='schedule')
        self.device.stop=Mock(); self.device.stop.is_set.side_effect=[False,True]
        self.device.route()
        self.device.catalog.schedule.assert_not_called(); self.device.catalog.get.assert_not_called()
    def test_manual_render_and_state_work_without_platform(self):
        self.device.apply(dict(mode='manual'))
        self.device.manual.command(dict(action='goal',revision=0,team=1,delta=1))
        self.device.render(); state=self.device.state()
        self.assertFalse(state['platform_available']); self.assertEqual(state['match']['teams'][1]['score'],1)
        self.assertEqual(state['effective_mode'],'manual')
        self.device.catalog.get.assert_not_called()
    def test_changing_screen_pauses_manual_clock(self):
        self.device.apply(dict(mode='manual'))
        self.device.manual.command(dict(action='start',revision=0))
        self.device.apply(dict(mode='logo'))
        self.assertFalse(self.device.manual.snapshot()['running'])
    def test_paired_connected_cannot_select_platform_match_locally(self):
        self.device.platform_available=lambda:True
        identity='00000000-0000-0000-0000-000000000001'
        self.device.catalog.get.return_value={'id':identity}
        with self.assertRaises(PlatformRequired): self.device.apply(dict(mode='match',match_id=identity))
        self.device.catalog.get.assert_not_called()

if __name__=='__main__': unittest.main()
