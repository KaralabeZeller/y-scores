"""Outbound registration, PIN pairing, health reports and authorized selection."""
import copy
from datetime import datetime, timezone
import json
import re
import threading
import time
from uuid import uuid4, UUID
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Device authentication never follows a server-controlled redirect.
        return None

class CredentialRejected(ValueError):
    pass


class Platform:
    CAPABILITIES = ['PIN_PAIRING_V1', 'LOCAL_DISPLAY_V1', 'display-health-v1', 'remote-selection-v1', 'remote-schedule-v1']
    def __init__(self, identity, origin, stop=None, allow_http=False, version='development', opener=None, update_capable=False):
        endpoint = urlsplit(origin)
        if endpoint.scheme != 'https' and not (allow_http and endpoint.scheme == 'http'):
            raise ValueError('Platform requires HTTPS; explicit development opt-in permits HTTP')
        if not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError('Invalid configured platform origin')
        self.identity = identity
        self.base = origin.rstrip('/')
        self.version = version
        self.CAPABILITIES=list(type(self).CAPABILITIES)+(['remote-update-v1'] if update_capable else [])
        self.update_capability_provider=None
        self.stop = stop or threading.Event()
        self.opener = opener or build_opener(NoRedirect())
        self.lock = threading.RLock()
        self.operation = threading.RLock()
        self.status = None
        self.claim = None  # Deliberately ephemeral. Restart requires a fresh PIN.
        self.error = ''
        self.registered_revision = None
        self.revoked = False
        self.registration_blocked = False
        self.received = None
        self.boot_id=str(uuid4()); self.telemetry_session=None; self.report_sequence=0; self.session_unsupported=False
        self.session_opened=False
        self.telemetry_provider=None; self.control_consumer=None; self.control_report=None
    def request(self, method, path, body=None, anonymous=False, credential=None):
        identity = self.identity.snapshot()
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if not anonymous:
            headers.update(Authorization='Scoreboard ' + (credential or identity['credential']),
                           **{'X-Scoreboard-Device-Id': identity['id']})
        request = Request(self.base + '/scoreboard-device' + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=8) as response:
                if response.status == 204: return None
                limit=262144 if path=='/schedule' else 65536
                raw = response.read(limit+1)
                if len(raw) > limit: raise ValueError('Platform response too large')
                return json.loads(raw)
        except HTTPError as error:
            code = error.code
            error.close()
            if code == 401:
                self.telemetry_session=None
                if credential is None: self.revoked = True
                raise CredentialRejected('Device credential rejected. Ask an administrator for a recovery code in All devices, then enter it here.') from None
            if code == 404 and path == '/recoveries':
                raise ValueError('Recovery code is invalid or expired. Obtain a new admin code and confirm the Y-Sports server supports recovery.') from None
            if code == 404 and path == '/telemetry-session':
                self.session_unsupported=True
                return None
            if code == 409:
                if path == '/heartbeat': self.telemetry_session=None; self.session_opened=False
                if anonymous and path == '/registrations':
                    self.registration_blocked = True
                    raise ValueError('Device registration rejected; identity preserved. Contact an administrator for recovery') from None
                raise ValueError('Device identity or pairing conflict; identity was preserved') from None
            if code == 429: raise ValueError('Too many pairing requests; wait before trying again') from None
            raise ValueError('Platform request failed (HTTP ' + str(code) + ')') from None
    def ensure_registration(self):
        if self.update_capability_provider:
            self.CAPABILITIES=list(type(self).CAPABILITIES)+(['remote-update-v1'] if self.update_capability_provider() else [])
        self.resolve_pending_recovery()
        identity = self.identity.snapshot()
        if not identity['locked']: raise ValueError('Save device identity first')
        if self.registration_blocked:
            raise ValueError('Device registration rejected; identity preserved. Contact an administrator for recovery')
        if self.revoked: raise CredentialRejected('Device credential rejected. Ask an administrator for a recovery code in All devices, then enter it here.')
        metadata = dict(name=identity['name'], metadataRevision=identity['metadataRevision'],
                        softwareVersion=self.version, capabilities=self.CAPABILITIES)
        if self.status is None:
            response = self.request('POST', '/registrations', metadata |
                                    dict(id=identity['id'], credential=identity['credential']), anonymous=True)
            self.accept(response)
            self.registered_revision = response['metadataRevision']
        if self.registered_revision is not None and self.registered_revision > identity['metadataRevision']:
            raise ValueError('Platform metadata is newer than saved identity; preserve state and recover manually')
        if self.status and 'softwareVersion' in self.status and 'capabilities' in self.status:
            changed=self.status['softwareVersion']!=self.version or self.status['capabilities']!=self.CAPABILITIES
            identity=self.identity.prepare_runtime_metadata(self.version,self.CAPABILITIES,
                        advance=changed and self.registered_revision==identity['metadataRevision'])
            metadata=dict(name=identity['name'],metadataRevision=identity['metadataRevision'],
                          softwareVersion=self.version,capabilities=self.CAPABILITIES)
        if self.registered_revision != identity['metadataRevision']:
            self.accept(self.request('PUT', '/metadata', metadata))
            self.registered_revision = identity['metadataRevision']
    def accept(self, status):
        if not isinstance(status, dict) or status.get('id') != self.identity.snapshot()['id']:
            raise ValueError('Invalid platform device response')
        if status.get('lifecycle') not in ('REGISTERED', 'REVOKED'):
            raise ValueError('Unsupported device lifecycle')
        with self.lock:
            previous=self.status
            self.status = {k: copy.deepcopy(status.get(k)) for k in
                           ('id', 'name', 'metadataRevision', 'owner', 'ownershipState', 'lifecycle', 'lastSeen', 'revision')}
            for field in ('softwareVersion','capabilities'):
                if field in status: self.status[field]=copy.deepcopy(status[field])
            self.error = ''
            self.received = time.monotonic()
            if status.get('owner') or status['lifecycle'] == 'REVOKED': self.claim = None
            self.revoked = status['lifecycle'] == 'REVOKED'
            if previous and previous.get('owner')!=status.get('owner'):
                self.telemetry_session=None; self.report_sequence=0
            session=status.get('telemetrySession')
            session_changed=False
            if session is not None and self.session_opened:
                session=str(UUID(session))
                if self.telemetry_session!=session:
                    self.telemetry_session=session; self.report_sequence=0; session_changed=True
        if self.control_consumer:
            if session_changed: self.control_consumer(None,False)
            self.control_consumer(status.get('desiredControl'),self.selection_available())
    def selection_available(self):
        identity = self.identity.snapshot()
        with self.lock:
            return bool(identity['platformEnabled'] and identity['locked'] and self.status
                        and self.status.get('owner') and self.status['lifecycle'] == 'REGISTERED'
                        and not self.error and not self.revoked and not self.registration_blocked
                        and self.received is not None and time.monotonic() - self.received < 35)
    def public(self):
        with self.lock:
            if self.claim and datetime.fromisoformat(self.claim['expiresAt'].replace('Z', '+00:00')) <= datetime.now(timezone.utc):
                self.claim = None
            return dict(configured=True, status=copy.deepcopy(self.status), claim=copy.deepcopy(self.claim),
                        error=self.error, displayControlAvailable=bool(self.telemetry_session and not self.session_unsupported), selectionAvailable=self.selection_available(),
                        recoveryRequired=self.revoked or self.registration_blocked)
    def finish_recovery(self,status,credential):
        if (not isinstance(status,dict) or status.get('id')!=self.identity.snapshot()['id'] or status.get('lifecycle')!='REGISTERED'
                or type(status.get('metadataRevision')) is not int or status['metadataRevision']<0):
            raise ValueError('Invalid recovery response; pending credential preserved')
        self.identity.complete_credential_recovery(credential)
        with self.lock:
            self.revoked=False; self.registration_blocked=False; self.claim=None
            self.registered_revision=status['metadataRevision']
        self.accept(status)
    def resolve_pending_recovery(self):
        credential=self.identity.snapshot().get('pendingCredential')
        if not credential: return False
        try: status=self.request('GET','/status',credential=credential)
        except CredentialRejected: return False
        self.finish_recovery(status,credential)
        return True
    def recover_credential(self,pin):
        if not isinstance(pin,str) or not re.fullmatch(r'[0-9]{12}',pin):
            raise ValueError('Enter the twelve-digit recovery code from the administrator')
        with self.operation:
            if self.resolve_pending_recovery(): return self.public()
            if not (self.revoked or self.registration_blocked):
                raise ValueError('Credentials are still active. Use Unpair in Y-Sports to link another account.')
            self.identity.update(platformEnabled=True)
            credential=self.identity.prepare_credential_recovery()
            status=self.request('POST','/recoveries',dict(id=self.identity.snapshot()['id'],
                                recoveryPin=pin,credential=credential),anonymous=True)
            self.finish_recovery(status,credential)
            return self.public()
    def create_claim(self):
        with self.operation:
            self.identity.update(platformEnabled=True)
            self.ensure_registration()
            # A new server claim invalidates the old PIN even if its response is lost.
            with self.lock: self.claim = None
            result = self.request('POST', '/claim', {})
            if not isinstance(result, dict) or not re.fullmatch(r'[0-9]{8}', result.get('pairingPin', '')):
                raise ValueError('Invalid pairing response; generate another PIN')
            expiry = datetime.fromisoformat(result['expiresAt'].replace('Z', '+00:00'))
            if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                raise ValueError('Expired pairing response; generate another PIN')
            with self.lock: self.claim = {k: result[k] for k in ('pairingPin', 'expiresAt')}
            return self.public()
    def cancel_claim(self):
        with self.operation:
            self.ensure_registration()
            self.request('DELETE', '/claim')
            with self.lock: self.claim = None
            return self.public()
    def disable(self):
        # Do not wait for an in-flight heartbeat before stopping display access.
        self.identity.update(platformEnabled=False)
        with self.operation:
            # Local disconnection takes effect even when the cloud is unreachable.
            try:
                if self.status is not None: self.cancel_claim()
            except Exception:
                with self.lock: self.error = 'Account connection disabled locally; remote pairing cancellation unavailable'
            finally:
                with self.lock: self.claim = None
    def tick(self):
        with self.operation:
            identity = self.identity.snapshot()
            if not identity['platformEnabled'] or not identity['locked']: return
            self.ensure_registration()
            if self.telemetry_provider and (not self.telemetry_session or not self.session_opened) and not self.session_unsupported:
                response=self.request('POST','/telemetry-session',dict(bootId=self.boot_id))
                if response: self.session_opened=True; self.accept(response)
            body=dict(softwareVersion=self.version, controlSource='LOCAL')
            if self.telemetry_provider and self.telemetry_session:
                if self.control_report:
                    report=self.control_report(); body.update(report)
                    if self.status and self.status.get('owner') and not report.get('localTakeover'):
                        body['controlSource']='PLATFORM'
                self.report_sequence+=1
                body.update(telemetrySession=self.telemetry_session,reportSequence=self.report_sequence,telemetry=self.telemetry_provider())
            self.accept(self.request('POST', '/heartbeat',body))
            claim = self.request('GET', '/claim')
            if claim.get('state') != 'ACTIVE':
                with self.lock: self.claim = None
    def run(self):
        while not self.stop.is_set():
            try: self.tick()
            except Exception as error:
                # No response bodies, headers, credentials or PINs in logs/status.
                with self.lock: self.error = str(error) if isinstance(error, ValueError) else 'Platform unavailable; retrying'
            self.stop.wait(15)
