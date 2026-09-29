@echo off
REM Copyright (C) 2026 Abdul Moez (https://github.com/Abdul-Moez)
REM SPDX-License-Identifier: GPL-3.0-or-later -- see the LICENSE file.
REM
REM One-time setup: creates the venv-win folder and installs the libraries
REM listed in requirements.txt into it. Safe to run again; it only installs
REM what is missing.
cd /d "%~dp0"

REM Exit code 0 = usable, 2 = older than 3.10, 3 = exactly 3.13.0.
REM 3.13.0 is refused: in a venv made by it, pythonw.exe starts the console
REM python.exe (CPython issue #126084, fixed in 3.13.1), so autostart opens a
REM black window, and clicking into it freezes the clock.
set "CHECK=import sys; v = sys.version_info[:3]; sys.exit(3 if v == (3, 13, 0) else 0 if v >= (3, 10) else 2)"

REM Reuse an existing venv-win, unless 3.13.0 made it.
if not exist "venv-win\Scripts\python.exe" goto create
"venv-win\Scripts\python.exe" -c "%CHECK%" >nul 2>&1
if %errorlevel% EQU 3 goto venv3130
goto install

:create
REM Prefer the "py" launcher that the python.org installer adds; fall back to
REM "python". The check also rules out the Microsoft Store placeholder, which
REM exists on every Windows install but is not a real Python.
set "PY="
set "FOUND3130="
call :try py -3
if not defined PY call :try python
if defined PY goto makevenv
if defined FOUND3130 goto python3130
echo.
echo Python 3.10 or newer was not found.
echo Install it from https://www.python.org/downloads/windows/ and tick
echo "Add python.exe to PATH" during the install, then run setup.cmd again.
echo.
pause
exit /b 1

:makevenv
echo Creating venv-win ...
%PY% -m venv venv-win
if errorlevel 1 goto failed

:install
echo Installing libraries from requirements.txt ...
"venv-win\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed

REM Files extracted from a downloaded ZIP carry Windows' "from the internet"
REM mark, and .NET refuses to load DLLs that have it. Clear it on the bundled
REM LibreHardwareMonitor DLLs so the temperature readings can load.
echo Unblocking the bundled temperature libraries ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath 'lib' -Recurse -File | Unblock-File"
if errorlevel 1 goto failed

echo.
echo Setup complete.
echo For CPU temperature, also install PawnIO from https://pawnio.eu/
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

:python3130
echo.
echo Python 3.13.0 was found, but it has a bug that opens a black window when
echo the clock starts with Windows. Install the latest Python from
echo https://www.python.org/downloads/windows/ (any version from 3.10 on,
echo except 3.13.0), then run setup.cmd again.
echo.
pause
exit /b 1

:venv3130
echo.
echo The venv-win folder was made with Python 3.13.0, which has a bug that
echo opens a black window when the clock starts with Windows.
echo 1. Install the latest Python from https://www.python.org/downloads/windows/
echo 2. Delete the venv-win folder in this project.
echo 3. Run setup.cmd again.
echo.
pause
exit /b 1

REM Try one way of starting Python: set PY if it is usable, FOUND3130 if it
REM is 3.13.0. "Not found" (9009) and too old leave both unset.
:try
%* -c "%CHECK%" >nul 2>&1
set "RC=%errorlevel%"
if "%RC%"=="0" set "PY=%*"
if "%RC%"=="3" set "FOUND3130=1"
exit /b
