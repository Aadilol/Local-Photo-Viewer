"""Photo Viewer — adaptive, virtualized Qt timeline and safe operation workflow."""
from __future__ import annotations
import calendar
from bisect import bisect_left
import io
import os
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable
import subprocess
import logging
import sys

from PIL import Image, ImageOps
from PySide6.QtCore import (Property, QEasingCurve, QDate, QDateTime, QObject, QPoint,
    QRect, QRunnable, QSize, Qt, QThread, QThreadPool, QTime, QTimer, QUrl,
    QPropertyAnimation, QParallelAnimationGroup, QByteArray, Signal, Slot)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QImage,
    QPainter, QPainterPath, QPen, QPixmap, QShortcut, QKeySequence)
from PySide6.QtSvg import QSvgRenderer
from shiboken6 import delete as delete_qt_object
# QMediaPlayer uses Qt's native/FFmpeg multimedia backend; no separate player window.
try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    VIDEO_PLAYBACK_AVAILABLE = True
except ImportError:
    VIDEO_PLAYBACK_AVAILABLE = False
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QCheckBox,
    QComboBox, QDateTimeEdit, QDialog, QFileDialog, QFormLayout, QFrame,
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPushButton, QProgressBar, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QHeaderView, QSizePolicy,
    QStackedWidget, QSlider)

from . import actions
from .date_resolvers import media_tool
from .models import PhotoRecord
from .preferences import (SettingsStore, SOURCE_KEY, DESTINATION_KEY, SORT_KEY,
    THEME_KEY, SIDEBAR_KEY, DRY_KEY, EXIF_KEY, accessible_folder)
from .scanner import scan
from .theme import Palette, palette, qss, color, source_style, DARK

ASSETS = Path(__file__).resolve().parent / 'assets' / 'icons'


def icon(name: str, p: Palette, size: int = 20) -> QIcon:
    """Render bundled, token-colored SVGs; icons also work in frozen builds."""
    path = ASSETS / (name + '.svg')
    if not path.is_file():
        return QIcon()
    xml = path.read_text(encoding='utf8').replace('currentColor', p.text)
    renderer = QSvgRenderer(QByteArray(xml.encode('utf8')))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return QIcon(pm)


def set_button_role(b: QPushButton, role: str):
    b.setProperty('role', role)
    b.style().unpolish(b)
    b.style().polish(b)
    b.update()


def button(text: str, p: Palette, name: str | None = None, role: str = 'secondary') -> QPushButton:
    b = QPushButton(text)
    b.setProperty('role', role)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if name:
        b.setIcon(icon(name, p))
        b.setIconSize(QSize(18, 18))
    return b


class TaskWorker(QObject):
    progress = Signal(int, int)
    message = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, function: Callable, cancel: Event):
        super().__init__()
        self.function, self.cancel = function, cancel

    def run(self):
        try:
            self.done.emit(self.function(self.cancel, self.progress.emit, self.message.emit))
        except Exception as exc:
            self.failed.emit(f'{type(exc).__name__}: {exc}')


class ImageSignals(QObject):
    result = Signal(int, str, object)


def pil_to_qimage(im: Image.Image, max_dim: int = 400) -> QImage:
    img = ImageOps.exif_transpose(im)
    img.thumbnail((max_dim, max_dim))
    img = img.convert('RGB')
    return QImage(img.tobytes(), img.width, img.height,
                  img.width * 3, QImage.Format.Format_RGB888).copy()


class ImageJob(QRunnable):
    def __init__(self, path: Path, is_video: bool, max_dim: int = 400, request: str | None = None, generation: int = 0):
        super().__init__()
        self.path, self.is_video, self.max_dim = path, is_video, max_dim
        self.key = request or str(path)
        self.generation = generation
        self.signals = ImageSignals()

    def run(self):
        image = None
        try:
            if self.is_video:
                ffmpeg = media_tool('ffmpeg')
                if ffmpeg:
                    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, 'CREATE_NO_WINDOW') else {}
                    # Try a representative frame then the opening frame: a one-
                    # second seek falls past the end of very short videos.
                    for seek in ('00:00:01', '00:00:00'):
                        proc = subprocess.run([ffmpeg, '-v', 'error', '-ss', seek, '-i',
                            str(self.path), '-frames:v', '1', '-vf', f'scale={min(self.max_dim,1920)}:-2',
                            '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1'],
                            capture_output=True, timeout=20, **kwargs)
                        if proc.returncode == 0 and proc.stdout:
                            with Image.open(io.BytesIO(proc.stdout)) as img:
                                image = pil_to_qimage(img, self.max_dim)
                            break
            else:
                with Image.open(self.path) as img:
                    img.draft('RGB', (self.max_dim, self.max_dim))
                    image = pil_to_qimage(img, self.max_dim)
        except Exception:
            pass  # Invalid or unreadable image: show a safe placeholder.
        self.signals.result.emit(self.generation, self.key, image)


class ElidedPath(QLabel):
    """Middle-elided label with always-available original path tooltip."""
    def __init__(self):
        super().__init__()
        self.path = ''
        self.setObjectName('PathLabel')
        self.setMinimumWidth(90)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_path(self, path: str) -> None:
        self.path = path
        self.setToolTip(path)
        self._redraw()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._redraw()

    def _redraw(self):
        self.setText(self.fontMetrics().elidedText(self.path or 'No folder selected',
                    Qt.TextElideMode.ElideMiddle, max(20, self.width() - 6)))


