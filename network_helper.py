"""Optional root helper. Fixed NetworkManager operations; never runs browser commands.

Requires Linux, NetworkManager checkpoint support, busctl and nmcli. Install only
after validating AP/client transitions on the supported Pi OS and radio.
"""
import json
import os
from pathlib import Path
import re
import secrets
import socket
import socketserver
import struct
import subprocess
import threading
import time
from uuid import uuid4
from device_identity import protected_write


class NetworkManager:
    def __init__(self, interface='wlan0', directory='/etc/NetworkManager/system-connections', country=None, admin_port=8080):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,16}', interface): raise ValueError('Invalid fixed Wi-Fi interface')
        self.interface = interface
        self.directory = Path(directory)
        if not country or not re.fullmatch(r'[A-Z]{2}', country) or country == '00':
            raise ValueError('Configure a valid Wi-Fi regulatory country before enabling recovery')
        if type(admin_port) is not int or not 1 <= admin_port <= 65535: raise ValueError('Invalid fixed admin port')
        self.country, self.admin_port = country, admin_port
    def command(self, *args):
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=40, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
        if result.returncode: raise ValueError('NetworkManager operation failed; previous network remains recoverable')
        return result.stdout.strip()
    def nm(self, *args): return self.command('/usr/bin/nmcli', '--wait', '30', *args)
    def checkpoint(self):
        value = self.command('/usr/bin/busctl', 'call', 'org.freedesktop.NetworkManager',
                             '/org/freedesktop/NetworkManager', 'org.freedesktop.NetworkManager',
                             'CheckpointCreate', 'aouu', '0', '120', '0')
        found = re.fullmatch(r'o "(/org/freedesktop/NetworkManager/Checkpoint/[0-9]+)"', value)
        if not found: raise ValueError('NetworkManager checkpoint support required')
        return found[1]
    def checkpoint_action(self, path, method):
        if not re.fullmatch(r'/org/freedesktop/NetworkManager/Checkpoint/[0-9]+', path): raise ValueError('Invalid checkpoint')
        return self.command('/usr/bin/busctl', 'call', 'org.freedesktop.NetworkManager',
                            '/org/freedesktop/NetworkManager', 'org.freedesktop.NetworkManager', method, 'o', path)
    def rollback(self, checkpoint): self.checkpoint_action(checkpoint, 'CheckpointRollback')
    def commit(self, checkpoint): self.checkpoint_action(checkpoint, 'CheckpointDestroy')
    def connected(self):
        rows = self.nm('-t', '-f', 'TYPE,STATE', 'device', 'status').splitlines()
        # Connectivity is local link state. A cloud outage never triggers the AP.
        return 'ethernet:connected' in rows or ('wifi:connected' in rows and not self.hotspot_active())
    def candidate_ready(self, profile):
        return (self.nm('-g', 'GENERAL.CON-UUID', 'device', 'show', self.interface) == profile and
                bool(self.nm('-g', 'IP4.ADDRESS', 'device', 'show', self.interface)))
    def isolation(self, enabled):
        # A dedicated table touches only the recovery Wi-Fi interface. Do not
        # flush host rules or rely on NM's shared-network NAT to isolate clients.
        exists = self.isolation_active()
        if not enabled:
            if exists: self.command('/usr/sbin/nft', 'delete', 'table', 'inet', 'y_scores_recovery')
            return
        if exists: return
        rules = ('table inet y_scores_recovery {\n'
                 ' chain input { type filter hook input priority -10; policy accept;\n'
                 f'  iifname "{self.interface}" udp dport 67 accept\n'
                 f'  iifname "{self.interface}" udp sport 67 udp dport 68 accept\n'
                 f'  iifname "{self.interface}" meta nfproto ipv4 fib daddr type local tcp dport {self.admin_port} accept\n'
                 f'  iifname "{self.interface}" drop\n }}\n'
                 ' chain forward { type filter hook forward priority -10; policy accept;\n'
                 f'  iifname "{self.interface}" drop\n  oifname "{self.interface}" drop\n }}\n}}\n')
        result = subprocess.run(['/usr/sbin/nft', '-f', '-'], input=rules, text=True, capture_output=True, timeout=5)
        if result.returncode: raise ValueError('Recovery firewall could not be installed; hotspot was not opened')
    def isolation_active(self):
        return subprocess.run(['/usr/sbin/nft', 'list', 'table', 'inet', 'y_scores_recovery'],
                              capture_output=True, timeout=5).returncode == 0
    def hotspot_active(self):
        return self.nm('-g', 'GENERAL.CONNECTION', 'device', 'show', self.interface) == 'Y-Scores recovery'
    def scan(self):
        rows = self.nm('--escape', 'no', '-t', '-f', 'SSID', 'device', 'wifi', 'list', 'ifname', self.interface).splitlines()
        return sorted(set(row for row in rows if row and len(row.encode()) <= 32))[:100]
    @staticmethod
    def text(value, label, limit):
        if not isinstance(value, str) or not value or len(value.encode('utf-8')) > limit or any(ord(c) < 32 or c == '\\' for c in value):
            raise ValueError('Invalid ' + label)
        return value
    def profile(self, ssid, password, hidden=False, hotspot=False):
        ssid = self.text(ssid, 'SSID', 32)
        password = self.text(password, 'Wi-Fi password', 63)
        if len(password) < 8: raise ValueError('WPA password must have 8 to 63 characters')
        if type(hidden) is not bool: raise ValueError('Invalid hidden network setting')
        if hotspot:
            self.command('/usr/sbin/iw', 'reg', 'set', self.country)
            if 'country ' + self.country + ':' not in self.command('/usr/sbin/iw', 'reg', 'get'):
                raise ValueError('Wi-Fi regulatory country was not accepted; hotspot was not opened')
        identifier = str(uuid4())
        # Create a separate profile. Existing profiles are never overwritten/deleted.
        path = self.directory / ('y-scores-' + identifier + '.nmconnection')
        content = ('[connection]\nid=' + ('Y-Scores recovery' if hotspot else 'Y-Scores Wi-Fi ' + identifier[:8]) +
                   '\nuuid=' + identifier + '\ntype=wifi\ninterface-name=' + self.interface +
                   '\nautoconnect=' + ('false' if hotspot else 'true') +
                   '\n[wifi]\nssid=' + ''.join(str(byte)+';' for byte in ssid.encode('utf-8')) + '\nmode=' + ('ap' if hotspot else 'infrastructure') +
                   '\nhidden=' + str(hidden).lower() + '\n[wifi-security]\nkey-mgmt=wpa-psk\npsk=' + password.replace(' ', '\\s') +
                   '\n[ipv4]\nmethod=' + ('shared\naddress1=192.168.4.1/24' if hotspot else 'auto') +
                   '\n[ipv6]\nmethod=disabled\n')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(content); output.flush(); os.fsync(output.fileno())
        self.nm('connection', 'load', str(path))
        return identifier
    def activate(self, identifier):
        self.nm('connection', 'up', 'uuid', identifier, 'ifname', self.interface)
    def deactivate(self, identifier): self.nm('connection', 'down', 'uuid', identifier)
    def delete_trial(self, identifier): self.nm('connection', 'delete', 'uuid', identifier)
    def retry_saved(self): self.nm('device', 'connect', self.interface)


