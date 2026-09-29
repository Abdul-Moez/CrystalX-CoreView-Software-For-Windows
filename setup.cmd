@echo off
REM Copyright (C) 2026 Abdul Moez (https://github.com/Abdul-Moez)
REM SPDX-License-Identifier: GPL-3.0-or-later -- see the LICENSE file.
REM
REM One-time setup: creates the venv-win folder and installs the libraries
REM listed in requirements.txt into it. Safe to run again; it only installs
REM what is missing.
cd /d "%~dp0"

REM Prefer the "py" launcher that the python.org installer adds; fall back to
REM "python". The version check also catches the Microsoft Store placeholder,
REM which exists on every Windows install but is not a real Python.
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo.
    echo Python 3.10 or newer was not found.
    echo Install it from https://www.python.org/downloads/windows/ and tick
    echo "Add python.exe to PATH" during the install, then run setup.cmd again.
    echo.
    pause
    exit /b 1
)

if not exist "venv-win\Scripts\python.exe" (
    echo Creating venv-win ...
    %PY% -m venv venv-win
    if errorlevel 1 goto failed
)

echo Installing libraries from requirements.txt ...
"venv-win\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed

echo.
echo Setup complete.
echo Close LCD Control if it is running, then double-click run-clock.cmd.
echo.
pause
exit /b 0

:failed
echo.
echo Setup failed -- see the messages above. Check your internet connection
echo and try again.
echo.
pause
exit /b 1
