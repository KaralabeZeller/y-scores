"""Configuration and court routing for the local scoreboard appliance."""
import json
import math
import os
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

DEFAULTS = dict(mode='logo',match_id='',
                tournament_id='',court_id='',brightness=.08,timezone='Europe/Budapest')
MODES = {'match','court','schedule','logo','blank','manual'}
LIVE = {'IN_PROGRESS','RUNNING','PAUSED','PERIOD_COMPLETE','LIVE'}
DONE = {'FINISHED','ENDED','COMPLETED','CANCELLED','CANCELED'}

def validate_config(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULTS): raise ValueError('Unknown settings')
    result=DEFAULTS | value
    if result['mode'] not in MODES: raise ValueError('Choose a display mode')
    for field in ('match_id','tournament_id','court_id'):
        item=result[field]
        if not isinstance(item,str): raise ValueError(f'Invalid {field}')
        if item: result[field]=str(UUID(item.strip()))
    brightness=result['brightness']
    if isinstance(brightness,bool) or not isinstance(brightness,(int,float)) or not math.isfinite(brightness) or not .01<=brightness<=1:
        raise ValueError('Brightness must be between 1 and 100 percent')
    try: ZoneInfo(result['timezone'])
    except (ValueError,TypeError,KeyError): raise ValueError('Unknown timezone')
    if result['mode']=='match' and not result['match_id']: raise ValueError('Select a match or enter its ID')
    if result['mode']=='court' and not result['court_id']: raise ValueError('Select a court')
    return result

def save_config(path,value):
    value=validate_config(value)
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as output:
        json.dump(value,output,indent=2)
        output.flush(); os.fsync(output.fileno())
    temporary.replace(path)
    return value

def filtered_matches(matches,court_id=''):
    return sorted((m for m in matches if not court_id or (m.get('court') or {}).get('id')==court_id),
                  key=lambda m:(m.get('matchTime') or '9999',m['id']))

def select_court(matches,court_id,current_id=None,finished=()):
    eligible=[m for m in filtered_matches(matches,court_id)
              if str(m.get('status','')).upper() not in DONE and m['id'] not in finished]
    live=[m for m in eligible if str(m.get('status','')).upper() in LIVE]
    if live:
        return next((m for m in live if m['id']==current_id),live[0]),True
    return (eligible[0],False) if eligible else (None,False)
