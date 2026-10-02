"""LAN admin and display service for the three-panel Y-Sports scoreboard."""
import argparse
import copy
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import socket
import threading
import time
from urllib.parse import urlsplit
from uuid import UUID
from matrix_output import create_matrix, fill_framebuffer, TOPOLOGIES
from PIL import Image
from live_scoreboard import Feed, Renderer
from device_model import DEFAULTS, validate_config, save_config
from device_screens import Screens
from device_identity import Identity
from platform_client import Platform
from network_client import Network
from manual_match import ManualMatch, Conflict
from device_control import Control
from device_health import Health, age_ms
from update_core import version as release_version, installed_sequence
from update_local import status as update_status, deferred, defer

ROOT=Path(__file__).resolve().parent
LOG=logging.getLogger('device')
CLOUD_MODES={'match','court','schedule'}

class PlatformRequired(ValueError):
    pass

def existing_installation(config_path,pin_path):
    # Capture this before Auth creates a first-boot PIN.
    return config_path.exists() or pin_path.exists()

class Device:
    def __init__(self,path,base,fonts):
        self.path=path; self.base=base
        self.lock=threading.RLock(); self.apply_lock=threading.Lock(); self.stop=threading.Event()
        self.config=validate_config(json.loads(path.read_text()) if path.exists() else DEFAULTS)
        self.version=0; self.feed=None; self.feed_threads=[]; self.active_id=None
        self.matches=[]; self.next_match=None; self.error=''; self.catalog_at=0; self.finished=set()
        self.image=Image.new('RGB',(192,64)); self.frame_at=0; self.hardware_error=''
        self.renderer=Renderer(fonts); self.screens=Screens(self.renderer,ROOT/'assets/logo-white.png')
        self.manual=ManualMatch(path.parent/'manual-match.json')
        self.platform_available=lambda: False
        self.control=Control(path.parent/'device-control.json',self.config if path.exists() else None)
        self.health=Health(path.parent)
        self.output_at=0; self.output_state='SIMULATOR'; self.driver='simulator'; self.refresh_hz=None
        self.rendered_mode='UNKNOWN'; self.rendered_match=None; self.rendered_revision=None
        self.network_kind='UNKNOWN'; self.topology='unknown'; self.renderer_error=False
        self.remote_request=None; self.feed_scoped=False
        self.schedule_revision=None; self.schedule_failed=False
        self.update_reserved_until=0; self.network_status={'state':'CHECKING'}
    def effective_settings(self):
        with self.lock:
            config=dict(self.config); desired=self.control.desired
            if config['mode'] in CLOUD_MODES: config.update(mode='logo',match_id='',court_id='',tournament_id='')
            if not self.control.value['localTakeover'] and self.control.authorized and self.platform_available() and desired:
                if desired.get('mode')=='MATCH' and desired.get('effectiveMatchId'):
                    config.update(mode='match',match_id=str(UUID(desired['effectiveMatchId'])))
                elif desired.get('mode')=='LOGO': config.update(mode='logo',match_id='')
                elif desired.get('mode')=='SCHEDULE': config.update(mode='schedule',match_id='',court_id='',tournament_id='')
            return config
    def accept_control(self, desired, available):
        with self.apply_lock, self.lock:
            if self.update_reserved_until>time.monotonic(): return
            previous=self.control.desired
            self.control.accept(desired,available)
            key=lambda item:tuple((item or {}).get(k) for k in ('revision','mode','effectiveMatchId'))
            selection_changed=key(previous)!=key(self.control.desired)
            if selection_changed: self.version+=1
            if previous!=self.control.desired: self.control.ack=None
            if not available or selection_changed:
                self.matches=[]; self.catalog_at=0; self.schedule_revision=None; self.schedule_failed=False
            if not self.control.desired: return
            revision=desired.get('revision')
            if type(revision) is not int or revision<0: self.control.desired=None; return
            if self.control.value['localTakeover']: self.control.reject(revision,'LOCAL_TAKEOVER')
            elif desired.get('mode') not in ('MATCH','LOGO','SCHEDULE'): self.control.reject(revision,'UNSUPPORTED'); self.control.desired=None
            elif desired.get('mode')=='MATCH':
                try: UUID(desired.get('effectiveMatchId',''))
                except (ValueError,TypeError,AttributeError): self.control.reject(revision,'APPLY_FAILED'); self.control.desired=None
    def guard_update(self):
        if self.update_reserved_until>time.monotonic(): raise Conflict('Software activation is in progress')
    def reserve_update(self,now=False):
        with self.apply_lock,self.lock:
            network_busy=self.network_status.get('state') not in ('CONNECTED','UNAVAILABLE')
            reason='NETWORK_BUSY' if network_busy else None
            if deferred(self.path.parent/'update-deferral.json') or self.control.value['localTakeover'] or self.effective_settings()['mode']=='manual': reason='LOCAL_EVENT'
            elif not reason and self.effective_settings()['mode']=='match':
                view=self.feed.view() if self.feed else None
                if not view or str(view.get('status','')).upper() not in ('FINISHED','COMPLETED','CANCELLED'): reason='MATCH_ACTIVE'
            reserved=not reason or (reason=='MATCH_ACTIVE' and now is True)
            if reserved: self.update_reserved_until=time.monotonic()+45
            return dict(reserved=reserved,busyReason=reason,networkBusy=network_busy)
    def resume_platform(self):
        self.require_platform()
        with self.apply_lock, self.lock:
            self.guard_update()
            self.manual.pause()
            self.control.resume(); self.version+=1
        return self.state()
    def output_success(self):
        with self.lock: self.output_at=time.monotonic(); self.output_state='ACTIVE'; self.hardware_error=''
    def telemetry(self):
        now=time.monotonic(); available=self.platform_available()
        with self.lock:
            mode=self.rendered_mode; match=self.rendered_match if available else None
            feed=self.feed if match else None
            revision=self.rendered_revision if match else None
            frame=age_ms(self.frame_at,now); output=age_ms(self.output_at,now)
            selected=self.effective_settings()
            result=dict(configuredMode='MATCH' if selected['mode']=='court' else selected['mode'].upper(),
                effectiveMode=mode if available or mode not in ('MATCH','SCHEDULE') else 'LOGO',
                selectedMatchId=self.active_id if available and selected['mode'] in ('match','court') else None,
                renderedMatchId=match,matchRevision=revision,frameAgeMs=frame,outputAgeMs=output,
                rendererState='ERROR' if self.renderer_error else ('ACTIVE' if frame is not None and frame<3000 else 'STOPPED'),
                outputState=self.output_state,driver=self.driver,measuredRefreshHz=self.refresh_hz,
                network=self.network_kind,inventory=dict(model=self.health.model(),panelCount=3,panelWidth=64,panelHeight=64,
                    width=192,height=64,topology=self.topology,brightness=selected['brightness']*100,hardware=self.driver!='simulator'),
                errors=['OUTPUT'] if self.hardware_error else [])
        if selected['mode']=='schedule':
            result['feedAgeMs']=age_ms(self.catalog_at,now)
            result['feedState']='FRESH' if self.schedule_revision==(self.control.desired or {}).get('revision') and result['feedAgeMs'] is not None and result['feedAgeMs']<15000 else 'DELAYED'
            if self.schedule_failed: result['feedState']='DISCONNECTED'
        elif feed:
            with feed.lock: result['feedAgeMs']=age_ms(feed.received,now)
            result['feedState']='FRESH' if result['feedAgeMs'] is not None and result['feedAgeMs']<3000 else 'DELAYED'
        else: result.update(feedAgeMs=None,feedState='UNKNOWN')
        result.update(self.health.sample())
        if self.renderer_error: result['errors'].append('RENDERER')
        if result['feedState'] in ('DELAYED','DISCONNECTED'): result['errors'].append('FEED')
        return result
    def require_platform(self):
        if not self.platform_available():
            raise PlatformRequired('Pair this device and enable a healthy Y-Sports connection, or use manual handball control')
    def settings(self):
        with self.lock: return dict(self.config)
    def apply(self,value):
        if not isinstance(value,dict): raise ValueError('Expected display settings')
        if value.get('mode') in CLOUD_MODES or any(key in value for key in ('match_id','court_id','tournament_id')):
            raise PlatformRequired('Assign matches, courts and schedules in Y-Sports management')
        if set(value)-{'mode','brightness','timezone'}: raise ValueError('Unknown display settings')
        with self.apply_lock:
            self.guard_update()
            config=validate_config(self.settings()|value)
            if 'mode' in value and config['mode']!='manual': self.manual.pause()
            save_config(self.path,config)
            with self.lock:
                if 'mode' in value: self.control.local_selection(True); self.version+=1
                self.config=config
        return config
    def switch_feed(self,match_id):
        if match_id: self.require_platform()
        with self.lock:
            desired=self.control.desired
            scoped=bool(match_id and self.remote_request and desired and self.control.authorized
                        and not self.control.value['localTakeover'] and desired.get('effectiveMatchId')==match_id)
        if match_id and not scoped: raise PlatformRequired('Assign this match in Y-Sports management')
        if match_id==self.active_id and scoped==self.feed_scoped and (not self.feed or not self.feed.stop.is_set()): return
        old=self.feed
        if old: old.stop.set()
        for thread in self.feed_threads: thread.join(timeout=6)
        feed=Feed(self.base,match_id,device_request=self.remote_request if scoped else None) if match_id else None
        with self.lock: self.feed=feed; self.active_id=match_id; self.feed_scoped=scoped
        self.feed_threads=[]
        if feed:
            for target in (feed.run,feed.run_colors,feed.run_events):
                thread=threading.Thread(target=target,daemon=True); thread.start(); self.feed_threads.append(thread)
    def route(self):
        version=-1; last_refresh=0; schedule=None
        while not self.stop.is_set():
            with self.lock: config=self.effective_settings(); current_version=self.version
            try:
                if current_version!=version:
                    self.switch_feed(None)
                    schedule=None; last_refresh=0; self.finished.clear()
                    with self.lock: self.matches=[]; self.next_match=None; self.error=''; self.catalog_at=0; self.schedule_revision=None
                    version=current_version
                mode=config['mode']
                if mode in CLOUD_MODES and not self.platform_available():
                    self.switch_feed(None)
                    schedule=None; last_refresh=0
                    with self.lock:
                        self.matches=[]; self.next_match=None; self.catalog_at=0
                    self.stop.wait(.25)
                    continue
                if mode=='match': self.switch_feed(config['match_id'])
                elif mode=='schedule':
                    if time.monotonic()-last_refresh>=5:
                        fresh=self.remote_request('GET','/schedule')
                        with self.lock: revision=(self.control.desired or {}).get('revision')
                        if (not isinstance(fresh,dict) or fresh.get('controlRevision')!=revision
                                or not isinstance(fresh.get('matches'),list) or len(fresh['matches'])>100):
                            raise ValueError('Schedule assignment changed; waiting for a fresh scoped report')
                        schedule=fresh; last_refresh=time.monotonic()
                        with self.lock:
                            if revision==(self.control.desired or {}).get('revision'):
                                self.matches=fresh['matches']; self.catalog_at=time.monotonic(); self.error=''; self.schedule_revision=revision; self.schedule_failed=False
                    self.switch_feed(None)
                else: self.switch_feed(None)
            except Exception as error:
                with self.lock:
                    self.error=str(error)
                    if config['mode']=='schedule': self.schedule_failed=True
                LOG.warning('Selection unavailable: %s',error)
                self.stop.wait(2)
            self.stop.wait(.25)
        self.switch_feed(None)
    def render(self,publish=True):
        with self.lock:
            config=self.effective_settings(); feed=self.feed; matches=self.matches; upcoming=self.next_match; error=self.error
            catalog_at=self.catalog_at
            desired=self.control.desired
            control_revision=desired.get('revision') if desired else None
        mode=config['mode']
        rendered_view=None
        if feed and not self.platform_available(): feed.stop.set()
        if mode in CLOUD_MODES and not self.platform_available():
            if feed: feed.stop.set()
            mode='logo'
        if mode=='blank': image=Image.new('RGB',(192,64))
        elif mode=='manual': image=self.renderer.render(self.manual.view())
        elif mode=='logo': image=self.screens.logo_screen()
        elif mode=='schedule':
            if not self.schedule_failed and self.schedule_revision==control_revision and catalog_at and time.monotonic()-catalog_at<15:
                image=self.screens.schedule(matches,config)
            else: mode='logo'; image=self.screens.logo_screen()
        elif mode=='court' and not feed: image=self.screens.upcoming(upcoming,config)
        else:
            rendered_view=feed.view() if feed else None
            image=self.renderer.render(rendered_view)
        if mode in ('court','schedule') and (error or not catalog_at or time.monotonic()-catalog_at>30):
            # Make stale schedule data visible rather than silently presenting it as current.
            from PIL import ImageDraw
            ImageDraw.Draw(image).rectangle((138,54,191,63),fill=(0,0,0))
            self.screens.text(image,'OFFLINE' if error else 'LOADING',140,54,(255,210,40))
        # Publish content metadata with the exact rendered frame, never an old feed object.
        rendered_match=None; revision=None
        if mode in ('match','court') and rendered_view:
            rendered_match=rendered_view.get('match_id'); revision=rendered_view.get('match_revision')
        self.pending_frame=('MATCH' if mode=='court' else mode.upper(),rendered_match,revision,control_revision)
        if publish: self.publish_frame(image,*self.pending_frame)
        return image,config['brightness']
    def publish_frame(self,image,mode,rendered_match,revision,control_revision=None,output_success=False):
        with self.lock:
            self.image=image; self.frame_at=time.monotonic(); self.renderer_error=False
            self.rendered_mode=mode
            self.rendered_match=rendered_match; self.rendered_revision=revision
            if output_success:
                self.output_at=self.frame_at; self.output_state='ACTIVE'; self.hardware_error=''
            desired=self.control.desired
            if (desired and desired.get('revision')==control_revision and self.control.authorized
                    and not self.control.value['localTakeover'] and (self.driver=='simulator' or output_success)):
                target=desired.get('effectiveMatchId')
                if (desired.get('mode')==mode and mode in ('LOGO','SCHEDULE')) or (target and rendered_match==target):
                    if self.control.value['appliedRevision']!=desired['revision'] or not self.control.ack: self.control.applied(desired['revision'])
    def state(self):
        available=self.platform_available()
        manual=self.manual.snapshot()
        with self.lock:
            selected=self.effective_settings()
            blocked=selected['mode'] in CLOUD_MODES and not available
            view=self.manual.view() if selected['mode']=='manual' else (self.feed.view() if self.feed and not blocked and selected['mode']=='match' else None)
            return dict(config=self.effective_settings(),effective_mode='logo' if blocked else self.effective_settings()['mode'],
                        local_takeover=self.control.value['localTakeover'],
                        platform_assignment=copy.deepcopy(self.control.desired),
                        platform_available=available,manual=manual,active_match_id=self.active_id if not blocked and selected['mode'] in ('match','court') else None,match=view,error=self.error,
                        installedVersion=release_version(ROOT,os.environ.get('Y_SCORES_VERSION','development')),installedSequence=installed_sequence(ROOT),
                        updateStatus=update_status(),deferredUntil=deferred(self.path.parent/'update-deferral.json'),
                        state_valid=True,renderer_error=self.renderer_error,hardware_output_age=round(time.monotonic()-self.output_at,1) if self.output_at else None,
                        hardware_error=self.hardware_error,frame_age=round(time.monotonic()-self.frame_at,1) if self.frame_at else None)
    def preview(self):
        with self.lock: image=self.image.copy()
        output=BytesIO(); image.save(output,format='PNG'); return output.getvalue()

