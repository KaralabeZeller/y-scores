import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
from device_identity import Identity


class IdentityTests(unittest.TestCase):
    def test_immutable_id_and_secret_survive_rename_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'identity.json'; identity=Identity(path)
            secret=identity.snapshot()['credential']; device_id=str(uuid4())
            identity.save('Board 1',device_id); identity.save('Board 2')
            with self.assertRaises(ValueError): identity.save('Board 3',str(uuid4()))
            restarted=Identity(path)
            self.assertEqual(restarted.public()['id'],device_id)
            self.assertEqual(restarted.snapshot()['credential'],secret)
            self.assertNotIn('credential',restarted.public())
            self.assertEqual(restarted.public()['metadataRevision'],2)
    def test_existing_settings_skip_first_time_pin_disclosure(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'identity.json'
            self.assertTrue(Identity(path,legacy=True).public()['setupComplete'])
            self.assertTrue(Identity(path).public()['setupComplete'])
    def test_failed_atomic_save_preserves_previous_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'identity.json'; identity=Identity(path); original=path.read_bytes()
            with patch('device_identity.os.replace',side_effect=OSError('disk failure')):
                with self.assertRaises(OSError): identity.save('Name')
            self.assertEqual(path.read_bytes(),original)
            self.assertFalse(identity.public()['locked'])
            self.assertFalse(list(Path(folder).glob('*.tmp')))
    def test_future_state_version_is_never_regenerated(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'identity.json'; path.write_text(json.dumps({'schemaVersion':2}))
            original=path.read_bytes()
            with self.assertRaises(ValueError): Identity(path)
            self.assertEqual(path.read_bytes(),original)


if __name__=='__main__': unittest.main()
