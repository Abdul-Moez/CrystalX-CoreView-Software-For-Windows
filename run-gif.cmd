@echo off
REM Known-good fallback: plain GIF, no overlay, at the settings confirmed
REM working on 2026-09-22.
REM
REM If clock_win.py ever misbehaves, run this. It proves the panel, the COM
REM port and the frame format are all fine, and narrows the fault to the
REM overlay.
cd /d "%~dp0"
venv-win\Scripts\python.exe lcd_win.py "retro_pixel_guy_smoking_on_rooftop.gif" --width 320 --fps 10 %*