class Auth:
    def __init__(self,pin_path):
        if not pin_path.exists():
            pin_path.write_text(str(secrets.randbelow(900000)+100000)+'\n')
            pin_path.chmod(0o600)
        self.pin=pin_path.read_text().strip(); self.sessions={}; self.csrf_tokens={}; self.attempts={}; self.lock=threading.Lock()
    def login(self,pin,address):
        with self.lock:
            now=time.monotonic(); count,start=self.attempts.get(address,(0,now))
            if now-start>60: count,start=0,now
            if count>=5: raise ValueError('Too many attempts. Try again in a minute.')
            if not isinstance(pin,str) or not secrets.compare_digest(pin,self.pin):
                self.attempts[address]=(count+1,start); raise ValueError('Incorrect PIN')
            self.attempts.pop(address,None)
            self.sessions={k:v for k,v in self.sessions.items() if v>now}
            token=secrets.token_urlsafe(32); self.sessions[token]=now+43200
            self.csrf_tokens={k:v for k,v in self.csrf_tokens.items() if k in self.sessions}
            self.csrf_tokens[token]=secrets.token_urlsafe(32)
            return token
    def session_token(self,header):
        try:
            jar=cookies.SimpleCookie(); jar.load(header or ''); return jar['session'].value
        except (KeyError,cookies.CookieError): return ''
    def csrf(self,header):
        token=self.session_token(header)
        with self.lock: return self.csrf_tokens.get(token,'') if self.sessions.get(token,0)>time.monotonic() else ''
    def valid(self,header):
        try:
            jar=cookies.SimpleCookie(); jar.load(header or ''); token=jar['session'].value
            with self.lock: return self.sessions.get(token,0)>time.monotonic()
        except (KeyError,cookies.CookieError): return False

