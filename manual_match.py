"""Authoritative local handball clock, independent of accounts and networks."""
import copy
import json
import math
import os
import threading
import time
import uuid

SETTINGS = dict(sport='handball', teamA='Team A', teamB='Team B', periodSeconds=1800,
                periods=2, clockDirection='up', timeoutLimit=3, timeoutSeconds=60, penaltySeconds=120)

class Conflict(ValueError):
    pass

def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer between {low} and {high}')
    return value

def settings(value):
    if not isinstance(value, dict) or set(value) - set(SETTINGS):
        raise ValueError('Unknown manual settings')
    result = SETTINGS | value
    if result['sport'] != 'handball': raise ValueError('Only handball is currently supported')
    for key in ('teamA', 'teamB'):
        name = result[key]
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40 or any(ord(c) < 32 for c in name):
            raise ValueError('Team names must contain 1–40 printable characters')
        result[key] = name.strip()
    if result['clockDirection'] not in ('up', 'down'): raise ValueError('Choose a clock direction')
    for key, low, high in [('periodSeconds', 1, 7200), ('periods', 1, 10), ('timeoutLimit', 0, 10),
                           ('timeoutSeconds', 1, 300), ('penaltySeconds', 1, 600)]:
        integer(result[key], low, high, key)
    return result

