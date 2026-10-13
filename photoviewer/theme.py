"""Single source of truth for Photo Viewer colors, typography and Qt styling.

All QSS and custom QPainter views use Palette tokens from this module.
Change LIGHT/DARK values here; no widget needs a color edit.
"""
from __future__ import annotations
from dataclasses import dataclass
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    background: str
    surface: str
    border: str
    text: str
    secondary: str
    accent: str
    reliable: str = '#22C55E'
    inferred: str = '#3B82F6'
    unreliable: str = '#F59E0B'
    danger: str = '#EF4444'
    # legible foreground on the semantic tint (badges are NOT semantic text alone)
    semantic_foreground: str = '#14161B'
    image_placeholder: str = ''
    accent_hover: str = ''
    pressed: str = ''
    selection: str = ''
    tooltip: str = ''
    accent_hover_ink: str = ''


LIGHT = Palette('#FFFFFF', '#F6F7F9', '#E3E6EB', '#1F2430', '#6B7280', '#3B82F6',
                image_placeholder='#EDF0F4', accent_hover='#2563EB', pressed='#1D4ED8',
                selection='#DBEAFE', tooltip='#F6F7F9', accent_hover_ink='#FFFFFF')
DARK = Palette('#14161B', '#1D2027', '#2C303A', '#ECEEF2', '#9AA1AE', '#60A5FA',
               image_placeholder='#292E38', accent_hover='#93C5FD', pressed='#3B82F6',
               selection='#22344F', tooltip='#1D2027', accent_hover_ink='#14161B')


def system_is_dark() -> bool:
    app = QApplication.instance()
    if app is None:
        return False
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def palette(preference: str = 'system') -> Palette:
    dark = system_is_dark() if preference == 'system' else preference == 'dark'
    return DARK if dark else LIGHT


def source_style(source: str, pal: Palette) -> tuple[str, str]:
    """(short glyph/text, semantic color) with accessible non-color labels."""
    if source == 'EXIF':
        return '✓ EXIF', pal.reliable
    if source == 'Video':
        return '✓ Video', pal.reliable
    if source == 'JSON':
        return '✓ JSON', pal.reliable
    if source == 'Filename':
        return '⌕ Name', pal.inferred
    if source == 'Manual':
        return '✓ Edited', pal.reliable
    return '! File date', pal.unreliable


def qss(p: Palette) -> str:
    """Tokens -> stylesheet; no widget-local color literals."""
    return f'''
    * {{ font-family: "Segoe UI", "Arial"; font-size: 14px; color: {p.text}; }}
    QMainWindow, QDialog, QWidget {{ background: {p.background}; }}
    QWidget#Sidebar, QFrame#Inspector, QFrame#Toolbar, QFrame#Card, QFrame#InfoPane,
    QFrame#Banner, QFrame#EmptyCard {{ background: {p.surface}; border-radius: 10px; }}
    QFrame#TopBar {{ background: {p.background}; border-bottom: 1px solid {p.border}; }}
    QLabel#AppTitle {{ font-size: 17px; font-weight: 600; }}
    QLabel#Muted, QLabel#Hint, QLabel#Counter, QLabel#PathLabel {{ color: {p.secondary}; }}
    QLabel#SectionTitle {{ font-size: 18px; font-weight: 600; }}
    QLabel#Warning {{ background: {p.surface}; border-left: 3px solid {p.unreliable};
                      color: {p.text}; border-radius: 4px; padding: 9px 12px; }}
    QPushButton {{ padding: 8px 12px; border: 1px solid {p.border};
                   border-radius: 9px; background: {p.surface}; font-weight: 500; }}
    QPushButton:hover {{ border-color: {p.accent}; background: {p.selection}; }}
    QPushButton:pressed {{ background: {p.pressed}; color: {p.background}; }}
    QPushButton:checked {{ border-color: {p.accent}; background: {p.selection}; }}
    QPushButton:focus {{ border: 2px solid {p.accent}; }}
    QPushButton:disabled {{ color: {p.secondary}; background: {p.surface}; border-color: {p.border}; }}
    QPushButton[role="primary"] {{ background: {p.accent}; color: {p.semantic_foreground}; border-color: {p.accent}; font-weight: 600; }}
    QPushButton[role="primary"]:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; color: {p.accent_hover_ink}; }}
    QPushButton[role="primary"]:pressed {{ background: {p.pressed}; color: {p.background}; }}
    QPushButton[role="primary"]:disabled {{ background: {p.border}; color: {p.secondary}; }}
    QPushButton[role="destructive"] {{ color: {p.text}; border-color: {p.danger}; }}
    QPushButton[role="destructive"]:hover {{ background: {p.danger}; color: {p.semantic_foreground}; }}
    QPushButton[role="destructive"]:pressed {{ background: {p.pressed}; }}
    QLineEdit, QComboBox, QDateTimeEdit, QSpinBox {{ padding: 7px 10px;
        border: 1px solid {p.border}; border-radius: 8px; background: {p.surface}; selection-background-color: {p.accent}; }}
    QLineEdit:focus, QComboBox:focus, QDateTimeEdit:focus {{ border: 2px solid {p.accent}; }}
    QLineEdit:disabled, QComboBox:disabled, QDateTimeEdit:disabled {{ color: {p.secondary}; }}
    QComboBox::drop-down {{ border: 0; width: 22px; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox:focus {{ border: 1px solid {p.accent}; }}
    QTreeWidget {{ background: {p.surface}; border: none; outline: 0; padding: 5px; }}
    QTreeWidget::item {{ height: 30px; border-radius: 6px; padding-left: 5px; }}
    QTreeWidget::item:hover {{ background: {p.selection}; }}
    QTreeWidget::item:selected {{ background: {p.selection}; color: {p.text}; }}
    QTreeWidget::item:focus {{ border: 1px solid {p.accent}; }}
    QHeaderView::section {{ background: {p.surface}; border: 0; border-bottom: 1px solid {p.border}; padding: 9px; font-weight: 600; }}
    QTableWidget {{ gridline-color: {p.border}; alternate-background-color: {p.surface};
                    background: {p.background}; border: 1px solid {p.border}; border-radius: 8px;
                    selection-background-color: {p.selection}; selection-color: {p.text}; }}
    QTableWidget::item {{ padding: 7px; }}
    QTableWidget::item:focus {{ border: 1px solid {p.accent}; }}
    QProgressBar {{ border: 0; background: {p.surface}; border-radius: 3px; min-height: 5px; max-height: 5px; text-align: center; }}
    QProgressBar::chunk {{ background: {p.accent}; border-radius: 3px; }}
    QScrollBar:vertical {{ background: transparent; width: 9px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {p.border}; min-height: 30px; border-radius: 4px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QMenu {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 9px; padding: 6px; }}
    QMenu::item {{ padding: 8px 22px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {p.selection}; }}
    QToolTip {{ background: {p.tooltip}; color: {p.text}; border: 1px solid {p.border}; padding: 5px; }}
    '''


def color(code: str, alpha: int = 255) -> QColor:
    c = QColor(code)
    c.setAlpha(alpha)
    return c
