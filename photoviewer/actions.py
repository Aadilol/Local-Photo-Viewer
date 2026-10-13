"""Previewable, collision-safe file operations with journalled undo.

No filesystem mutation is performed by plan(). A real run requires the GUI to
show a preview and collect confirmation. Existing destination files are NEVER
opened for writing. In-place changes are opt-in and logged before modification.
"""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable
from .models import PhotoRecord

HISTORY_ROOT = Path.home() / 'PhotoViewerHistory'


@dataclass(frozen=True)
class Settings:
    kind: str                        # fix or organize
    strategy: str                    # copy, move (organize), inplace (fix)
    destination: Path | None = None
    layout: str = 'folders'           # folders, rename, both (organize)
    write_exif: bool = False          # JPEG only, when DateTimeOriginal is missing


@dataclass
class PlanItem:
    source: Path
    target: Path
    taken: datetime
    date_source: str
    precision: str


def choose_unique(path: Path, reserved: set[str]) -> Path:
    candidate = path
    count = 1
    while str(candidate).casefold() in reserved or candidate.exists():
        candidate = path.with_name(f'{path.stem}_{count}{path.suffix}')
        count += 1
    reserved.add(str(candidate).casefold())
    return candidate


def plan(records: list[PhotoRecord], scan_root: Path, settings: Settings) -> list[PlanItem]:
    if settings.kind not in {'fix', 'organize'}:
        raise ValueError('Unknown action')
    if settings.kind == 'fix' and settings.strategy not in {'copy', 'inplace'}:
        raise ValueError('Fix dates must copy or edit in-place')
    if settings.kind == 'organize' and settings.strategy not in {'copy', 'move'}:
        raise ValueError('Organize must copy or move')
    if settings.kind == 'organize' and settings.layout not in {'folders', 'rename', 'both'}:
        raise ValueError('Unknown organize layout')
    root = scan_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Source folder is not a directory')
    if settings.strategy != 'inplace':
        if settings.destination is None:
            raise ValueError('Select a destination folder')
        output = settings.destination.resolve(strict=False)
        if output == root or output.is_relative_to(root):
            raise ValueError('Destination must be outside the scanned folder, to prevent loops')
        if root.is_relative_to(output):
            raise ValueError('Destination cannot be a parent of the scanned folder')
    else:
        output = None
    result = []
    reserved = set()
    for rec in records:
        src = rec.path.absolute()
        if not src.is_relative_to(root):
            raise ValueError(f'File escaped scan root: {src}')
        if src.is_symlink():
            raise ValueError(f'Symlink not supported: {src}')
        if settings.strategy == 'inplace':
            dest = src
        elif settings.kind == 'fix':
            dest = choose_unique(output / src.relative_to(root), reserved)
        else:
            folder = output
            if settings.layout in ('folders', 'both'):
                folder = output / f'{rec.taken.year:04d}' / f'{rec.taken.month:02d}'
            if settings.layout in ('rename', 'both'):
                filename = rec.taken.strftime('%Y-%m-%d_%H%M%S') + src.suffix.lower()
            else:
                filename = src.name
            dest = choose_unique(folder / filename, reserved)
        result.append(PlanItem(src, dest, rec.taken, rec.source, rec.precision))
    return result


def windows_creation_time(path: Path) -> float | None:
    if os.name != 'nt':
        return None
    return path.stat().st_ctime