class Recovery:
    def __init__(self, backend, path, clock=time.monotonic):
        self.backend, self.path, self.clock = backend, Path(path), clock
        self.lock = threading.RLock()
        self.operation_lock = threading.Lock()
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if self.data.get('schemaVersion') != 1: raise ValueError('Unsupported network state version')
        else:
            self.data = dict(schemaVersion=1, ssid='Y-Scores-Setup-' + secrets.token_hex(2).upper(),
                             password=secrets.token_urlsafe(18), hotspotProfile=None)
            protected_write(self.path, self.data)
        self.state, self.pending, self.error = 'CHECKING', None, ''
        self.no_network_since = self.clock()
        self.hotspot_since = None
        # A helper restart must clean up interrupted trials (including reboot,
        # when NetworkManager's in-memory checkpoint no longer exists).
        trial = self.data.pop('trialProfile', None)
        checkpoint = self.data.pop('checkpoint', None)
        previous_isolation = self.data.pop('previousIsolation', False)
        # Keep restrictive rules during cleanup. A checkpoint can independently
        # restore the AP if this process dies anywhere before confirmation.
        if checkpoint or self.backend.hotspot_active(): self.backend.isolation(True)
        if checkpoint:
            try: self.backend.rollback(checkpoint)
            except ValueError: pass
        if trial:
            try: self.backend.delete_trial(trial)
            except ValueError: pass
        self.backend.isolation(bool(previous_isolation) or self.backend.hotspot_active())
        if self.backend.hotspot_active():
            self.state='RECOVERY_HOTSPOT'; self.hotspot_since=self.clock()
        protected_write(self.path, self.data)
    def status(self):
        with self.lock:
            value = dict(available=True, state=self.state, error=self.error, confirmationRequired=bool(self.pending),
                         recoverySsid=self.data['ssid'], recoveryPassword=self.data['password'],
                         recoveryUrl='http://192.168.4.1:'+str(self.backend.admin_port)+'/')
            return value
    def connect(self, ssid, password, hidden=False):
        # Validate before acquiring a checkpoint/changing any interface.
        self.backend.text(ssid, 'SSID', 32)
        self.backend.text(password, 'Wi-Fi password', 63)
        if len(password) < 8 or type(hidden) is not bool: raise ValueError('Invalid Wi-Fi settings')
        with self.lock:
            if self.pending or self.state == 'CONNECTING': raise ValueError('A network change is already running')
            self.state = 'CONNECTING'; self.error = ''
        threading.Thread(target=self._connect, args=(ssid, password, hidden), daemon=True).start()
        return self.status()
    def _connect(self, ssid, password, hidden):
        try: self._connect_locked(ssid,password,hidden)
        except Exception:
            with self.lock:
                self.state='RECOVERING'; self.error='Install or network operation in progress; try again later'
    def _connect_locked(self, ssid, password, hidden):
        checkpoint = profile = None
        with self.operation_lock:
            previous_isolation=self.backend.isolation_active() or self.backend.hotspot_active()
            try:
                self.data['previousIsolation']=previous_isolation; protected_write(self.path,self.data)
                self.backend.isolation(True)
                checkpoint = self.backend.checkpoint()
                deadline=self.clock()+110
                self.data['checkpoint'] = checkpoint; protected_write(self.path, self.data)
                profile = self.backend.profile(ssid, password, hidden)
                self.data['trialProfile'] = profile; protected_write(self.path, self.data)
                if self.backend.hotspot_active():
                    self.backend.deactivate(self.data['hotspotProfile'])
                self.backend.activate(profile)
                if not self.backend.candidate_ready(profile): raise ValueError('Wi-Fi address not ready')
                with self.lock:
                    self.pending = dict(checkpoint=checkpoint, profile=profile, previousIsolation=previous_isolation,
                                        deadline=min(self.clock()+75,deadline))
                    self.state = 'AWAITING_CONFIRMATION'
            except Exception:
                if checkpoint:
                    try:
                        self.backend.isolation(True)
                        self.backend.rollback(checkpoint)
                    except Exception: pass
                if profile:
                    try: self.backend.delete_trial(profile)
                    except Exception: pass
                self._restore_isolation(previous_isolation)
                with self.lock:
                    self.state = 'RECOVERING'; self.error = 'Connection failed; restoring the previous network'
                self._clear_trial()
    def _clear_trial(self):
        self.data.pop('checkpoint', None); self.data.pop('trialProfile', None); self.data.pop('previousIsolation',None)
        protected_write(self.path, self.data)
    def _restore_isolation(self, previous):
        self.backend.isolation(bool(previous) or self.backend.hotspot_active())
    def authenticated_activity(self):
        # Only a PIN+CSRF-validated local mutation calls this. Polling/status,
        # scans and unauthenticated network clients never renew the AP lease.
        with self.lock:
            if self.hotspot_since is not None: self.hotspot_since=self.clock()
    def confirm(self):
        with self.operation_lock, self.lock:
            if not self.pending or self.clock() >= self.pending['deadline']: raise ValueError('Network confirmation expired')
            if not self.backend.candidate_ready(self.pending['profile']): raise ValueError('Wi-Fi connection is not ready')
            self.backend.commit(self.pending['checkpoint'])
            self.pending = None; self.state = 'CONNECTED'; self.error = ''; self.hotspot_since = None
            self._clear_trial()
            # If firewall cleanup fails, keep the confirmed profile and retry
            # removal on the next station tick; never delete a committed trial.
            self.backend.isolation(False)
            return self.status()
    def hotspot(self):
        if not self.data['hotspotProfile']:
            self.data['hotspotProfile'] = self.backend.profile(self.data['ssid'], self.data['password'], hotspot=True)
            protected_write(self.path, self.data)
        self.backend.isolation(True)
        self.backend.activate(self.data['hotspotProfile'])
        self.state = 'RECOVERY_HOTSPOT'; self.hotspot_since = self.clock()
    def tick(self):
        if not self.operation_lock.acquire(blocking=False): return
        try:
            with self.lock:
                if self.pending:
                    if self.clock() < self.pending['deadline']: return
                    pending = self.pending; self.pending = None
                    try:
                        self.backend.isolation(True)
                        self.backend.rollback(pending['checkpoint'])
                    except ValueError: pass  # NM may already have auto-rolled back.
                    self._restore_isolation(pending['previousIsolation'])
                    self.backend.delete_trial(pending['profile']); self._clear_trial()
                    self.state = 'RECOVERING'; self.error = 'New network was not confirmed; previous connection restored'
                if self.backend.hotspot_active():
                    self.backend.isolation(True)
                    self.state = 'RECOVERY_HOTSPOT'
                    if self.hotspot_since is None: self.hotspot_since = self.clock()
                    if self.clock() - self.hotspot_since >= 900:
                        self.backend.deactivate(self.data['hotspotProfile'])
                        self.backend.isolation(False)
                        try: self.backend.retry_saved()
                        except ValueError: pass
                        self.no_network_since = self.clock(); self.hotspot_since = None; self.state = 'CHECKING'
                elif self.backend.connected():
                    self.backend.isolation(False)
                    self.state = 'CONNECTED'; self.no_network_since = self.clock(); self.hotspot_since = None
                elif self.clock() - self.no_network_since >= 90: self.hotspot()
        finally: self.operation_lock.release()


