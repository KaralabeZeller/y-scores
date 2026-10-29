import unittest
from unittest.mock import patch
import schedule_screen as screen

class ScheduleScreenTest(unittest.TestCase):
    def test_next_two_are_stable_and_completed_matches_are_excluded(self):
        matches=[dict(id=str(i),status='FINISHED' if i==0 else 'SCHEDULED',matchTime=f'2026-10-29T{10+i:02}:00:00Z',teamA={'name':f'Team {i}'},teamB={'name':'Visitor'},court={'name':'Court 1'},categoryName='Juvenil femenil') for i in range(5)]
        with patch('schedule_screen.text',wraps=screen.text) as text:
            image=screen.render(list(reversed(matches)),dict(timezone='Europe/Budapest',court_id=''))
        labels=[str(c.args[1]) for c in text.call_args_list]
        self.assertEqual(image.size,(192,64))
        self.assertIn('12:00',labels)
        self.assertIn('29 Oct',labels)
        self.assertEqual(labels.count('Juvenil femenil'),2)
        self.assertTrue(any('TEAM 1' in s for s in labels))
        self.assertFalse(any('TEAM 0' in s or 'TEAM 3' in s or 'TEAM 4' in s for s in labels))
        self.assertIn('COURT 1',labels)
    def test_empty_and_missing_metadata(self):
        config=dict(timezone='UTC',court_id='')
        self.assertEqual(screen.render([],config).size,(192,64))
        self.assertEqual(screen.render([dict(id='x')],config).size,(192,64))

    def test_scroll_pauses_at_both_ends_and_leaves_short_names_static(self):
        self.assertEqual(screen.scroll_offset(140,142,10),0)
        self.assertEqual(screen.scroll_offset(322,142,1),0)
        self.assertGreater(screen.scroll_offset(322,142,5),0)
        self.assertEqual(screen.scroll_offset(322,142,12),180)
        self.assertEqual(screen.scroll_offset(322,142,12.8),0)

    def test_header_alignment_and_long_names_preserve_fixed_time_and_category(self):
        match=dict(id='long',matchTime='2026-10-29T16:00:00Z',court={'name':'Cancha principal'},teamA={'name':'Universidad Autonoma'},teamB={'name':'Deportivo Ciudad Valles'},categoryName='Juvenil')
        config=dict(timezone='UTC',court_id='')
        with patch('schedule_screen.text',wraps=screen.text) as text:
            first=screen.render([match],config,elapsed=0)
        calls={str(c.args[1]):c for c in text.call_args_list}
        self.assertEqual(calls['29 Oct'].args[2],96-screen.text_width('29 Oct')//2)
        self.assertEqual(calls['CANCHA PRINCIPAL'].args[2]+screen.text_width('CANCHA PRINCIPAL'),190)
        self.assertIn('UNIVERSIDAD AUTONOMA / DEPORTIVO CIUDAD VALLES',calls)
        later=screen.render([match],config,elapsed=5)
        self.assertEqual(first.crop((0,0,48,64)).tobytes(),later.crop((0,0,48,64)).tobytes())
        self.assertEqual(first.crop((48,30,192,36)).tobytes(),later.crop((48,30,192,36)).tobytes())
        self.assertNotEqual(first.crop((48,14,190,27)).tobytes(),later.crop((48,14,190,27)).tobytes())
