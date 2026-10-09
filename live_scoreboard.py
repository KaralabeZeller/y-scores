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
from matrix_output import create_matrix, fill_framebuffer, TOPOLOGIES
from PIL import Image, ImageDraw, ImageFilter
from bdfparser import Font
from live_state import MEDIA_TYPE, project, clock, primary_colors, timeout_details
LOG = logging.getLogger('scoreboard')

def dark_color(color):
    # Display-only brightness heuristic; preserve the original team colour.
    return sum(channel*weight for channel,weight in zip(color,(.2126,.7152,.0722)))<60

class Feed:
    def __init__(self, base, match, poll_interval=.2, device_request=None):
        self.match = str(UUID(match))
        self.device_request=device_request
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
        self.events = []
        self.events_revision = 0
        self.events_url = self.url.removesuffix("/snapshot") + "/events"
    def fetch_events(self):
        with self.lock:
            target = self.snapshot['revision'] if self.snapshot else 0
            cursor = self.events_revision
        while cursor < target and not self.stop.is_set():
            if self.device_request:
                page=self.device_request('GET',f'/matches/{self.match}/operations/events?afterRevision={cursor}&limit=20')
            else:
                request = Request(f'{self.events_url}?afterRevision={cursor}&limit=100',
                                  headers={'Accept': MEDIA_TYPE, 'Cache-Control': 'no-cache'})
                with urlopen(request, timeout=3) as response:
                    page = json.load(response)
            next_cursor = page['toRevision']
            if next_cursor <= cursor:
                raise ValueError('Event history did not advance')
            with self.lock:
                self.events.extend(page['events'])
                self.events_revision = next_cursor
            cursor = next_cursor

    def run_events(self):
        while not self.stop.is_set():
            try:
                self.fetch_events()
            except Exception as error:
                LOG.warning('Timeout history unavailable: %s', error)
                self.stop.wait(2)
            self.stop.wait(.2)

    def fetch_colors(self):
        try:
            if self.device_request:
                colors=primary_colors(self.device_request('GET',f'/matches/{self.match}'))
            else:
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
            if self.device_request:
                payload=self.device_request('GET',f'/matches/{self.match}/operations/snapshot')
            else:
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
            age = time.monotonic()-self.received
            view = project(self.snapshot,age)
            view['match_id']=self.match; view['match_revision']=self.snapshot['revision']
            timeout = timeout_details(self.snapshot,self.events,age) if self.events_revision >= self.snapshot['revision'] else None
            view['timeout_seconds'] = timeout['seconds'] if timeout is not None else None
            view['timeout_team'] = timeout['team'] if timeout is not None else None
            for team,color in zip(view["teams"],self.colors): team["color"] = color
            return view
