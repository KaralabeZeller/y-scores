from pathlib import Path
import unittest
from unittest.mock import patch
from PIL import Image, ImageChops
from device_screens import Screens
from live_scoreboard import Renderer


class SetupScreenTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).parent
        self.renderer=Renderer(root/'fonts')
        self.screens=Screens(self.renderer,root/'assets/logo-white.png')
        self.network=dict(state='RECOVERY_HOTSPOT',recoverySsid='Y-Scores-Setup-ABCD',recoveryPassword='ACDEF-GHJKM')
    def test_short_password_uses_double_size_bold_font_and_fits_the_panels(self):
        image=self.screens.setup_screen(dict(setupComplete=True),'123456','board',self.network,8080,0)
        tile=self.renderer.tile(self.network['recoveryPassword'],(235,235,235),big=True)
        tile=tile.resize((tile.width*2,tile.height*2),Image.Resampling.NEAREST)
        x=(192-tile.width)//2
        self.assertIsNone(ImageChops.difference(image.crop((x,28,x+tile.width,28+tile.height)),tile).getbbox())
        self.assertLessEqual(tile.width,188)
        self.assertLessEqual(28+tile.height,64)
    def test_existing_password_uses_full_case_sensitive_lines_without_clipping(self):
        self.network['recoveryPassword']='aBcD_1234-LongPasswordXYZ'
        with patch.object(self.renderer,'tile',wraps=self.renderer.tile) as render:
            image=self.screens.setup_screen(dict(setupComplete=True),'123456','board',self.network,8080,0)
        parts=[call.args[0] for call in render.call_args_list if call.kwargs.get('big') and call.args[0]!=self.network['recoveryPassword']]
        self.assertEqual(''.join(parts),self.network['recoveryPassword'])
        self.assertEqual(image.size,(192,64))
        for part in parts:self.assertLessEqual(self.renderer.tile(part,(235,235,235),big=True).width,188)
    def test_recovery_access_page_uses_hotspot_address_and_hides_saved_pin(self):
        for complete in (False,True):
            with self.subTest(setupComplete=complete),patch.object(self.screens,'text',wraps=self.screens.text) as text:
                self.screens.setup_screen(dict(setupComplete=complete),'123456','board',self.network,8080,1)
                labels=[call.args[1] for call in text.call_args_list]
                self.assertIn('192.168.4.1:8080',labels)
                self.assertEqual('ADMIN PIN 123456' in labels,not complete)
                self.assertEqual('USE SAVED ADMIN PIN' in labels,complete)
    def test_normal_network_displays_ip_with_configured_port_before_hostname(self):
        network=dict(state='CONNECTED',adminUrls=['http://192.168.1.25:8081/'])
        with patch.object(self.screens,'text',wraps=self.screens.text) as text:
            self.screens.setup_screen(dict(setupComplete=False),'123456','board',network,8081,0)
            labels=[call.args[1] for call in text.call_args_list]
            self.assertEqual(labels[1],'192.168.1.25:8081')
            self.assertIn('board.local:8081',labels)
    def test_idle_logo_can_show_numeric_admin_address(self):
        with patch.object(self.screens,'text',wraps=self.screens.text) as text:
            image=self.screens.logo_screen('192.168.1.25:8081')
            self.assertEqual(image.size,(192,64))
            self.assertEqual(text.call_args.args[1],'192.168.1.25:8081')
    def test_normal_first_setup_preserves_hostname_and_pin(self):
        with patch.object(self.screens,'text',wraps=self.screens.text) as text:
            self.screens.setup_screen(dict(setupComplete=False),'123456','board',dict(state='CONNECTED'),8080,0)
            labels=[call.args[1] for call in text.call_args_list]
            self.assertIn('board.local:8080',labels)
            self.assertIn('ADMIN PIN 123456',labels)

if __name__=='__main__':unittest.main()
