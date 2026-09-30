"""Read-only projection of public Y-Sports v2 snapshots."""
from datetime import datetime
import math
import re

def primary_colors(metadata):
    colors = []
    for key, fallback in [("teamA", (40,200,255)), ("teamB", (255,155,40))]:
        value = ((metadata.get(key) or {}).get("details") or {}).get("home_color")
        colors.append(tuple(int(value[i:i+2],16) for i in (1,3,5))
                      if isinstance(value,str) and re.fullmatch(r"#[0-9a-fA-F]{6}",value) else fallback)
    return colors

STALE_SECONDS = 15
MEDIA_TYPE = 'application/vnd.ysports.public-match-operations.v2+json'
def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
def clock(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60:02}:{seconds % 60:02}'
def project(snapshot, age=0):
    state, config = snapshot['state'], snapshot['configuration']
    period = state['currentPeriod']
    duration = config['regularPeriodDurationMs'] if period <= config['regularPeriodCount'] else config['overtimePeriodDurationMs']
    running = state['clockRunning'] and state['status'] == 'RUNNING'
    advance = 0
    if running:
        anchor = state.get('clockAnchorAt') or snapshot['serverTime']
        advance = max(0, (timestamp(snapshot['serverTime']) - timestamp(anchor))*1000)
        advance += min(max(age, 0), STALE_SECONDS)*1000
        advance = min(advance, max(0, duration-state['periodElapsedAnchorMs']))
    effective = state['effectiveElapsedAnchorMs']+advance
    teams = []
    for suffix, side in [('A','TEAM_A'),('B','TEAM_B')]:
        team = snapshot['participants']['team'+suffix]
        numbers = {p['id']: p.get('playerNumber') for p in team['players']}
        penalties = []
        for penalty in snapshot['activeSuspensions']:
            remaining = penalty['expiresAtEffectiveElapsedMs']-effective
            if penalty['teamSide']==side and penalty['status']=='ACTIVE' and remaining>0:
                number = numbers.get(penalty['participantId'])
                penalties.append((number if number is not None else '?', math.floor(remaining/1000)))
        teams.append(dict(name=team.get('name') or ('TEAM '+suffix), score=state['scoreTeam'+suffix],
                          timeouts=state['team'+suffix+'TimeoutsUsed'], penalties=sorted(penalties,key=lambda p:p[1])))
    return dict(teams=teams, period=period,
                elapsed_seconds=int(min(duration,max(0,state['periodElapsedAnchorMs']+advance))//1000),
                status='OFFLINE' if age>=STALE_SECONDS else state['status'], revision=snapshot['revision'])


def timeout_seconds(snapshot, events, age=0):
    """Match Center's 60-second timeout, anchored to the accepted event timestamp."""
    state = snapshot['state']
    if age >= STALE_SECONDS or state['clockRunning'] or state['status'] != 'PAUSED':
        return None
    events = [e for e in events if e['revision'] <= snapshot['revision']]
    invalid = {e.get('targetEventId') for e in events
               if e['type'] in ('EVENT_VOIDED', 'EVENT_REPLACED')}
    effective = [e for e in events if e['id'] not in invalid]
    candidates = [e for e in effective if e['type'] == 'TEAM_TIMEOUT_RECORDED'
                  and e.get('period') == state['currentPeriod']
                  and e.get('effectiveElapsedMs') == state['effectiveElapsedAnchorMs']]
    if not candidates:
        return None
    timeout = max(candidates, key=lambda e: e['revision'])
    if any(e['revision'] > timeout['revision'] and e['type'] in
           ('CLOCK_STARTED', 'PERIOD_COMPLETED', 'PERIOD_INITIALIZED',
            'MATCH_FINISHED', 'CLOCK_ADJUSTED') for e in effective):
        return None
    elapsed = timestamp(snapshot['serverTime']) - timestamp(timeout['recordedAt']) + max(0, age)
    return math.floor(max(0, min(60, 60-elapsed)))