class PhotoTimeline(QAbstractScrollArea):
    selected = Signal(object)
    open_requested = Signal(object)
    empty_choose_requested = Signal()
    GAP = 8
    MARGIN = 20
    CAPTION = 47

    def __init__(self, p: Palette, parent=None):
        super().__init__(parent)
        self.pal = p
        self.records: list[PhotoRecord] = []
        self.visible_records: list[PhotoRecord] = []
        self.tiles: list[tuple[QRect, PhotoRecord]] = []
        self.headers: list[tuple[QRect, int, int | None, str]] = []
        self.newest = True
        self.unreliable_only = False
        self.search_text = ''
        self.navigation: tuple[int, int | None] | None = None
        self.active_path: str | None = None
        self.hover_path: str | None = None
        self.cache: OrderedDict[str, QPixmap | None] = OrderedDict()
        self.pending: set[str] = set()
        self.scan_generation = 0
        # Keep a strong reference to every asynchronous signal object until its
        # worker finishes, even when the source library is rescanned meanwhile.
        self._thumbnail_signals: dict[str, ImageSignals] = {}
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(3)
        self._tile_bottoms: list[int] = []
        self._hover_alpha = 0.0
        self.hover_animation = QPropertyAnimation(self, b'hoverOpacity', self)
        self.hover_animation.setDuration(160)
        self.hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.verticalScrollBar().valueChanged.connect(self.viewport().update)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setMouseTracking(True)
        self.setMouseTracking(True)
        self.empty_button = button('Choose a folder', p, 'folder', 'primary')
        self.empty_button.setParent(self.viewport())
        self.empty_button.clicked.connect(self.empty_choose_requested)
        self.empty_button.hide()

    def get_hover_opacity(self):
        return self._hover_alpha

    def set_hover_opacity(self, val):
        self._hover_alpha = float(val)
        self.viewport().update()

    hoverOpacity = Property(float, get_hover_opacity, set_hover_opacity)

    def set_palette(self, p: Palette):
        self.pal = p
        self.empty_button.setIcon(icon('folder', p))
        self.viewport().update()

    def set_records(self, records: list[PhotoRecord], reset_cache: bool = True):
        # A library change invalidates all in-flight thumbnail results from old
        # scans. Jobs may still finish but must not reinsert deleted files.
        self.scan_generation += 1
        self.records = records
        self.active_path = None
        self.hover_path = None
        self.pending.clear()
        if reset_cache:
            self.cache.clear()
        self.relayout()
        self.verticalScrollBar().setValue(0)

    def relayout(self):
        self.visible_records = [r for r in self.records
            if (not self.unreliable_only or r.unreliable)
            and (not self.search_text or self.search_text in r.path.name.casefold())
            and (self.navigation is None or
                 (r.taken.year == self.navigation[0] and
                  (self.navigation[1] is None or r.taken.month == self.navigation[1])))]
        self.visible_records.sort(key=lambda r: (r.taken, r.path.name.casefold()), reverse=self.newest)
        width = max(180, self.viewport().width())
        cols = max(1, (width - 2*self.MARGIN + self.GAP) // 188)
        tile = max(110, (width - 2*self.MARGIN - (cols-1)*self.GAP)//cols)
        tile_h = tile + self.CAPTION
        self.tiles, self.headers = [], []
        y = 16
        old_y = old_m = None
        col = 0
        for rec in self.visible_records:
            year, month = rec.taken.year, rec.taken.month
            if (year, month) != (old_y, old_m):
                if col:
                    y += tile_h + self.GAP
                    col = 0
                if year != old_y:
                    self.headers.append((QRect(self.MARGIN, y, width - 2*self.MARGIN, 38), year, None, str(year)))
                    y += 44
                self.headers.append((QRect(self.MARGIN, y, width - 2*self.MARGIN, 31), year, month,
                                    calendar.month_name[month]))
                y += 38
                old_y, old_m = year, month
            x = self.MARGIN + col*(tile+self.GAP)
            self.tiles.append((QRect(x, y, tile, tile_h), rec))
            col += 1
            if col == cols:
                y += tile_h + self.GAP
                col = 0
        if col:
            y += tile_h + self.GAP
        self._tile_bottoms = [r.bottom() for r, _ in self.tiles]
        scrollbar = self.verticalScrollBar()
        scrollbar.setPageStep(self.viewport().height())
        scrollbar.setRange(0, max(0, y + 24 - self.viewport().height()))
        self.empty_button.setVisible(not bool(self.visible_records))
        self.empty_button.setGeometry((width - 160)//2, max(120, self.viewport().height()//2 + 22), 160, 40)
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout()

    def leaveEvent(self, event):
        self.hover_path = None
        self.viewport().update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        mouse = event.position().toPoint() + QPoint(0, self.verticalScrollBar().value())
        found = next((str(r.path) for rect, r in self.tiles if rect.contains(mouse)), None)
        if found != self.hover_path:
            self.hover_path = found
            self.hover_animation.stop()
            self.hover_animation.setStartValue(0.0)
            self.hover_animation.setEndValue(1.0 if found else 0.0)
            self.hover_animation.start()
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self.pal
        offset = self.verticalScrollBar().value()
        bottom = offset + self.viewport().height()
        p.fillRect(self.viewport().rect(), color(t.background))
        for rect, year, month, title in self.headers:
            if rect.bottom() < offset or rect.top() > bottom:
                continue
            box = rect.translated(0, -offset)
            font = QFont('Segoe UI', 13 if month is None else 10,
                         QFont.Weight.DemiBold)
            p.setFont(font)
            p.setPen(color(t.text if month is None else t.secondary))
            p.drawText(box, Qt.AlignmentFlag.AlignVCenter, title)
        first = bisect_left(self._tile_bottoms, offset)
        for rect, rec in self.tiles[first:]:
            if rect.top() > bottom:
                break
            box = rect.translated(0, -offset)
            chosen = str(rec.path) == self.active_path
            hovered = str(rec.path) == self.hover_path
            if hovered or chosen:
                shadow = box.adjusted(-2, -2, 2, 2)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color(t.text, int(18 * (self._hover_alpha if hovered else 1))))
                p.drawRoundedRect(shadow.translated(0, 2), 11, 11)
            p.setBrush(color(t.surface))
            p.setPen(QPen(color(t.accent if chosen else t.border), 2 if chosen else 1))
            p.drawRoundedRect(box.adjusted(1, 1, -1, -1), 10, 10)
            pic = QRect(box.left()+5, box.top()+5, box.width()-10, box.width()-10)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(t.image_placeholder))
            p.drawRoundedRect(pic, 8, 8)
            key = str(rec.path)
            if key in self.cache and self.cache[key] is not None:
                pm = self.cache[key]
                scale_factor = 1 + (0.025*self._hover_alpha if hovered else 0)
                imgsize = QSize(int(pic.width()*scale_factor), int(pic.height()*scale_factor))
                cropped = pm.scaled(imgsize, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                    Qt.TransformationMode.SmoothTransformation)
                mask = QPainterPath()
                mask.addRoundedRect(pic, 8, 8)
                p.save()
                p.setClipPath(mask)
                p.drawPixmap(pic.center().x() - cropped.width()//2,
                             pic.center().y() - cropped.height()//2, cropped)
                p.restore()
            elif key not in self.cache:
                self.request_thumbnail(rec)
            else:
                p.setPen(color(t.secondary))
                p.drawText(pic, Qt.AlignmentFlag.AlignCenter, 'Preview unavailable')
            if rec.is_video:
                self._pill(p, QRect(pic.left()+7, pic.bottom()-28, 68, 21), '▶ Video',
                           t.text, t.surface)
            badge_text, sem = source_style(rec.source, t)
            badge_width = min(pic.width()-12, max(64, len(badge_text)*7+16))
            self._pill(p, QRect(pic.right()-badge_width-5, pic.top()+8, badge_width, 22),
                       badge_text, t.semantic_foreground, sem)
            if chosen:
                check = QRect(pic.left()+10, pic.top()+10, 26, 26)
                p.setBrush(color(t.accent))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(check)
                p.setPen(color(t.background))
                p.setFont(QFont('Segoe UI', 12, QFont.Weight.Bold))
                p.drawText(check, Qt.AlignmentFlag.AlignCenter, '✓')
            p.setFont(QFont('Segoe UI', 9))
            p.setPen(color(t.text))
            name_rect = QRect(pic.left()+3, pic.bottom()+8, pic.width()-6, 17)
            p.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(rec.path.name, Qt.TextElideMode.ElideMiddle, name_rect.width()))
            p.setPen(color(t.secondary))
            p.setFont(QFont('Segoe UI', 8))
            p.drawText(QRect(pic.left()+3, pic.bottom()+25, pic.width()-6, 17),
                       Qt.AlignmentFlag.AlignVCenter, rec.taken.strftime('%b %d, %Y · %H:%M'))
        if not self.visible_records:
            p.setPen(color(t.secondary))
            p.setFont(QFont('Segoe UI', 12))
            message = ('No matches. Clear the search or filters.' if self.records
                       else 'Your photo timeline will appear here.\nChoose a folder to begin.')
            area = QRect(20, max(5, self.viewport().height()//2 - 85),
                         max(120, self.viewport().width()-40), 75)
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, message)
        if offset > 8 and self.headers:
            # Sticky section context is drawn after tiles so it stays visible.
            yr = None
            mo = None
            for rect, year, month, title in self.headers:
                if rect.top() <= offset + 62:
                    if month is None:
                        yr = title
                        mo = None
                    else:
                        mo = title
                else:
                    break
            sticky = QRect(0, 0, self.viewport().width(), 61)
            p.fillRect(sticky, color(t.background, 245))
            p.setPen(QPen(color(t.border), 1))
            p.drawLine(0, 60, self.viewport().width(), 60)
            p.setPen(color(t.text))
            p.setFont(QFont('Segoe UI', 13, QFont.Weight.DemiBold))
            p.drawText(QRect(self.MARGIN, 4, sticky.width()-40, 29),
                       Qt.AlignmentFlag.AlignVCenter, yr or '')
            p.setPen(color(t.secondary))
            p.setFont(QFont('Segoe UI', 10, QFont.Weight.DemiBold))
            p.drawText(QRect(self.MARGIN, 32, sticky.width()-40, 25),
                       Qt.AlignmentFlag.AlignVCenter, mo or '')
        if self.hasFocus():
            p.setPen(QPen(color(t.accent), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self.viewport().rect().adjusted(1, 1, -2, -2))
        p.end()

    def _pill(self, p: QPainter, rect: QRect, label: str, ink: str, bg: str):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(bg))
        p.drawRoundedRect(rect, 5, 5)
        p.setPen(color(ink))
        p.setFont(QFont('Segoe UI', 8, QFont.Weight.DemiBold))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    def request_thumbnail(self, rec: PhotoRecord):
        key = str(rec.path)
        if key in self.pending or len(self.pending) >= 16:
            return
        self.pending.add(key)
        generation = self.scan_generation
        job = ImageJob(rec.path, rec.is_video, generation=generation)
        ticket = f'{generation}:{key}'
        self._thumbnail_signals[ticket] = job.signals
        # A QObject receiver + queued delivery prevents QPixmap being created
        # in a thread-pool worker (one cause of native Qt crashes after scanning).
        job.signals.result.connect(self.thumbnail_ready, Qt.ConnectionType.QueuedConnection)
        self.pool.start(job)

    @Slot(int, str, object)
    def thumbnail_ready(self, generation: int, key: str, img: object):
        signal_ref = self._thumbnail_signals.pop(f'{generation}:{key}', None)
        if generation != self.scan_generation:
            return
        self.pending.discard(key)
        if not any(str(rec.path) == key for rec in self.records):
            return
        # QPixmap must be constructed on the GUI thread; ImageJob produces only
        # a deep-copied QImage in background threads.
        self.cache[key] = (QPixmap.fromImage(img)
                           if isinstance(img, QImage) and not img.isNull() else None)
        self.cache.move_to_end(key)
        while len(self.cache) > 300:
            self.cache.popitem(last=False)
        self.viewport().update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        point = event.position().toPoint() + QPoint(0, self.verticalScrollBar().value())
        for rect, rec in self.tiles[bisect_left(self._tile_bottoms, point.y()):]:
            if rect.top() > point.y():
                break
            if rect.contains(point):
                self.active_path = str(rec.path)
                self.selected.emit(rec)
                self.open_requested.emit(rec)
                self.viewport().update()
                return

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Down):
            if not self.visible_records:
                return
            idx = next((i for i,r in enumerate(self.visible_records) if str(r.path) == self.active_path), 0)
            direction = 1 if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down) else -1
            idx = max(0, min(len(self.visible_records)-1, idx + direction))
            rec = self.visible_records[idx]
            self.active_path = str(rec.path)
            self.selected.emit(rec)
            match = next((rect for rect,r in self.tiles if r is rec), None)
            if match:
                self.verticalScrollBar().setValue(max(0, match.top()-90))
            self.viewport().update()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            rec = next((r for r in self.visible_records if str(r.path) == self.active_path), None)
            if rec:
                self.open_requested.emit(rec)
        else:
            super().keyPressEvent(event)


class ImageCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image: QPixmap | None = None
        self.note = 'Loading preview…'
        self.setMinimumSize(320, 300)

    def set_image(self, image: QPixmap | None, note: str = 'Preview unavailable'):
        self.image = image
        self.note = note
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), color(DARK.background))
        if self.image:
            scaled = self.image.scaled(QSize(max(1, self.width()-24), max(1, self.height()-24)),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((self.width()-scaled.width())//2, (self.height()-scaled.height())//2, scaled)
        else:
            p.setPen(color(DARK.secondary))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.note)
        p.end()


class PhotoLightbox(QDialog):
    """Full-size photo viewer and integrated MP4/MOV video player."""
    def __init__(self, parent, records: list[PhotoRecord], selected: PhotoRecord, p: Palette):
        super().__init__(parent)
        self.setWindowTitle('Photo Viewer — Preview')
        self.setModal(True)
        self.setMinimumSize(580, 400)
        screen = QApplication.primaryScreen()
        usable = screen.availableGeometry() if screen else QRect(0, 0, 1200, 800)
        self.resize(min(1350, int(usable.width() * .9)), min(880, int(usable.height() * .9)))
        self.pal = DARK
        self.setStyleSheet(qss(DARK))
        self.records = records
        self.index = next((i for i, r in enumerate(records) if str(r.path) == str(selected.path)), 0)
        self.pool = QThreadPool.globalInstance()
        self.request_key = ''
        self.image_signals: list[ImageSignals] = []
        self.media_player = None
        self.audio_output = None
        self._video_error = ''
        # Loading is deferred until the dialog is actually displayed.
        # On Windows an otherwise hidden QMediaPlayer can lock the file.
        self._pending_video: Path | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        viewer_col = QVBoxLayout()
        viewer_col.setSpacing(0)
        viewer_col.setContentsMargins(0, 0, 0, 0)
        nav = QHBoxLayout()
        nav.setContentsMargins(14, 12, 14, 10)
        self.back = button('Previous', DARK, 'chevron-left')
        self.forward = button('Next', DARK, 'chevron-right')
        self.close_btn = button('Close', DARK, 'x')
        nav.addWidget(self.back)
        nav.addWidget(self.forward)
        nav.addStretch()
        nav.addWidget(self.close_btn)
        viewer_col.addLayout(nav)

        self.pages = QStackedWidget()
        self.canvas = ImageCanvas()
        self.pages.addWidget(self.canvas)
        video_page = QWidget()
        video_layout = QVBoxLayout(video_page)
        video_layout.setContentsMargins(4, 4, 4, 10)
        video_layout.setSpacing(8)
        self.video_screen = QVideoWidget() if VIDEO_PLAYBACK_AVAILABLE else None
        if self.video_screen is not None:
            video_layout.addWidget(self.video_screen, 1)
        else:
            video_layout.addWidget(QLabel('Video playback unavailable: install PySide6 with Qt Multimedia.'), 1)
        self.video_message = QLabel('')
        self.video_message.setObjectName('Muted')
        self.video_message.setWordWrap(True)
        self.video_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        video_layout.addWidget(self.video_message)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.play_button = button('Pause', DARK, 'pause')
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.setToolTip('Seek within video')
        self.time_label = QLabel('00:00 / 00:00')
        self.time_label.setObjectName('Muted')
        self.mute_button = button('Mute', DARK)
        for w in (self.play_button, self.seek, self.time_label, self.mute_button):
            controls.addWidget(w, 1 if w is self.seek else 0)
        video_layout.addLayout(controls)
        self.pages.addWidget(video_page)
        viewer_col.addWidget(self.pages, 1)

        viewing = QWidget()
        viewing.setLayout(viewer_col)
        layout.addWidget(viewing, 1)
        panel = QFrame()
        panel.setObjectName('InfoPane')
        panel.setFixedWidth(250 if usable.width() < 900 else 288)
        info = QVBoxLayout(panel)
        info.setContentsMargins(20, 24, 20, 24)
        info.setSpacing(13)
        caption = QLabel('PHOTO DETAILS')
        caption.setObjectName('Muted')
        info.addWidget(caption)
        self.name = QLabel()
        self.name.setWordWrap(True)
        self.name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addWidget(self.name)
        self.date = QLabel()
        self.date.setObjectName('SectionTitle')
        self.date.setWordWrap(True)
        info.addWidget(self.date)
        self.source = QLabel()
        self.source.setWordWrap(True)
        info.addWidget(self.source)
        self.details = QLabel()
        self.details.setObjectName('Muted')
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addWidget(self.details)
        info.addStretch()
        self.open_file = button('Open in default app', DARK, 'external-link')
        info.addWidget(self.open_file)
        layout.addWidget(panel)

        if VIDEO_PLAYBACK_AVAILABLE:
            self.media_player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.audio_output.setVolume(.65)
            self.media_player.setAudioOutput(self.audio_output)
            self.media_player.setVideoOutput(self.video_screen)
            self.media_player.positionChanged.connect(self.video_position_changed)
            self.media_player.durationChanged.connect(self.video_duration_changed)
            self.media_player.playbackStateChanged.connect(self.video_playback_changed)
            self.media_player.errorOccurred.connect(self.video_error_occurred)
            self.media_player.mediaStatusChanged.connect(self.video_status_changed)
            self.play_button.clicked.connect(self.toggle_playback)
            self.seek.sliderMoved.connect(self.media_player.setPosition)
            self.mute_button.clicked.connect(self.toggle_mute)
        else:
            self.play_button.setEnabled(False)
            self.seek.setEnabled(False)
            self.mute_button.setEnabled(False)
        self.back.clicked.connect(lambda: self.navigate(-1))
        self.forward.clicked.connect(lambda: self.navigate(1))
        self.close_btn.clicked.connect(self.accept)
        self.open_file.clicked.connect(self.open_current)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        opacity = QGraphicsOpacityEffect(panel)
        panel.setGraphicsEffect(opacity)
        self.fade = QPropertyAnimation(opacity, b'opacity', self)
        self.fade.setDuration(180)
        self.fade.setStartValue(0.2)
        self.fade.setEndValue(1.0)
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade.start()
        self.left_shortcut = QShortcut(QKeySequence('Left'), self)
        self.right_shortcut = QShortcut(QKeySequence('Right'), self)
        self.escape_shortcut = QShortcut(QKeySequence('Escape'), self)
        self.space_shortcut = QShortcut(QKeySequence('Space'), self)
        self.left_shortcut.activated.connect(lambda: self.navigate(-1))
        self.right_shortcut.activated.connect(lambda: self.navigate(1))
        self.escape_shortcut.activated.connect(self.accept)
        self.space_shortcut.activated.connect(self.toggle_playback)
        self.show_record()

    @staticmethod
    def _format_time(milliseconds: int) -> str:
        seconds = max(0, milliseconds // 1000)
        h, rest = divmod(seconds, 3600)
        m, s = divmod(rest, 60)
        return f'{h:02}:{m:02}:{s:02}' if h else f'{m:02}:{s:02}'

    def stop_video(self):
        """Unload the current media source when navigating to another file."""
        self._pending_video = None
        if self.media_player is not None:
            self.media_player.stop()
            self.media_player.setSource(QUrl())

    def _open_pending_video(self):
        """Start playback only when the preview dialog is visible."""
        video = self._pending_video
        if video is None or self.media_player is None or not self.isVisible():
            return
        self._pending_video = None
        if not video.is_file():
            self.video_message.setText('Video was removed. Rescan to refresh the library.')
            return
        self.media_player.setSource(QUrl.fromLocalFile(str(video)))
        self.media_player.play()

    def showEvent(self, event):
        super().showEvent(event)
        self._open_pending_video()

    def _release_media(self):
        """Synchronously destroy the native player on exit (Windows file locks).

        A stopped Qt MediaPlayer can still own a backend/decoder which holds the
        file open. Merely calling stop() and setSource(QUrl()) is not always
        sufficient, particularly when a QDialog.close() leaves the Python
        dialog object alive (as in tests and short-lived preview windows).
        """
        self._pending_video = None
        player = self.media_player
        if player is None:
            return
        # Make late signal deliveries safe, then shut down the native decoder.
        self.media_player = None
        try:
            player.stop()
            player.setSource(QUrl())
            player.setVideoOutput(None)
            player.setAudioOutput(None)
        finally:
            # deleteLater() alone does not suffice here: the caller may
            # immediately delete or move the video before the Qt event loop
            # processes DeferredDelete. shiboken6.delete invokes the QObject
            # destructor now and disconnects native signal connections.
            delete_qt_object(player)

    def show_record(self):
        rec = self.records[self.index]
        self.stop_video()
        # Invalidate any late image decode from a previous picture.
        self.request_key = f'{self.index}:{rec.path}'
        self.name.setText(rec.path.name)
        self.date.setText(rec.display_date())
        source_text, _ = source_style(rec.source, self.pal)
        self.source.setText(f'Date source: {source_text}\n{rec.detail}')
        try:
            size = f'{rec.path.stat().st_size / 1024 / 1024:.2f} MB'
        except OSError:
            size = 'Size unavailable'
        warning = f'\n\n⚠ {rec.warning}' if rec.warning else ''
        self.details.setText(f'{size}\n{rec.path.suffix.upper()} · {self.index+1:,} of {len(self.records):,}'
                             f'\n\n{rec.path}{warning}')
        self.back.setEnabled(self.index > 0)
        self.forward.setEnabled(self.index < len(self.records)-1)
        if not rec.path.is_file():
            self.pages.setCurrentWidget(self.canvas)
            self.canvas.set_image(None, 'File missing. Rescan the folder to refresh your library.')
            return
        if rec.is_video:
            self.pages.setCurrentIndex(1)
            self.seek.setRange(0, 0)
            self.time_label.setText('00:00 / 00:00')
            self._video_error = ''
            if self.media_player is not None:
                self.video_message.setText('Opening video…')
                self._pending_video = rec.path
                self._open_pending_video()
            else:
                self.video_message.setText('Video playback is unavailable. Use “Open in default app”.')
            return
        self.pages.setCurrentWidget(self.canvas)
        self.canvas.set_image(None, 'Loading photo…')
        job = ImageJob(rec.path, False, max_dim=3000, request=self.request_key)
        self.image_signals.append(job.signals)
        job.signals.result.connect(self.image_ready, Qt.ConnectionType.QueuedConnection)
        self.pool.start(job)

    @Slot(int, str, object)
    def image_ready(self, generation: int, key: str, img: object):
        # Every result belongs to exactly one in-flight job; dropped records
        # cannot force old results into the currently displayed picture.
        sender = self.sender()
        if sender in self.image_signals:
            self.image_signals.remove(sender)
        if key == self.request_key and not self.records[self.index].is_video:
            self.canvas.set_image(QPixmap.fromImage(img) if isinstance(img, QImage) and not img.isNull() else None,
                                  'Preview unavailable — open with another app')

    def video_position_changed(self, position: int):
        if self.media_player is None:
            return
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        self.time_label.setText(f'{self._format_time(position)} / {self._format_time(self.media_player.duration())}')

    def video_duration_changed(self, duration: int):
        self.seek.setRange(0, max(0, duration))
        self.video_position_changed(self.media_player.position())

    def video_playback_changed(self, state):
        if self.media_player is None:
            return
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText('Pause' if playing else 'Play')
        self.play_button.setIcon(icon('pause' if playing else 'play', DARK))

    def video_error_occurred(self, error, message: str):
        if self.media_player is None or error == QMediaPlayer.Error.NoError:
            return
        self._video_error = message or 'Unknown multimedia error'
        self.video_message.setText('Cannot play this video (unsupported codec or damaged file).\n'
                                   'Use “Open in default app”. Details: ' + self._video_error)

    def video_status_changed(self, status):
        if self.media_player is None:
            return
        if status == QMediaPlayer.MediaStatus.LoadedMedia or status == QMediaPlayer.MediaStatus.BufferedMedia:
            self.video_message.setText('')
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.video_message.setText('End of video — press Play to replay.')
        elif status == QMediaPlayer.MediaStatus.InvalidMedia and not self._video_error:
            self.video_message.setText('Video format or codec unsupported. Use “Open in default app”.')

    def toggle_playback(self):
        if self.media_player is None or not self.records[self.index].is_video:
            return
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def toggle_mute(self):
        if self.audio_output is not None:
            muted = not self.audio_output.isMuted()
            self.audio_output.setMuted(muted)
            self.mute_button.setText('Unmute' if muted else 'Mute')

    def navigate(self, delta: int):
        pos = max(0, min(len(self.records)-1, self.index+delta))
        if pos != self.index:
            self.index = pos
            self.show_record()

    def open_current(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.records[self.index].path)))

    def accept(self):
        self._release_media()
        super().accept()

    def reject(self):
        self._release_media()
        super().reject()

    def done(self, result):
        self._release_media()
        super().done(result)

    def closeEvent(self, event):
        # QWidget.close() is not guaranteed to enter our done() override;
        # release even if users click the title-bar X or a test calls close().
        self._release_media()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Left:
            self.navigate(-1)
        elif event.key() == Qt.Key.Key_Right:
            self.navigate(1)
        elif event.key() == Qt.Key.Key_Escape:
            self.accept()
        elif event.key() == Qt.Key.Key_Space:
            self.toggle_playback()
        else:
            super().keyPressEvent(event)


