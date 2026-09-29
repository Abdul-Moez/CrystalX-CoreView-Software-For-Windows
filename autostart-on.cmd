@echo off
REM Copyright (C) 2026 Abdul Moez (https://github.com/Abdul-Moez)
REM SPDX-License-Identifier: GPL-3.0-or-later -- see the LICENSE file.
REM
REM Start the clock at every login: hidden, with administrator rights (for CPU
REM temperature) and without a prompt -- the same way LCD Control starts
REM itself. Setting that up needs administrator rights once, so Windows asks.
REM Run it again after moving this folder. autostart-off.cmd undoes it.
cd /d "%~dp0"
if not exist "venv-win\Scripts\pythonw.exe" (
    echo venv-win is missing. Double-click setup.cmd first.
    pause
    exit /b 1
)
fltmc >nul 2>&1 && goto elevated
set "CLOCK_CMD=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath $env:CLOCK_CMD -Verb RunAs -ErrorAction Stop; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    echo Autostart needs administrator rights. Nothing was changed.
    pause
)
exit /b

:elevated
set "CLOCK_DIR=%~dp0"
REM The task starts pythonw.exe (no console window) in this folder, at logon
REM of the current user, with highest privileges. Scheduled tasks stop after
REM 3 days by default; the clock should never stop, so the limit is removed.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d = $env:CLOCK_DIR.TrimEnd('\'); $who = \"$env:USERDOMAIN\$env:USERNAME\"; $a = New-ScheduledTaskAction -Execute \"$d\venv-win\Scripts\pythonw.exe\" -Argument 'clock_win.py retro_pixel_guy_smoking_on_rooftop.gif' -WorkingDirectory $d; $t = New-ScheduledTaskTrigger -AtLogOn -User $who; $p = New-ScheduledTaskPrincipal -UserId $who -LogonType Interactive -RunLevel Highest; $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero); Register-ScheduledTask -TaskName 'CrystalX LCD' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null"
if errorlevel 1 goto failed
echo.
echo Autostart is on. The clock will start by itself at your next login.

REM LCD Control starts itself at login through its own task; if it does too,
REM the two fight over the screen.
powershell -NoProfile -Command "if ((Get-ScheduledTask -TaskName 'LCD ControlPowerBoot' -ErrorAction SilentlyContinue).State -eq 'Ready') { exit 2 }"
if errorlevel 2 (
    echo.
    echo LCD Control is also set to start at login, and only one program can
    echo use the screen.
    choice /c YN /m "Turn off LCD Control's autostart"
    if not errorlevel 2 (
        powershell -NoProfile -Command "Disable-ScheduledTask -TaskName 'LCD ControlPowerBoot' | Out-Null"
        echo LCD Control's autostart is off. It still works if you open it yourself.
    )
)
echo.
pause
exit /b 0

:failed
echo.
echo Could not create the autostart task -- see the message above.
pause
exit /b 1
