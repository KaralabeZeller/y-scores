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
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID
import numpy as np
from PIL import Image
from live_scoreboard import Feed, Renderer
from device_model import DEFAULTS, validate_config, save_config, select_court, filtered_matches
from device_screens import Screens

ROOT=Path(__file__).resolve().parent
LOG=logging.getLogger('device')

class Catalog:
    def __init__(self,base):
        self.base=base.rstrip('/'); self.cache={}; self.lock=threading.Lock()
    def get(self,path,ttl=10):
        with self.lock:
            cached=self.cache.get(path)
            if cached and time.monotonic()-cached[0]<ttl: return copy.deepcopy(cached[1])
        with urlopen(Request(self.base+path,headers={'Accept':'application/json'}),timeout=5) as response:
            value=json.load(response)
        with self.lock: self.cache[path]=(time.monotonic(),value)
        return copy.deepcopy(value)
    def schedule(self,tournament):
        if tournament: return self.get('/tournaments/'+str(UUID(tournament))+'/schedule-summary')
        matches=self.get('/matches/ad-hoc/public')
        courts={m['court']['id']:m['court'] for m in matches if (m.get('court') or {}).get('id')}
        return dict(matches=matches,courts=list(courts.values()))

class Device:
    def __init__(self,path,base,fonts):
        self.path=path; self.base=base; self.catalog=Catalog(base)
        self.lock=threading.RLock(); self.apply_lock=threading.Lock(); self.stop=threading.Event()
        self.config=validate_config(json.loads(path.read_text()) if path.exists() else DEFAULTS)
        self.version=0; self.feed=None; self.feed_threads=[]; self.active_id=None
        self.matches=[]; self.next_match=None; self.error=''; self.catalog_at=0; self.finished=set()
        self.image=Image.new('RGB',(192,64)); self.frame_at=0; self.hardware_error=''
        self.renderer=Renderer(fonts); self.screens=Screens(self.renderer,ROOT/'assets/logo-white.png')
    def settings(self):
        with self.lock: return dict(self.config)
    def apply(self,value):
        config=validate_config(value)
        # Resolve IDs before changing the running display; never write to Y-Sports.
        if config['mode']=='match':
            match=self.catalog.get('/matches/'+config['match_id'])
            if match.get('id')!=config['match_id']: raise ValueError('Match not found')
        if config['mode'] in ('court','schedule'):
            schedule=self.catalog.schedule(config['tournament_id'])
            if config['court_id'] and not any(c['id']==config['court_id'] for c in schedule['courts']):
                raise ValueError('Court does not belong to this schedule')
        with self.apply_lock:
            save_config(self.path,config)
            with self.lock:
                self.config=config; self.version+=1
        return config
    def switch_feed(self,match_id):
        if match_id==self.active_id: return
        old=self.feed
        if old: old.stop.set()
        for thread in self.feed_threads: thread.join(timeout=6)
        feed=Feed(self.base,match_id) if match_id else None
        with self.lock: self.feed=feed; self.active_id=match_id
        self.feed_threads=[]
        if feed:
            for target in (feed.run,feed.run_colors):
                thread=threading.Thread(target=target,daemon=True); thread.start(); self.feed_threads.append(thread)
    def route(self):
        version=-1; last_refresh=0; schedule=None
        while not self.stop.is_set():
            with self.lock: config=dict(self.config); current_version=self.version
            try:
                if current_version!=version:
                    self.switch_feed(None)
                    schedule=None; last_refresh=0; self.finished.clear()
                    with self.lock: self.matches=[]; self.next_match=None; self.error=''; self.catalog_at=0
                    version=current_version
                mode=config['mode']
                if mode=='match': self.switch_feed(config['match_id'])
                elif mode in ('court','schedule'):
                    if time.monotonic()-last_refresh>=5:
                        fresh=self.catalog.schedule(config['tournament_id'])
                        schedule=fresh; last_refresh=time.monotonic()
                        with self.lock:
                            self.matches=fresh['matches']; self.catalog_at=time.monotonic(); self.error=''
                    if mode=='court' and schedule is not None:
                        view=self.feed.view() if self.feed else None
                        if view and view['status']=='FINISHED': self.finished.add(self.active_id)
                        selected,live=select_court(schedule['matches'],config['court_id'],self.active_id,self.finished)
                        self.switch_feed(selected['id'] if selected and live else None)
                        with self.lock: self.next_match=selected
                    else: self.switch_feed(None)
                else: self.switch_feed(None)
            except Exception as error:
                with self.lock: self.error=str(error)
                LOG.warning('Selection unavailable: %s',error)
                self.stop.wait(2)
            self.stop.wait(.25)
        self.switch_feed(None)
    def render(self):
        with self.lock:
            config=dict(self.config); feed=self.feed; matches=self.matches; upcoming=self.next_match; error=self.error
            catalog_at=self.catalog_at
        mode=config['mode']
        if mode=='blank': image=Image.new('RGB',(192,64))
        elif mode=='logo': image=self.screens.logo_screen()
        elif mode=='schedule': image=self.screens.schedule(matches,config)
        elif mode=='court' and not feed: image=self.screens.upcoming(upcoming,config)
        else: image=self.renderer.render(feed.view() if feed else None)
        if mode in ('court','schedule') and (error or not catalog_at or time.monotonic()-catalog_at>30):
            # Make stale schedule data visible rather than silently presenting it as current.
            from PIL import ImageDraw
            ImageDraw.Draw(image).rectangle((138,54,191,63),fill=(0,0,0))
            self.screens.text(image,'OFFLINE' if error else 'LOADING',140,54,(255,210,40))
        with self.lock: self.image=image; self.frame_at=time.monotonic()
        return image,config['brightness']
    def state(self):
        with self.lock:
            view=self.feed.view() if self.feed else None
            return dict(config=dict(self.config),active_match_id=self.active_id,match=view,error=self.error,
                        hardware_error=self.hardware_error,frame_age=round(time.monotonic()-self.frame_at,1) if self.frame_at else None)
    def preview(self):
        with self.lock: image=self.image.copy()
        output=BytesIO(); image.save(output,format='PNG'); return output.getvalue()

