# Photo Viewer — scan stability and video playback update

## Changes

- Scans and thumbnail workers now deliver results through explicitly queued Qt GUI-thread slots, never receiverless cross-thread lambdas.
- Scan generations discard outdated thumbnail completions; removed files are ignored during scanning and never restored by a late result.
- Selection and year/month navigation are cleared when scan results are replaced.
- Exception guards prevent a Python error in the completion callback from leaving the thread running; persistent Windows log and native crash log are written under `%LOCALAPPDATA%\PhotoViewer`.
- Click MP4/MOV thumbnails to play videos inside the preview. Play/Pause, seek, mute, arrow navigation and Esc/Close are supported.
- `imageio-ffmpeg` now supplies an FFmpeg helper for MP4/MOV thumbnails and basic video capture-date metadata even if no system FFmpeg is installed. `ffprobe.exe` on PATH is optional for fuller metadata extraction.
- The PyInstaller spec bundles imageio-ffmpeg and QtMultimedia. `dist\PhotoViewer.exe` remains the build output.
- Optional `--no-auto-scan` launch flag allows a problematic photo folder to be changed without starting an automatic scan; does not erase QSettings.

## Upgrade

1. Extract the entire ZIP, replacing the **project source** (do not delete your actual photo folders or undo journals).
2. Run `build_windows.bat` from the extracted `PhotoViewer` folder; it installs the added imageio-ffmpeg dependency, runs tests, and builds `dist\PhotoViewer.exe`.
3. Quit the old running app and use the new executable. The source/destination folders and other QSettings preferences are preserved.
4. For crash diagnostics use `%LOCALAPPDATA%\PhotoViewer\PhotoViewer.log` and `PhotoViewer_crash.log`.

## Remaining limits

- Codec support is subject to Qt's multimedia backend on the running PC; unsupported MOV/HEVC footage may need the **Open in default app** button.
- Building and running the actual Windows EXE requires Windows and a working Python/PySide6 installation. This source release was syntax-checked and tested in Linux; Qt GUI tests were unavailable there.
