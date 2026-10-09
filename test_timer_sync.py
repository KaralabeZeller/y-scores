import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from live_state import project
from live_scoreboard import Feed
from manual_match import ManualMatch, SETTINGS
import test_live_state


class LiveTimerSyncTests(unittest.TestCase):
    def setUp(self):
        fixture=test_live_state.ProjectionTests();fixture.setUp()
        self.snapshot=fixture.snapshot
        self.snapshot['serverTime']='2026-09-29T12:00:00Z'
        self.snapshot['state'].update(periodElapsedAnchorMs=10000,effectiveElapsedAnchorMs=50000)
        self.snapshot['participants']['teamB']['players']=[dict(id='p2',playerNumber=8)]
        self.snapshot['activeSuspensions']=[
            dict(teamSide='TEAM_A',participantId='p1',status='ACTIVE',expiresAtEffectiveElapsedMs=55050),
            dict(teamSide='TEAM_B',participantId='p2',status='ACTIVE',expiresAtEffectiveElapsedMs=55850)]

    def digits(self,age,snapshot=None):
        view=project(self.snapshot if snapshot is None else snapshot,age)
        return view['elapsed_seconds'],[p[1] for team in view['teams'] for p in team['penalties']]

    def test_fractional_deadlines_tick_only_with_match_clock(self):
        for age,expected in [(0.01,(10,[5,5])),(.1,(10,[5,5])),(.9,(10,[5,5])),
                             (.999,(10,[5,5])),(1,(11,[4,4])),(1.9,(11,[4,4])),(2,(12,[3,3]))]:
            with self.subTest(age=age):self.assertEqual(self.digits(age),expected)

    def test_exact_expiry_does_not_wait_for_next_display_tick(self):
        self.assertEqual(self.digits(5.049),(15,[0,0]))
        self.assertEqual(self.digits(5.05),(15,[0]))
        self.assertEqual(self.digits(5.849),(15,[0]))
        self.assertEqual(self.digits(5.85),(15,[]))

    def test_snapshot_refresh_cannot_create_an_extra_tick(self):
        refreshed=copy.deepcopy(self.snapshot)
        refreshed['serverTime']='2026-09-29T12:00:00.450Z'
        self.assertEqual(self.digits(.85),self.digits(.4,refreshed))
        self.assertEqual(self.digits(1.05),self.digits(.6,refreshed))

    def test_pause_freezes_digits_and_resume_uses_same_phase(self):
        paused=copy.deepcopy(self.snapshot)
        paused['state'].update(status='PAUSED',clockRunning=False,periodElapsedAnchorMs=10450,effectiveElapsedAnchorMs=50450)
        self.assertEqual(self.digits(.45),self.digits(0,paused))
        self.assertEqual(self.digits(0,paused),self.digits(12,paused))
        resumed=copy.deepcopy(paused)
        resumed['state'].update(status='RUNNING',clockRunning=True)
        self.assertEqual(self.digits(.54,resumed),(10,[5,5]))
        self.assertEqual(self.digits(.55,resumed),(11,[4,4]))

    def test_events_and_corrections_are_not_delayed_until_tick(self):
        revised=copy.deepcopy(self.snapshot)
        revised['revision']+=1
        revised['state']['scoreTeamA']=9
        revised['activeSuspensions'][0]['expiresAtEffectiveElapsedMs']=52050
        view=project(revised,.45)
        self.assertEqual(view['elapsed_seconds'],10)
        self.assertEqual(view['teams'][0]['score'],9)
        self.assertEqual(view['teams'][0]['penalties'],[(6,2)])
        self.assertEqual(view['revision'],revised['revision'])
        revised['activeSuspensions'].clear()
        self.assertEqual(project(revised,.45)['teams'][0]['penalties'],[])

    def test_display_does_not_mutate_deadlines_and_stale_view_still_freezes(self):
        self.snapshot['activeSuspensions'][0]['expiresAtEffectiveElapsedMs']=100050
        before=copy.deepcopy(self.snapshot)
        for age in (.1,.8,1.1,15,50):project(self.snapshot,age)
        self.assertEqual(self.snapshot,before)
        self.assertEqual(project(self.snapshot,15),project(self.snapshot,50))


