"""Native 192x64 next-three schedule using a compact pixel alphabet."""
from datetime import datetime
from zoneinfo import ZoneInfo
import unicodedata
from PIL import Image, ImageDraw
from device_model import DONE, filtered_matches

GLYPHS = {'A': ['010', '101', '111', '101', '101'], 'B': ['110', '101', '110', '101', '110'], 'C': ['011', '100', '100', '100', '011'], 'D': ['110', '101', '101', '101', '110'], 'E': ['111', '100', '110', '100', '111'], 'F': ['111', '100', '110', '100', '100'], 'G': ['011', '100', '101', '101', '011'], 'H': ['101', '101', '111', '101', '101'], 'I': ['111', '010', '010', '010', '111'], 'J': ['001', '001', '001', '101', '010'], 'K': ['101', '101', '110', '101', '101'], 'L': ['100', '100', '100', '100', '111'], 'M': ['101', '111', '111', '101', '101'], 'N': ['101', '111', '111', '111', '101'], 'O': ['010', '101', '101', '101', '010'], 'P': ['110', '101', '110', '100', '100'], 'Q': ['010', '101', '101', '111', '011'], 'R': ['110', '101', '110', '101', '101'], 'S': ['011', '100', '010', '001', '110'], 'T': ['111', '010', '010', '010', '010'], 'U': ['101', '101', '101', '101', '111'], 'V': ['101', '101', '101', '101', '010'], 'W': ['101', '101', '111', '111', '101'], 'X': ['101', '101', '010', '101', '101'], 'Y': ['101', '101', '010', '010', '010'], 'Z': ['111', '001', '010', '100', '111'], '0': ['111', '101', '101', '101', '111'], '1': ['010', '110', '010', '010', '111'], '2': ['110', '001', '010', '100', '111'], '3': ['110', '001', '010', '001', '110'], '4': ['101', '101', '111', '001', '001'], '5': ['111', '100', '110', '001', '110'], '6': ['011', '100', '111', '101', '111'], '7': ['111', '001', '010', '010', '010'], '8': ['111', '101', '111', '101', '111'], '9': ['111', '101', '111', '001', '110'], ':': ['0', '1', '0', '1', '0'], '/': ['001', '001', '010', '100', '100'], '-': ['000', '000', '111', '000', '000'], ' ': ['00', '00', '00', '00', '00']}

def label(value):
    return unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode().upper()

def text(image, value, x, y, color, scale=1, max_width=None):
    draw = ImageDraw.Draw(image)
    edge = x + max_width if max_width is not None else image.width
    for char in label(value):
        rows = GLYPHS.get(char, GLYPHS[' '])
        width = len(rows[0]) * scale
        if x + width > edge: break
        for dy, row in enumerate(rows):
            for dx, bit in enumerate(row):
                if bit == '1':
                    draw.rectangle((x+dx*scale,y+dy*scale,x+(dx+1)*scale-1,y+(dy+1)*scale-1),fill=color)
        x += width + scale

def render(matches, config):
    image = Image.new('RGB', (192,64))
    draw = ImageDraw.Draw(image)
    rows = [m for m in filtered_matches(matches,config.get('court_id',''))
            if str(m.get('status','')).upper() not in DONE][:3]
    def local_time(match):
        try: return datetime.fromisoformat(match['matchTime'].replace('Z','+00:00')).astimezone(ZoneInfo(config['timezone']))
        except (KeyError,ValueError,TypeError): return None
    text(image,'UP NEXT',3,3,(255,212,59))
    if rows:
        date = local_time(rows[0])
        text(image,date.strftime('%d %b') if date else 'DATE TBD',59,3,(190,212,236),max_width=38)
        courts = {(m.get('court') or {}).get('name') or 'COURT TBD' for m in rows}
        court = next(iter(courts)) if len(courts)==1 else 'MULTI COURT'
        text(image,court,110,3,(190,212,236),max_width=79)
    draw.line((3,11,188,11),fill=(49,67,82))
    if not rows: text(image,'NO UPCOMING MATCHES',59,29,(190,212,236))
    for i, match in enumerate(rows):
        y=15+i*16
        accent=(255,212,59) if i==0 else (133,203,255)
        draw.rectangle((2,y,41,y+12),fill=(40,48,26) if i==0 else (16,27,38))
        draw.line((2,y,2,y+12),fill=accent)
        date=local_time(match)
        text(image,date.strftime('%H:%M') if date else 'TBD',5,y+2,accent,2,max_width=36)
        a=label((match.get('teamA') or {}).get('name') or 'TBD')[:16]
        b=label((match.get('teamB') or {}).get('name') or 'TBD')[:16]
        text(image,a+' / '+b,48,y+1,(242,245,248),max_width=142)
        category=match.get('categoryName') or (match.get('category') or {}).get('name') or 'CATEGORY TBD'
        text(image,category,48,y+8,(131,169,200),max_width=142)
    return image
