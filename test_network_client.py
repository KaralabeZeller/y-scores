import json
import unittest
from unittest.mock import patch
from network_client import Network


class NetworkClientTests(unittest.TestCase):
    def response(self, payload):
        with patch('network_client.socket.AF_UNIX', 1, create=True), patch('network_client.socket.socket') as factory:
            factory.return_value.__enter__.return_value.recv.return_value = json.dumps(payload).encode() + b'\n'
            return Network().status()

    def test_connected_helper_with_empty_error_remains_available(self):
        payload = dict(available=True, state='CONNECTED', error='', confirmationRequired=False)
        self.assertEqual(self.response(payload), payload)

    def test_nonempty_helper_error_reports_unavailable(self):
        self.assertEqual(self.response(dict(error='Network helper unavailable')),
                         dict(available=False, state='UNAVAILABLE', error='Network helper unavailable'))


if __name__ == '__main__':
    unittest.main()