class ManualCorrectionDialog(QDialog):
    """One-photo before/after preview; only updates session memory on confirmation."""
    def __init__(self, parent, record: PhotoRecord, proposed: datetime, p: Palette,
                 dry_run: bool):
        super().__init__(parent)
        self.setWindowTitle('Correct capture date — Preview')
        self.resize(660, 340)
        self.record = record
        main = QVBoxLayout(self)
        main.setContentsMargins(22, 20, 22, 20)
        main.setSpacing(12)
        title = QLabel('Correct capture date')
        title.setObjectName('SectionTitle')
        main.addWidget(title)
        explanation = QLabel('Review the proposed date. This updates the timeline only; '
                             'use Fix dates to write the date to disk.')
        explanation.setObjectName('Muted')
        explanation.setWordWrap(True)
        main.addWidget(explanation)
        self.editor = QDateTimeEdit()
        self.editor.setCalendarPopup(True)
        self.editor.setDisplayFormat('yyyy-MM-dd HH:mm:ss')
        self.editor.setDateTime(QDateTime(QDate(proposed.year, proposed.month, proposed.day),
                                         QTime(proposed.hour, proposed.minute, proposed.second)))
        main.addWidget(self.editor)
        self.table = QTableWidget(1, 4)
        self.table.setHorizontalHeaderLabels(['Photo', 'Before', 'After', 'Previous source'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        main.addWidget(self.table)
        self.table.setItem(0, 0, QTableWidgetItem(record.path.name))
        self.table.setItem(0, 1, QTableWidgetItem(record.display_date()))
        self.table.setItem(0, 3, QTableWidgetItem(record.source))
        self.editor.dateTimeChanged.connect(self.update_preview)
        self.update_preview()
        note = QLabel('Dry run is ON — no files will change.' if dry_run else
            'This manual correction changes only the on-screen record, not the file.')
        note.setObjectName('Warning')
        main.addWidget(note)
        row = QHBoxLayout()
        row.addStretch()
        cancel = button('Cancel', p)
        confirm = button('Apply to timeline', p, 'check', 'primary')
        row.addWidget(cancel)
        row.addWidget(confirm)
        main.addLayout(row)
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self.accept)
        effect = QGraphicsOpacityEffect(self.table)
        self.table.setGraphicsEffect(effect)
        self.animation = QPropertyAnimation(effect, b'opacity', self)
        self.animation.setDuration(160)
        self.animation.setStartValue(0.3)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.start()

    def update_preview(self, *_):
        self.table.setItem(0, 2, QTableWidgetItem(
            self.editor.dateTime().toPython().strftime('%Y-%m-%d %H:%M:%S')))

    def chosen_date(self) -> datetime:
        return self.editor.dateTime().toPython().replace(tzinfo=None)


class OperationDialog(QDialog):
    def __init__(self, parent, records: list[PhotoRecord], root: Path, kind: str,
                 store: SettingsStore, p: Palette):
        super().__init__(parent)
        self.setWindowTitle('Fix dates — Preview' if kind == 'fix' else 'Organize — Preview')
        self.resize(1040, 710)
        self.setMinimumSize(680, 520)
        self.records, self.root, self.kind, self.store = records, root, kind, store
        self.pal = p
        self.items: list[actions.PlanItem] = []
        self.settings: actions.Settings | None = None
        self.execute_requested = False
        self.destination = store.destination_folder  # read-only; never auto-resave
        box = QVBoxLayout(self)
        box.setContentsMargins(24,20,24,20)
        box.setSpacing(12)
        title = QLabel('Fix capture dates' if kind == 'fix' else 'Organize your library')
        title.setObjectName('SectionTitle')
        box.addWidget(title)
        hint = QLabel('Preview exactly what will change. Nothing is written until you confirm.')
        hint.setObjectName('Muted')
        hint.setWordWrap(True)
        box.addWidget(hint)
        options = QFormLayout()
        options.setSpacing(12)
        self.strategy = QComboBox()
        self.strategy.addItem('Work on copies (recommended)', 'copy')
        self.strategy.addItem('Edit original files (higher risk)' if kind == 'fix'
                              else 'Move originals (higher risk)', 'inplace' if kind=='fix' else 'move')
        options.addRow('Method', self.strategy)
        self.dest_path = ElidedPath()
        self.dest_path.setMinimumWidth(250)
        self.browse_btn = button('Change destination', p, 'folder')
        dest = QHBoxLayout()
        dest.addWidget(self.dest_path, 1)
        dest.addWidget(self.browse_btn)
        dest_frame = QWidget()
        dest_frame.setLayout(dest)
        options.addRow('Destination', dest_frame)
        self.dest_frame = dest_frame
        self.layout_kind = QComboBox()
        if kind == 'organize':
            self.layout_kind.addItem('YYYY / MM folders', 'folders')
            self.layout_kind.addItem('Rename using capture date', 'rename')
            self.layout_kind.addItem('Folders + rename', 'both')
            options.addRow('Layout', self.layout_kind)
        self.exif = QCheckBox('Write missing JPEG EXIF DateTimeOriginal')
        self.exif.setChecked(store.bool_value(EXIF_KEY, False))
        if kind == 'fix':
            options.addRow('JPEG EXIF', self.exif)
        self.include_unreliable = QCheckBox('Include unreliable file dates')
        options.addRow('Dates', self.include_unreliable)
        self.dry = QCheckBox('Dry run — do not change any files')
        self.dry.setChecked(store.bool_value(DRY_KEY, True))
        options.addRow('Safety', self.dry)
        box.addLayout(options)
        self.warning = QLabel()
        self.warning.setObjectName('Warning')
        self.warning.setWordWrap(True)
        box.addWidget(self.warning)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['File', 'Before (modified)', 'After (capture)', 'Source', 'Destination'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        box.addWidget(self.table, 1)
        footer = QHBoxLayout()
        self.preview_btn = button('Preview changes', p, 'eye')
        self.apply_btn = button('Finish dry run', p, 'check', 'primary')
        self.apply_btn.setEnabled(False)
        close = button('Cancel', p)
        footer.addWidget(self.preview_btn)
        footer.addStretch()
        footer.addWidget(close)
        footer.addWidget(self.apply_btn)
        box.addLayout(footer)
        self.preview_btn.clicked.connect(self.build_preview)
        self.apply_btn.clicked.connect(self.confirm)
        close.clicked.connect(self.reject)
        self.browse_btn.clicked.connect(self.change_destination)
        self.strategy.currentIndexChanged.connect(self.toggle_destination)
        self.strategy.currentIndexChanged.connect(self.invalidate)
        self.layout_kind.currentIndexChanged.connect(self.invalidate)
        self.include_unreliable.toggled.connect(self.invalidate)
        self.exif.toggled.connect(lambda value: self.store.set_flag(EXIF_KEY, value))
        self.exif.toggled.connect(self.invalidate)
        self.dry.toggled.connect(lambda value: self.store.set_flag(DRY_KEY, value))
        self.dry.toggled.connect(self.invalidate)
        self.toggle_destination()
        self.update_destination()
        self.update_banner()

    def update_destination(self):
        self.dest_path.set_path(self.destination if self.destination else 'Not selected — choose a destination')
        self.update_banner()

    def update_banner(self, detail: str = ''):
        extra = ('Dry run is ON. No files will be modified.' if self.dry.isChecked()
                 else 'Dry run is OFF. A confirmation is required before writing files.')
        if self.destination and not accessible_folder(self.destination)[0]:
            extra = 'Destination folder not found. Choose a new one. ' + extra
        self.warning.setText((detail + '\n' if detail else '') + extra)
        self.apply_btn.setText('Finish dry run' if self.dry.isChecked() else 'Confirm changes…')

    def toggle_destination(self):
        risky = self.strategy.currentData() in ('inplace', 'move')
        self.dest_frame.setEnabled(not (self.strategy.currentData() == 'inplace'))
        set_button_role(self.apply_btn, 'destructive' if risky else 'primary')

    def invalidate(self, *_):
        self.apply_btn.setEnabled(False)
        self.update_banner()

    def change_destination(self):
        start = self.destination or str(self.root.parent)
        if not accessible_folder(start)[0]:
            start = str(Path.home())
        path = QFileDialog.getExistingDirectory(self, 'Change destination folder', start)
        if path:
            self.store.select_destination(path)  # ONLY an explicit file-dialog selection persists
            self.destination = self.store.destination_folder
            self.update_destination()
            self.invalidate()

    def build_preview(self):
        try:
            strategy = self.strategy.currentData()
            target = None
            if strategy != 'inplace':
                if not self.destination:
                    raise ValueError('Choose a destination for the output copies or moved files.')
                valid, reason = accessible_folder(self.destination)
                if not valid:
                    raise ValueError(reason)
                target = Path(self.destination)
            settings = actions.Settings(self.kind, strategy, target,
                self.layout_kind.currentData() if self.kind == 'organize' else 'folders',
                self.exif.isChecked() if self.kind == 'fix' else False)
            chosen = [r for r in self.records if self.include_unreliable.isChecked() or not r.unreliable]
            self.items = actions.plan(chosen, self.root, settings)
            if not self.items:
                raise ValueError('No matching reliable dates. Correct dates or enable the file-date fallback.')
            self.settings = settings
            self.table.setRowCount(min(100, len(self.items)))
            for idx, item in enumerate(self.items[:100]):
                try:
                    before = datetime.fromtimestamp(item.source.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                except (OSError, ValueError):
                    before = 'Unavailable'
                for col, value in enumerate((item.source.name, before,
                    item.taken.strftime('%Y-%m-%d %H:%M:%S'), item.date_source, str(item.target))):
                    cell = QTableWidgetItem(value)
                    cell.setToolTip(value)
                    self.table.setItem(idx, col, cell)
            number_unreliable = sum(i.date_source == 'File date' for i in self.items)
            unknown = sum(i.precision == 'date' for i in self.items)
            note = (f'{len(self.items):,} planned; {number_unreliable:,} unreliable; '
                    f'{unknown:,} have no known capture time. '
                    + ('Showing first 100 files.' if len(self.items)>100 else '')
                    + (' JPEG EXIF changes are JPEG-only.' if settings.write_exif else ''))
            self.update_banner(note)
            self.apply_btn.setEnabled(True)
        except (ValueError, OSError) as exc:
            self.items = []
            self.settings = None
            self.warning.setText(f'Cannot preview: {exc}')
            self.apply_btn.setEnabled(False)

    def confirm(self):
        if not self.items or self.settings is None:
            return
        if self.dry.isChecked():
            QMessageBox.information(self, 'Dry run complete',
                f'{len(self.items):,} changes previewed. No files were changed. '
                'Turn off dry run, preview again, then confirm to apply.')
            self.accept()
            return
        risky = self.settings.strategy in ('move','inplace')
        message = (f'Apply {len(self.items):,} operations to '
            f'{"original files" if risky else "copies"}?\n\n'
            f'Method: {self.strategy.currentText()}\n'
            f'Destination: {self.destination or "Original locations"}\n'
            f'EXIF update: {self.settings.write_exif}\n\n'
            'Files changed after an operation may not be reversible. '
            'An undo journal will be created. Keep an independent backup.')
        if QMessageBox.warning(self, 'Confirm real file changes', message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            self.execute_requested = True
            self.accept()


class MainWindow(QMainWindow):
    def __init__(self, store: SettingsStore | None = None):
        super().__init__()
        self.store = store or SettingsStore()
        self.theme_choice = self.store.theme
        self.pal = palette(self.theme_choice)
        self.records: list[PhotoRecord] = []
        saved = self.store.source_folder
        self.root: Path | None = Path(saved) if saved else None
        self.current: PhotoRecord | None = None
        self.active_thread: QThread | None = None
        self._worker: TaskWorker | None = None
        self._task_completion: Callable | None = None
        self.cancel_event: Event | None = None
        self.last_journal: Path | None = None
        self._messages: list[str] = []
        self._busy = False
        self.setWindowTitle('Photo Viewer')
        self.setMinimumSize(660, 480)
        self.resize(1230, 800)
        saved_geometry = self.store.window_geometry()
        if saved_geometry is not None:
            self.restoreGeometry(saved_geometry)
        self._make_ui()
        self.apply_theme()
        self.update_path()
        # An emergency launch option can defer a faulty drive's automatic scan.
        # It does not clear or rewrite any remembered folder preference.
        if '--no-auto-scan' not in sys.argv:
            QTimer.singleShot(0, self.restore_and_scan)
        elif self.root is not None:
            self.status.setText('Auto scan paused. Press Rescan or Change to choose another folder.')

    def _make_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0,0,0,0)
        outer.setSpacing(0)
        bar = QFrame()
        bar.setObjectName('TopBar')
        top = QHBoxLayout(bar)
        top.setContentsMargins(16,10,16,10)
        top.setSpacing(8)
        self.sidebar_btn = button('', self.pal, 'panel-left')
        self.sidebar_btn.setToolTip('Show / hide the timeline sidebar')
        self.sidebar_btn.setFixedWidth(37)
        brand = QLabel('Photo Viewer')
        self.brand = brand
        brand.setObjectName('AppTitle')
        top.addWidget(self.sidebar_btn)
        top.addWidget(brand)
        top.addSpacing(15)
        self.folder_path = ElidedPath()
        top.addWidget(self.folder_path, 3)
        self.browse_btn = button('Change', self.pal, 'folder')
        top.addWidget(self.browse_btn)
        self.scan_btn = button('Rescan', self.pal, 'refresh-cw')
        top.addWidget(self.scan_btn)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search filenames')
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(95)
        top.addWidget(self.search, 2)
        self.filter_btn = button('Unreliable', self.pal, 'alert-triangle')
        self.filter_btn.setCheckable(True)
        self.filter_btn.setToolTip('Show only photos with uncertain filesystem dates')
        top.addWidget(self.filter_btn)
        self.sort = QComboBox()
        self.sort.addItems(['Newest first', 'Oldest first'])
        self.sort.setToolTip('Sort by capture date')
        self.sort.setCurrentIndex(0 if self.store.bool_value(SORT_KEY, True) else 1)
        top.addWidget(self.sort)
        self.settings_btn = button('', self.pal, 'settings')
        self.settings_btn.setToolTip('Settings and appearance')
        self.settings_btn.setFixedWidth(37)
        top.addWidget(self.settings_btn)
        outer.addWidget(bar)
        self.notice = QFrame()
        self.notice.setObjectName('Banner')
        note_row = QHBoxLayout(self.notice)
        note_row.setContentsMargins(16,8,16,8)
        self.notice_label = QLabel()
        self.notice_label.setWordWrap(True)
        self.notice_label.setObjectName('Warning')
        self.notice_action = button('Change folder', self.pal, 'folder')
        note_row.addWidget(self.notice_label, 1)
        note_row.addWidget(self.notice_action)
        outer.addWidget(self.notice)
        self.notice.hide()
        content = QHBoxLayout()
        content.setContentsMargins(0,0,0,0)
        content.setSpacing(0)
        self.sidebar = QFrame()
        self.sidebar.setObjectName('Sidebar')
        self.sidebar.setMinimumWidth(0)
        self.sidebar.setMaximumWidth(220)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(12,20,12,12)
        side.setSpacing(9)
        cap = QLabel('LIBRARY')
        cap.setObjectName('Muted')
        side.addWidget(cap)
        self.library_tree = QTreeWidget()
        self.library_tree.setHeaderHidden(True)
        self.library_tree.setIndentation(13)
        side.addWidget(self.library_tree, 1)
        self.unreliable = QCheckBox('Review uncertain dates')
        self.unreliable.setToolTip('Show only dates inferred from filesystem timestamps')
        side.addWidget(self.unreliable)
        content.addWidget(self.sidebar)
        main_col = QVBoxLayout()
        main_col.setContentsMargins(12,10,12,10)
        main_col.setSpacing(8)
        counter_row = QHBoxLayout()
        header = QLabel('Timeline')
        header.setObjectName('SectionTitle')
        self.counter = QLabel('Choose a folder to begin')
        self.counter.setObjectName('Counter')
        counter_row.addWidget(header)
        counter_row.addStretch()
        counter_row.addWidget(self.counter)
        main_col.addLayout(counter_row)
        self.timeline = PhotoTimeline(self.pal, self)
        main_col.addWidget(self.timeline, 1)
        self.inspector = QFrame()
        self.inspector.setObjectName('Inspector')
        inspector_row = QHBoxLayout(self.inspector)
        inspector_row.setContentsMargins(12,12,12,12)
        inspector_row.setSpacing(10)
        left = QVBoxLayout()
        self.selection_label = QLabel('Select a photo to inspect its capture date.')
        self.selection_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.selection_label.setObjectName('PathLabel')
        self.selection_label.setMinimumWidth(140)
        self.selection_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                           QSizePolicy.Policy.Preferred)
        self.source_label = QLabel('')
        self.source_label.setObjectName('Muted')
        self.source_label.setWordWrap(True)
        left.addWidget(self.selection_label)
        left.addWidget(self.source_label)
        inspector_row.addLayout(left, 2)
        self.date_edit = QDateTimeEdit()
        self.date_edit.setDisplayFormat('yyyy-MM-dd HH:mm:ss')
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setEnabled(False)
        self.manual_btn = button('Correct date', self.pal, 'calendar')
        self.open_btn = button('Open', self.pal, 'external-link')
        inspector_row.addWidget(self.date_edit)
        inspector_row.addWidget(self.manual_btn)
        inspector_row.addWidget(self.open_btn)
        main_col.addWidget(self.inspector)
        actionbar = QFrame()
        actionbar.setObjectName('Toolbar')
        controls = QHBoxLayout(actionbar)
        controls.setContentsMargins(12,10,12,10)
        controls.setSpacing(8)
        self.dry = QCheckBox('Dry run')
        self.dry.setChecked(self.store.bool_value(DRY_KEY, True))
        self.exif = QCheckBox('Write missing JPEG EXIF')
        self.exif.setChecked(self.store.bool_value(EXIF_KEY, False))
        self.fix_btn = button('Fix dates', self.pal, 'calendar', 'primary')
        self.organize_btn = button('Organize', self.pal, 'folder')
        self.undo_btn = button('Undo', self.pal, 'rotate-ccw')
        controls.addWidget(self.dry)
        controls.addWidget(self.exif)
        controls.addSpacing(6)
        controls.addWidget(self.fix_btn)
        controls.addWidget(self.organize_btn)
        controls.addWidget(self.undo_btn)
        controls.addStretch()
        main_col.addWidget(actionbar)
        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0,1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.status = QLabel('Ready')
        self.status.setObjectName('Muted')
        self.cancel_btn = button('Cancel', self.pal, 'x')
        self.cancel_btn.setEnabled(False)
        bottom.addWidget(self.progress, 2)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.cancel_btn)
        main_col.addLayout(bottom)
        layout_holder = QWidget()
        layout_holder.setLayout(main_col)
        content.addWidget(layout_holder, 1)
        outer.addLayout(content, 1)
        self.sidebar_animation = QPropertyAnimation(self.sidebar, b'maximumWidth', self)
        self.sidebar_animation.setDuration(180)
        self.sidebar_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.sidebar_expanded = self.store.bool_value(SIDEBAR_KEY, True)
        self.sidebar.setMaximumWidth(220 if self.sidebar_expanded else 0)
        self.sidebar_btn.clicked.connect(self.toggle_sidebar)
        self.browse_btn.clicked.connect(self.choose_folder)
        self.notice_action.clicked.connect(self.choose_folder)
        self.scan_btn.clicked.connect(self.start_scan)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.apply_filters)
        self.search.textChanged.connect(lambda _: self.search_timer.start(160))
        self.sort.currentIndexChanged.connect(self.sort_changed)
        self.unreliable.toggled.connect(self.apply_filters)
        self.unreliable.toggled.connect(self.filter_btn.setChecked)
        self.filter_btn.toggled.connect(self.unreliable.setChecked)
        self.timeline.selected.connect(self.select_record)
        self.timeline.open_requested.connect(self.open_lightbox)
        self.timeline.empty_choose_requested.connect(self.choose_folder)
        self.manual_btn.clicked.connect(self.set_manual_date)
        self.open_btn.clicked.connect(self.open_selection)
        self.fix_btn.clicked.connect(lambda: self.open_preview('fix'))
        self.organize_btn.clicked.connect(lambda: self.open_preview('organize'))
        self.undo_btn.clicked.connect(self.choose_undo)
        self.cancel_btn.clicked.connect(self.cancel_active)
        self.dry.toggled.connect(lambda v: self.store.set_flag(DRY_KEY, v))
        self.exif.toggled.connect(lambda v: self.store.set_flag(EXIF_KEY, v))
        self.settings_btn.clicked.connect(self.show_settings)
        self.library_tree.itemClicked.connect(self.navigate_tree)
        self.set_busy(False)
        self._rebuild_sidebar()

    def apply_theme(self):
        self.pal = palette(self.theme_choice)
        self.setStyleSheet(qss(self.pal))
        for b, name in ((self.sidebar_btn,'panel-left'), (self.browse_btn,'folder'),
            (self.scan_btn,'refresh-cw'), (self.filter_btn,'alert-triangle'), (self.settings_btn,'settings'),
            (self.manual_btn,'calendar'), (self.open_btn,'external-link'),
            (self.fix_btn,'calendar'), (self.organize_btn,'folder'),
            (self.undo_btn,'rotate-ccw'), (self.cancel_btn,'x'),
            (self.notice_action,'folder')):
            b.setIcon(icon(name, self.pal))
        self.timeline.set_palette(self.pal)
        self.timeline.empty_button.setStyleSheet(qss(self.pal))

    def show_settings(self):
        menu = QMenu(self)
        theme_menu = menu.addMenu('Appearance')
        for mode, label in (('system','Follow Windows'), ('light','Light'), ('dark','Dark')):
            action = theme_menu.addAction(('✓  ' if self.theme_choice == mode else '     ') + label)
            action.triggered.connect(lambda _, value=mode: self.set_theme(value))
        menu.addSeparator()
        menu.addAction('Change destination…', self.change_saved_destination)
        menu.addAction('Reset saved settings…', self.reset_settings)
        menu.addSeparator()
        menu.addAction('About Photo Viewer', self.about)
        menu.exec(self.settings_btn.mapToGlobal(QPoint(0, self.settings_btn.height())))

    def about(self):
        QMessageBox.about(self, 'About Photo Viewer',
            'Photo Viewer\n\nA local photo timeline and capture-date recovery tool.\n'
            'Supports EXIF, Google Takeout sidecars, filenames, and safe undoable operations.\n'
            'Your photos stay on your computer.')

    def set_theme(self, mode: str):
        self.theme_choice = mode
        self.store.set_theme(mode)
        self.apply_theme()

    def change_saved_destination(self):
        start = self.store.destination_folder or str(Path.home())
        if not accessible_folder(start)[0]:
            start = str(Path.home())
        path = QFileDialog.getExistingDirectory(self, 'Change default output destination', start)
        if path:
            self.store.select_destination(path)
            self.status.setText('Output destination saved. Original files were not changed.')

    def reset_settings(self):
        if self._busy:
            return
        if QMessageBox.question(self, 'Reset saved settings',
            'Clear remembered photo/destination folders, window position, appearance and safety preferences?\n'
            'This does not delete photos, change dates or erase undo journals.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        self.store.reset()
        self.root = None
        self.records = []
        self.current = None
        self.timeline.navigation = None
        self.timeline.set_records([])
        self.unreliable.setChecked(False)
        self.sort.blockSignals(True)
        self.sort.setCurrentIndex(0)
        self.sort.blockSignals(False)
        self.dry.setChecked(True)
        self.exif.setChecked(False)
        self.theme_choice = 'system'
        self.sidebar_expanded = True
        self.sidebar.setMaximumWidth(220)
        self.apply_theme()
        self.update_path()
        self._rebuild_sidebar()
        self.set_busy(False)
        self.status.setText('Saved preferences cleared')

    def toggle_sidebar(self):
        self.sidebar_expanded = not self.sidebar_expanded
        self.store.set_flag(SIDEBAR_KEY, self.sidebar_expanded)
        self.sidebar_animation.stop()
        self.sidebar_animation.setStartValue(self.sidebar.maximumWidth())
        self.sidebar_animation.setEndValue(220 if self.sidebar_expanded else 0)
        self.sidebar_animation.start()

    def update_path(self):
        self.folder_path.set_path(str(self.root) if self.root else '')

    def _folder_warning(self, message: str):
        self.notice_label.setText(message)
        self.notice.show()
        self.status.setText(message)

    def restore_and_scan(self):
        if self.root is None:
            return
        valid, message = accessible_folder(self.root)
        if not valid:
            self._folder_warning(message)
            return
        self.notice.hide()
        self.start_scan()

    def choose_folder(self):
        if self._busy:
            return
        start = str(self.root) if self.root and accessible_folder(self.root)[0] else str(Path.home())
        path = QFileDialog.getExistingDirectory(self, 'Choose photo source folder', start)
        if not path:
            return  # Cancellation NEVER rewrites saved folder.
        self.store.select_source(path)  # ONLY explicit chooser selection persists.
        self.root = Path(self.store.source_folder)
        self.update_path()
        self.notice.hide()
        self.records = []
        self.current = None
        self.timeline.navigation = None
        self.timeline.set_records([])
        self._rebuild_sidebar()
        self.start_scan()

    def set_busy(self, busy: bool):
        self._busy = busy
        self.browse_btn.setEnabled(not busy)
        self.scan_btn.setEnabled(not busy and self.root is not None)
        self.fix_btn.setEnabled(not busy and bool(self.records))
        self.organize_btn.setEnabled(not busy and bool(self.records))
        self.undo_btn.setEnabled(not busy)
        self.manual_btn.setEnabled(not busy and self.current is not None)
        self.open_btn.setEnabled(not busy and self.current is not None)
        self.cancel_btn.setEnabled(busy)
        self.settings_btn.setEnabled(not busy)

    def run_task(self, label: str, task: Callable, complete: Callable):
        if self.active_thread is not None:
            return
        self.cancel_event = Event()
        thread = QThread(self)
        worker = TaskWorker(task, self.cancel_event)
        worker.moveToThread(thread)
        self.active_thread = thread
        self._worker = worker
        self.set_busy(True)
        self.status.setText(label)
        self.progress.setRange(0,0)
        self._task_completion = complete
        thread.started.connect(worker.run)
        # IMPORTANT: connect signals directly to QObject slots, not lambdas.
        # On some PySide6 versions a lambda can run in the worker thread,
        # leading to a native Qt crash just after a scan completes.
        worker.progress.connect(self.task_progress, Qt.ConnectionType.QueuedConnection)
        worker.message.connect(self.task_message, Qt.ConnectionType.QueuedConnection)
        worker.done.connect(self.task_succeeded, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self.task_failed, Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self.thread_finished, Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    @Slot(object)
    def task_succeeded(self, result):
        try:
            if self._task_completion is not None:
                self._task_completion(result)
        except Exception:
            logging.exception('Error processing the completed scan or file operation')
            QMessageBox.critical(self, 'Photo Viewer error',
                'An error occurred while displaying the scan results. '
                'Your photos were not modified by scanning.\n\n'
                'See PhotoViewer.log for the detailed error.')
        finally:
            self.finish_task()

    @Slot(str)
    def task_failed(self, message: str):
        try:
            logging.error('Background operation failed: %s', message)
            self.status.setText(message[:120])
            QMessageBox.critical(self, 'Task error', message)
        finally:
            self.finish_task()

    def finish_task(self):
        self.set_busy(False)
        self._task_completion = None
        if self.active_thread is not None:
            self.active_thread.quit()

    @Slot()
    def thread_finished(self):
        self.active_thread = None
        self._worker = None

    @Slot(int, int)
    def task_progress(self, done: int, total: int):
        self.progress.setRange(0, max(total,1))
        self.progress.setValue(done)
        self.status.setText(f'{done:,} / {total:,} processed')

    @Slot(str)
    def task_message(self, message: str):
        self._messages.append(message)
        self.status.setText(message[-110:])

    def start_scan(self):
        if self.root is None:
            self._folder_warning('Choose a folder to get started.')
            return
        if self.active_thread is not None:
            return
        ok, reason = accessible_folder(self.root)
        if not ok:
            self._folder_warning(reason)
            return
        self.notice.hide()
        self._messages = []
        root = self.root
        logging.info('Scanning photo library: %s', root)
        self.run_task('Scanning photos…',
            lambda cancel, progress, message: scan(root, cancel, progress, message),
            self.scan_complete)

    def scan_complete(self, records):
        logging.info('Scan complete: %d media files', len(records))
        self.records = records
        self.timeline.navigation = None
        self.current = None
        self.selection_label.setText('Select a photo to inspect its capture date.')
        self.source_label.clear()
        self.date_edit.setEnabled(False)
        self.timeline.set_records(records)
        self._rebuild_sidebar()
        self.apply_filters()
        self.progress.setRange(0,1)
        self.progress.setValue(1)
        self.status.setText('Scan complete' + (' (cancelled; partial)' if self.cancel_event.is_set() else ''))
        if self._messages:
            QMessageBox.warning(self, 'Scan warnings', '\n'.join(self._messages[:12]) +
                (f'\nAnd {len(self._messages)-12} more warnings.' if len(self._messages)>12 else ''))

    def _rebuild_sidebar(self):
        self.library_tree.blockSignals(True)
        self.library_tree.clear()
        top = QTreeWidgetItem(['All photos'])
        top.setData(0, Qt.ItemDataRole.UserRole, None)
        self.library_tree.addTopLevelItem(top)
        years: dict[int,dict[int,int]] = {}
        for r in self.records:
            years.setdefault(r.taken.year, {})[r.taken.month] = years.setdefault(r.taken.year, {}).get(r.taken.month, 0)+1
        for year in sorted(years, reverse=self.sort.currentIndex()==0):
            item = QTreeWidgetItem([str(year)])
            item.setData(0, Qt.ItemDataRole.UserRole, (year, None))
            self.library_tree.addTopLevelItem(item)
            for month in sorted(years[year], reverse=self.sort.currentIndex()==0):
                child = QTreeWidgetItem([f'{calendar.month_abbr[month]}    {years[year][month]:,}'])
                child.setData(0, Qt.ItemDataRole.UserRole, (year, month))
                item.addChild(child)
            item.setExpanded(True)
        self.library_tree.blockSignals(False)

    def navigate_tree(self, item, column):
        self.timeline.navigation = item.data(0, Qt.ItemDataRole.UserRole)
        self.apply_filters()
        self.timeline.verticalScrollBar().setValue(0)

    def sort_changed(self):
        self.store.set_flag(SORT_KEY, self.sort.currentIndex()==0)
        self._rebuild_sidebar()
        self.apply_filters()

    def apply_filters(self):
        self.timeline.newest = self.sort.currentIndex()==0
        self.timeline.unreliable_only = self.unreliable.isChecked()
        self.timeline.search_text = self.search.text().strip().casefold()
        self.timeline.relayout()
        uncertain = sum(r.unreliable for r in self.records)
        self.counter.setText(f'{len(self.timeline.visible_records):,} displayed · '
                             f'{len(self.records):,} total · {uncertain:,} uncertain')

    def select_record(self, r: PhotoRecord):
        self.current = r
        self.selection_label.setText(r.path.name)
        self.selection_label.setToolTip(str(r.path))
        source, _ = source_style(r.source, self.pal)
        self.source_label.setText(f'{r.display_date()}  ·  {source}  ·  {r.detail}'
                                  + (f'  ·  {r.warning}' if r.warning else ''))
        d = r.taken
        self.date_edit.setDateTime(QDateTime(QDate(d.year,d.month,d.day),QTime(d.hour,d.minute,d.second)))
        self.date_edit.setEnabled(True)
        self.manual_btn.setEnabled(not self._busy)
        self.open_btn.setEnabled(not self._busy)

    def set_manual_date(self):
        if not self.current:
            return
        proposed = self.date_edit.dateTime().toPython().replace(tzinfo=None)
        dialog = ManualCorrectionDialog(self, self.current, proposed, self.pal, self.dry.isChecked())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.current.taken = dialog.chosen_date()
        self.current.source = 'Manual'
        self.current.detail = 'Corrected in this session; not written yet'
        self.current.unreliable = False
        self.current.precision = 'second'
        self.select_record(self.current)
        self._rebuild_sidebar()
        self.apply_filters()
        self.status.setText('Capture date corrected in preview. Use Fix dates to write it.')

    def open_selection(self):
        if self.current:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.current.path)))

    def open_lightbox(self, rec: PhotoRecord):
        if self._busy:
            return
        dlg = PhotoLightbox(self, self.timeline.visible_records, rec, self.pal)
        dlg.exec()
        self.timeline.setFocus()

    def open_preview(self, kind: str):
        if not self.records or not self.root:
            return
        dlg = OperationDialog(self, self.records, self.root, kind, self.store, self.pal)
        outcome = dlg.exec()
        # Reflect the dialog's saved checkbox state without overwriting folder state.
        self.dry.blockSignals(True)
        self.exif.blockSignals(True)
        self.dry.setChecked(self.store.bool_value(DRY_KEY, True))
        self.exif.setChecked(self.store.bool_value(EXIF_KEY, False))
        self.dry.blockSignals(False)
        self.exif.blockSignals(False)
        if outcome != QDialog.DialogCode.Accepted or not dlg.execute_requested:
            return
        self._messages = []
        items, settings = dlg.items, dlg.settings
        self.run_task('Applying confirmed changes…',
            lambda cancel, progress, message: actions.execute(items, settings, cancel, progress, message),
            self.change_complete)

    def change_complete(self, result):
        journal, succeeded, failed = result
        self.last_journal = journal
        self.status.setText(f'{succeeded:,} completed; {failed:,} skipped')
        detail = '\n'.join(self._messages[:10]) if self._messages else 'No reported errors.'
        QMessageBox.information(self, 'Operation finished',
            f'Completed: {succeeded:,}\nFailed: {failed:,}\nUndo journal:\n{journal}\n\n{detail}\n\n'
            'Rescan to see changes.')

    def choose_undo(self):
        initial = str(self.last_journal or actions.HISTORY_ROOT)
        path, _ = QFileDialog.getOpenFileName(self, 'Choose undo journal', initial,
                                              'Undo journals (*.jsonl);;All files (*)')
        if not path:
            return
        journal = Path(path)
        try:
            entries, undone = actions._read_journal(journal)
            n = sum(e['begin']['id'] not in undone for e in entries)
        except Exception as exc:
            QMessageBox.warning(self, 'Invalid journal', str(exc))
            return
        if n == 0:
            QMessageBox.information(self, 'Nothing to undo', 'This journal contains no pending changes.')
            return
        if QMessageBox.question(self, 'Confirm undo',
                f'Undo up to {n:,} completed operations?\n'
                'Files edited since the operation will not be overwritten or deleted.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        self._messages = []
        self.run_task('Reverting changes…',
            lambda cancel, progress, message: actions.undo(journal, cancel, progress, message),
            self.undo_complete)

    def undo_complete(self, result):
        restored, skipped = result
        self.status.setText(f'Undo: {restored} completed; {skipped} skipped')
        QMessageBox.information(self, 'Undo complete',
            f'Undone: {restored:,}\nSkipped: {skipped:,}\n\n' +
            ('\n'.join(self._messages[:12]) if self._messages else 'No conflicts.'))

    def cancel_active(self):
        if self.cancel_event is not None:
            self.cancel_event.set()
            self.status.setText('Stopping after the current file…')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < 1010
        compact = self.width() < 780
        self.brand.setVisible(not compact)
        self.filter_btn.setVisible(not narrow)
        self.scan_btn.setText('' if narrow else 'Rescan')
        self.organize_btn.setText('' if compact else 'Organize')
        self.undo_btn.setText('' if compact else 'Undo')
        self.exif.setVisible(not compact)
        self.manual_btn.setText('' if compact else 'Correct date')
        self.open_btn.setText('' if compact else 'Open')
        self.search.setPlaceholderText('Search' if narrow else 'Search filenames')

    def closeEvent(self, event):
        if self.active_thread and self.active_thread.isRunning():
            self.cancel_active()
            QMessageBox.information(self, 'Operation in progress',
                'Stop requested. Leave the window open while the current file finishes.')
            event.ignore()
            return
        self.store.save_geometry(self.saveGeometry())
        super().closeEvent(event)
