from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.heic', '.webp'}
VIDEO_EXTS = {'.mp4', '.mov'}
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS


@dataclass
class PhotoRecord:
    path: Path
    taken: datetime  # Local wall-clock time, timezone unknown for EXIF and filenames
    source: str  # EXIF, JSON, Filename, File date, Manual
    detail: str
    unreliable: bool = False
    precision: str = 'second'  # 'date' if filename carries no time
    warning: str = ''

    @property
    def is_video(self) -> bool:
        return self.path.suffix.lower() in VIDEO_EXTS

    def display_date(self) -> str:
        return self.taken.strftime('%Y-%m-%d %H:%M:%S')
