import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from live_scoreboard import Feed, Renderer
from live_state import project, timeout_details
from manual_match import ManualMatch, SETTINGS
import test_live_state


class DisplayLayoutTests(unittest.TestCase):
    def setUp(self):
        fixture=test_live_state.ProjectionTests();fixture.setUp()
        self.snapshot=fixture.snapshot
        self.snapshot['revision']=5
        self.snapshot['state'].update(status='PAUSED',clockRunning=False)
        self.event=dict(id='timeout',revision=5,type='TEAM_TIMEOUT_RECORDED',period=1,
                        effectiveElapsedMs=1860000,recordedAt='2026-09-29T12:00:00Z',teamSide='TEAM_A')
        self.view=project(self.snapshot)
        self.view.update(timeout_seconds=42,timeout_team=1)
        for team,color in zip(self.view['teams'],[(0,0,0),(40,125,255)]):team['color']=color
        self.renderer=Renderer(Path(__file__).parent/'fonts')

    def test_requester_and_countdown_follow_same_effective_event(self):
        self.assertEqual(timeout_details(self.snapshot,[self.event]),dict(seconds=50,team=0))
        self.snapshot['revision']=7
        replacement=self.event | dict(id='replacement',revision=7,teamSide='TEAM_B',recordedAt='2026-09-29T12:00:05Z')
        correction=dict(id='correction',revision=6,type='EVENT_REPLACED',targetEventId='timeout')
        self.assertEqual(timeout_details(self.snapshot,[self.event,correction,replacement]),dict(seconds=55,team=1))
        self.assertIsNone(timeout_details(self.snapshot,[self.event,correction]))

    def test_unknown_team_does_not_guess_requester(self):
        for side in (None,'UNKNOWN'):
            self.assertEqual(timeout_details(self.snapshot,[self.event | dict(teamSide=side)]),dict(seconds=50,team=None))
        self.view['timeout_team']=None
        frame=self.renderer.render(self.view)
        self.assertIn((255,255,0),[colour for _,colour in frame.crop((64,44,128,59)).getcolors()])

    def test_timeout_owner_is_published_only_with_complete_event_history(self):
        feed=Feed('https://example.invalid','7ccdc25d-c50e-49a1-be46-cba1cf9d482d')
        self.addCleanup(feed.connection.close)
        feed.snapshot=self.snapshot;feed.events=[self.event];feed.events_revision=5;feed.received=100
        with patch('live_scoreboard.time.monotonic',return_value=100):
            view=feed.view()
            self.assertEqual((view['timeout_team'],view['timeout_seconds']),(0,50))
            feed.events_revision=4
            view=feed.view()
            self.assertIsNone(view['timeout_team'])
            self.assertIsNone(view['timeout_seconds'])

    def test_plain_timeout_digits_use_requesting_team_colour(self):
        before=copy.deepcopy(self.view)
        frame=self.renderer.render(self.view)
        colours={colour for _,colour in frame.crop((64,44,128,59)).getcolors()}
        self.assertIn((40,125,255),colours)
        self.assertNotIn((235,235,235),colours)
        self.assertEqual(self.view,before)
        self.view['timeout_team']=0
        frame=self.renderer.render(self.view)
        self.assertIn((235,235,235),[colour for _,colour in frame.crop((64,44,128,59)).getcolors()])

    def test_offline_overrides_timeout_and_no_regular_period_text(self):
        self.view['status']='OFFLINE'
        with patch.object(self.renderer,'tile',wraps=self.renderer.tile) as tile:
            self.renderer.render(self.view)
        labels=[str(call.args[0]) for call in tile.call_args_list]
        self.assertIn('OFFLINE',labels)
        for absent in ('42','TO 42s','P1'):self.assertNotIn(absent,labels)

    def test_period_markers_fit_one_to_ten_periods(self):
        for count in range(1,11):
            with self.subTest(count=count):
                self.view.update(period_count=count,period=count)
                frame=self.renderer.render(self.view)
                blocks=[];last=(0,0,0)
                for x in range(64,128):
                    colour=frame.getpixel((x,4))
                    if colour!=(0,0,0) and last==(0,0,0):blocks.append(colour)
                    last=colour
                self.assertEqual(len(blocks),count)
                self.assertEqual(blocks[-1],(235,235,235))
                self.assertEqual(blocks.count((235,235,235)),1)
                self.assertEqual(frame.getpixel((64,4)),(0,0,0))
                self.assertEqual(frame.getpixel((127,4)),(0,0,0))

    def test_overtime_and_large_count_use_readable_labels(self):
        for period,count,label in [(3,2,'OT1'),(11,12,'P11')]:
            self.view.update(period=period,period_count=count)
            with patch.object(self.renderer,'tile',wraps=self.renderer.tile) as tile:self.renderer.render(self.view)
            self.assertIn(label,[str(call.args[0]) for call in tile.call_args_list])

    def test_live_clock_remains_period_relative(self):
        self.snapshot['configuration']['regularPeriodCount']=4
        self.snapshot['state'].update(currentPeriod=2,periodElapsedAnchorMs=0)
        view=project(self.snapshot)
        self.assertEqual((view['period_count'],view['period'],view['elapsed_seconds']),(4,2,0))

    def test_manual_configuration_and_owner_without_changing_clock_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            match=ManualMatch(Path(directory)/'manual.json',lambda:100.)
            def command(action,**extra):return match.command(dict(action=action,revision=match.snapshot()['revision'],**extra))
            command('setup',settings=SETTINGS | dict(periods=10))
            command('timeout',team=1)
            view=match.view()
            self.assertEqual((view['period_count'],view['timeout_team'],view['timeout_seconds']),(10,1,60))
            command('endTimeout')
            self.assertIsNone(match.view()['timeout_team'])
            command('nextPeriod')
            self.assertEqual(match.view()['elapsed_seconds'],0)
