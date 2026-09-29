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
venv-win\Scripts\python.exe clock_win.py "retro_pixel_guy_smoking_on_rooftop.gif" %*
REM Keep the window open on an error so it can be read.
if errorlevel 1 pause
