"""Capture date extraction. Order: embedded metadata > Takeout JSON > filename > file date.

EXIF has no timezone in most photos; dates are stored and displayed as local wall time.
UTC/Z offsets from JSON and video metadata are converted to the machine's local zone.
"""
from __future__ import annotations
import json
import re
from functools import lru_cache
import shutil
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image

from .models import PhotoRecord, VIDEO_EXTS

try:
    from pillow_heif import register_heif_opener
    register_heif_opener(thumbnails=False)
except ImportError:
    pass  # Missing HEIC support is reported per file, not fatal.


EXIF_TAGS = ((36867, 'DateTimeOriginal'), (36868, 'DateTimeDigitized / CreateDate'))
DATE8 = r'(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})'
TIME6 = r'(?P<h>\d{2})(?P<mi>\d{2})(?P<s>\d{2})'
PATTERNS = (
    # Pixel / camera (allow suffix for milliseconds, e.g. PXL_20240102_030405123)
    (re.compile(r'(?<!\d)(?:IMG|PXL|VID|MVIMG)[_-]' + DATE8 + r'[_-]' + TIME6 + r'(?:\d{3})?(?!\d)', re.I), 'Camera filename', 'second'),
    (re.compile(r'(?<!\d)Screenshot[_ -]?' + DATE8 + r'[-_ ]' + TIME6 + r'(?!\d)', re.I), 'Screenshot filename', 'second'),
    (re.compile(r'(?<!\d)' + r'(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})[ _T]+' + r'(?P<h>\d{2})[.:-](?P<mi>\d{2})[.:-](?P<s>\d{2})(?!\d)', re.I), 'Dated filename', 'second'),
    (re.compile(r'(?<!\d)(?:IMG|VID)[-_]' + DATE8 + r'[-_]WA\d{3,}(?!\d)', re.I), 'WhatsApp filename (time unknown)', 'date'),
    (re.compile(r'(?<!\d)' + DATE8 + r'[-_]' + TIME6 + r'(?!\d)', re.I), 'Timestamp filename', 'second'),
)


def parse_datetime(value: object) -> Optional[datetime]:
    """Parse EXIF yyyy:mm:dd and ISO 8601 metadata; return naive local datetime."""
    if isinstance(value, bytes):
        value = value.decode('utf-8', 'replace')
    if not isinstance(value, str):
        return None
    value = value.strip().strip('\x00')
    if not value or value.startswith('0000:00:00'):
        return None
    try:
        # JPEG EXIF format
        match = re.match(r'^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})', value)
        if match:
            return datetime(*(int(s) for s in match.groups()))
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except (ValueError, OverflowError):
        return None


def embedded_date(path: Path) -> tuple[Optional[datetime], str, str]:
    """Embedded photo EXIF or video QuickTime/MP4 metadata."""
    if path.suffix.lower() in VIDEO_EXTS:
        return video_date(path)
    with Image.open(path) as img:
        exif = img.getexif()
        # Some HEIC files expose metadata as EXIF info blobs instead.
        if not exif and img.info.get('exif'):
            exif = Image.Exif()
            exif.load(img.info['exif'])
        # On JPEG the capture tags commonly live in the 0x8769 EXIF sub-IFD,
        # not in the top-level IFD returned by getexif().
        sub_ifd = exif.get_ifd(0x8769) if exif else {}
        for tag, label in EXIF_TAGS:
            parsed = parse_datetime(sub_ifd.get(tag)) or parse_datetime(exif.get(tag))
            if parsed:
                return parsed, f'EXIF {label}', ''
        # Fallback for JPEGs whose sub-IFD Pillow cannot decode.
        if path.suffix.lower() in {'.jpg', '.jpeg'}:
            try:
                import piexif
                raw_exif = piexif.load(str(path)).get('Exif', {})
                for tag, label in EXIF_TAGS:
                    parsed = parse_datetime(raw_exif.get(tag))
                    if parsed:
                        return parsed, f'EXIF {label}', ''
            except (ImportError, ValueError, OSError, KeyError):
                pass
        # Do not promote the EXIF DateTime (last image edit) to capture date.
    return None, '', ''


@lru_cache(maxsize=4)
def media_tool(name: str) -> str | None:
    """Find ffmpeg/ffprobe on PATH, beside the exe, or inside a frozen bundle."""
    from_path = shutil.which(name)
    if from_path:
        return from_path
    # imageio-ffmpeg ships its own Windows executable, including in PyInstaller
    # builds. This avoids asking users to install ffmpeg just for thumbnails.
    if name == 'ffmpeg':
        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            bundled = get_ffmpeg_exe()
            if Path(bundled).is_file():
                return bundled
        except (ImportError, OSError, RuntimeError):
            pass
    filename = name + ('.exe' if sys.platform == 'win32' else '')
    locations = [Path(sys.executable).parent, Path(__file__).resolve().parent.parent]
    if getattr(sys, '_MEIPASS', None):
        locations.append(Path(sys._MEIPASS))
    for location in locations:
        candidate = location / filename
        if candidate.is_file():
            return str(candidate)
    return None


