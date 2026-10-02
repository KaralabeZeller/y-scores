"""Unprivileged bridge to the optional, separately installed network helper."""
import json
import socket


class Network:
    def __init__(self, path='/run/y-scores-network/control.sock'):
        self.path = path
    def request(self, action, **values):
        if not hasattr(socket, 'AF_UNIX'): raise ValueError('Network setup requires the Raspberry Pi helper')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            try: client.connect(self.path)
            except OSError: raise ValueError('Network helper unavailable; use Ethernet or configure Wi-Fi on the Pi') from None
            client.sendall(json.dumps(dict(action=action, **values)).encode() + b'\n')
            data = bytearray()
            while b'\n' not in data:
                part = client.recv(4096)
                if not part: raise ValueError('Network helper disconnected')
                data.extend(part)
                if len(data) > 32768: raise ValueError('Network helper response too large')
            response = json.loads(data)
            if response.get('error'): raise ValueError(response['error'])
            return response
    def status(self):
        try: return self.request('status')
        except (ValueError, OSError) as error: return dict(available=False, state='UNAVAILABLE', error=str(error))
