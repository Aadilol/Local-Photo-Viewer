"""File enumeration and capture date resolution, no Qt/UI dependency."""
from __future__ import annotations
import os
from pathlib import Path
from threading import Event
from typing import Callable
from .date_resolvers import resolve_date
from .models import PhotoRecord, SUPPORTED_EXTS


def find_media(root: Path, cancel: Event, report: Callable[[str], None] | None = None) -> list[Path]:
    found = []
    def onerror(e: OSError) -> None:
        if report:
            report(f'Cannot open directory: {e}')
    for folder, _dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        if cancel.is_set():
            break
        for filename in files:
            path = Path(folder, filename)
            if path.suffix.lower() in SUPPORTED_EXTS and not path.is_symlink():
                found.append(path)
    return found


def scan(root: Path, cancel: Event, progress: Callable[[int, int], None] | None = None,
         warn: Callable[[str], None] | None = None) -> list[PhotoRecord]:
    files = find_media(root, cancel, warn)
    results = []
    for idx, path in enumerate(files, 1):
        if cancel.is_set():
            break
        try:
            record = resolve_date(path)
            # A picture can disappear between enumeration and metadata reading.
            # Never hand a known-deleted item to the thumbnail/viewer pipeline.
            if path.is_file():
                results.append(record)
        except (OSError, ValueError, OverflowError, TypeError) as exc:
            if warn:
                warn(f'Skipped {path}: {exc}')
        if progress and (idx % 10 == 0 or idx == len(files)):
            progress(idx, len(files))
    return results
