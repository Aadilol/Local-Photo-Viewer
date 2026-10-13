"""Regression tests for removed files and reliable video-preview wiring."""
from __future__ import annotations

from datetime import datetime
import importlib.util
import os
from pathlib import Path
from threading import Event
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from photoviewer import date_resolvers
from photoviewer.models import PhotoRecord
from photoviewer.scanner import scan

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from photoviewer.preferences import SettingsStore
    from photoviewer.ui import MainWindow, PhotoLightbox, VIDEO_PLAYBACK_AVAILABLE


class TestRemovedFiles(unittest.TestCase):
    def test_removed_since_prior_scan_does_not_appear(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            files = [folder / f'IMG_2020010{i}_000000.jpg' for i in range(1, 4)]
            for path in files:
                Image.new('RGB', (10, 10)).save(path)
            self.assertEqual(len(scan(folder, Event())), 3)
            files[1].unlink()
            result = scan(folder, Event())
            self.assertEqual(len(result), 2)
            self.assertNotIn(files[1], [rec.path for rec in result])

    def test_removed_during_scan_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            photo = Path(td) / 'IMG_20240101_120000.jpg'
            Image.new('RGB', (10, 10)).save(photo)
            real_resolver = date_resolvers.resolve_date
            def remove_after_resolving(path):
                rec = real_resolver(path)
                path.unlink()
                return rec
            with patch('photoviewer.scanner.resolve_date', side_effect=remove_after_resolving):
                self.assertEqual(scan(Path(td), Event()), [])

    def test_video_date_falls_back_to_ffmpeg_metadata(self):
        def fake_tool(name):
            return None if name == 'ffprobe' else '/fake/ffmpeg'
        result = SimpleNamespace(stdout=';FFMETADATA1\ncreation_time=2022-06-05T11:12:13Z\n', returncode=0)
        with patch('photoviewer.date_resolvers.media_tool', side_effect=fake_tool), \
             patch('photoviewer.date_resolvers.subprocess.run', return_value=result):
            timestamp, description, warning = date_resolvers.video_date(Path('clip.mp4'))
        self.assertEqual((timestamp.year, timestamp.month, timestamp.day), (2022, 6, 5))
        self.assertIn('creation_time', description)
        self.assertEqual(warning, '')

    def test_bundled_ffmpeg_helper_is_used_when_no_system_ffmpeg(self):
        with patch('photoviewer.date_resolvers.shutil.which', return_value=None), \
             patch('photoviewer.date_resolvers.Path.is_file', return_value=True), \
             patch.dict('sys.modules', {'imageio_ffmpeg': type('Stub', (), {
                 'get_ffmpeg_exe': staticmethod(lambda: '/mock/ffmpeg.exe')})()}):
            date_resolvers.media_tool.cache_clear()
            try:
                self.assertEqual(date_resolvers.media_tool('ffmpeg'), '/mock/ffmpeg.exe')
            finally:
                date_resolvers.media_tool.cache_clear()


@unittest.skipUnless(HAS_QT, 'PySide6 unavailable in this environment')
class TestQtRescanAndVideo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.photos = [self.folder / f'IMG_2024020{i}_112233.jpg' for i in range(1, 5)]
        for path in self.photos:
            Image.new('RGB', (40, 40)).save(path)
        self.pref_path = str(self.folder / 'prefs.ini')

    def _process_until(self, condition, limit=7):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return True
            time.sleep(.02)
        return False

    def test_second_launch_after_deletions_uses_only_existing_files(self):
        settings = SettingsStore(QSettings(self.pref_path, QSettings.Format.IniFormat))
        settings.select_source(self.folder)
        first = MainWindow(settings)
        first.show()
        self.assertTrue(self._process_until(lambda: first.active_thread is None and len(first.records) == 4))
        first.close()
        self.photos[0].unlink()
        self.photos[-1].unlink()
        second = MainWindow(SettingsStore(QSettings(self.pref_path, QSettings.Format.IniFormat)))
        second.show()
        self.assertTrue(self._process_until(lambda: second.active_thread is None and len(second.records) == 2))
        self.assertEqual({r.path for r in second.records}, set(self.photos[1:3]))
        self.assertTrue(self._process_until(lambda: not second.timeline.pending))
        self.assertTrue(all(Path(k).exists() for k in second.timeline.cache))
        second.close()

    def _assert_can_remove_video(self, video: Path):
        """Qt's decoder thread may need a short time to release Win32 handles."""
        deadline = time.monotonic() + 3.0
        while True:
            try:
                video.unlink()
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    self.fail(f'Qt still holds an open video handle: {video}')
                self.app.processEvents()
                time.sleep(.05)

    def test_video_preview_uses_media_page_and_releases_file(self):
        video = self.folder / 'VID_20230101_120000.mp4'
        video.write_bytes(b'not a real video (codec failure should be friendly)')
        rec = PhotoRecord(video, datetime(2023, 1, 1, 12), 'Filename', 'test')
        dialog = PhotoLightbox(None, [rec], rec, None)
        self.assertEqual(dialog.pages.currentIndex(), 1)
        if VIDEO_PLAYBACK_AVAILABLE:
            self.assertIsNotNone(dialog.media_player)
            self.assertTrue(dialog.media_player.source().isEmpty(),
                            'A hidden preview must not open/lock its video')
        # Exercise the real window lifecycle. The constructor should NOT
        # open a media file until the preview is visible.
        dialog.show()
        self.app.processEvents()
        dialog.close()  # title-bar X, not QDialog.done()
        self.assertIsNone(dialog.media_player)
        # Verify the file can actually be deleted, not just that the UI hid.
        self._assert_can_remove_video(video)

    def test_preview_releases_media_when_accepted(self):
        video = self.folder / 'VID_20230101_120000.mp4'
        video.write_bytes(b'invalid media data')
        rec = PhotoRecord(video, datetime(2023, 1, 1, 12), 'Filename', 'test')
        dialog = PhotoLightbox(None, [rec], rec, None)
        dialog.show()
        self.app.processEvents()
        dialog.accept()
        self.assertIsNone(dialog.media_player)
        self._assert_can_remove_video(video)

    def test_unshown_video_preview_does_not_keep_file_open(self):
        video = self.folder / 'VID_20230101_120000.mp4'
        video.write_bytes(b'invalid media data')
        rec = PhotoRecord(video, datetime(2023, 1, 1, 12), 'Filename', 'test')
        dialog = PhotoLightbox(None, [rec], rec, None)
        dialog.close()
        self.assertIsNone(dialog.media_player)
        self._assert_can_remove_video(video)


if __name__ == '__main__':
    unittest.main()