class LiveFeedTimerSyncTests(unittest.TestCase):
    def setUp(self):
        fixture=test_live_state.ProjectionTests();fixture.setUp()
        self.snapshot=fixture.snapshot
        self.snapshot['matchId']='7ccdc25d-c50e-49a1-be46-cba1cf9d482d'
        self.snapshot['serverTime']='2026-09-29T12:00:00Z'
        self.snapshot['state'].update(periodElapsedAnchorMs=424200,effectiveElapsedAnchorMs=424200)
        self.snapshot['activeSuspensions'][0]['expiresAtEffectiveElapsedMs']=480000
        self.snapshot['participants']['teamB']['players']=[dict(id='p2',playerNumber=8)]
        self.snapshot['activeSuspensions'].append(dict(teamSide='TEAM_B',participantId='p2',
            status='ACTIVE',expiresAtEffectiveElapsedMs=485800))
        self.feed=Feed('https://example.invalid/api-next',self.snapshot['matchId'],device_request=Mock())
        self.addCleanup(self.feed.connection.close)

    def refresh(self,age=0):
        self.feed.device_request.return_value=copy.deepcopy(self.snapshot)
        with patch('live_scoreboard.time.monotonic',return_value=100):self.feed.fetch()
        with patch('live_scoreboard.time.monotonic',return_value=100+age):return self.feed.view()

    def digits(self,age=0):
        view=self.refresh(age)
        return view['elapsed_seconds'],[p[1] for team in view['teams'] for p in team['penalties']]

    def test_poll_anchor_jitter_cannot_change_digits_between_main_ticks(self):
        self.assertEqual(self.digits(),(424,[56,61]))
        for delta in (1,-1,2,0):
            self.snapshot['state']['effectiveElapsedAnchorMs']=424200+delta
            self.assertEqual(self.digits(.4),(424,[56,61]))
        self.assertEqual(self.digits(.81),(425,[55,60]))
        self.assertEqual(self.digits(.9),(425,[55,60]))

    def test_poll_time_regression_cannot_increase_penalties(self):
        self.assertEqual(self.digits(.81),(425,[55,60]))
        self.assertEqual(self.digits(.79),(424,[55,60]))
        self.assertEqual(self.digits(.81),(425,[55,60]))
        self.assertEqual(self.digits(1.81),(426,[54,59]))

    def test_score_event_does_not_rephase_unchanged_penalties(self):
        self.digits(.81)
        self.snapshot['revision']+=1
        self.snapshot['state']['scoreTeamA']=9
        view=self.refresh(.79)
        self.assertEqual(view['teams'][0]['score'],9)
        self.assertEqual(view['teams'][0]['penalties'],[(6,55)])

    def test_real_clock_correction_can_increase_penalty_immediately(self):
        self.digits()
        self.snapshot['revision']+=1
        self.snapshot['state']['effectiveElapsedAnchorMs']-=10000
        self.assertEqual(self.digits(),(424,[66,71]))

    def test_changed_new_and_removed_penalties_apply_within_same_tick(self):
        self.digits()
        self.snapshot['revision']+=1
        self.snapshot['activeSuspensions'][0]['expiresAtEffectiveElapsedMs']=490000
        new=copy.deepcopy(self.snapshot['activeSuspensions'][0])
        new.update(sourceEventId='new',expiresAtEffectiveElapsedMs=450000)
        self.snapshot['activeSuspensions'].append(new)
        self.assertEqual(self.refresh()['teams'][0]['penalties'],[(6,26),(6,66)])
        self.snapshot['revision']+=1
        self.snapshot['activeSuspensions']=[]
        self.assertEqual(self.refresh()['teams'][0]['penalties'],[])
        self.assertEqual(self.feed.penalty_display.values,{})

    def test_exact_expiry_cannot_flicker_back_after_poll_time_regression(self):
        self.snapshot['activeSuspensions'][0]['expiresAtEffectiveElapsedMs']=424500
        self.assertEqual(self.refresh(.299)['teams'][0]['penalties'],[(6,0)])
        self.assertEqual(self.refresh(.301)['teams'][0]['penalties'],[])
        self.assertEqual(self.refresh(.299)['teams'][0]['penalties'],[])
        self.snapshot['revision']+=1
        self.snapshot['state']['effectiveElapsedAnchorMs']-=10000
        self.assertEqual(self.refresh()['teams'][0]['penalties'],[(6,10)])


class ManualTimerSyncTests(unittest.TestCase):
    def test_both_clock_directions_keep_exact_deadlines_and_shared_ticks(self):
        for direction in ('up','down'):
            with self.subTest(direction=direction), tempfile.TemporaryDirectory() as directory:
                now=[100.]
                match=ManualMatch(Path(directory)/'manual.json',lambda:now[0])
                def command(action,**extra):
                    return match.command(dict(action=action,revision=match.snapshot()['revision'],**extra))
                command('setup',settings=SETTINGS | dict(periodSeconds=20,clockDirection=direction))
                command('start')
                now[0]=100.1;command('penalty',team=0,number=4,seconds=5)
                now[0]=100.8;command('penalty',team=1,number=8,seconds=5)
                expires=[team['penalties'][0]['expires'] for team in match.data['teams']]
                for instant,elapsed,remaining in [(100.8,0,5),(100.99,0,5),(101.,1,4),(101.9,1,4),(102.,2,3)]:
                    now[0]=instant;view=match.view()
                    self.assertEqual(view['elapsed_seconds'],elapsed if direction=='up' else 20-elapsed)
                    self.assertEqual([team['penalties'][0][1] for team in view['teams']],[remaining,remaining])
                now[0]=105.101;view=match.view()
                self.assertEqual(view['elapsed_seconds'],5 if direction=='up' else 15)
                self.assertEqual(view['teams'][0]['penalties'],[])
                self.assertEqual(view['teams'][1]['penalties'],[(8,0)])
                now[0]=105.801;self.assertEqual(match.view()['teams'][1]['penalties'],[])
                self.assertEqual([team['penalties'][0]['expires'] for team in match.data['teams']],expires)

    def test_pause_and_timeout_freeze_digits_without_changing_control_values(self):
        with tempfile.TemporaryDirectory() as directory:
            now=[100.]
            match=ManualMatch(Path(directory)/'manual.json',lambda:now[0])
            def command(action,**extra):return match.command(dict(action=action,revision=match.snapshot()['revision'],**extra))
            command('start')
            now[0]=100.8;command('penalty',team=0,number=8,seconds=5)
            now[0]=101.15
            self.assertEqual(match.view()['teams'][0]['penalties'],[(8,4)])
            self.assertEqual(match.snapshot()['teams'][0]['penalties'][0]['remainingSeconds'],5)
            command('timeout',team=1)
            before=match.view()
            now[0]=110.15;after=match.view()
            self.assertEqual(after['elapsed_seconds'],before['elapsed_seconds'])
            self.assertEqual(after['teams'][0]['penalties'],before['teams'][0]['penalties'])
            self.assertLess(after['timeout_seconds'],before['timeout_seconds'])