class ManualMatch:
    def __init__(self, path, now=time.monotonic):
        self.path, self.now = path, now
        self.lock = threading.RLock()
        self.data = json.loads(path.read_text()) if path.exists() else self.fresh(SETTINGS, 0)
        self.data['settings'] = settings(self.data['settings'])
        # A reboot never starts a clock without an operator. The last five-second
        # checkpoint is retained, without counting power-off or installation time.
        self.data['running'] = False
        self.data['timeout'] = None
        self.anchor = self.checkpoint = self.now()
    @staticmethod
    def fresh(config, revision):
        return dict(settings=copy.deepcopy(config), revision=revision, period=1, elapsed=0., effective=0.,
                    running=False, timeout=None,
                    teams=[dict(score=0, timeoutsUsed=0, penalties=[]) for _ in range(2)])
    def save(self):
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as output:
            json.dump(self.data, output, ensure_ascii=False)
            output.flush(); os.fsync(output.fileno())
        temporary.chmod(0o600)
        temporary.replace(self.path)
        self.checkpoint = self.now()
    def advance(self):
        now = self.now()
        if self.data['running']:
            advance = min(max(0, now - self.anchor), self.data['settings']['periodSeconds'] - self.data['elapsed'])
            self.data['elapsed'] += advance
            self.data['effective'] += advance
            if self.data['elapsed'] >= self.data['settings']['periodSeconds']:
                self.data['running'] = False
                self.data['revision'] += 1
                self.save()
        self.anchor = now
        timeout = self.data['timeout']
        if timeout and now >= timeout['until']:
            self.data['timeout'] = None
            self.data['revision'] += 1
            self.save()
        if self.data['running'] and now - self.checkpoint >= 5: self.save()
    def snapshot(self):
        with self.lock:
            self.advance()
            result = copy.deepcopy(self.data)
            for team in result['teams']:
                team['penalties'] = [p | dict(remainingSeconds=max(0, math.ceil(p['expires'] - result['effective'])))
                                     for p in team['penalties'] if p['expires'] > result['effective']]
                team['timeoutsRemaining'] = max(0, result['settings']['timeoutLimit'] - team['timeoutsUsed'])
            result['timeoutRemaining'] = math.ceil(max(0, result['timeout']['until'] - self.anchor)) if result['timeout'] else None
            if result['timeout']: result['timeout'].pop('until')
            result['clockSeconds'] = int(result['elapsed']) if result['settings']['clockDirection'] == 'up' else math.ceil(result['settings']['periodSeconds'] - result['elapsed'])
            return result
    def view(self):
        state = self.snapshot()
        teams = []
        for index, team in enumerate(state['teams']):
            teams.append(dict(name=state['settings']['teamA' if index == 0 else 'teamB'], score=team['score'],
                              timeouts=team['timeoutsUsed'], color=((40, 200, 255), (255, 155, 40))[index],
                              penalties=[(p['number'], p['remainingSeconds']) for p in team['penalties']]))
        return dict(teams=teams, period=state['period'], elapsed_seconds=state['clockSeconds'],
                    timeout_seconds=state['timeoutRemaining'], revision=state['revision'],
                    status='RUNNING' if state['running'] else ('PERIOD_COMPLETE' if state['elapsed'] >= state['settings']['periodSeconds'] else 'PAUSED'))
    def pause(self):
        with self.lock:
            self.advance()
            if self.data['running'] or self.data['timeout']:
                self.data['running'] = False; self.data['timeout'] = None
                self.data['revision'] += 1; self.save()
    def command(self, value):
        with self.lock:
            self.advance()
            if type(value.get('revision')) is not int or value['revision'] != self.data['revision']:
                raise Conflict('Controls changed on another page. Review the current score and try again.')
            previous = copy.deepcopy(self.data)
            try:
                action = value.get('action')
                if action == 'setup':
                    if self.data['running'] or self.data['timeout']: raise ValueError('Pause the clock and end the timeout before changing setup')
                    config = settings(value.get('settings'))
                    if config['periods'] < self.data['period'] or config['periodSeconds'] < self.data['elapsed']:
                        raise ValueError('New clock settings cannot precede the current match; reset first')
                    if any(t['timeoutsUsed'] > config['timeoutLimit'] for t in self.data['teams']):
                        raise ValueError('Timeout allowance cannot be below the number already used')
                    self.data['settings'] = config
                elif action == 'reset':
                    if self.data['running'] or self.data['timeout']: raise ValueError('Pause the clock and end the timeout before resetting')
                    self.data = self.fresh(self.data['settings'], self.data['revision'])
                elif action == 'start':
                    if self.data['timeout']: raise ValueError('End the timeout before starting the clock')
                    if self.data['elapsed'] >= self.data['settings']['periodSeconds']: raise ValueError('This period has ended; select the next period')
                    self.data['running'] = True
                elif action == 'pause': self.data['running'] = False
                elif action == 'nextPeriod':
                    if self.data['running'] or self.data['timeout']: raise ValueError('Pause the clock and end the timeout first')
                    if self.data['period'] >= self.data['settings']['periods']: raise ValueError('This is the final configured period')
                    self.data['period'] += 1; self.data['elapsed'] = 0.
                elif action == 'setClock':
                    if self.data['running'] or self.data['timeout']: raise ValueError('Pause the clock and end the timeout first')
                    seconds = integer(value.get('seconds'), 0, self.data['settings']['periodSeconds'], 'Display seconds')
                    self.data['elapsed'] = (seconds if self.data['settings']['clockDirection'] == 'up'
                                            else self.data['settings']['periodSeconds'] - seconds)
                elif action == 'adjustClock':
                    if self.data['running'] or self.data['timeout']: raise ValueError('Pause the clock and end the timeout first')
                    # Corrections change the displayed period clock, not already-served penalty time.
                    self.data['elapsed'] = integer(value.get('seconds'), 0, self.data['settings']['periodSeconds'], 'Elapsed seconds')
                elif action == 'endTimeout': self.data['timeout'] = None
                elif action in ('goal', 'penalty', 'removePenalty', 'timeout', 'restoreTimeout'):
                    team_index = integer(value.get('team'), 0, 1, 'Team')
                    team = self.data['teams'][team_index]
                    if action == 'goal':
                        delta = value.get('delta')
                        if type(delta) is not int or delta not in (-1, 1): raise ValueError('Goal change must be +1 or -1')
                        team['score'] = integer(team['score'] + delta, 0, 999, 'Score')
                    elif action == 'penalty':
                        number = integer(value.get('number'), 0, 999, 'Player number')
                        seconds = integer(value.get('seconds', self.data['settings']['penaltySeconds']), 1, 600, 'Penalty seconds')
                        team['penalties'] = [p for p in team['penalties'] if p['expires'] > self.data['effective']]
                        if len(team['penalties']) >= 20: raise ValueError('Too many active penalties')
                        team['penalties'].append(dict(id=str(uuid.uuid4()), number=number, expires=self.data['effective'] + seconds))
                    elif action == 'removePenalty':
                        if not any(p['id'] == value.get('id') for p in team['penalties']): raise ValueError('Penalty no longer exists')
                        team['penalties'] = [p for p in team['penalties'] if p['id'] != value['id']]
                    elif action == 'timeout':
                        if self.data['timeout']: raise ValueError('A timeout is already active')
                        if team['timeoutsUsed'] >= self.data['settings']['timeoutLimit']: raise ValueError('No timeouts remaining')
                        if self.data['elapsed'] >= self.data['settings']['periodSeconds']: raise ValueError('This period has ended')
                        self.data['running'] = False; team['timeoutsUsed'] += 1
                        self.data['timeout'] = dict(team=team_index, until=self.now() + self.data['settings']['timeoutSeconds'])
                    else:
                        team['timeoutsUsed'] = integer(team['timeoutsUsed'] - 1, 0, 10, 'Timeouts used')
                else: raise ValueError('Unknown manual action')
                self.data['revision'] += 1
                self.save()
            except Exception:
                self.data = previous
                raise
            return self.snapshot()
