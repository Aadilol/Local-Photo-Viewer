@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PHOTO VIEWER - Windows EXE Builder

rem Always run from the extracted project root, even if started elsewhere.
pushd "%~dp0"
if errorlevel 1 (
    echo ERROR: Cannot open the project folder.
    echo Please extract the entire ZIP to a local writable folder first.
    goto :early_failure
)

set "LOG=%CD%\build_windows.log"
>"%LOG%" echo PHOTO VIEWER - Windows build log
>>"%LOG%" echo Project folder: %CD%
>>"%LOG%" ver

echo.
echo ===========================================================
echo       PHOTO VIEWER - Build Windows Executable
echo ===========================================================
echo.
echo Project: %CD%
echo Log:     %LOG%
echo.

if not exist "main.py" (
    echo ERROR: main.py was not found. Extract the ENTIRE ZIP first.
    >>"%LOG%" echo ERROR: main.py missing.
    goto :failure
)
if not exist "requirements.txt" (
    echo ERROR: requirements.txt was not found. Extract the ENTIRE ZIP first.
    >>"%LOG%" echo ERROR: requirements.txt missing.
    goto :failure
)
if not exist "PhotoViewer.spec" (
    echo ERROR: PhotoViewer.spec was not found. Extract the ENTIRE ZIP first.
    >>"%LOG%" echo ERROR: PhotoViewer.spec missing.
    goto :failure
)

rem Reuse an existing virtual environment if it works.
if exist ".venv\Scripts\python.exe" goto :check_environment

rem Prefer Python 3.12, then 3.13/3.11; also support a python.exe on PATH.
set "PY_BOOT="
where py >nul 2>&1
if errorlevel 1 goto :try_python_path
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY_BOOT=py -3.12"
if defined PY_BOOT goto :create_environment
py -3.13 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY_BOOT=py -3.13"
if defined PY_BOOT goto :create_environment
py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY_BOOT=py -3.11"
if defined PY_BOOT goto :create_environment

:try_python_path
where python >nul 2>&1
if errorlevel 1 goto :no_python
python -c "import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1
if not errorlevel 1 set "PY_BOOT=python"
if not defined PY_BOOT goto :no_python

:create_environment
echo [1/4] Creating Python virtual environment...
echo [1/4] Creating environment with %PY_BOOT% >>"%LOG%"
%PY_BOOT% -m venv .venv >>"%LOG%" 2>&1
if errorlevel 1 (
    echo ERROR: Could not create the virtual environment.
    goto :failure
)

:check_environment
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 1)" >>"%LOG%" 2>&1
if errorlevel 1 (
    echo ERROR: The existing .venv uses an unsupported or broken Python.
    echo Close all PHOTO VIEWER windows, delete the .venv folder, and retry.
    echo Install Python 3.12 x64 if you have no supported version.
    goto :failure
)

".venv\Scripts\python.exe" -c "import sys; print('Python:',sys.version); print('Executable:',sys.executable)" >>"%LOG%" 2>&1

echo [2/4] Installing dependencies... this may take a few minutes.
echo [2/4] Installing dependencies >>"%LOG%"
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --prefer-binary -r requirements.txt >>"%LOG%" 2>&1
if errorlevel 1 (
    echo ERROR: Dependency installation failed.
    echo Check internet connectivity and the details in build_windows.log.
    goto :failure
)

echo [3/4] Running automated tests...
echo [3/4] Running tests >>"%LOG%"
set "QT_QPA_PLATFORM=offscreen"
".venv\Scripts\python.exe" -m unittest discover -s tests -v >>"%LOG%" 2>&1
if errorlevel 1 (
    echo ERROR: Tests failed, so packaging was stopped.
    echo See build_windows.log for the failing test and traceback.
    goto :failure
)
set "QT_QPA_PLATFORM="

echo [4/4] Building PhotoViewer.exe with PyInstaller...
echo [4/4] Packaging executable >>"%LOG%"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean PhotoViewer.spec >>"%LOG%" 2>&1
if errorlevel 1 (
    echo ERROR: PyInstaller could not complete the build.
    goto :failure
)

if not exist "dist\PhotoViewer.exe" (
    echo ERROR: PyInstaller returned success but dist\PhotoViewer.exe is missing.
    >>"%LOG%" echo ERROR: Expected EXE is missing.
    goto :failure
)

echo.
echo ===========================================================
echo BUILD SUCCESSFUL!
echo Executable: "%CD%\dist\PhotoViewer.exe"
echo ===========================================================
echo.
>>"%LOG%" echo SUCCESS: dist\PhotoViewer.exe created.
popd
pause
exit /b 0

:no_python
echo ERROR: A supported Python installation was not found.
echo Install Python 3.12 ^(64-bit^) from https://www.python.org/downloads/
echo Make sure you include the Python Launcher, then run this file again.
>>"%LOG%" echo ERROR: No compatible Python 3.11, 3.12, or 3.13 found.
goto :failure

:failure
echo.
echo ===========================================================
echo BUILD FAILED. No executable was created.
echo Full diagnostic log: "%LOG%"
echo ===========================================================
echo.
echo Last part of the build log:
where powershell >nul 2>&1
if errorlevel 1 (
    type "%LOG%"
) else (
    powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 35" 2>nul
)
echo.
echo Tip: Open this folder, type cmd into Explorer's address bar,
echo press Enter, and run build_windows.bat to keep the window open.
echo.
popd
pause
exit /b 1

:early_failure
pause
exit /b 1
