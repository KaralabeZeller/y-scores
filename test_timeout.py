import copy
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from live_state import timeout_seconds
from live_scoreboard import Feed, Renderer
import test_live_state

class TimeoutTests(unittest.TestCase):
    def setUp(self):
        fixture=test_live_state.ProjectionTests(); fixture.setUp()
        self.snapshot=fixture.snapshot
        self.snapshot['revision']=5
        self.snapshot['state'].update(status='PAUSED',clockRunning=False)
        self.event=dict(id='timeout',revision=5,type='TEAM_TIMEOUT_RECORDED',period=1,
                        effectiveElapsedMs=1860000,recordedAt='2026-09-29T12:00:00Z')

    def test_server_timestamp_and_floor_match_management(self):
        self.assertEqual(timeout_seconds(self.snapshot,[self.event],.2),49)
        self.assertEqual(timeout_seconds(self.snapshot,[self.event],14),36)

    def test_expired_timeout_stays_zero_until_resume(self):
        self.snapshot['serverTime']='2026-09-29T12:02:00Z'
        self.assertEqual(timeout_seconds(self.snapshot,[self.event]),0)
        self.snapshot['state']['clockRunning']=True
        self.assertIsNone(timeout_seconds(self.snapshot,[self.event]))

    def test_resume_then_pause_does_not_reopen_timeout(self):
        self.snapshot['revision']=7
        resume=dict(id='resume',revision=6,type='CLOCK_STARTED')
        self.assertIsNone(timeout_seconds(self.snapshot,[self.event,resume]))

    def test_void_future_wrong_period_and_offline(self):
        self.snapshot['revision']=6
        void=dict(id='void',revision=6,type='EVENT_VOIDED',targetEventId='timeout')
        self.assertIsNone(timeout_seconds(self.snapshot,[self.event,void]))
        self.assertIsNone(timeout_seconds(self.snapshot,[self.event],15))
        self.assertIsNone(timeout_seconds(self.snapshot,[]))
        event=copy.deepcopy(self.event);event['revision']=7
        self.assertIsNone(timeout_seconds(self.snapshot,[event]))
        event['revision']=5;event['period']=2
        self.assertIsNone(timeout_seconds(self.snapshot,[event]))

    def test_renderer_uses_countdown_instead_of_status(self):
        from live_state import project
        view=project(self.snapshot);view['timeout_seconds']=42
        for team in view['teams']:team['color']=(255,255,255)
        renderer=Renderer(Path(__file__).parent/'fonts')
        with patch.object(renderer,'tile',wraps=renderer.tile) as tile:
            renderer.render(view)
            labels=[str(call.args[0]) for call in tile.call_args_list]
            self.assertIn('TO 42s',labels)
            self.assertNotIn('PAUSED',labels)

class EventFetchTests(unittest.TestCase):
    def test_pagination_including_filtered_empty_page(self):
        feed=Feed('https://example.invalid','7ccdc25d-c50e-49a1-be46-cba1cf9d482d')
        feed.snapshot={'revision':3}
        pages=[{'toRevision':2,'events':[]},{'toRevision':3,'events':[{'id':'timeout'}]}]
        with patch('live_scoreboard.urlopen') as open_url, patch('live_scoreboard.json.load',side_effect=pages):
            feed.fetch_events()
        self.assertEqual(feed.events_revision,3)
        self.assertEqual(feed.events,[{'id':'timeout'}])
        self.assertEqual(open_url.call_count,2)

    def test_incomplete_event_history_does_not_display_timeout(self):
        fixture=test_live_state.ProjectionTests();fixture.setUp()
        feed=Feed('https://example.invalid','7ccdc25d-c50e-49a1-be46-cba1cf9d482d')
        feed.snapshot=fixture.snapshot
        self.assertIsNone(feed.view()['timeout_seconds'])
