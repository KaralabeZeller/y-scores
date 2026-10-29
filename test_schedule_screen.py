import unittest
from unittest.mock import patch
import schedule_screen as screen

class ScheduleScreenTest(unittest.TestCase):
    def test_next_three_are_stable_and_completed_matches_are_excluded(self):
        matches=[dict(id=str(i),status='FINISHED' if i==0 else 'SCHEDULED',matchTime=f'2026-10-29T{10+i:02}:00:00Z',teamA={'name':f'Team {i}'},teamB={'name':'Visitor'},court={'name':'Court 1'},categoryName='Juvenil femenil') for i in range(5)]
        with patch('schedule_screen.text',wraps=screen.text) as text:
            image=screen.render(list(reversed(matches)),dict(timezone='Europe/Budapest',court_id=''))
        labels=[str(c.args[1]) for c in text.call_args_list]
        self.assertEqual(image.size,(192,64))
        self.assertIn('12:00',labels)
        self.assertIn('29 Oct',labels)
        self.assertEqual(labels.count('Juvenil femenil'),3)
        self.assertTrue(any('TEAM 1' in s for s in labels))
        self.assertFalse(any('TEAM 0' in s or 'TEAM 4' in s for s in labels))
        self.assertIn('Court 1',labels)
    def test_empty_and_missing_metadata(self):
        config=dict(timezone='UTC',court_id='')
        self.assertEqual(screen.render([],config).size,(192,64))
        self.assertEqual(screen.render([dict(id='x')],config).size,(192,64))
