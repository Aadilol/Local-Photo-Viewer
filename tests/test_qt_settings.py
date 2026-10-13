"""Tests using the real QSettings backend; CI runs them with Qt offscreen."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import QSettings
    from photoviewer.preferences import SettingsStore, SOURCE_KEY, DESTINATION_KEY


@unittest.skipUnless(HAS_QT, 'PySide6 unavailable in this build environment')
class TestRealQtSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.photo = root / 'images'
        self.dest = root / 'output'
        self.photo.mkdir()
        self.dest.mkdir()
        self.new_path = str(root / 'new.ini')
        self.old_path = str(root / 'old.ini')

    def get_new(self):
        return QSettings(self.new_path, QSettings.Format.IniFormat)

    def test_real_qsettings_persistence(self):
        store = SettingsStore(self.get_new())
        store.select_source(self.photo)
        store.select_destination(self.dest)
        loaded = SettingsStore(self.get_new())
        self.assertEqual(loaded.source_folder, str(self.photo))
        self.assertEqual(loaded.destination_folder, str(self.dest))

    def test_real_migration(self):
        old = QSettings(self.old_path, QSettings.Format.IniFormat)
        old.setValue('sourceFolder', str(self.photo))
        old.sync()
        store = SettingsStore(self.get_new(), [old])
        self.assertEqual(store.source_folder, str(self.photo))
        self.assertEqual(SettingsStore(self.get_new(), [old]).source_folder, str(self.photo))


if __name__ == '__main__':
    unittest.main()
