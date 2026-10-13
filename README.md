# Photo Viewer

**Photo Viewer** is a local, Windows-first photo timeline for recovering original capture dates after copying pictures from Google Photos, a phone, or an external drive. It also runs on macOS and Linux using PySide6. Files are not uploaded to a server.

## Quick start (Windows)

1. Extract this ZIP to a writable folder.
2. If needed, install [Python 3.12 (64-bit)](https://www.python.org/downloads/) (the `py` launcher is optional; the scripts also support `python` on PATH).
3. Double-click **`run_windows.bat`**. On first launch this creates `.venv` and installs Python dependencies, which requires internet access.
4. Click **Change** beside the photo folder path; select your source folder. The scan starts automatically. The app remembers the folder and automatically rescans on its next launch.
5. Browse photos grouped by **Year > Month**, choose **Newest first / Oldest first**, search names, filter **Review uncertain dates**, or click a thumbnail for a larger viewer (←/→, Esc). **MP4 and MOV play inside the viewer** with Play/Pause, a seek bar, volume mute, and automatic stopping when switching files. The left timeline sidebar collapses using the upper-left button.
6. To correct one photo's date, select it, adjust the date/time in the bottom inspector, and choose **Correct date**. A before/after review appears; its confirmation changes the in-memory preview only.
7. To write dates or reorganize, click **Fix dates** or **Organize**. Choose an output directory with **Change destination** the first time you use copies or moves. Review the before/after plan and leave **Dry run** on until you are satisfied. Real operations require a separate confirmation.
8. To revert an operation, choose **Undo** and select its `.jsonl` journal file. Files changed after the operation are protected from automatic undo.

> **Keep a backup** before moving files or editing originals in place. Journals help reverse completed operations but cannot fully protect against an external drive failure or an unexpected power loss.

## Build the standalone Windows EXE

Double-click **`build_windows.bat`**. The script installs required packages, runs tests and packages the app using the included **`PhotoViewer.spec`** with its bundled SVGs and app icon. Output:

```text
dist\PhotoViewer.exe
```

This `.exe` is self-contained and does not need Python installed on the target PC. It must be compiled **on Windows**; PyInstaller cannot cross-compile a Windows executable from Linux/macOS. An unsigned personal app can trigger Windows SmartScreen.

### If `build_windows.bat` closes, fails, or does not create `dist`

1. **Extract All** from the downloaded ZIP into a normal local folder (for example, `Documents\PhotoViewer`); do not run the `.bat` directly from inside the ZIP preview. Confirm `main.py`, `requirements.txt`, and `PhotoViewer.spec` are next to `build_windows.bat`.
2. Install **Python 3.12 (64-bit)** if needed. Internet access is required the first time to install packages. The scripts accept the `py` launcher **or** `python` on PATH.
3. Double-click **`build_windows.bat`**. The updated file uses Windows CRLF line endings and **pauses on both success and failure**. It prints the build stage and writes the complete diagnostic log to **`build_windows.log`** (next to the batch file).
4. If the window still vanishes, open the project folder in Explorer, click the **address bar**, type **`cmd`** and press Enter. In the Command Prompt type `build_windows.bat` and press Enter. The existing console stays open even if Windows aborts the script.
5. If `build_windows.log` reports an error in `.venv`, close the app, delete **only `.venv`**, then retry. If pip fails, check the last entries of the log and confirm Python/network access. If a test or PyInstaller fails, the log identifies that exact stage. The build intentionally stops on test errors instead of creating an untested executable.
6. Once complete, look for **`dist\PhotoViewer.exe`**. Windows Defender or other security software can quarantine unsigned local executables; consult its quarantine history if a successful build later disappears.

A build failure should **not** modify photos or saved folder preferences. The `.venv`, `build`, and `dist` folders are generated build artifacts and can be recreated. Do not delete your photo folders or `PhotoViewerHistory` undo journals.

**Optional cloud Windows build:** push this project to a GitHub repository, then open **Actions → Build Windows EXE → Run workflow**. Download the `PhotoViewer-Windows` artifact created by `.github/workflows/build-windows.yml`.

**Video support:** MP4 and MOV playback uses Qt Multimedia (PySide6) inside the viewer. The build includes the `imageio-ffmpeg` helper executable for video thumbnails and a fallback reader for common embedded `creation_time` metadata. You no longer need to install FFmpeg separately just to show video thumbnails. Installing `ffprobe.exe` beside `PhotoViewer.exe` or on PATH is optional and improves video metadata extraction. The Qt playback backend supports common H.264/AAC MP4 files; HEVC/H.265 and some MOV codecs depend on the Windows/Qt decoder available. When a format cannot be decoded, the viewer shows an explanation and an **Open in default app** button. Videos are never transcoded or modified merely by previewing them.


## Scan stability, removed files, and crash diagnostics

This update fixes the scan completion and thumbnail cross-thread dispatch: all timeline and `QPixmap` updates now run through explicitly **queued GUI-thread slots**. Completed thumbnail jobs are tagged with a scan generation, so old jobs cannot reinsert removed images after a rescan. Files removed between discovery and metadata extraction are safely skipped. The photo selection is cleared when a new scan finishes, and errors in the completion callback are logged instead of leaving the worker thread running.

If Windows closes Photo Viewer unexpectedly, open **`%LOCALAPPDATA%\PhotoViewer`** in File Explorer (paste it into the address bar). The two useful files are:

- `PhotoViewer.log` — normal scan activity, Python errors, and error details
- `PhotoViewer_crash.log` — best-effort native stack trace, even from the windowed EXE

These log files contain paths to your photographs, so remove/redact personal folder names before sharing them publicly. They are independent of your photo library and your undo journals.

If the automatic scan prevents the app from opening, open Command Prompt in the extracted project directory and run **`run_windows.bat --no-auto-scan`**. You can then choose a different folder or press Rescan manually. When running the standalone EXE, use **`PhotoViewer.exe --no-auto-scan`** from a Command Prompt in `dist`. The saved source/destination folder settings remain untouched. On the following regular launch, auto-scanning resumes normally.

To update an older executable, extract this version into a clean folder and run **`build_windows.bat`**. Replace the old `.exe` with the newly built `dist\PhotoViewer.exe`; **do not delete `%LOCALAPPDATA%\PhotoViewer`** or the QSettings registry location. The earlier undo journals are unchanged.

## Design system

All interface colors live in **`photoviewer/theme.py`**, which defines `LIGHT`, `DARK`, the semantic date-source colors, and generates one shared Qt stylesheet (`qss`). The QPainter-based grid reads those same tokens; there are **no separate thumbnail color constants** to maintain. Windows system light/dark appearance is used by default; **Settings → Appearance** can override it.

| Token | Light | Dark |
| --- | --- | --- |
| Background | `#FFFFFF` | `#14161B` |
| Surface | `#F6F7F9` | `#1D2027` |
| Border | `#E3E6EB` | `#2C303A` |
| Text | `#1F2430` | `#ECEEF2` |
| Secondary text | `#6B7280` | `#9AA1AE` |
| Accent | `#3B82F6` | `#60A5FA` |

Semantic colors: **reliable** `#22C55E` (EXIF/video metadata/JSON), **inferred** `#3B82F6` (filename), **unreliable** `#F59E0B` (filesystem date), and **destructive** `#EF4444` (moves/higher-risk operations). Chips contain a *text label or symbol* as well as color. Fonts are Segoe UI, 18px section headings, 14px controls and 12px badges. A consistent 8px layout rhythm, rounded 8–12px cards, lightweight borders and simple 160–180ms easing are used throughout. Source SVG icons are shipped under `photoviewer/assets/icons/`.

The thumbnail timeline is **virtualized**: layouts are computed without allocating a QWidget for every image. Only visible thumbnails are loaded on background threads and a bounded 300-image in-memory cache is used. The adaptive square grid, sticky year/month context, and selected/highlighted states are custom drawn.

## Saved folders and preferences

Photo Viewer uses **QSettings** with organization `PhotoViewer` and application `Photo Viewer`. On Windows, native-format preferences are stored in the current user's Registry under:

```text
HKEY_CURRENT_USER\Software\PhotoViewer\Photo Viewer
```

| Setting | Key | Default |
| --- | --- | --- |
| Source photo folder | `folders/source` | Unset |
| Output destination | `folders/destination` | Unset |
| Window geometry | `window/geometry` | 1230 × 800 |
| Newest first | `timeline/newest_first` | True |
| Theme | `appearance/theme` | `system` |
| Sidebar expanded | `appearance/sidebar_expanded` | True |
| Dry run | `safety/dry_run` | True |
| Write missing JPEG EXIF | `safety/write_exif` | False |

**Only an explicit Change photo folder / Change destination chooser writes a folder setting.** Neither restoring the window, scanning, rescanning, a preview, undo, dragging files nor running operations writes a source/destination setting. Cancelling the chooser does not change the saved path. Missing or unplugged directories stay remembered; the top bar displays an inline **Folder not found. Choose a new one?** prompt, instead of silently switching folders. For a missing output directory, the operation dialog requires you to explicitly choose another destination before previewing.

Settings are migrated **once** from legacy organization/application variants and common previous key names if the new key is still missing. New values are never overwritten and old settings are never deleted. An older release did not persist QSettings at all, so there may be nothing to import for users of that release. **Settings → Reset saved settings** asks for confirmation, clears preferences, and marks migration complete so previous values do not reappear on restart. It does **not** touch your photos or undo journals.

Qt stores analogous user preferences in its native storage on macOS or in the user's XDG configuration directory on Linux. To change a theme color, edit `LIGHT` or `DARK` in `photoviewer/theme.py`, then rerun the app or rebuild the executable.

## Date priority and accuracy

The scanner supports `.jpg`, `.jpeg`, `.png`, `.heic`, `.webp`, `.mp4`, `.mov` recursively. For each media file, it tries:

1. **EXIF DateTimeOriginal**, then embedded DateTimeDigitized/CreateDate. For videos, ffprobe reads QuickTime/MP4 creation tags when available. UI chip **✓ EXIF**.
2. **Google Takeout JSON**: `filename.ext.json`, `filename.json`, and common supplemental names; reads `photoTakenTime.timestamp`. UI chip **✓ JSON**.
3. **Filename** patterns such as `IMG_20240102_030405`, `PXL_20240102_030405123`, `2024-01-02 03.04.05`, `Screenshot_20240102-030405`, `IMG-20240102-WA1234`. UI chip **⌕ Name**. A WhatsApp filename with only a calendar date uses `00:00:00` and is flagged as having unknown time.
4. **File modified/creation date** as an explicitly **unreliable** fallback. UI chip **! File date**.

EXIF/filename capture dates without timezone information retain the local wall-clock time. JSON UTC timestamps and offset-aware video dates use the computer's local timezone. Check ambiguous captures (especially around midnight) before writing them to disk. A date encoded into a filename is not independently authenticated.

## Safety and Undo

- **Dry run defaults ON**, and **working on copies** is the default for either operation. Turning off dry run and clicking the confirm button in the modal are both required before making changes.
- **Fix dates** sets filesystem modified time and Windows creation time; on Linux/macOS, birth/creation timestamps are generally not editable. Optional writing of missing `DateTimeOriginal` supports **JPEG only**, and does not replace an existing JPEG capture tag.
- **Organize** can copy or move into `YYYY/MM`, rename to `YYYY-MM-DD_HHMMSS`, or both. Collision-safe suffixes prevent overwriting; destination reservation is exclusive. Output directories cannot be inside the scanned source directory or a parent of it.
- Files with unreliable dates are excluded from operation plans unless deliberately included or corrected manually.
- Each confirmed real operation creates `photo_viewer_job.json` and `photo_viewer_journal.jsonl` in `~/PhotoViewerHistory/<job-id>/` (plus JPEG backups where necessary). Undo checks file size, timestamps and/or content fingerprints to avoid trampling modifications made afterward. Keep that history folder until you no longer need undo.
- Undo can read an *older journal with a different filename* as long as its contents have the expected JSONL structure; choose it through **Undo** and the all-files filter.
- Google Takeout sidecars are read but **not** moved/copied with organized media, so keep the original Takeout export. A later rescan of organized media may no longer have the JSON capture source.
- Corrupt/unreadable media is skipped or shown with a placeholder and a warning, without terminating a multi-thousand-file scan.

## Developer setup / tests

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
python -m unittest discover -s tests -v
```

On CI/headless machines, use `QT_QPA_PLATFORM=offscreen` before running Qt tests. The core tests don't need Qt; the real-QSettings tests are automatically skipped if PySide6 is not installed.

```text
main.py                          Launcher and QApplication identity
PhotoViewer.spec                 PyInstaller executable and bundled icons
photoviewer/
  models.py                      Media records and formats
  scanner.py                     Recursive scanning and progress
  date_resolvers.py              EXIF, Takeout, filename, video metadata
  actions.py                     Preview plans, copy/move, undo logs
  preferences.py                 QSettings, legacy migration, folder policy
  theme.py                       Light/dark tokens, semantic chips, stylesheet
  ui.py                          Responsive window, timeline, dialogs, preview
  assets/icons/                  Bundled SVG icon set
  assets/PhotoViewer.ico         Windows taskbar/executable icon
requirements.txt
run_windows.bat                   Launch and install dependencies
build_windows.bat                 Tests + Windows EXE builder
tests/                          Core + saved-folder + Qt integration tests
.github/workflows/               Optional Windows CI build
```

## Rename audit

- Application window title, About entry, QApplication name and organization: **Photo Viewer** / `PhotoViewer`.
- Python import package: `photoviewer`.
- PyInstaller spec and executable: `PhotoViewer.spec` / `dist\PhotoViewer.exe`.
- Windows run and build scripts, GitHub artifact/build workflow, README, package description and LICENSE attribution updated.
- New history folder and journal names: `PhotoViewerHistory`, `photo_viewer_journal.jsonl`, `photo_viewer_job.json`.
- Preferences use a new application identity while preserving supported legacy keys through one-time migration.

**Known limitations:** Unsupported EXIF formats are never rewritten; a manual correction affects the session until Fix dates is confirmed; duplicate file content is not deduplicated. Undo is best-effort across crashes or externally edited files, so backups are still recommended. Windows EXE builds have to be validated on Windows.
