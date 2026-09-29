"""Live three-panel scoreboard using the public match snapshot; no server writes."""
import argparse
from functools import lru_cache
import json
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urlsplit
import logging
from pathlib import Path
import signal
import threading
import time
import unicodedata
from urllib.request import Request, urlopen
from uuid import UUID
import numpy as np
from PIL import Image, ImageDraw
from bdfparser import Font
from live_state import MEDIA_TYPE, project, clock, primary_colors
LOG = logging.getLogger('scoreboard')
class Feed:
    def __init__(self, base, match, poll_interval=.2):
        self.match = str(UUID(match))
        self.url = f'{base.rstrip("/")}/public/matches/{self.match}/operations/snapshot'
        endpoint = urlsplit(self.url)
        if endpoint.scheme not in ("http", "https"): raise ValueError("HTTP(S) API required")
        self.connection = (HTTPSConnection if endpoint.scheme == "https" else HTTPConnection)(endpoint.hostname, endpoint.port, timeout=3)
        self.snapshot_path = endpoint.path
        self.poll_interval = poll_interval
        self.last_fetch_seconds = 0
        self.metadata_url = f'{base.rstrip("/")}/matches/{self.match}'
        self.colors = primary_colors({})
        self.lock = threading.Lock()
        self.snapshot = None
        self.received = 0
        self.stop = threading.Event()
    def fetch_colors(self):
        try:
            with urlopen(Request(self.metadata_url,headers={"Cache-Control":"no-cache"}),timeout=5) as response:
                colors = primary_colors(json.load(response))
            with self.lock:
                if colors != self.colors: LOG.info("Team primary colors: %s", colors)
                self.colors = colors
        except Exception as error:
            LOG.warning("Team colors unavailable; retaining palette: %s",error)
    def run_colors(self):
        while not self.stop.is_set():
            self.fetch_colors()
            self.stop.wait(30)
    def fetch(self):
        started = time.monotonic()
        try:
            self.connection.request('GET',self.snapshot_path,headers={'Accept':MEDIA_TYPE,'Cache-Control':'no-cache'})
            response = self.connection.getresponse()
            body = response.read()
            if response.status != 200: raise RuntimeError(f'Snapshot HTTP {response.status}')
            payload = json.loads(body)
        except Exception:
            self.connection.close()
            raise
        self.last_fetch_seconds = time.monotonic()-started
        if payload['matchId'] != self.match:
            raise ValueError('Unexpected match ID')
        project(payload)
        with self.lock:
            if self.snapshot and payload['revision'] < self.snapshot['revision']:
                return
            if not self.snapshot or payload['revision'] != self.snapshot['revision']:
                s=payload['state']
                LOG.info('revision=%s score=%s:%s status=%s period=%s',payload['revision'],s['scoreTeamA'],s['scoreTeamB'],s['status'],s['currentPeriod'])
            self.snapshot,self.received = payload,time.monotonic()
    def run(self):
        failures=0
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                self.fetch()
                if failures: LOG.info('Connection restored')
                failures=0
            except Exception as error:
                failures+=1
                if failures==1 or failures%15==0: LOG.warning('Snapshot unavailable: %s',error)
            delay = min(5,failures) if failures else max(.01,self.poll_interval-(time.monotonic()-started))
            self.stop.wait(delay)
        self.connection.close()
    def view(self):
        with self.lock:
            if not self.snapshot: return None
            view = project(self.snapshot,time.monotonic()-self.received)
            for team,color in zip(view["teams"],self.colors): team["color"] = color
            return view
