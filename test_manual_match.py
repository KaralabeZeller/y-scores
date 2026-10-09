from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from manual_match import ManualMatch, Conflict, SETTINGS

class ManualTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'manual.json'
        self.time=100.; self.match=ManualMatch(self.path,lambda:self.time)
    def tearDown(self): self.tmp.cleanup()
    def command(self,action,**extra):
        return self.match.command(dict(action=action,revision=self.match.snapshot()['revision'],**extra))
    def test_goals_and_duplicate_revision(self):
        value=dict(action='goal',revision=0,team=0,delta=1)
        self.match.command(value)
        with self.assertRaises(Conflict): self.match.command(value)
        self.assertEqual(self.match.snapshot()['teams'][0]['score'],1)
        self.command('goal',team=0,delta=-1)
        with self.assertRaises(ValueError): self.command('goal',team=0,delta=-1)
        self.assertEqual(self.match.snapshot()['teams'][0]['score'],0)
    def test_timeout_uses_the_snapshot_instant_at_second_boundary(self):
        self.command('timeout',team=0)
        self.match.now=Mock(side_effect=[101.999,102.001])
        state=self.match.snapshot()
        self.assertEqual(state['timeoutRemaining'],59)
        self.assertFalse(state['running'])
    def test_clock_start_pause_and_period_end(self):
        self.command('setup',settings=SETTINGS|dict(periodSeconds=10))
        self.command('start'); self.time+=3.5
        self.assertEqual(self.match.snapshot()['clockSeconds'],3)
        self.command('pause'); self.time+=50
        self.assertEqual(self.match.snapshot()['elapsed'],3.5)
        self.command('start'); self.time+=20
        self.assertEqual(self.match.snapshot()['elapsed'],10)
        self.assertFalse(self.match.snapshot()['running'])
        with self.assertRaises(ValueError): self.command('start')
        self.command('nextPeriod'); self.assertEqual(self.match.snapshot()['period'],2)
        with self.assertRaises(ValueError): self.command('nextPeriod')
    def test_countdown_and_clock_adjustment(self):
        self.command('setup',settings=SETTINGS|dict(periodSeconds=20,clockDirection='down'))
        self.command('adjustClock',seconds=7)
        self.assertEqual(self.match.view()['elapsed_seconds'],13)
        self.command('start')
        with self.assertRaises(ValueError): self.command('adjustClock',seconds=3)
        self.time+=3; self.command('pause')
        self.assertEqual(self.match.view()['elapsed_seconds'],10)
    def test_set_display_time_counts_up_and_down_without_serving_penalties(self):
        self.command('penalty',team=0,number=8)
        for direction in ('up','down'):
            self.command('setup',settings=SETTINGS|dict(clockDirection=direction))
            self.command('setClock',seconds=754)
            self.assertEqual(self.match.snapshot()['clockSeconds'],754)
            self.assertEqual(self.match.view()['teams'][0]['penalties'],[(8,120 if direction=='up' else 117)])
            self.command('start'); self.time+=3; self.command('pause')
            self.assertEqual(self.match.snapshot()['clockSeconds'],757 if direction=='up' else 751)
            self.command('adjustClock',seconds=0)
        self.assertEqual(self.match.snapshot()['clockSeconds'],1800)
        self.assertEqual(self.match.snapshot()['teams'][0]['score'],0)
    def test_invalid_display_time_and_active_clock_changes_are_atomic(self):
        for seconds in (-1,1801,True,1.5):
            before=self.match.snapshot()
            with self.assertRaises(ValueError): self.command('setClock',seconds=seconds)
            self.assertEqual(self.match.snapshot(),before)
        self.command('start')
        with self.assertRaises(ValueError): self.command('setClock',seconds=10)
        self.command('pause'); self.command('timeout',team=0)
        with self.assertRaises(ValueError): self.command('setClock',seconds=10)
    def test_clock_reset_keeps_current_period_scores_penalties_and_timeouts(self):
        self.command('goal',team=0,delta=1)
        self.command('penalty',team=1,number=6)
        self.command('timeout',team=0); self.command('endTimeout')
        self.command('nextPeriod'); self.command('setClock',seconds=120)
        state=self.command('adjustClock',seconds=0)
        self.assertEqual(state['clockSeconds'],0)
        self.assertEqual(state['period'],2)
        self.assertEqual(state['teams'][0]['score'],1)
        self.assertEqual(state['teams'][0]['timeoutsUsed'],1)
        self.assertEqual(state['teams'][1]['penalties'][0]['number'],6)
    def test_player_penalties_only_advance_during_play_and_carry_between_periods(self):
        self.command('penalty',team=1,number=17,seconds=120)
        self.time+=50
        self.assertEqual(self.match.view()['teams'][1]['penalties'],[(17,120)])
        self.command('start'); self.time+=30; self.command('pause')
        self.assertEqual(self.match.view()['teams'][1]['penalties'],[(17,90)])
        self.command('nextPeriod'); self.command('start'); self.time+=91
        self.assertEqual(self.match.view()['teams'][1]['penalties'],[])
    def test_penalty_removal_and_clock_corrections_do_not_change_served_time(self):
        self.command('penalty',team=0,number=4)
        self.command('start'); self.time+=10; self.command('pause')
        self.command('adjustClock',seconds=500)
        self.assertEqual(self.match.view()['teams'][0]['penalties'],[(4,110)])
        identity=self.match.snapshot()['teams'][0]['penalties'][0]['id']
        self.command('removePenalty',team=0,id=identity)
        self.assertEqual(self.match.view()['teams'][0]['penalties'],[])
    def test_timeout_pauses_play_and_expiry_does_not_resume_clock(self):
        self.command('penalty',team=0,number=9); self.command('start'); self.time+=5
        self.command('timeout',team=0)
        self.assertEqual(self.match.snapshot()['teams'][0]['timeoutsRemaining'],2)
        with self.assertRaises(ValueError): self.command('start')
        self.time+=61
        value=self.match.snapshot()
        self.assertIsNone(value['timeout']); self.assertFalse(value['running'])
        self.assertEqual(value['elapsed'],5)
        self.assertEqual(self.match.view()['teams'][0]['penalties'],[(9,115)])
    def test_timeout_limits_and_undo_count(self):
        self.command('setup',settings=SETTINGS|dict(timeoutLimit=1))
        self.command('timeout',team=1)
        with self.assertRaises(ValueError): self.command('timeout',team=0)
        self.command('endTimeout')
        with self.assertRaises(ValueError): self.command('timeout',team=1)
        self.command('restoreTimeout',team=1)
        self.command('timeout',team=1)
        self.assertEqual(self.match.snapshot()['teams'][1]['timeoutsUsed'],1)
    def test_setup_preserves_score_and_reset_preserves_setup(self):
        self.command('goal',team=0,delta=1)
        self.command('setup',settings=SETTINGS|dict(teamA='Home',teamB='Visitors'))
        self.assertEqual(self.match.snapshot()['teams'][0]['score'],1)
        self.command('penalty',team=1,number=2); self.command('reset')
        state=self.match.snapshot()
        self.assertEqual(state['settings']['teamA'],'Home')
        self.assertEqual(state['teams'][0]['score'],0)
        self.assertEqual(state['teams'][1]['penalties'],[])
    def test_restart_preserves_checkpoint_scores_penalties_and_pauses(self):
        self.command('goal',team=0,delta=1); self.command('penalty',team=0,number=22)
        self.command('start'); self.time+=6; self.match.snapshot(); self.time+=2
        restored=ManualMatch(self.path,lambda:self.time)
        state=restored.snapshot()
        self.assertFalse(state['running']); self.assertEqual(state['elapsed'],6)
        self.assertEqual(state['teams'][0]['score'],1)
        self.assertEqual(restored.view()['teams'][0]['penalties'],[(22,114)])
    def test_leave_manual_pauses_and_clears_active_timeout(self):
        self.command('start'); self.time+=2; self.match.pause()
        self.time+=30; self.assertEqual(self.match.snapshot()['elapsed'],2)
        self.command('timeout',team=0); self.match.pause()
        self.assertIsNone(self.match.snapshot()['timeout'])
    def test_running_match_disallows_setup_reset_and_period_changes(self):
        self.command('start')
        for action,extra in [('setup',dict(settings=SETTINGS)),('reset',{}),('nextPeriod',{})]:
            with self.assertRaises(ValueError): self.command(action,**extra)
        self.assertTrue(self.match.snapshot()['running'])
    def test_validation_is_atomic_and_rejects_booleans_and_unsupported_sports(self):
        for action,extra in [('goal',dict(team=True,delta=1)),('penalty',dict(team=0,number=-1)),
                             ('setup',dict(settings=SETTINGS|dict(sport='football'))),
                             ('setup',dict(settings=SETTINGS|dict(teamA=''))),
                             ('setup',dict(settings=SETTINGS|dict(periodSeconds=True)))]:
            with self.assertRaises(ValueError): self.command(action,**extra)
        self.assertEqual(self.match.snapshot()['revision'],0)

if __name__=='__main__': unittest.main()
