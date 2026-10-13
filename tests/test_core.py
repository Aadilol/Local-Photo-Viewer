"""Run from project folder: python -m unittest discover -s tests -v"""
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from threading import Event

from PIL import Image
from photoviewer import actions
from photoviewer.date_resolvers import filename_date, resolve_date
from photoviewer.models import PhotoRecord
from photoviewer.scanner import scan


class TestDates(unittest.TestCase):
    def test_filename_patterns(self):
        expected = [
            ('IMG_20240102_030405.jpg', datetime(2024, 1, 2, 3, 4, 5), 'second'),
            ('PXL_20231231_235959123.heic', datetime(2023, 12, 31, 23, 59, 59), 'second'),
            ('2022-06-15 09.20.33.jpg', datetime(2022, 6, 15, 9, 20, 33), 'second'),
            ('Screenshot_20200811-101112.png', datetime(2020, 8, 11, 10, 11, 12), 'second'),
            ('IMG-20191110-WA0043.jpg', datetime(2019, 11, 10), 'date'),
            ('VID_20240708_090102.mp4', datetime(2024, 7, 8, 9, 1, 2), 'second'),
        ]
        for name, val, precision in expected:
            with self.subTest(name=name):
                dt, _detail, found_precision = filename_date(Path(name))
                self.assertEqual(dt, val)
                self.assertEqual(found_precision, precision)
        self.assertIsNone(filename_date(Path('IMG_20241365_255959.jpg'))[0])

    def test_source_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_path = root / 'IMG_20240102_030405.jpg'
            exif = Image.Exif()
            exif[36867] = '2020:04:05 06:07:08'
            Image.new('RGB', (4, 4), 'blue').save(image_path, exif=exif)
            image_path.with_name(image_path.name+'.json').write_text(json.dumps({
                'photoTakenTime': {'timestamp': '1600000000'}}))
            rec = resolve_date(image_path)
            self.assertEqual(rec.source, 'EXIF')
            self.assertEqual(rec.taken, datetime(2020, 4, 5, 6, 7, 8))
            # JSON takes priority over the filename when embedded date absent.
            other_path = root / 'IMG_20240102_030405.png'
            Image.new('RGB', (4, 4), 'red').save(other_path)
            other_path.with_suffix('.json').write_text(json.dumps({
                'photoTakenTime': {'timestamp': '1600000000'}}))
            rec2 = resolve_date(other_path)
            self.assertEqual(rec2.source, 'JSON')
            self.assertEqual(rec2.taken, datetime.fromtimestamp(1600000000).astimezone().replace(tzinfo=None))

    def test_filename_then_unreliable(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / 'Screenshot_20240910-112233.png'
            p2 = Path(tmp) / 'untitled_abc.png'
            for p in (p1, p2):
                Image.new('RGB', (4, 4)).save(p)
            self.assertEqual(resolve_date(p1).source, 'Filename')
            rec = resolve_date(p2)
            self.assertTrue(rec.unreliable)
            self.assertEqual(rec.source, 'File date')
            found = scan(Path(tmp), Event())
            self.assertEqual(len(found), 2)


class TestActions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'source'
        self.dest = Path(self.tmp.name) / 'destination'
        self.root.mkdir()
        self.dest.mkdir()
        self.old_history = actions.HISTORY_ROOT
        actions.HISTORY_ROOT = Path(self.tmp.name) / 'history'
        self.addCleanup(setattr, actions, 'HISTORY_ROOT', self.old_history)

    def record(self, name='IMG_20200101_120000.jpg'):
        file = self.root / name
        file.write_bytes(b'file content 123\x00\x01')
        return PhotoRecord(file, datetime(2020, 1, 1, 12), 'Filename', 'test')

    def test_copy_collision_and_undo(self):
        rec = self.record()
        already = self.dest / '2020' / '01'
        already.mkdir(parents=True)
        (already / rec.path.name).write_bytes(b'keep me')
        settings = actions.Settings('organize', 'copy', self.dest)
        planned = actions.plan([rec], self.root, settings)
        self.assertEqual(planned[0].target.name, 'IMG_20200101_120000_1.jpg')
        journal, count, failures = actions.execute(planned, settings, Event())
        self.assertEqual((count, failures), (1, 0))
        created = planned[0].target
        self.assertTrue(created.exists())
        self.assertEqual((already / rec.path.name).read_bytes(), b'keep me')
        self.assertTrue(rec.path.exists())
        self.assertEqual(actions.undo(journal, Event()), (1, 0))
        self.assertFalse(created.exists())
        self.assertTrue(rec.path.exists())
        self.assertEqual(actions.undo(journal, Event()), (0, 0))

    def test_rename_collisions(self):
        first = self.record('one.jpg')
        second = self.record('two.jpg')
        settings = actions.Settings('organize', 'copy', self.dest, layout='both')
        planned = actions.plan([first, second], self.root, settings)
        self.assertNotEqual(planned[0].target, planned[1].target)
        self.assertEqual(planned[1].target.name, '2020-01-01_120000_1.jpg')

    def test_move_and_restore(self):
        rec = self.record()
        settings = actions.Settings('organize', 'move', self.dest)
        planned = actions.plan([rec], self.root, settings)
        journal, completed, errors = actions.execute(planned, settings, Event())
        self.assertEqual((completed, errors), (1, 0))
        self.assertFalse(rec.path.exists())
        self.assertTrue(planned[0].target.exists())
        self.assertEqual(actions.undo(journal, Event()), (1, 0))
        self.assertTrue(rec.path.exists())
        self.assertFalse(planned[0].target.exists())

    def test_fix_inplace_undo_timestamp(self):
        rec = self.record()
        original = rec.path.stat().st_mtime_ns
        settings = actions.Settings('fix', 'inplace')
        planned = actions.plan([rec], self.root, settings)
        journal, succeeded, failures = actions.execute(planned, settings, Event())
        self.assertEqual((succeeded, failures), (1, 0))
        self.assertEqual(datetime.fromtimestamp(rec.path.stat().st_mtime).year, 2020)
        self.assertEqual(actions.undo(journal, Event()), (1, 0))
        self.assertEqual(rec.path.stat().st_mtime_ns, original)

    def test_undo_refuses_changed_content(self):
        rec = self.record()
        settings = actions.Settings('organize', 'copy', self.dest)
        item = actions.plan([rec], self.root, settings)[0]
        journal, succeeded, failures = actions.execute([item], settings, Event())
        self.assertEqual((succeeded, failures), (1, 0))
        item.target.write_bytes(b'edited by user')
        self.assertEqual(actions.undo(journal, Event()), (0, 1))
        self.assertEqual(item.target.read_bytes(), b'edited by user')

    def test_refuse_output_inside_source(self):
        rec = self.record()
        with self.assertRaises(ValueError):
            actions.plan([rec], self.root,
                actions.Settings('organize', 'copy', self.root / 'nested'))


if __name__ == '__main__':
    unittest.main()