def video_date(path: Path) -> tuple[Optional[datetime], str, str]:
    ffprobe = media_tool('ffprobe')
    if not ffprobe:
        # The bundled imageio-ffmpeg package contains ffmpeg, not ffprobe.
        # The ffmetadata muxer still exposes basic format-level creation tags.
        ffmpeg = media_tool('ffmpeg')
        if not ffmpeg:
            return None, '', 'No video metadata reader available'
        try:
            proc = subprocess.run(
                [ffmpeg, '-hide_banner', '-v', 'error', '-i', str(path),
                 '-map_metadata', '0', '-f', 'ffmetadata', 'pipe:1'],
                capture_output=True, text=True, timeout=15,
                **({'creationflags': subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, 'CREATE_NO_WINDOW') else {}),
            )
            for line in proc.stdout.splitlines():
                key, separator, value = line.partition('=')
                if separator and key.strip().lower() in ('creation_time', 'com.apple.quicktime.creationdate', 'date'):
                    parsed = parse_datetime(value.strip())
                    if parsed:
                        return parsed, f'Video metadata: {key.strip()}', ''
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return None, '', f'Video metadata skipped: {exc}'
        return None, '', 'No embedded video capture date found (ffprobe optional)'
    try:
        proc = subprocess.run(
            [ffprobe, '-v', 'error', '-show_entries', 'format_tags:stream_tags',
             '-of', 'json', str(path)],
            capture_output=True, text=True, timeout=15, check=True,
            **({'creationflags': subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, 'CREATE_NO_WINDOW') else {}),
        )
        metadata = json.loads(proc.stdout)
        tags = [metadata.get('format', {}).get('tags', {})]
        tags.extend(stream.get('tags', {}) for stream in metadata.get('streams', []))
        for key in ('com.apple.quicktime.creationdate', 'date', 'creation_time'):
            for tagset in tags:
                for k, value in tagset.items():
                    if k.lower() == key:
                        parsed = parse_datetime(value)
                        if parsed:
                            return parsed, f'Video metadata: {key}', ''
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return None, '', f'Video metadata skipped: {exc}'
    return None, '', ''


def json_date(path: Path) -> tuple[Optional[datetime], str]:
    candidates = [
        path.with_name(path.name + '.json'),
        path.with_suffix('.json'),
        path.with_name(path.name + '.supplemental-metadata.json'),
        path.with_name(path.name + '.suppl.json'),
    ]
    checked = set()
    for candidate in candidates:
        if candidate in checked or not candidate.is_file():
            continue
        checked.add(candidate)
        try:
            with candidate.open('r', encoding='utf-8-sig') as fh:
                data = json.load(fh)
            stamp = data.get('photoTakenTime', {}).get('timestamp')
            if stamp is None:
                continue
            taken = datetime.fromtimestamp(float(stamp)).astimezone().replace(tzinfo=None)
            return taken, f'Google Takeout: {candidate.name}'
        except (OSError, ValueError, TypeError, OverflowError, AttributeError):
            continue
    return None, ''


def filename_date(path: Path) -> tuple[Optional[datetime], str, str]:
    for pattern, detail, precision in PATTERNS:
        match = pattern.search(path.stem)
        if not match:
            continue
        try:
            values = match.groupdict()
            dt = datetime(int(values['y']), int(values['m']), int(values['d']),
                          int(values.get('h') or 0), int(values.get('mi') or 0),
                          int(values.get('s') or 0))
            return dt, detail, precision
        except ValueError:
            continue
    return None, '', ''


def resolve_date(path: Path) -> PhotoRecord:
    notes = []
    try:
        dt, detail, warning = embedded_date(path)
        if warning:
            notes.append(warning)
        if dt:
            return PhotoRecord(path, dt, 'Video' if path.suffix.lower() in VIDEO_EXTS else 'EXIF',
                               detail, warning='; '.join(notes))
    except (OSError, ValueError, SyntaxError, KeyError, RuntimeError) as exc:
        notes.append(f'Embedded metadata unreadable: {type(exc).__name__}: {exc}')
    dt, detail = json_date(path)
    if dt:
        return PhotoRecord(path, dt, 'JSON', detail, warning='; '.join(notes))
    dt, detail, precision = filename_date(path)
    if dt:
        return PhotoRecord(path, dt, 'Filename', detail, precision=precision,
                           warning='; '.join(notes))
    # ctime is not a creation time on macOS/Linux. Use earlier of Windows
    # creation and modified timestamp, or modification time alone elsewhere.
    st = path.stat()
    import os
    if os.name == 'nt' and st.st_ctime < st.st_mtime:
        ts, label = st.st_ctime, 'Windows file created date (unreliable)'
    else:
        ts, label = st.st_mtime, 'File modified date (unreliable)'
    dt = datetime.fromtimestamp(ts)
    return PhotoRecord(path, dt, 'File date', label, unreliable=True,
                       warning='; '.join(notes))
