@echo off
REM Rooftop GIF with clock, date and system stats overlaid. Windows equivalent
REM of run-clock.sh. Any extra arguments are passed straight through, e.g.
REM   run-clock.cmd --slots cpu,gpu,ram%%,vram
REM
REM Close the vendor LCD Control.exe first: only one program can hold COM5.
cd /d "%~dp0"
venv-win\Scripts\python.exe clock_win.py "retro_pixel_guy_smoking_on_rooftop.gif" %*