class Handler(BaseHTTPRequestHandler):
    def network_mutation(self,action,**values):
        with self.server.device.apply_lock:
            self.server.device.guard_update()
            with self.server.device.lock: self.server.device.network_status={'state':'CONNECTING'}
            return self.server.network.request(action,**values)
    def log_message(self,format,*args): LOG.debug(format,*args)
    def send(self,status,body,content_type='application/json',cookie=None):
        if not isinstance(body,bytes): body=json.dumps(body).encode()
        self.send_response(status); self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
        if cookie: self.send_header('Set-Cookie',cookie)
        self.end_headers(); self.wfile.write(body)
    def authorized(self):
        if not self.server.auth.valid(self.headers.get('Cookie')):
            self.send(401,{'error':'Enter the device PIN'}); return False
        return True
    def host_allowed(self):
        host=self.headers.get('Host','')
        try:
            parsed=urlsplit('//'+host)
            if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment: raise ValueError()
            if (parsed.port or 80)!=self.server.server_port: raise ValueError()
            names={'localhost',socket.gethostname().lower(),socket.gethostname().lower()+'.local',self.connection.getsockname()[0]}
            names.update(getattr(self.server,'allowed_hosts',set()))
            if not parsed.hostname or parsed.hostname.lower() not in names: raise ValueError()
        except ValueError:
            self.send(403,{'error':'Unrecognized device hostname'}); return False
        return True
    def do_GET(self):
        if not self.host_allowed(): return
        route=urlsplit(self.path)
        if route.path=='/healthz':
            ready=self.server.device.frame_at and time.monotonic()-self.server.device.frame_at<3
            return self.send(200 if ready else 503,{'ready':bool(ready)})
        if route.path=='/': return self.send(200,(ROOT/'admin.html').read_bytes(),'text/html; charset=utf-8')
        if route.path=='/admin.js': return self.send(200,(ROOT/'admin.js').read_bytes(),'text/javascript; charset=utf-8')
        if route.path=='/logo.png': return self.send(200,(ROOT/'assets/logo-white.png').read_bytes(),'image/png')
        if not self.authorized(): return
        try:
            if route.path=='/api/session': return self.send(200,{'csrfToken':self.server.auth.csrf(self.headers.get('Cookie'))})
            if route.path=='/api/setup': return self.send(200,dict(identity=self.server.identity.public(),platform=self.server.platform.public(),network=self.server.network.status()))
            if route.path=='/api/network/scan': return self.send(200,self.server.network.request('scan'))
            if route.path=='/api/status': return self.send(200,self.server.device.state())
            if route.path=='/api/preview.png': return self.send(200,self.server.device.preview(),'image/png')
            self.send(404,{'error':'Not found'})
        except PlatformRequired as error: self.send(403,{'error':str(error)})
        except ValueError as error: self.send(400,{'error':str(error)})
        except Exception as error: self.send(502,{'error':'Y-Sports unavailable: '+str(error)})
    def do_POST(self):
        if not self.host_allowed(): return
        if self.headers.get('Content-Type','').split(';')[0]!='application/json' or self.headers.get('X-Scoreboard-Request')!='1':
            return self.send(403,{'error':'Use the device admin page'})
        origin=self.headers.get('Origin')
        if origin and (urlsplit(origin).scheme!='http' or urlsplit(origin).netloc!=self.headers.get('Host')): return self.send(403,{'error':'Cross-origin request blocked'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=8192: raise ValueError('Invalid request size')
            value=json.loads(self.rfile.read(size))
            if not isinstance(value,dict): raise ValueError('Expected a JSON object')
            if self.path=='/api/login':
                token=self.server.auth.login(value.get('pin'),self.client_address[0])
                if getattr(self.server,'network',None) is not None:
                    try: self.server.network.request('authenticated-activity')
                    except (ValueError,OSError): pass
                return self.send(200,{'ok':True,'csrfToken':self.server.auth.csrf_tokens[token]},cookie='session='+token+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200')
            if not self.authorized(): return
            csrf=self.server.auth.csrf(self.headers.get('Cookie'))
            if not csrf or not secrets.compare_digest(csrf,self.headers.get('X-Scoreboard-CSRF','')):
                return self.send(403,{'error':'Refresh the local admin session'})
            if self.path=='/api/updates/defer': return self.send(200,defer(self.server.device.path.parent/'update-deferral.json',value.get('minutes')))
            if self.path in ('/api/updates/reserve','/api/updates/release'):
                if self.client_address[0] not in ('127.0.0.1','::1'): return self.send(403,{'error':'Local updater only'})
                if self.path.endswith('/reserve'): return self.send(200,self.server.device.reserve_update(value.get('now') is True))
                with self.server.device.lock: self.server.device.update_reserved_until=0
                return self.send(200,{'ok':True})
            if self.path not in ('/api/logout',): self.server.device.guard_update()
            if self.path in {'/api/identity','/api/setup/complete','/api/platform/claim','/api/platform/cancel',
                             '/api/platform/enabled','/api/platform/recover','/api/config','/api/manual','/api/network/connect','/api/network/confirm','/api/network/reopen'}:
                try: self.server.network.request('authenticated-activity')
                except (ValueError,OSError): pass
            if self.path=='/api/identity': return self.send(200,self.server.identity.save(value.get('name'),value.get('id')))
            if self.path=='/api/setup/complete':
                if not self.server.identity.public()['locked']: raise ValueError('Save device identity first')
                return self.send(200,self.server.identity.update(setupComplete=True))
            if self.path=='/api/platform/claim': return self.send(200,self.server.platform.create_claim())
            if self.path=='/api/platform/cancel': return self.send(200,self.server.platform.cancel_claim())
            if self.path=='/api/platform/recover': return self.send(200,self.server.platform.recover_credential(value.get('recoveryPin')))
            if self.path=='/api/platform/resume': return self.send(200,self.server.device.resume_platform())
            if self.path=='/api/platform/enabled':
                if type(value.get('enabled')) is not bool: raise ValueError('Invalid platform setting')
                if value['enabled']: self.server.identity.update(platformEnabled=True)
                else: self.server.platform.disable()
                return self.send(200,self.server.platform.public())
            if self.path=='/api/network/connect': return self.send(200,self.network_mutation('connect',ssid=value.get('ssid'),password=value.get('password'),hidden=value.get('hidden',False)))
            if self.path=='/api/network/confirm': return self.send(200,self.network_mutation('confirm'))
            if self.path=='/api/network/reopen': return self.send(200,self.network_mutation('reopen'))
            if self.path=='/api/config': return self.send(200,self.server.device.apply(value))
            if self.path=='/api/manual':
                with self.server.device.apply_lock:
                    self.server.device.guard_update()
                    if value.get('action') not in ('setup','reset') and self.server.device.settings()['mode']!='manual':
                        raise ValueError('Apply manual mode to the display before controlling the match')
                    return self.send(200,self.server.device.manual.command(value))
            if self.path=='/api/logout':
                jar=cookies.SimpleCookie(); jar.load(self.headers.get('Cookie',''))
                with self.server.auth.lock:
                    self.server.auth.sessions.pop(jar['session'].value,None)
                    self.server.auth.csrf_tokens.pop(jar['session'].value,None)
                return self.send(200,{'ok':True},cookie='session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            self.send(404,{'error':'Not found'})
        except Conflict as error: self.send(409,{'error':str(error)})
        except PlatformRequired as error: self.send(403,{'error':str(error)})
        except (ValueError,TypeError,KeyError) as error: self.send(400,{'error':str(error)})
        except Exception as error: self.send(502,{'error':'Unable to apply: '+str(error)})

class Server(ThreadingHTTPServer):
    daemon_threads=True
    def get_request(self):
        connection,address=super().get_request(); connection.settimeout(10); return connection,address

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=int(os.environ.get('Y_SCORES_PORT','8080')))
    parser.add_argument('--bind',default=os.environ.get('Y_SCORES_BIND','0.0.0.0'))
    parser.add_argument('--base-url',default=os.environ.get('Y_SCORES_API','https://y-sports.xyz/api-next'))
    parser.add_argument('--fonts',type=Path,default=ROOT/'fonts')
    parser.add_argument('--config',type=Path,default=Path(os.environ.get('Y_SCORES_STATE_DIR',str(ROOT/'state')))/'device-config.json')
    parser.add_argument('--pin-file',type=Path,default=Path(os.environ.get('Y_SCORES_STATE_DIR',str(ROOT/'state')))/'admin-pin.txt')
    parser.add_argument('--topology',choices=TOPOLOGIES,default=os.environ.get('Y_SCORES_TOPOLOGY','parallel'))
    parser.add_argument('--order',default=os.environ.get('Y_SCORES_ORDER','2,1,0'))
    parser.add_argument('--rotate',default=os.environ.get('Y_SCORES_ROTATE','180,0,0'))
    parser.add_argument('--pinout',choices=['Active3','Active3BGR'],default=os.environ.get('Y_SCORES_PINOUT','Active3BGR'))
    parser.add_argument('--no-hardware',action='store_true')
    parser.add_argument('--platform-url',default=os.environ.get('Y_SCORES_PLATFORM_API','https://y-sports.xyz/api-next'))
    parser.add_argument('--allow-http-platform',action='store_true',default=os.environ.get('Y_SCORES_ALLOW_HTTP_PLATFORM')=='1',help='Explicit local-development opt-in only')
    args=parser.parse_args(); logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    order=[int(v) for v in args.order.split(',')]; rotations=[int(v) for v in args.rotate.split(',')]
    if sorted(order)!=[0,1,2] or len(rotations)!=3 or any(r not in (0,90,180,270) for r in rotations):
        parser.error('Invalid panel order or rotations')
    args.config.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    args.pin_file.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    device=Device(args.config,args.base_url,args.fonts)
    legacy_setup=existing_installation(args.config,args.pin_file)
    server=Server((args.bind,args.port),Handler); server.device=device; server.auth=Auth(args.pin_file)
    server.allowed_hosts={h.strip().lower() for h in os.environ.get('Y_SCORES_ALLOWED_HOSTS','').split(',') if h.strip()}
    server.identity=Identity(args.config.parent/'device-identity.json',legacy=legacy_setup)
    server.network=Network()
    server.platform=Platform(server.identity,args.platform_url,device.stop,args.allow_http_platform,release_version(ROOT,os.environ.get('Y_SCORES_VERSION','development')),update_capable=update_status().get('enabled') is True)
    device.platform_available=server.platform.selection_available
    server.platform.update_capability_provider=lambda:update_status().get('enabled') is True
    device.remote_request=server.platform.request
    server.platform.telemetry_provider=device.telemetry
    server.platform.control_consumer=lambda desired,available:device.accept_control(desired,available and server.identity.public()['setupComplete'])
    server.platform.control_report=lambda:dict(localTakeover=device.control.value['localTakeover'],acknowledgement=device.control.ack)
    matrix=None
    if not args.no_hardware:
        matrix,framebuffer=create_matrix(args.topology,args.pinout)
        device.driver='Adafruit_Blinka_Raspberry_Pi5_Piomatter'; device.output_state='UNKNOWN'
    device.topology=args.topology
    def stop(*_): device.stop.set()
    signal.signal(signal.SIGINT,stop); signal.signal(signal.SIGTERM,stop)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    worker=threading.Thread(target=device.route,daemon=True); worker.start()
    platform_worker=threading.Thread(target=server.platform.run,daemon=True); platform_worker.start()
    setup_network={}; setup_network_lock=threading.Lock()
    def refresh_network():
        while not device.stop.is_set():
            value=server.network.status()
            with device.lock: device.network_status=value
            device.network_kind={'RECOVERY_HOTSPOT':'HOTSPOT','DISCONNECTED':'DISCONNECTED'}.get(value.get('state'),device.health.network())
            with setup_network_lock:
                setup_network.clear(); setup_network.update(value)
            device.stop.wait(10)
    threading.Thread(target=refresh_network,daemon=True).start()
    next_refresh_log=time.monotonic()+5
    LOG.info('Device admin listening on port %s; topology=%s order=%s rotations=%s',args.port,args.topology,order,rotations)
    try:
        while not device.stop.is_set():
            try: image,brightness=device.render(publish=False)
            except Exception:
                with device.lock: device.renderer_error=True
                device.stop.wait(.1)
                continue
            identity=server.identity.public()
            frame_metadata=device.pending_frame
            with setup_network_lock: network=dict(setup_network)
            if not identity['setupComplete'] or (network.get('state')=='RECOVERY_HOTSPOT' and device.settings()['mode']!='manual'):
                # Show credentials physically only on genuinely new, unfinished units.
                # Legacy settings suppress this first-run disclosure.
                from PIL import ImageDraw
                image=Image.new('RGB',(192,64)); page=int(time.monotonic()/6)%2
                if network.get('state')=='RECOVERY_HOTSPOT' and page==0:
                    lines=['SETUP WI-FI',network['recoverySsid'],network['recoveryPassword'],'192.168.4.1:'+str(args.port)]
                else:
                    lines=['Y-SCORES SETUP',socket.gethostname()+'.local:'+str(args.port),
                           'ADMIN PIN '+server.auth.pin if not identity['setupComplete'] else 'USE SAVED ADMIN PIN','OPEN LOCAL ADMIN']
                for index,line in enumerate(lines): server.device.screens.text(image,line[:30],2,index*15,(235,235,235))
                frame_metadata=('SETUP',None,None,None)
            submitted=False
            if matrix:
                try:
                    fill_framebuffer(framebuffer,image,brightness,args.topology,order,rotations)
                    matrix.show(); submitted=True
                except Exception:
                    with device.lock: device.hardware_error='Output submission failed'; device.output_state='ERROR'
                if time.monotonic() >= next_refresh_log:
                    device.refresh_hz=matrix.fps if 0<=matrix.fps<=1000 else None
                    LOG.info('Matrix hardware refresh: %.1f Hz',matrix.fps)
                    next_refresh_log=time.monotonic()+60
            device.publish_frame(image,*frame_metadata,output_success=submitted)
            device.stop.wait(.05)
    finally:
        device.manual.pause()
        device.stop.set(); server.shutdown(); server.server_close(); worker.join(timeout=12); platform_worker.join(timeout=10)
        if matrix: framebuffer.fill(0); matrix.show()

if __name__=='__main__': main()