class Renderer:
    def __init__(self,fonts):
        self.small=Font(str(fonts/'6x9.bdf')); self.big=Font(str(fonts/'8x13B.bdf'))
    @lru_cache(maxsize=512)
    def tile(self,label,color,big=False,scale=1):
        data=(self.big if big else self.small).draw(label,linelimit=2048).todata(2)
        tile=Image.new('RGB',(len(data[0]),len(data)))
        for y,row in enumerate(data):
            for x,on in enumerate(row):
                if on: tile.putpixel((x,y),color)
        return tile.resize((tile.width*scale,tile.height*scale),Image.Resampling.NEAREST)
    def render(self,view):
        im=Image.new('RGB',(192,64)); draw=ImageDraw.Draw(im)
        white,yellow,dim=(235,235,235),(255,255,0),(70,70,70)
        def text(label,center,y,color,big=False,scale=1):
            tile=self.tile(str(label),color,big,scale)
            im.paste(tile,(int(center-tile.width/2),y))
        if view is None:
            text('CONNECTING',96,24,yellow)
            return im
        text('Y-SPORTS',96,0,white)
        text(clock(view['elapsed_seconds']),96,14,white,True)
        text('P'+str(view['period']),96,31,white)
        status={'RUNNING':'LIVE','FINISHED':'FINAL','PERIOD_COMPLETE':'BREAK'}.get(view['status'],view['status'])
        text(status[:10],96,47,yellow if status=='OFFLINE' else dim)
        for side,team in enumerate(view['teams']):
            offset=side*128
            color=team["color"]
            name=unicodedata.normalize('NFKD',team['name']).encode('ascii','ignore').decode().upper() or 'TEAM'
            if len(name)>10:
                cycle=name+'   '; start=int(time.monotonic()/.6)%len(cycle)
                name=(cycle+cycle)[start:start+10]
            text(name,offset+32,0,color)
            score=str(team['score'])
            text(score,offset+32,10,color,True,2 if len(score)<=3 else 1)
            text('TO',offset+10,34,color)
            for index in range(3):
                x=offset+24+index*9
                draw.rectangle((x,36,x+3,39),fill=color if index<team['timeouts'] else dim)
            if team['timeouts']>3: text(str(team['timeouts']),offset+57,33,color)
            penalties=team['penalties']
            if len(penalties)>3:
                start=(int(time.monotonic()/3)*3)%len(penalties)
                penalties=(penalties+penalties)[start:start+3]
            for index,(number,remaining) in enumerate(penalties):
                text(f'#{number} {clock(remaining)}',offset+32,41+index*8,yellow)
        return im

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--match-id',required=True)
    parser.add_argument('--base-url',default='https://y-sports.xyz/api-next')
    parser.add_argument('--fonts',type=Path,default=Path(__file__).parent/'fonts')
    parser.add_argument('--poll-interval',type=float,default=.2,help='Snapshot cadence in seconds (minimum .1)')
    parser.add_argument('--brightness',type=float,default=.08)
    parser.add_argument('--order',default='2,1,0')
    parser.add_argument('--rotate',default='180,0,0')
    parser.add_argument('--pinout',choices=['Active3','Active3BGR'],default='Active3BGR')
    parser.add_argument('--preview',type=Path,help='Save one live frame without accessing GPIO')
    args=parser.parse_args()
    order=[int(v) for v in args.order.split(',')]; rotations=[int(v) for v in args.rotate.split(',')]
    if sorted(order)!=[0,1,2] or len(rotations)!=3 or any(r not in (0,90,180,270) for r in rotations) or not 0<args.brightness<=1:
        parser.error('Invalid mapping, rotations or brightness')
    if args.poll_interval < .1: parser.error('Poll interval must be at least .1 seconds')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    renderer=Renderer(args.fonts); feed=Feed(args.base_url,args.match_id,args.poll_interval)
    if args.preview:
        feed.fetch_colors(); feed.fetch(); renderer.render(feed.view()).save(args.preview); feed.connection.close()
        return
    import adafruit_blinka_raspberry_pi5_piomatter as p
    from adafruit_blinka_raspberry_pi5_piomatter.pixelmappers import simple_multilane_mapper
    geometry=p.Geometry(width=64,height=192,n_addr_lines=5,n_planes=10,n_temporal_planes=2,n_lanes=6,map=simple_multilane_mapper(64,192,5,6))
    fb=np.zeros((192,64,3),dtype=np.uint8)
    matrix=p.PioMatter(colorspace=p.Colorspace.RGB888Packed,pinout=getattr(p.Pinout,args.pinout),framebuffer=fb,geometry=geometry)
    def stop(*_): feed.stop.set()
    signal.signal(signal.SIGINT,stop); signal.signal(signal.SIGTERM,stop)
    threading.Thread(target=feed.run_colors,daemon=True).start()
    threading.Thread(target=feed.run,daemon=True).start()
    LOG.info('Live match %s; port order=%s rotations=%s pinout=%s',args.match_id,order,rotations,args.pinout)
    try:
        while not feed.stop.is_set():
            im=renderer.render(feed.view())
            for port,tile in enumerate(order):
                panel=im.crop((tile*64,0,tile*64+64,64)).rotate(rotations[port])
                fb[port*64:port*64+64]=np.rint(np.asarray(panel).astype(np.float32)*args.brightness).astype(np.uint8)
            matrix.show(); feed.stop.wait(.05)
    finally:
        feed.stop.set(); fb.fill(0); matrix.show()
if __name__=='__main__': main()