class Renderer:
    def __init__(self,fonts):
        self.small=Font(str(fonts/'6x9.bdf')); self.big=Font(str(fonts/'8x13B.bdf'))
    @lru_cache(maxsize=512)
    def tile(self,label,color,big=False,scale=1,outline=False):
        data=(self.big if big else self.small).draw(label,linelimit=2048).todata(2)
        tile=Image.new('RGB',(len(data[0]),len(data)))
        for y,row in enumerate(data):
            for x,on in enumerate(row):
                if on: tile.putpixel((x,y),color)
        tile=tile.resize((tile.width*scale,tile.height*scale),Image.Resampling.NEAREST)
        if outline:
            mask=self.tile(label,(255,255,255),big,scale).convert('L')
            padded=Image.new('L',(tile.width+2,tile.height+2))
            padded.paste(mask,(1,1))
            tile=Image.new('RGB',padded.size)
            tile.paste((235,235,235),(0,0),padded.filter(ImageFilter.MaxFilter(3)))
            tile.paste(color,(0,0),padded)
        return tile
    def render(self,view):
        im=Image.new('RGB',(192,64)); draw=ImageDraw.Draw(im)
        frame_time=time.monotonic()
        white,yellow,dim=(235,235,235),(255,255,0),(70,70,70)
        def text(label,center,y,color,big=False,scale=1,outline=False):
            tile=self.tile(str(label),color,big,scale,outline)
            im.paste(tile,(int(center-tile.width/2),y-int(outline)))
        if view is None:
            text('CONNECTING',96,24,yellow)
            return im
        timer=self.tile(clock(view['elapsed_seconds']),white,True)
        timer=timer.resize((60,26),Image.Resampling.NEAREST)
        im.paste(timer,(66,14))
        period_count=view.get('period_count',2)
        if view['period']>period_count:
            text('OT'+str(view['period']-period_count),96,2,white)
        elif 1<=period_count<=10:
            gap=2; width=min(10,(60-(period_count-1)*gap)//period_count)
            start=64+(64-(period_count*width+(period_count-1)*gap))//2
            for index in range(period_count):
                x=start+index*(width+gap)
                draw.rectangle((x,4,x+width-1,7),fill=white if index+1==view['period'] else (55,55,55))
        else:
            text('P'+str(view['period']),96,2,white)
        status={'RUNNING':'LIVE','FINISHED':'FINAL','PERIOD_COMPLETE':'BREAK'}.get(view['status'],view['status'])
        remaining=view.get('timeout_seconds')
        if status=='OFFLINE': text('OFFLINE',96,47,yellow)
        elif remaining is not None:
            team=view.get('timeout_team')
            color=view['teams'][team]['color'] if type(team) is int and 0<=team<len(view['teams']) else yellow
            text(str(remaining),96,45,color,True,outline=dark_color(color))
        for side,team in enumerate(view['teams']):
            offset=side*128
            color=team["color"]
            dark=dark_color(color)
            label_color=white if dark else color
            name=unicodedata.normalize('NFKD',team['name']).encode('ascii','ignore').decode().upper() or 'TEAM'
            if len(name)>10:
                cycle=name+'   '; start=int(frame_time/.6)%len(cycle)
                name=(cycle+cycle)[start:start+10]
            text(name,offset+32,0,label_color)
            score=str(team['score'])
            text(score,offset+32,10,color,True,2 if len(score)<=3 else 1,dark)
            for index in range(3):
                x=offset+20+index*9
                draw.rectangle((x,36,x+5,37),fill=label_color if index<team['timeouts'] else dim)
            if team['timeouts']>3: text(str(team['timeouts']),offset+57,33,label_color)
            penalties=team['penalties']
            if len(penalties)>3:
                start=(int(frame_time/3)*3)%len(penalties)
                penalties=(penalties+penalties)[start:start+3]
            for index,(number,remaining) in enumerate(penalties):
                y=40+index*8
                number_tile=self.tile(f'#{number}',yellow)
                timer_tile=self.tile(clock(remaining),yellow)
                im.paste(number_tile,(offset,y))
                im.paste(timer_tile,(offset+64-timer_tile.width,y))
        return im

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--match-id',required=True)
    parser.add_argument('--base-url',default='https://y-sports.xyz/api-next')
    parser.add_argument('--fonts',type=Path,default=Path(__file__).parent/'fonts')
    parser.add_argument('--poll-interval',type=float,default=.2,help='Snapshot cadence in seconds (minimum .1)')
    parser.add_argument('--brightness',type=float,default=.08)
    parser.add_argument('--topology',choices=TOPOLOGIES,default='parallel')
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
    matrix,fb=create_matrix(args.topology,args.pinout)
    def stop(*_): feed.stop.set()
    signal.signal(signal.SIGINT,stop); signal.signal(signal.SIGTERM,stop)
    threading.Thread(target=feed.run_colors,daemon=True).start()
    threading.Thread(target=feed.run_events,daemon=True).start()
    threading.Thread(target=feed.run,daemon=True).start()
    LOG.info('Live match %s; port order=%s rotations=%s pinout=%s',args.match_id,order,rotations,args.pinout)
    try:
        while not feed.stop.is_set():
            im=renderer.render(feed.view())
            fill_framebuffer(fb,im,args.brightness,args.topology,order,rotations)
            matrix.show(); feed.stop.wait(.05)
    finally:
        feed.stop.set(); fb.fill(0); matrix.show()
if __name__=='__main__': main()
