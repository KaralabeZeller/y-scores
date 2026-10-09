import copy
import json
import unittest
from unittest.mock import Mock, patch
from live_scoreboard import Feed
import test_live_state

class FeedTests(unittest.TestCase):
    def setUp(self):
        fixture=test_live_state.ProjectionTests(); fixture.setUp()
        self.snapshot=fixture.snapshot
        self.match='7ccdc25d-c50e-49a1-be46-cba1cf9d482d'
        self.snapshot['matchId']=self.match
        self.feed=Feed('https://example.invalid/api-next',self.match)
        self.feed.connection=Mock()
    def response(self,payload):
        response=Mock(status=200)
        response.read.return_value=json.dumps(payload).encode()
        return response
    def test_device_match_switch_uses_the_new_matches_primary_colors(self):
        next_match='99d418ef-ef06-4123-b901-3c7e28c5b4c3'
        palettes={self.match:('#008000','#f8181e'),next_match:('#1234ab','#ffaa00')}
        def request(method,path):
            match_id=path.split('/')[2]
            if path.endswith('/snapshot'):
                snapshot=copy.deepcopy(self.snapshot)
                snapshot['matchId']=match_id
                return snapshot
            a,b=palettes[match_id]
            return dict(teamA=dict(details=dict(home_color=a)),teamB=dict(details=dict(home_color=b)))
        request_mock=Mock(side_effect=request)
        for match_id,expected in [(self.match,[(0,128,0),(248,24,30)]),
                                  (next_match,[(18,52,171),(255,170,0)])]:
            with self.subTest(match=match_id):
                feed=Feed('https://example.invalid/api-next',match_id,device_request=request_mock)
                self.addCleanup(feed.connection.close)
                feed.fetch_colors();feed.fetch()
                self.assertEqual([team['color'] for team in feed.view()['teams']],expected)
                request_mock.assert_any_call('GET',f'/matches/{match_id}')
    def test_new_match_without_colors_does_not_reuse_previous_palette(self):
        self.feed.device_request=Mock(return_value=dict(teamA=dict(details=dict(home_color='#008000'))))
        self.feed.fetch_colors()
        replacement=Feed('https://example.invalid/api-next','99d418ef-ef06-4123-b901-3c7e28c5b4c3',device_request=Mock(return_value=dict(teamA=None,teamB=dict(details=None))))
        self.addCleanup(replacement.connection.close)
        replacement.fetch_colors()
        self.assertEqual(replacement.colors,[(40,200,255),(255,155,40)])
    def test_pause_replaces_extrapolated_clock_immediately(self):
        paused=copy.deepcopy(self.snapshot)
        paused['revision']=3
        paused['state'].update(status='PAUSED',clockRunning=False,periodElapsedAnchorMs=71500,effectiveElapsedAnchorMs=1871500)
        self.feed.connection.getresponse.side_effect=[self.response(self.snapshot),self.response(paused)]
        with patch('live_scoreboard.time.monotonic',return_value=100): self.feed.fetch()
        with patch('live_scoreboard.time.monotonic',return_value=103):
            self.assertEqual(self.feed.view()['elapsed_seconds'],73)
            self.feed.fetch()
        with patch('live_scoreboard.time.monotonic',return_value=108):
            view=self.feed.view()
            self.assertEqual(view['status'],'PAUSED')
            self.assertEqual(view['elapsed_seconds'],71)
            self.assertEqual(view['teams'][0]['penalties'],[(6,108)])
        self.assertEqual(self.feed.connection.request.call_count,2)
        self.feed.connection.close.assert_not_called()
    def test_failed_connection_resets_transport_preserves_last_snapshot(self):
        self.feed.connection.getresponse.return_value=self.response(self.snapshot)
        self.feed.fetch()
        self.feed.connection.request.side_effect=ConnectionError('disconnected')
        with self.assertRaises(ConnectionError): self.feed.fetch()
        self.feed.connection.close.assert_called_once()
        self.assertEqual(self.feed.snapshot,self.snapshot)
    def test_old_revision_cannot_undo_pause(self):
        paused=copy.deepcopy(self.snapshot)
        paused['revision']=3
        paused['state'].update(status='PAUSED',clockRunning=False)
        self.feed.connection.getresponse.side_effect=[self.response(paused),self.response(self.snapshot)]
        self.feed.fetch(); self.feed.fetch()
        self.assertEqual(self.feed.view()['status'],'PAUSED')

    def test_frame_timeout_and_status_share_age_at_offline_boundary(self):
        self.snapshot['state'].update(status='PAUSED',clockRunning=False)
        self.feed.snapshot=self.snapshot
        self.feed.received=100
        self.feed.events_revision=self.snapshot['revision']
        self.feed.events=[dict(id='timeout',revision=2,type='TEAM_TIMEOUT_RECORDED',
                              period=1,effectiveElapsedMs=1860000,
                              recordedAt='2026-09-29T12:00:00Z')]
        with patch('live_scoreboard.time.monotonic',side_effect=[114.999,115.001]):
            view=self.feed.view()
        self.assertEqual(view['status'],'PAUSED')
        self.assertEqual(view['timeout_seconds'],35)
        with patch('live_scoreboard.time.monotonic',return_value=115.001):
            view=self.feed.view()
        self.assertEqual(view['status'],'OFFLINE')
        self.assertIsNone(view['timeout_seconds'])

    def test_score_and_penalty_correction_appear_in_same_frame(self):
        revised=copy.deepcopy(self.snapshot)
        revised['revision']=3
        revised['state']['scoreTeamA']=9
        revised['activeSuspensions']=[]
        self.feed.connection.getresponse.side_effect=[self.response(self.snapshot),self.response(revised)]
        with patch('live_scoreboard.time.monotonic',return_value=100):
            self.feed.fetch()
            before=self.feed.view()
            self.feed.fetch()
            after=self.feed.view()
        self.assertEqual((before['revision'],before['teams'][0]['score']),(2,2))
        self.assertTrue(before['teams'][0]['penalties'])
        self.assertEqual((after['revision'],after['teams'][0]['score']),(3,9))
        self.assertEqual(after['teams'][0]['penalties'],[])

if __name__=='__main__': unittest.main()
