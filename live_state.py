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

def penalty_display_seconds(expires_ms, effective_ms, period_elapsed_ms):
    """Sample penalty digits on the period clock's whole-second boundaries.

    Expiry checks must still use the exact effective clock, not this display value.
    Translating the deadline to the period axis keeps fractional starts in sync;
    the tiny tolerance only removes floating-point noise in that translation.
    """
    deadline_seconds = (expires_ms-effective_ms+period_elapsed_ms)/1000
    return max(0, math.floor(deadline_seconds+1e-9)-math.floor(period_elapsed_ms/1000))

class PenaltyDisplay:
    """Keep unchanged live penalty digits attached to the visible match tick.

    One instance belongs to one Feed and is accessed under that Feed's lock.
    Refreshing an unchanged event must not recalculate its displayed deadline.
    """
    def __init__(self):
        self.values = {}
        self.revision = None
        self.timing = None

    def begin(self, snapshot, tick):
        state = snapshot['state']
        timing = (state['currentPeriod'], state['status'], state['clockRunning'],
                  state.get('clockAnchorAt'), state['periodElapsedAnchorMs'],
                  state['effectiveElapsedAnchorMs'])
        # New clock commands may legitimately move penalty time in either direction.
        # Same-revision refreshes, however, are never a new operator correction.
        if self.timing is not None and (
                timing[:3] != self.timing[:3] or
                (snapshot['revision'] != self.revision and timing != self.timing)):
            self.values.clear()
        self.revision, self.timing, self.tick = snapshot['revision'], timing, tick
        self.active = set()

    def seconds(self, side, penalty, candidate, expired=False):
        key = (side, penalty.get('sourceEventId'), penalty['participantId'],
               penalty['expiresAtEffectiveElapsedMs'])
        self.active.add(key)
        # Once expired, a refresh with an older time sample must not revive a row.
        if expired or (key in self.values and self.values[key] is None):
            self.values[key] = None
            return None
        deadline, previous = self.values.get(key, (self.tick+candidate, candidate))
        # Network jitter may briefly move the main projection backwards; never
        # increase an unchanged penalty in response. A clock correction resets us.
        displayed = min(previous, max(0, deadline-self.tick))
        self.values[key] = deadline, displayed
        return displayed

    def finish(self):
        self.values = {key:value for key,value in self.values.items() if key in self.active}


def project(snapshot, age=0, penalty_display=None):
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
    period_elapsed = min(duration,max(0,state['periodElapsedAnchorMs']+advance))
    elapsed_seconds = int(period_elapsed//1000)
    if penalty_display is not None:
        penalty_display.begin(snapshot, elapsed_seconds)
    teams = []
    for suffix, side in [('A','TEAM_A'),('B','TEAM_B')]:
        team = snapshot['participants']['team'+suffix]
        numbers = {p['id']: p.get('playerNumber') for p in team['players']}
        penalties = []
        for penalty in snapshot['activeSuspensions']:
            remaining = penalty['expiresAtEffectiveElapsedMs']-effective
            if penalty['teamSide']==side and penalty['status']=='ACTIVE':
                number = numbers.get(penalty['participantId'])
                seconds = penalty_display_seconds(penalty['expiresAtEffectiveElapsedMs'],effective,period_elapsed)
                if penalty_display is not None:
                    seconds = penalty_display.seconds(side, penalty, seconds, remaining<=0)
                if remaining<=0 or seconds is None:
                    continue
                penalties.append((number if number is not None else '?', seconds))
        teams.append(dict(name=team.get('name') or ('TEAM '+suffix), score=state['scoreTeam'+suffix],
                          timeouts=state['team'+suffix+'TimeoutsUsed'], penalties=sorted(penalties,key=lambda p:p[1])))
    if penalty_display is not None:
        penalty_display.finish()
    return dict(teams=teams, period=period, period_count=config['regularPeriodCount'],
                elapsed_seconds=elapsed_seconds,
                status='OFFLINE' if age>=STALE_SECONDS else state['status'], revision=snapshot['revision'])


def timeout_details(snapshot, events, age=0):
    """Countdown and requesting team from the same accepted timeout event."""
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
    return dict(seconds=math.floor(max(0, min(60, 60-elapsed))),
                team={'TEAM_A': 0, 'TEAM_B': 1}.get(timeout.get('teamSide')))


def timeout_seconds(snapshot, events, age=0):
    """Match Center's 60-second timeout, anchored to the accepted event timestamp."""
    timeout = timeout_details(snapshot, events, age)
    return timeout['seconds'] if timeout is not None else None
