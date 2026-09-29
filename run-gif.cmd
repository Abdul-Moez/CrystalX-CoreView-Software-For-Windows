@echo off
REM Copyright (C) 2026 Abdul Moez (https://github.com/Abdul-Moez)
REM SPDX-License-Identifier: GPL-3.0-or-later -- see the LICENSE file.
REM
REM Known-good fallback: plain GIF, no overlay, at the settings confirmed
REM working on 2026-09-22.
REM
REM If clock_win.py ever misbehaves, run this. It proves the panel, the COM
REM port and the frame format are all fine, and narrows the fault to the
REM overlay.
cd /d "%~dp0"
if not exist "venv-win\Scripts\python.exe" (
    echo venv-win is missing. Double-click setup.cmd first.
    pause
    exit /b 1
)
venv-win\Scripts\python.exe lcd_win.py "retro_pixel_guy_smoking_on_rooftop.gif" --width 320 --fps 10 %*
REM Keep the window open on an error so it can be read.
if errorlevel 1 pause