class OperationLock:
    """Same advisory lock as the trusted installer/rollback, plus thread exclusion."""
    def __init__(self, path='/run/lock/y-scores-install.lock'):
        import fcntl
        self.fcntl, self.path, self.thread_lock, self.fd = fcntl, path, threading.Lock(), None
    def acquire(self, blocking=True):
        if not self.thread_lock.acquire(blocking=blocking): return False
        try:
            self.fd=os.open(self.path,os.O_WRONLY|os.O_CREAT,0o600)
            self.fcntl.flock(self.fd,self.fcntl.LOCK_EX|self.fcntl.LOCK_NB)
            return True
        except OSError:
            if self.fd is not None: os.close(self.fd); self.fd=None
            self.thread_lock.release()
            return False
    def release(self):
        self.fcntl.flock(self.fd,self.fcntl.LOCK_UN); os.close(self.fd); self.fd=None
        self.thread_lock.release()
    def __enter__(self):
        if not self.acquire(): raise ValueError('An install or network operation is running')
        return self
    def __exit__(self,*_): self.release()


class RequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        # File mode plus peer UID: only the scoreboard service user can call it.
        _, uid, _ = struct.unpack('3i', self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid not in (0, self.server.allowed_uid): return
        try:
            raw = self.rfile.readline(4097)
            if len(raw) > 4096 or not raw.endswith(b'\n'): raise ValueError('Invalid network request')
            request = json.loads(raw)
            action = request.pop('action')
            if action == 'status' and not request: result = self.server.recovery.status()
            elif action == 'authenticated-activity' and not request:
                self.server.recovery.authenticated_activity(); result={'ok':True}
            elif action == 'scan' and not request: result = dict(ssids=self.server.recovery.backend.scan())
            elif action == 'connect' and not set(request) - {'ssid', 'password', 'hidden'}:
                result = self.server.recovery.connect(**request)
            elif action == 'confirm' and not request: result = self.server.recovery.confirm()
            elif action == 'reopen' and not request:
                with self.server.recovery.operation_lock, self.server.recovery.lock:
                    if self.server.recovery.pending: raise ValueError('Network confirmation pending')
                    self.server.recovery.hotspot()
                result = self.server.recovery.status()
            else: raise ValueError('Unsupported network action')
        except (ValueError, TypeError, KeyError): result = dict(error='Network request failed; check local network status')
        except Exception: result = dict(error='Network helper unavailable')
        self.wfile.write(json.dumps(result).encode() + b'\n')


def main():
    if os.name != 'posix' or os.geteuid() != 0: raise SystemExit('Network helper requires Linux root service')
    import pwd
    account = pwd.getpwnam('yscores')
    if os.getegid()!=account.pw_gid: raise SystemExit('Run the root helper through its Group=yscores systemd unit')
    operation_lock=OperationLock()
    with operation_lock:
        recovery = Recovery(NetworkManager(os.environ.get('Y_SCORES_WIFI_INTERFACE', 'wlan0'),
                                           country=os.environ.get('Y_SCORES_WIFI_COUNTRY'),
                                           admin_port=int(os.environ.get('Y_SCORES_PORT', '8080'))),
                            '/var/lib/y-scores-network/network-state.json')
    recovery.operation_lock=operation_lock
    directory = Path('/run/y-scores-network'); directory.mkdir(mode=0o750, exist_ok=True)
    path = directory / 'control.sock'; path.unlink(missing_ok=True)
    class Server(socketserver.ThreadingUnixStreamServer): daemon_threads = True
    server = Server(str(path), RequestHandler); server.allowed_uid = account.pw_uid; server.recovery = recovery
    # PID1 creates root:yscores RuntimeDirectory, and this process's primary
    # group owns the new socket. No CAP_CHOWN is required or granted.
    os.chmod(path, 0o660)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        try: recovery.tick()
        except Exception:
            with recovery.lock: recovery.error = 'Network helper could not recover Wi-Fi; use Ethernet'
        time.sleep(5)


if __name__ == '__main__': main()
