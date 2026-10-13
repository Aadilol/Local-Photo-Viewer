# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-file build. Run on Windows for dist/PhotoViewer.exe."""
from PyInstaller.utils.hooks import collect_all

heif_datas, heif_bins, heif_hidden = collect_all('pillow_heif')
video_datas, video_bins, video_hidden = collect_all('imageio_ffmpeg')
analysis = Analysis(
    ['main.py'],
    pathex=[],
    binaries=heif_bins + video_bins,
    datas=heif_datas + video_datas + [('photoviewer/assets', 'photoviewer/assets')],
    hiddenimports=heif_hidden + video_hidden + ['PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, analysis.binaries, analysis.datas,
    [], name='PhotoViewer', icon='photoviewer/assets/PhotoViewer.ico', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
)
