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

if __name__=='__main__': unittest.main()
