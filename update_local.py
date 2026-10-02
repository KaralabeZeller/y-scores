"""Safe updater status and operator deferral for the protected local admin."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from update_core import future, write_json

PUBLIC=Path('/var/lib/y-scores-updater/public.json')
def status(path=PUBLIC):
    try:
        value=json.loads(path.read_text())
        return {key:value.get(key) for key in ('enabled','state','progressPercent','reason','installedVersion','installedSequence','jobId')}
    except (OSError,ValueError): return dict(enabled=False,state='UNAVAILABLE',reason='NOT_CONFIGURED')

def deferred(path):
    try:
        until=json.loads(Path(path).read_text()).get('until')
        return until if future(until) else None
    except (OSError,ValueError): return None

def defer(path,minutes):
    if type(minutes) is not int or not 0<=minutes<=1440: raise ValueError('Defer updates for 0 to 1440 minutes')
    until=(datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat() if minutes else None
    write_json(path,{'until':until}); return {'deferredUntil':until}
