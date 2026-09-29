"""Small native-resolution screens; keeps the existing scoreboard renderer."""
from datetime import datetime
import time
import unicodedata
from zoneinfo import ZoneInfo
from PIL import Image
from device_model import DONE, filtered_matches

class Screens:
    def __init__(self,renderer,logo_path):
        self.renderer=renderer
        logo=Image.open(logo_path).convert('RGBA')
        bounds=logo.getbbox()
        if bounds: logo=logo.crop(bounds)
        logo.thumbnail((174,48),Image.Resampling.LANCZOS)
        self.logo=logo
    def text(self,image,label,x,y,color=(235,235,235),center=False):
        label=unicodedata.normalize('NFKD',str(label)).encode('ascii','ignore').decode()
        tile=self.renderer.tile(label,color)
        image.paste(tile,(int(x-tile.width/2) if center else x,y))
    def time_label(self,match,zone):
        value=match.get('matchTime')
        if not value: return 'TBD'
        try: return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(ZoneInfo(zone)).strftime('%H:%M')
        except ValueError: return 'TBD'
    def logo_screen(self):
        image=Image.new('RGB',(192,64))
        image.paste(self.logo,((192-self.logo.width)//2,(64-self.logo.height)//2),self.logo)
        return image
    def upcoming(self,match,config):
        image=Image.new('RGB',(192,64))
        self.text(image,'UP NEXT',96,1,(255,210,40),True)
        if not match:
            self.text(image,'NO UPCOMING MATCH',96,25,center=True)
            return image
        a=(match.get('teamA') or {}).get('name') or 'TBD'
        b=(match.get('teamB') or {}).get('name') or 'TBD'
        self.text(image,a[:30],96,16,center=True)
        self.text(image,'vs '+b[:27],96,29,center=True)
        court=(match.get('court') or {}).get('name') or ''
        self.text(image,(self.time_label(match,config['timezone'])+'  '+court)[:31],96,48,(100,180,255),True)
        return image
    def schedule(self,matches,config):
        image=Image.new('RGB',(192,64))
        matches=filtered_matches(matches,config['court_id'])
        # Keep fixtures still to play first. Historic fixtures remain browseable.
        matches=([m for m in matches if str(m.get('status','')).upper() not in DONE]
                 +[m for m in matches if str(m.get('status','')).upper() in DONE])
        pages=max(1,(len(matches)+2)//3); page=int(time.monotonic()/6)%pages
        self.text(image,f'SCHEDULE {page+1}/{pages}',96,0,(100,180,255),True)
        if not matches: self.text(image,'NO MATCHES SCHEDULED',96,28,center=True)
        for row,match in enumerate(matches[page*3:page*3+3]):
            y=13+row*17
            a=(match.get('teamA') or {}).get('name') or 'TBD'; b=(match.get('teamB') or {}).get('name') or 'TBD'
            self.text(image,f'{self.time_label(match,config["timezone"])} {a[:11]} / {b[:11]}',2,y)
            date=str(match.get('matchTime') or '')[:10]
            court=(match.get('court') or {}).get('name') or 'Court TBD'
            self.text(image,(date+' '+court)[:31],2,y+8,(100,120,150))
        return image