def set_windows_creation_time(path: Path, unix_secs: float) -> None:
    if os.name != 'nt':
        return
    from ctypes import wintypes
    FILE_WRITE_ATTRIBUTES = 0x0100
    FILE_SHARE_READ_WRITE_DELETE = 0x1 | 0x2 | 0x4
    OPEN_EXISTING = 3
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetFileTime.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    kernel.SetFileTime.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    h = kernel.CreateFileW(str(path), FILE_WRITE_ATTRIBUTES, FILE_SHARE_READ_WRITE_DELETE,
                           None, OPEN_EXISTING, 0, None)
    if h == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        ft = int((unix_secs + 11644473600) * 10_000_000)
        # FILETIME is a pair of DWORD values.
        raw = wintypes.FILETIME(ft & 0xffffffff, (ft >> 32) & 0xffffffff)
        if not kernel.SetFileTime(h, ctypes.byref(raw), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(h)


def saved_stat(path: Path) -> dict:
    st = path.stat()
    return {'atime_ns': st.st_atime_ns, 'mtime_ns': st.st_mtime_ns,
            'ctime': windows_creation_time(path), 'size': st.st_size}


def restore_timestamps(path: Path, prev: dict) -> None:
    os.utime(path, ns=(int(prev['atime_ns']), int(prev['mtime_ns'])))
    if prev.get('ctime') is not None:
        set_windows_creation_time(path, float(prev['ctime']))


def set_capture_timestamps(path: Path, taken: datetime) -> None:
    # Naive EXIF dates are interpreted in the user's local timezone.
    ts = taken.timestamp()
    existing_atime = path.stat().st_atime_ns
    os.utime(path, ns=(existing_atime, int(ts * 1_000_000_000)))
    set_windows_creation_time(path, ts)


def exif_missing(path: Path) -> bool:
    if path.suffix.lower() not in {'.jpg', '.jpeg'}:
        return False
    import piexif
    exif = piexif.load(str(path))
    return not bool(exif.get('Exif', {}).get(piexif.ExifIFD.DateTimeOriginal))


def add_exif_if_missing(path: Path, taken: datetime) -> str:
    if path.suffix.lower() not in {'.jpg', '.jpeg'}:
        return 'EXIF writing supported for JPG/JPEG only; skipped'
    import piexif
    values = piexif.load(str(path))
    if values['Exif'].get(piexif.ExifIFD.DateTimeOriginal):
        return 'Existing EXIF DateTimeOriginal retained'
    values['Exif'][piexif.ExifIFD.DateTimeOriginal] = taken.strftime('%Y:%m:%d %H:%M:%S').encode('ascii')
    piexif.insert(piexif.dump(values), str(path))
    return 'DateTimeOriginal written'


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        while block := fh.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def copy_exclusive(src: Path, dst: Path) -> None:
    """Reserve destination with O_EXCL; copy attributes and bytes; never overwrite."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(dst, flags, 0o666)
    try:
        with os.fdopen(fd, 'wb') as out, src.open('rb') as inp:
            shutil.copyfileobj(inp, out, 1024 * 1024)
        shutil.copystat(src, dst)
    except BaseException:
        dst.unlink(missing_ok=True)
        raise


def journal_write(file, entry: dict) -> None:
    file.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')
    file.flush()
    os.fsync(file.fileno())


def execute(items: list[PlanItem], settings: Settings, cancel: Event,
            progress: Callable[[int, int], None] | None = None,
            report: Callable[[str], None] | None = None) -> tuple[Path, int, int]:
    """Do the confirmed operation. Returns (journal, successes, failures)."""
    job_dir = HISTORY_ROOT / (datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
    job_dir.mkdir(parents=True, exist_ok=False)
    journal = job_dir / 'photo_viewer_journal.jsonl'
    (job_dir / 'photo_viewer_job.json').write_text(json.dumps({
        'version': 1, 'created': datetime.now().isoformat(),
        'kind': settings.kind, 'strategy': settings.strategy,
        'layout': settings.layout, 'write_exif': settings.write_exif,
        'destination': str(settings.destination) if settings.destination else None,
        'total': len(items),
    }, indent=2), encoding='utf8')
    completed = failed = 0
    with journal.open('a', encoding='utf-8') as log:
        for index, item in enumerate(items, 1):
            if cancel.is_set():
                break
            source, target = item.source, item.target
            prev = None
            backup = None
            done = False
            reserved = False
            dest_fd = None
            source_removed = False
            entry_id = uuid.uuid4().hex
            try:
                if not source.is_file() or source.is_symlink():
                    raise OSError('Source missing or symlink')
                prev = saved_stat(source)
                # A late-arriving collision is resolved, even after preview.
                if settings.strategy != 'inplace':
                    desired = target
                    attempt = 0
                    while True:
                        try:
                            # Exclusive creation means no existing file is replaced.
                            target.parent.mkdir(parents=True, exist_ok=True)
                            dest_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
                            reserved = True
                            break
                        except FileExistsError:
                            attempt += 1
                            target = desired.with_name(f'{desired.stem}_{attempt}{desired.suffix}')
                if settings.strategy == 'inplace' and settings.write_exif and source.suffix.lower() in {'.jpg', '.jpeg'}:
                    try:
                        if exif_missing(source):
                            backup = job_dir / (entry_id + '.original')
                            # A full backup is necessary for a byte-exact EXIF undo.
                            shutil.copy2(source, backup)
                    except (ValueError, OSError, ImportError) as exc:
                        if report:
                            report(f'EXIF preparation skipped for {source.name}: {exc}')
                journal_write(log, {'event': 'begin', 'id': entry_id,
                                    'source': str(source), 'target': str(target),
                                    'strategy': settings.strategy, 'kind': settings.kind,
                                    'before': prev, 'backup': str(backup) if backup else None})
                note = ''
                if settings.strategy != 'inplace':
                    # Write through the O_EXCL descriptor itself: no window for
                    # accidentally reopening a replaced path with truncation.
                    with os.fdopen(dest_fd, 'wb') as out:
                        dest_fd = None
                        with source.open('rb') as inp:
                            shutil.copyfileobj(inp, out, 1024 * 1024)
                    shutil.copystat(source, target)
                if settings.kind == 'fix':
                    if settings.write_exif:
                        try:
                            note = add_exif_if_missing(target, item.taken)
                        except (ValueError, OSError, ImportError) as exc:
                            note = f'EXIF skipped: {exc}'
                            if settings.strategy == 'inplace' and backup and backup.exists():
                                shutil.copy2(backup, source)
                    set_capture_timestamps(target, item.taken)
                elif settings.kind == 'organize':
                    # Keep folder-organized copies sortable in Windows Explorer too.
                    # The preview discloses this timestamp change on the destination.
                    set_capture_timestamps(target, item.taken)
                current = saved_stat(target)
                fingerprint = sha256_file(target) if (settings.strategy != 'inplace' or backup is not None) else None
                # Prepared entries are also recoverable after an unexpected crash.
                journal_write(log, {'event': 'prepared', 'id': entry_id,
                                    'after': current, 'sha256': fingerprint,
                                    'note': note})
                if settings.strategy == 'move':
                    # This happens only after data is fully copied AND journalled.
                    source.unlink()
                journal_write(log, {'event': 'done', 'id': entry_id,
                                    'after': current, 'sha256': fingerprint,
                                    'note': note})
                completed += 1
                done = True
            except Exception as exc:
                failed += 1
                if report:
                    report(f'FAILED: {source.name}: {exc}')
                journal_write(log, {'event': 'failed', 'id': entry_id, 'error': str(exc)})
                # If a move failed after unlinking its source, preserve the only
                # remaining copy and try to restore the source before cleanup.
                if settings.strategy == 'move' and not source.exists() and target.exists():
                    try:
                        copy_exclusive(target, source)
                        restore_timestamps(source, prev)
                    except OSError as restore_error:
                        if report:
                            report(f'URGENT: retained copy at {target}; restore manually: {restore_error}')
                # Best-effort rollback for the current item on failure.
                if settings.strategy == 'inplace' and prev and source.exists():
                    try:
                        if backup and backup.exists():
                            shutil.copy2(backup, source)
                        restore_timestamps(source, prev)
                    except OSError as restore_error:
                        if report:
                            report(f'URGENT: manual restoration may be needed for {source}; backup: {backup}; {restore_error}')
            finally:
                if dest_fd is not None:
                    try:
                        os.close(dest_fd)
                    except OSError:
                        pass
                if reserved and not done and not source_removed and source.exists() and target.exists():
                    # Only remove the file reserved by this job. An exception before
                    # source.unlink() leaves the original intact.
                    try:
                        target.unlink()
                    except OSError:
                        pass
                if progress:
                    progress(index, len(items))
    return journal, completed, failed


def _read_journal(journal: Path) -> tuple[list[dict], set[str]]:
    state = {}
    undone = set()
    with journal.open('r', encoding='utf8') as fh:
        for line in fh:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # possible torn final write; earlier lines remain usable
            identifier = event.get('id')
            if event.get('event') == 'begin':
                state[identifier] = {'begin': event}
            elif event.get('event') in ('prepared', 'done') and identifier in state:
                state[identifier]['done'] = event
            elif event.get('event') == 'failed' and identifier in state:
                state[identifier].pop('done', None)
            elif event.get('event') == 'undone':
                undone.add(identifier)
    return [v for v in state.values() if 'done' in v], undone


def undo(journal: Path, cancel: Event, progress: Callable[[int, int], None] | None = None,
         report: Callable[[str], None] | None = None) -> tuple[int, int]:
    """Undo completed entries in reverse order, without replacing user-changed files."""
    completed, already_undone = _read_journal(journal)
    pending = [entry for entry in reversed(completed)
               if entry['begin']['id'] not in already_undone]
    restored = skipped = 0
    with journal.open('a', encoding='utf8') as log:
        for idx, entry in enumerate(pending, 1):
            if cancel.is_set():
                break
            before = entry['begin']
            after = entry['done']
            src, dest = Path(before['source']), Path(before['target'])
            try:
                if not dest.is_file() or dest.is_symlink():
                    raise OSError('Target missing or symlink; cannot restore safely')
                cur = saved_stat(dest)
                if cur['size'] != after['after']['size'] or cur['mtime_ns'] != after['after']['mtime_ns']:
                    raise OSError('File changed since action; refusing to overwrite/delete it')
                if after.get('sha256') and sha256_file(dest) != after['sha256']:
                    raise OSError('File content changed; refusing to undo automatically')
                if before['strategy'] == 'inplace':
                    backup = Path(before['backup']) if before.get('backup') else None
                    if backup:
                        if not backup.is_file():
                            raise OSError('Required EXIF backup is missing')
                        shutil.copy2(backup, dest)
                    restore_timestamps(dest, before['before'])
                elif before['strategy'] == 'move':
                    # A crash might leave both files after the prepared record.
                    # Only remove our destination if the original still matches.
                    if src.exists():
                        if not src.is_file() or src.is_symlink() or sha256_file(src) != after['sha256']:
                            raise OSError('Original path occupied by different data')
                    else:
                        copy_exclusive(dest, src)
                        restore_timestamps(src, before['before'])
                    dest.unlink()
                else:
                    dest.unlink()  # only our creation, verified unchanged
                journal_write(log, {'event': 'undone', 'id': before['id']})
                restored += 1
            except Exception as exc:
                skipped += 1
                if report:
                    report(f'UNDO SKIPPED: {dest}: {exc}')
            finally:
                if progress:
                    progress(idx, len(pending))
    return restored, skipped
