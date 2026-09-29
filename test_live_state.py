import copy
import unittest
from live_state import project, primary_colors

class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = dict(revision=2,serverTime='2026-09-29T12:00:10Z',
            configuration=dict(regularPeriodCount=2,regularPeriodDurationMs=1800000,overtimePeriodDurationMs=300000),
            participants=dict(teamA=dict(name='Green',players=[dict(id='p1',playerNumber=6)]),teamB=dict(name='Rojo',players=[])),
            state=dict(currentPeriod=1,status='RUNNING',clockRunning=True,clockAnchorAt='2026-09-29T12:00:00Z',
                       periodElapsedAnchorMs=60000,effectiveElapsedAnchorMs=1860000,
                       scoreTeamA=2,scoreTeamB=1,teamATimeoutsUsed=1,teamBTimeoutsUsed=0),
            activeSuspensions=[dict(teamSide='TEAM_A',participantId='p1',status='ACTIVE',expiresAtEffectiveElapsedMs=1980000)])
    def test_server_anchor_and_monotonic_age(self):
        view=project(self.snapshot,2)
        self.assertEqual(view['elapsed_seconds'],72)
        self.assertEqual(view['teams'][0]['penalties'],[(6,108)])
        self.assertEqual(view['teams'][0]['timeouts'],1)
    def test_paused_freezes_match_and_suspensions(self):
        self.snapshot['state'].update(status='PAUSED',clockRunning=False,clockAnchorAt=None)
        view=project(self.snapshot,10)
        self.assertEqual(view['elapsed_seconds'],60)
        self.assertEqual(view['teams'][0]['penalties'],[(6,120)])
    def test_fractional_penalty_seconds_match_control(self):
        self.snapshot['state'].update(status='PAUSED',clockRunning=False)
        for remaining, expected in [(71001,71),(71999,71),(71000,71),(999,0)]:
            with self.subTest(remaining=remaining):
                self.snapshot['activeSuspensions'][0]['expiresAtEffectiveElapsedMs']=1860000+remaining
                view=project(self.snapshot)
                self.assertEqual(view['teams'][0]['penalties'],[(6,expected)])
                self.assertEqual(view['elapsed_seconds'],60)
    def test_stale_freezes_at_fixed_cutoff(self):
        self.assertEqual(project(self.snapshot,16),project(self.snapshot,100))
        self.assertEqual(project(self.snapshot,100)['status'],'OFFLINE')
    def test_period_end_caps_effective_clock(self):
        self.snapshot['state']['periodElapsedAnchorMs']=1795000
        view=project(self.snapshot,10)
        self.assertEqual(view['elapsed_seconds'],1800)
        self.assertEqual(view['teams'][0]['penalties'],[(6,115)])
    def test_expired_and_inactive_penalties_hidden(self):
        self.snapshot['activeSuspensions'][0]['expiresAtEffectiveElapsedMs']=1865000
        self.assertEqual(project(self.snapshot)['teams'][0]['penalties'],[])
    def test_corrected_snapshot_replaces_score_and_penalty(self):
        revised=copy.deepcopy(self.snapshot)
        revised['state']['scoreTeamA']=1
        revised['activeSuspensions']=[]
        self.assertEqual(project(revised)['teams'][0]['score'],1)
        self.assertEqual(project(revised)['teams'][0]['penalties'],[])
    def test_overtime_duration(self):
        self.snapshot['state'].update(currentPeriod=3,periodElapsedAnchorMs=299000)
        self.assertEqual(project(self.snapshot,2)['elapsed_seconds'],300)
class ColorTests(unittest.TestCase):
    def test_primary_color_not_visitor_color(self):
        self.assertEqual(primary_colors(dict(teamA=dict(details=dict(home_color='#008000',visitor_color='#ffffff')),teamB=dict(details=dict(home_color='#f8181e')))),[(0,128,0),(248,24,30)])
    def test_missing_or_invalid_colors_use_fallback(self):
        self.assertEqual(primary_colors(dict(teamA=None,teamB=dict(details=dict(home_color='bad')))),[(40,200,255),(255,155,40)])

if __name__=='__main__': unittest.main()
