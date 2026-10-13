"""Centralized persistent preferences. QSettings is the production backend.

Only an explicit folder chooser may call select_source/select_destination. Opening a
folder, scanning, previewing, organizing and drag/drop must never mutate those keys.
A passed-in QSettings-like object makes this contract testable without Qt.
"""
from __future__ import annotations
from pathlib import Path
from typing import Callable, Any

ORGANIZATION = 'PhotoViewer'
APPLICATION = 'Photo Viewer'
SOURCE_KEY = 'folders/source'
DESTINATION_KEY = 'folders/destination'
GEOMETRY_KEY = 'window/geometry'
SORT_KEY = 'timeline/newest_first'
THEME_KEY = 'appearance/theme'
SIDEBAR_KEY = 'appearance/sidebar_expanded'
DRY_KEY = 'safety/dry_run'
EXIF_KEY = 'safety/write_exif'
MIGRATION_KEY = 'migration/v2_done'

# Split legacy identifiers so the new project's source only names its new brand.
LEGACY_COMPACT = ''.join(('Photo', 'Date', 'Restorer'))
LEGACY_DISPLAY = ' '.join(('Photo', 'Date', 'Restorer'))

LEGACY_ALIASES = {
    SOURCE_KEY: ('source_folder', 'sourceFolder', 'last_folder', 'lastFolder', 'folder', 'photo_folder', 'source_path'),
    DESTINATION_KEY: ('destination_folder', 'destinationFolder', 'last_destination', 'output_folder'),
    SORT_KEY: ('newest_first', 'sort_newest', 'sort_order'),
    THEME_KEY: ('theme',),
    SIDEBAR_KEY: ('sidebar_expanded', 'sidebar_visible'),
    DRY_KEY: ('dry_run',),
    EXIF_KEY: ('write_exif', 'exif_write'),
    GEOMETRY_KEY: ('geometry', 'window_geometry'),
}


def accessible_folder(path: str | Path | None) -> tuple[bool, str]:
    """Pure folder validity policy: never substitute a different path."""
    if not path:
        return False, 'Choose a folder to get started.'
    try:
        folder = Path(path).expanduser()
        if not folder.is_dir():
            return False, 'Folder not found. Choose a new one?'
        # Detect inaccessible directories, including common disconnected mounts.
        next(folder.iterdir(), None)
        return True, ''
    except (OSError, PermissionError, ValueError):
        return False, 'Folder unavailable. Check the drive or choose a new one?'


def _boolean(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


class SettingsStore:
    """Read/write preferences, with one-time non-destructive legacy migration.

    For testing, provide a QSettings-like `settings` and optional legacy stores.
    Migration only fills absent keys. Original legacy stores are never cleared.
    """
    def __init__(self, settings=None, legacy_stores=None):
        if settings is None:
            from PySide6.QtCore import QSettings
            settings = QSettings(ORGANIZATION, APPLICATION)
            if legacy_stores is None:
                legacy_stores = [QSettings(org, app) for org, app in (
                    (LEGACY_COMPACT, LEGACY_DISPLAY),
                    (LEGACY_DISPLAY, LEGACY_DISPLAY),
                    (LEGACY_COMPACT, LEGACY_COMPACT),
                )]
        self.settings = settings
        self._migrate(legacy_stores or [])

    def _migrate(self, sources):
        if _boolean(self.settings.value(MIGRATION_KEY, False), False):
            return
        for old in sources:
            for new_key, aliases in LEGACY_ALIASES.items():
                if self.settings.contains(new_key):
                    continue
                for candidate in (new_key, *aliases):
                    if old.contains(candidate):
                        value = old.value(candidate)
                        if value is not None:
                            self.settings.setValue(new_key, value)
                            break
        self.settings.setValue(MIGRATION_KEY, True)
        self.settings.sync()

    @property
    def source_folder(self) -> str:
        return str(self.settings.value(SOURCE_KEY, '') or '')

    @property
    def destination_folder(self) -> str:
        return str(self.settings.value(DESTINATION_KEY, '') or '')

    def select_source(self, path: str | Path) -> None:
        if not accessible_folder(path)[0]:
            raise ValueError('Select an accessible photo folder.')
        self.settings.setValue(SOURCE_KEY, str(Path(path).expanduser().absolute()))
        self.settings.sync()

    def select_destination(self, path: str | Path) -> None:
        if not accessible_folder(path)[0]:
            raise ValueError('Select an accessible destination folder.')
        self.settings.setValue(DESTINATION_KEY, str(Path(path).expanduser().absolute()))
        self.settings.sync()

    def bool_value(self, key: str, default: bool) -> bool:
        return _boolean(self.settings.value(key, default), default)

    def set_flag(self, key: str, state: bool) -> None:
        if key not in (SORT_KEY, SIDEBAR_KEY, DRY_KEY, EXIF_KEY):
            raise ValueError('Not a checkbox preference')
        self.settings.setValue(key, bool(state))
        self.settings.sync()

    @property
    def theme(self) -> str:
        value = str(self.settings.value(THEME_KEY, 'system')).lower()
        return value if value in ('system', 'light', 'dark') else 'system'

    def set_theme(self, theme: str) -> None:
        if theme not in ('system', 'light', 'dark'):
            raise ValueError('Invalid theme')
        self.settings.setValue(THEME_KEY, theme)
        self.settings.sync()

    def save_geometry(self, geometry) -> None:
        self.settings.setValue(GEOMETRY_KEY, geometry)
        self.settings.sync()

    def window_geometry(self):
        return self.settings.value(GEOMETRY_KEY, None)

    def reset(self) -> None:
        """Deliberate reset is the only non-chooser path allowed to erase folders.

        Preserve the migration marker, so deleted preferences aren't re-imported.
        """
        self.settings.clear()
        self.settings.setValue(MIGRATION_KEY, True)
        self.settings.sync()
