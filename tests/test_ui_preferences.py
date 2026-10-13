"""Optional Qt GUI smoke tests; Windows CI runs with offscreen Qt rendering."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from photoviewer.preferences import SettingsStore
    from photoviewer.ui import MainWindow


@unittest.skipUnless(HAS_QT, 'PySide6 unavailable in this environment')
class TestWindowRestore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'source'
        self.folder.mkdir()
        self.path = str(self.root / 'preferences.ini')

    def store(self):
        return SettingsStore(QSettings(self.path, QSettings.Format.IniFormat))

    def test_existing_folder_autoscans_without_rewriting_setting(self):
        original = self.store()
        original.select_source(self.folder)
        with patch.object(MainWindow, 'start_scan') as trigger:
            window = MainWindow(self.store())
            window.show()
            self.app.processEvents()
            trigger.assert_called_once_with()
            self.assertEqual(window.root, self.folder)
            self.assertEqual(self.store().source_folder, str(self.folder))
            window.close()

    def test_missing_folder_inline_warning_with_saved_path_untouched(self):
        original = self.store()
        original.select_source(self.folder)
        self.folder.rmdir()
        with patch.object(MainWindow, 'start_scan') as trigger:
            window = MainWindow(self.store())
            window.show()
            self.app.processEvents()
            trigger.assert_not_called()
            self.assertTrue(window.notice.isVisible())
            self.assertIn('Folder not found', window.notice_label.text())
            self.assertEqual(self.store().source_folder, str(self.folder))
            window.close()

    def test_theme_setting_survives_restart(self):
        window = MainWindow(self.store())
        window.set_theme('dark')
        self.assertEqual(self.store().theme, 'dark')
        window.close()
        other = MainWindow(self.store())
        self.assertEqual(other.theme_choice, 'dark')
        other.close()


if __name__ == '__main__':
    unittest.main()
