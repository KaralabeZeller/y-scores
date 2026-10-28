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
    def test_shared_orientation_preserves_team_identity_and_legacy_default(self):
        original = project(self.snapshot)
        self.assertFalse(original['sides_swapped'])
        self.snapshot['state']['sidesSwapped'] = True
        swapped = project(self.snapshot)
        self.assertTrue(swapped['sides_swapped'])
        self.assertEqual(swapped['teams'], original['teams'])
        self.assertEqual(swapped['elapsed_seconds'], original['elapsed_seconds'])
        self.snapshot['state']['sidesSwapped'] = False
        self.assertEqual(project(self.snapshot), original)

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
    def test_home_side_uses_home_and_away_without_kit_uses_home(self):
        self.assertEqual(primary_colors(dict(teamA=dict(details=dict(home_color='#008000',visitor_color='#ffffff')),teamB=dict(details=dict(home_color='#f8181e')))),[(0,128,0),(248,24,30)])
    def test_away_side_uses_away_kit(self):
        metadata = dict(teamA=dict(details=dict(home_color='#112233',visitor_color='#ffffff')),
                        teamB=dict(details=dict(home_color='#445566',visitor_color='#aabbcc')))
        self.assertEqual(primary_colors(metadata), [(17,34,51),(170,187,204)])

    def test_match_overrides_take_precedence_including_black(self):
        metadata = dict(teamA=dict(details=dict(match_color='#000000',home_color='#112233')),
                        teamB=dict(details=dict(matchColor='#fF0011',visitorColor='#aabbcc',homeColor='#445566')))
        self.assertEqual(primary_colors(metadata), [(0,0,0),(255,0,17)])

    def test_invalid_or_reset_overrides_fall_back_to_valid_kit(self):
        metadata = dict(teamA=dict(details=dict(match_color='transparent',homeColor='#112233')),
                        teamB=dict(details=dict(match_color=None,visitor_color='bad',awayColor='#aabbcc',home_color='#445566')))
        self.assertEqual(primary_colors(metadata), [(17,34,51),(170,187,204)])

    def test_missing_or_invalid_colors_use_fallback(self):
        self.assertEqual(primary_colors(dict(teamA=None,teamB=dict(details=dict(home_color='bad')))),[(40,200,255),(255,155,40)])

if __name__=='__main__': unittest.main()
