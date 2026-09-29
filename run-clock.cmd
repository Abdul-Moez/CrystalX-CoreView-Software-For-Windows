@echo off
REM Copyright (C) 2026 Abdul Moez (https://github.com/Abdul-Moez)
REM SPDX-License-Identifier: GPL-3.0-or-later -- see the LICENSE file.
REM
REM Rooftop GIF with clock, date and system stats overlaid. Any extra
REM arguments are passed straight through, e.g.
REM   run-clock.cmd --slots cpu,gpu,ram%%,vram
REM
REM Close the vendor LCD Control.exe first: only one program can use the panel.
cd /d "%~dp0"
if not exist "venv-win\Scripts\python.exe" (
    echo venv-win is missing. Double-click setup.cmd first.
    pause
    exit /b 1
)

REM CPU temperature needs administrator rights, as LCD Control does. If this
REM window isn't elevated, ask Windows (the usual Yes/No prompt) and hand
REM over to an elevated copy of this file. Answering No carries on here
REM without them, and CPU temperature shows --.
fltmc >nul 2>&1 && goto elevated
set "CLOCK_CMD=%~f0"
set "CLOCK_ARGS=%*"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { if ($env:CLOCK_ARGS) { Start-Process -FilePath $env:CLOCK_CMD -ArgumentList $env:CLOCK_ARGS -Verb RunAs -ErrorAction Stop } else { Start-Process -FilePath $env:CLOCK_CMD -Verb RunAs -ErrorAction Stop }; exit 0 } catch { exit 1 }"
if not errorlevel 1 exit /b 0
echo Running without administrator rights -- CPU temperature will show --.

:elevated
venv-win\Scripts\python.exe clock_win.py "retro_pixel_guy_smoking_on_rooftop.gif" %*
REM Keep the window open on an error so it can be read.
if errorlevel 1 pause
