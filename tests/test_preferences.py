"""Settings policy regression tests, runnable even without PySide6 installed.

Use an in-memory QSettings double; UI integration tests use real QSettings.
"""
import tempfile
import unittest
from pathlib import Path

from photoviewer.preferences import (
    SettingsStore, SOURCE_KEY, DESTINATION_KEY, GEOMETRY_KEY, MIGRATION_KEY,
    SORT_KEY, THEME_KEY, SIDEBAR_KEY, DRY_KEY, EXIF_KEY, accessible_folder,
)


class MemorySettings:
    def __init__(self, items=None):
        self.items = dict(items or {})
    def value(self, key, default=None):
        return self.items.get(key, default)
    def contains(self, key):
        return key in self.items
    def setValue(self, key, value):
        self.items[key] = value
    def clear(self):
        self.items.clear()
    def sync(self):
        pass


class TestFolderPreferences(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / 'photos'
        self.output = Path(self.tmp.name) / 'output'
        self.folder.mkdir()
        self.output.mkdir()

    def test_source_and_destination_save_load_separately(self):
        qs = MemorySettings()
        first = SettingsStore(qs)
        first.select_source(self.folder)
        first.select_destination(self.output)
        second = SettingsStore(qs)
        self.assertEqual(second.source_folder, str(self.folder))
        self.assertEqual(second.destination_folder, str(self.output))

    def test_open_rescan_and_repair_do_not_change_saved_paths(self):
        qs = MemorySettings()
        store = SettingsStore(qs)
        store.select_source(self.folder)
        store.select_destination(self.output)
        old = dict(qs.items)
        # Just reading, validating and accessing files MUST NOT update settings.
        for _ in range(3):
            self.assertTrue(accessible_folder(store.source_folder)[0])
            self.assertTrue(accessible_folder(store.destination_folder)[0])
            self.assertEqual(dict(qs.items), old)

    def test_missing_folder_preserved_and_not_replaced(self):
        qs = MemorySettings()
        store = SettingsStore(qs)
        store.select_source(self.folder)
        self.folder.rename(self.folder.with_name('elsewhere'))
        self.assertFalse(accessible_folder(store.source_folder)[0])
        self.assertIn('Choose a new one', accessible_folder(store.source_folder)[1])
        reopened = SettingsStore(qs)
        self.assertEqual(reopened.source_folder, str(self.folder))
        self.assertEqual(reopened.destination_folder, '')

    def test_save_all_preferences(self):
        qs = MemorySettings()
        store = SettingsStore(qs)
        store.set_flag(SORT_KEY, False)
        store.set_flag(SIDEBAR_KEY, False)
        store.set_flag(DRY_KEY, False)
        store.set_flag(EXIF_KEY, True)
        store.set_theme('dark')
        store.save_geometry(b'window geometry placeholder')
        loaded = SettingsStore(qs)
        self.assertFalse(loaded.bool_value(SORT_KEY, True))
        self.assertFalse(loaded.bool_value(SIDEBAR_KEY, True))
        self.assertFalse(loaded.bool_value(DRY_KEY, True))
        self.assertTrue(loaded.bool_value(EXIF_KEY, False))
        self.assertEqual(loaded.theme, 'dark')
        self.assertEqual(loaded.window_geometry(), b'window geometry placeholder')

    def test_migrate_legacy_keys_and_never_overwrite_new(self):
        old = MemorySettings({'source_folder': str(self.folder),
                              'destinationFolder': str(self.output),
                              'sort_order': False, 'theme': 'light',
                              'geometry': 'old geometry', 'write_exif': 'true'})
        new = MemorySettings({SOURCE_KEY: str(self.output)})
        store = SettingsStore(new, [old])
        self.assertEqual(store.source_folder, str(self.output))
        self.assertEqual(store.destination_folder, str(self.output))
        self.assertFalse(store.bool_value(SORT_KEY, True))
        self.assertTrue(store.bool_value(EXIF_KEY, False))
        self.assertEqual(store.theme, 'light')
        self.assertEqual(store.window_geometry(), 'old geometry')
        self.assertTrue(new.value(MIGRATION_KEY))
        self.assertEqual(old.value('source_folder'), str(self.folder))

    def test_reset_does_not_import_removed_folders_again(self):
        legacy = MemorySettings({'sourceFolder': str(self.folder)})
        new = MemorySettings()
        store = SettingsStore(new, [legacy])
        self.assertEqual(store.source_folder, str(self.folder))
        store.reset()
        self.assertEqual(SettingsStore(new, [legacy]).source_folder, '')
        self.assertEqual(SettingsStore(new, [legacy]).theme, 'system')
        self.assertTrue(new.value(MIGRATION_KEY))

    def test_folder_validation_never_selects_fallback(self):
        self.assertEqual(accessible_folder(''), (False, 'Choose a folder to get started.'))
        missing = Path(self.tmp.name) / 'disconnected-drive'
        self.assertFalse(accessible_folder(missing)[0])
        self.assertFalse(accessible_folder(self.folder / 'missing')[0])
        self.assertRaises(ValueError, SettingsStore(MemorySettings()).select_source, missing)


if __name__ == '__main__':
    unittest.main()
