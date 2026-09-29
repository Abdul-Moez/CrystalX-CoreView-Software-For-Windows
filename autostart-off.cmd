@echo off
REM Copyright (C) 2026 Abdul Moez (https://github.com/Abdul-Moez)
REM SPDX-License-Identifier: GPL-3.0-or-later -- see the LICENSE file.
REM
REM Undo autostart-on.cmd: stop the hidden clock if it is running and remove
REM the login task. Needs administrator rights, so Windows asks.
fltmc >nul 2>&1 && goto elevated
set "CLOCK_CMD=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath $env:CLOCK_CMD -Verb RunAs -ErrorAction Stop; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    echo Removing autostart needs administrator rights. Nothing was changed.
    pause
)
exit /b

:elevated
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t = Get-ScheduledTask -TaskName 'CrystalX LCD' -ErrorAction SilentlyContinue; if (-not $t) { exit 2 }; Stop-ScheduledTask -TaskName 'CrystalX LCD'; Unregister-ScheduledTask -TaskName 'CrystalX LCD' -Confirm:$false"
if errorlevel 2 (
    echo Autostart was not on. Nothing to remove.
) else if errorlevel 1 (
    echo Could not remove the autostart task -- see the message above.
) else (
    echo Autostart is off, and the hidden clock has been stopped.
)
echo.
pause
