"""Persistent diagnostics for windowed (no-console) Windows builds.

A fatal native crash doesn't reach Python's usual exception hook, so faulthandler
writes a best-effort stack trace to a separate append-only crash log.
"""
from __future__ import annotations

import faulthandler
import logging
import os
from pathlib import Path
import sys
import traceback
from datetime import datetime

_crash_file = None


def log_directory() -> Path:
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
        path = (Path(base) if base else Path.home() / 'AppData' / 'Local') / 'PhotoViewer'
    else:
        path = Path(os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local' / 'state')) / 'PhotoViewer'
    path.mkdir(parents=True, exist_ok=True)
    return path


def initialize_diagnostics() -> Path:
    global _crash_file
    folder = log_directory()
    log_file = folder / 'PhotoViewer.log'
    logging.basicConfig(
        filename=str(log_file), level=logging.INFO,
        format='%(asctime)s %(levelname)s [%(threadName)s] %(message)s',
        encoding='utf-8',
    )
    logging.info('Photo Viewer started: Python %s, frozen=%s', sys.version.split()[0], bool(getattr(sys, 'frozen', False)))
    try:
        _crash_file = (folder / 'PhotoViewer_crash.log').open('a', encoding='utf-8')
        _crash_file.write(f'\n--- Start {datetime.now().isoformat(timespec="seconds")} ---\n')
        _crash_file.flush()
        faulthandler.enable(file=_crash_file, all_threads=True)
    except (OSError, RuntimeError):
        logging.exception('Could not start native crash tracing')

    def exception_hook(kind, value, tb):
        logging.error('Uncaught GUI exception:\n%s', ''.join(traceback.format_exception(kind, value, tb)))
        # QMessageBox from an exception hook risks re-entering a broken Qt event loop;
        # never open modal error dialogs from a native crash/unknown thread.
    sys.excepthook = exception_hook
    return log_file
