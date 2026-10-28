import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import display_test
from device_admin import Device, ROOT
from manual_match import Conflict
from matrix_output import fill_framebuffer, dimensions


class PatternTests(unittest.TestCase):
    def test_order_and_duration(self):
        elapsed = 0
        for phase, duration in display_test.STEPS:
            self.assertEqual(display_test.status(elapsed)['phase'], phase)
            image = display_test.render(elapsed)
            self.assertEqual(image.size, (192, 64))
            self.assertEqual(image.mode, 'RGB')
            elapsed += duration
        self.assertEqual(elapsed, 60)
        self.assertFalse(display_test.status(60)['active'])
        self.assertFalse(display_test.status(None)['active'])

    def test_identification_lights_only_the_selected_panel(self):
        for panel in range(3):
            image = display_test.render(panel * 3)
            for tile in range(3):
                crop = image.crop((tile * 64, 0, tile * 64 + 64, 64))
                self.assertEqual(crop.getbbox() is not None, tile == panel)
            self.assertEqual(image.getpixel((panel * 64 + 32, 3)), (255, 255, 255))

    def test_solid_colors_and_matching_gradients(self):
        for i, color in enumerate(display_test.COLORS.values()):
            self.assertEqual(display_test.render(9 + i * 3).getcolors(), [(192 * 64, color)])
        for elapsed in (33, 41):
            image = display_test.render(elapsed)
            panels = [image.crop((i * 64, 0, i * 64 + 64, 64)).tobytes() for i in range(3)]
            self.assertEqual(panels[0], panels[1])
            self.assertEqual(panels[1], panels[2])
        gray = display_test.render(41)
        self.assertEqual(gray.getpixel((0, 32)), (0, 0, 0))
        self.assertEqual(gray.getpixel((63, 32)), (255, 255, 255))

    def test_pixel_patterns_cover_every_pixel(self):
        first = np.asarray(display_test.render(46)).astype(int)
        second = np.asarray(display_test.render(47)).astype(int)
        self.assertTrue(np.all(first + second == 255))
        covered = np.zeros((64, 192), dtype=bool)
        for frame in range(160):
            covered |= np.asarray(display_test.render(52 + frame / 20))[:, :, 0] > 0
        self.assertTrue(covered.all())

    def test_identification_uses_existing_mapping_for_both_topologies(self):
        for topology in ('parallel', 'chain'):
            width, height, _ = dimensions(topology)
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            fill_framebuffer(frame, display_test.render(0), .08, topology, [2, 1, 0], [180, 0, 0])
            self.assertEqual(frame.max(), 20)
            if topology == 'parallel':
                self.assertFalse(frame[:128].any())
                self.assertTrue(frame[128:].any())
            else:
                self.assertFalse(frame[:, :128].any())
                self.assertTrue(frame[:, 128:].any())


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'config.json'
        self.device = Device(self.path, 'https://example.invalid', ROOT / 'fonts')

    def test_expiry_stop_restart_and_settings_preservation(self):
        self.device.apply({'mode': 'blank', 'brightness': .12})
        saved = {p.name: p.read_bytes() for p in self.path.parent.iterdir() if p.is_file()}
        control = copy.deepcopy(self.device.control.value)
        with patch('device_admin.time.monotonic', return_value=100):
            self.assertTrue(self.device.set_display_test({'action': 'start'})['active'])
            image, brightness = self.device.render()
            self.assertIsNotNone(image.getbbox())
            self.assertEqual(brightness, .12)
            self.assertEqual(self.device.rendered_mode, 'TEST')
        with patch('device_admin.time.monotonic', return_value=160):
            self.assertFalse(self.device.display_test_status()['active'])
            image, _ = self.device.render()
            self.assertIsNone(image.getbbox())
        self.device.set_display_test({'action': 'start'})
        self.device.set_display_test({'action': 'stop'})
        self.assertIsNone(self.device.render()[0].getbbox())
        self.device.set_display_test({'action': 'start'})
        restarted = Device(self.path, 'https://example.invalid', ROOT / 'fonts')
        self.assertFalse(restarted.display_test_status()['active'])
        self.assertEqual(control, self.device.control.value)
        self.assertEqual(saved, {p.name: p.read_bytes() for p in self.path.parent.iterdir() if p.is_file()})

    def test_test_does_not_acknowledge_cloud_and_returns_to_latest_assignment(self):
        self.device.platform_available = lambda: True
        self.device.set_display_test({'action': 'start'})
        self.device.accept_control(dict(mode='LOGO', revision=1), True)
        self.device.render()
        self.assertIsNone(self.device.control.ack)
        self.assertEqual(self.device.telemetry()['effectiveMode'], 'UNKNOWN')
        self.device.set_display_test({'action': 'stop'})
        self.device.render()
        self.assertEqual(self.device.control.ack['status'], 'APPLIED')

    def test_updates_and_tests_are_mutually_exclusive(self):
        self.device.network_status = {'state': 'CONNECTED'}
        self.device.set_display_test({'action': 'start'})
        self.assertEqual(self.device.reserve_update(now=True)['busyReason'], 'LOCAL_EVENT')
        self.device.set_display_test({'action': 'stop'})
        self.assertTrue(self.device.reserve_update()['reserved'])
        with self.assertRaises(Conflict):
            self.device.set_display_test({'action': 'start'})

    def test_invalid_commands_do_not_start_test(self):
        for value in ({}, {'action': 'oops'}, {'action': True}, {'action': 'start', 'brightness': 1}):
            with self.assertRaises(ValueError):
                self.device.set_display_test(value)
            self.assertFalse(self.device.display_test_status()['active'])