class Auth:
    def __init__(self,pin_path):
        if not pin_path.exists():
            pin_path.write_text(str(secrets.randbelow(900000)+100000)+'\n')
            pin_path.chmod(0o600)
        self.pin=pin_path.read_text().strip(); self.sessions={}; self.attempts={}; self.lock=threading.Lock()
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
            return token
    def valid(self,header):
        try:
            jar=cookies.SimpleCookie(); jar.load(header or ''); token=jar['session'].value
            with self.lock: return self.sessions.get(token,0)>time.monotonic()
        except (KeyError,cookies.CookieError): return False

class Handler(BaseHTTPRequestHandler):
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
    def do_GET(self):
        route=urlsplit(self.path)
        if route.path=='/healthz':
            ready=self.server.device.frame_at and time.monotonic()-self.server.device.frame_at<3
            return self.send(200 if ready else 503,{'ready':bool(ready)})
        if route.path=='/': return self.send(200,(ROOT/'admin.html').read_bytes(),'text/html; charset=utf-8')
        if route.path=='/admin.js': return self.send(200,(ROOT/'admin.js').read_bytes(),'text/javascript; charset=utf-8')
        if route.path=='/logo.png': return self.send(200,(ROOT/'assets/logo-white.png').read_bytes(),'image/png')
        if not self.authorized(): return
        try:
            if route.path=='/api/status': return self.send(200,self.server.device.state())
            if route.path=='/api/preview.png': return self.send(200,self.server.device.preview(),'image/png')
            if route.path=='/api/tournaments': return self.send(200,self.server.device.catalog.get('/tournaments',60))
            if route.path=='/api/catalog':
                tournament=parse_qs(route.query).get('tournament',[''])[0]
                return self.send(200,self.server.device.catalog.schedule(tournament))
            self.send(404,{'error':'Not found'})
        except ValueError as error: self.send(400,{'error':str(error)})
        except Exception as error: self.send(502,{'error':'Y-Sports unavailable: '+str(error)})
    def do_POST(self):
        if self.headers.get('Content-Type','').split(';')[0]!='application/json' or self.headers.get('X-Scoreboard-Request')!='1':
            return self.send(403,{'error':'Use the device admin page'})
        origin=self.headers.get('Origin')
        if origin and urlsplit(origin).netloc!=self.headers.get('Host'): return self.send(403,{'error':'Cross-origin request blocked'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=8192: raise ValueError('Invalid request size')
            value=json.loads(self.rfile.read(size))
            if self.path=='/api/login':
                token=self.server.auth.login(value.get('pin'),self.client_address[0])
                return self.send(200,{'ok':True},cookie='session='+token+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200')
            if not self.authorized(): return
            if self.path=='/api/config': return self.send(200,self.server.device.apply(value))
            if self.path=='/api/logout':
                jar=cookies.SimpleCookie(); jar.load(self.headers.get('Cookie',''))
                with self.server.auth.lock: self.server.auth.sessions.pop(jar['session'].value,None)
                return self.send(200,{'ok':True},cookie='session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            self.send(404,{'error':'Not found'})
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
    parser.add_argument('--order',default=os.environ.get('Y_SCORES_ORDER','2,1,0'))
    parser.add_argument('--rotate',default=os.environ.get('Y_SCORES_ROTATE','180,0,0'))
    parser.add_argument('--pinout',choices=['Active3','Active3BGR'],default=os.environ.get('Y_SCORES_PINOUT','Active3BGR'))
    parser.add_argument('--no-hardware',action='store_true')
    args=parser.parse_args(); logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    order=[int(v) for v in args.order.split(',')]; rotations=[int(v) for v in args.rotate.split(',')]
    if sorted(order)!=[0,1,2] or len(rotations)!=3 or any(r not in (0,90,180,270) for r in rotations):
        parser.error('Invalid panel order or rotations')
    args.config.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    args.pin_file.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    device=Device(args.config,args.base_url,args.fonts)
    server=Server((args.bind,args.port),Handler); server.device=device; server.auth=Auth(args.pin_file)
    matrix=None
    if not args.no_hardware:
        import adafruit_blinka_raspberry_pi5_piomatter as p
        from adafruit_blinka_raspberry_pi5_piomatter.pixelmappers import simple_multilane_mapper
        geometry=p.Geometry(width=64,height=192,n_addr_lines=5,n_planes=10,n_temporal_planes=2,n_lanes=6,map=simple_multilane_mapper(64,192,5,6))
        framebuffer=np.zeros((192,64,3),dtype=np.uint8)
        matrix=p.PioMatter(colorspace=p.Colorspace.RGB888Packed,pinout=getattr(p.Pinout,args.pinout),framebuffer=framebuffer,geometry=geometry)
    def stop(*_): device.stop.set()
    signal.signal(signal.SIGINT,stop); signal.signal(signal.SIGTERM,stop)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    worker=threading.Thread(target=device.route,daemon=True); worker.start()
    LOG.info('Device admin listening on port %s',args.port)
    try:
        while not device.stop.is_set():
            image,brightness=device.render()
            if matrix:
                for port,(tile,rotation) in enumerate(zip(order,rotations)):
                    panel=image.crop((tile*64,0,tile*64+64,64)).rotate(rotation)
                    framebuffer[port*64:port*64+64]=np.rint(np.asarray(panel).astype(np.float32)*brightness).astype(np.uint8)
                matrix.show()
            device.stop.wait(.05)
    finally:
        device.stop.set(); server.shutdown(); server.server_close(); worker.join(timeout=12)
        if matrix: framebuffer.fill(0); matrix.show()

if __name__=='__main__': main()
