@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PHOTO VIEWER - Run
pushd "%~dp0"
if errorlevel 1 (
    echo ERROR: Could not open project folder. Extract the ZIP first.
    goto :error
)
if not exist "main.py" (
    echo ERROR: main.py is missing. Extract the entire ZIP first.
    goto :error
)
if not exist "requirements.txt" (
    echo ERROR: requirements.txt is missing. Extract the entire ZIP first.
    goto :error
)
if exist ".venv\Scripts\python.exe" goto :ready
set "PY_BOOT="
where py >nul 2>&1
if errorlevel 1 goto :try_path
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY_BOOT=py -3.12"
if defined PY_BOOT goto :create
py -3.13 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY_BOOT=py -3.13"
if defined PY_BOOT goto :create
py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY_BOOT=py -3.11"
if defined PY_BOOT goto :create
:try_path
where python >nul 2>&1
if errorlevel 1 goto :no_python
python -c "import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1
if not errorlevel 1 set "PY_BOOT=python"
if not defined PY_BOOT goto :no_python
:create
echo Creating Python environment...
%PY_BOOT% -m venv .venv
if errorlevel 1 goto :error
:ready
echo Installing required packages...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --prefer-binary -r requirements.txt
if errorlevel 1 goto :error
echo Starting PHOTO VIEWER...
".venv\Scripts\python.exe" main.py
if errorlevel 1 goto :error
popd
exit /b 0
:no_python
echo ERROR: Install Python 3.12 ^(64-bit^) from python.org and retry.
:error
echo.
echo Startup failed. Review the error messages above.
popd
pause
exit /b 1
