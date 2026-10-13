"""Static packaging guards that can run even without Windows or PySide6."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TestWindowsBuildScripts(unittest.TestCase):
    def test_batch_files_use_windows_crlf(self):
        for filename in ('build_windows.bat', 'run_windows.bat'):
            with self.subTest(filename=filename):
                content = (ROOT / filename).read_bytes()
                self.assertIn(b'\r\n', content)
                self.assertEqual(content.count(b'\n'), content.count(b'\r\n'))
                self.assertNotIn(b'\x00', content)

    def test_build_has_persistent_diagnostics_and_expected_exe(self):
        script = (ROOT / 'build_windows.bat').read_text()
        self.assertIn('build_windows.log', script)
        self.assertIn('PhotoViewer.spec', script)
        self.assertIn('dist\\PhotoViewer.exe', script)
        self.assertIn('pause', script.lower())
        self.assertIn('unittest discover', script)

    def test_pyinstaller_spec_bundles_assets(self):
        spec = (ROOT / 'PhotoViewer.spec').read_text()
        self.assertIn("name='PhotoViewer'", spec)
        self.assertIn("('photoviewer/assets', 'photoviewer/assets')", spec)
        self.assertTrue((ROOT / 'photoviewer/assets/PhotoViewer.ico').is_file())


if __name__ == '__main__':
    unittest.main()
