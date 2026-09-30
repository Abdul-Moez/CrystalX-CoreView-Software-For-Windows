# Running the scripts instead of the app

Most people should use the installer — see the [README](../README.md). This
page is for running the project straight from its source files instead: no
installer, no service, just Python and a few double-clickable scripts.

> **Don't use both.** If the app is installed, its service already drives the
> screen; the scripts would fight it for the COM port. Uninstall the app first,
> or stop the display from its window.

- [What you need](#what-you-need)
- [Setup, step by step](#setup-step-by-step)
- [Everyday use](#everyday-use)
- [Start automatically with Windows](#start-automatically-with-windows-optional)
- [Change what the screen shows](#change-what-the-screen-shows-optional)
- [If something goes wrong](#if-something-goes-wrong)

---

## What you need

- **A CrystalX CoreView V-950 case**, with the LCD's USB cable plugged into the
  motherboard, as it comes from the factory.
- **Windows 10 or 11**, 64-bit.
- **Python 3.10 or newer — but not 3.13.0**, which has a bug that opens a
  black window when the clock starts with Windows. Step 1 below shows how to
  install it. Tested with Python 3.13.5.
- **An internet connection**, only during setup.
- **For the CPU temperature only: PawnIO 2.2.0**, a free driver, installed once
  from **https://pawnio.eu/** — see [Step 4](#step-4--install-pawnio-for-cpu-temperature).
  Everything else works without it.

---

## Setup, step by step

You only do this once. It takes about five minutes.

### Step 1 — Install Python

Skip this step if you already have Python 3.10 or newer (other than 3.13.0 —
`setup.cmd` tells you if yours won't work).

1. Go to **https://www.python.org/downloads/windows/** and download the latest
   **Windows installer (64-bit)**.
2. Run it. On the first screen, tick **"Add python.exe to PATH"**, then click
   **Install Now**.

### Step 2 — Download this project

1. At the top of the project page on GitHub, click the green **Code** button,
   then **Download ZIP**.
2. Right-click the downloaded ZIP file and choose **Extract All…**.
3. Put the extracted folder somewhere permanent with a short path, for example
   `C:\Users\<you>\Documents\crystalx-lcd`. Don't run it from inside the ZIP,
   and don't move the folder later if you set up autostart.

If you use git, `git clone` works just as well.

### Step 3 — Run setup

Open the folder and double-click **`setup.cmd`**.

A black window opens and installs the libraries the project needs. When it
says **"Setup complete"**, press any key to close it.

If Windows asks whether you want to run the file, choose **Run** (or
**More info → Run anyway**). It only runs the setup described here.

### Step 4 — Install PawnIO (for CPU temperature)

Skip this if you don't need the CPU temperature — it will just show `--`.

Windows only lets drivers read the CPU's temperature sensor, so this uses
**PawnIO**, a small free driver signed by Microsoft (the same one
LibreHardwareMonitor uses).

1. Go to **https://pawnio.eu/** and download the installer (version 2.2.0 was
   tested).
2. Run it and click through. Windows asks for administrator permission — click
   **Yes**.

### Step 5 — Close the vendor's LCD Control app

Only one program can drive the screen at a time.

1. If **LCD Control** is running, close it. If you can't find its window, look
   for its icon in the system tray (bottom-right, near the clock), or end
   **LCD Control** in Task Manager.
2. LCD Control usually starts itself at every login and takes the screen back.
   See [Stop LCD Control starting with Windows](../README.md#stop-lcd-control-starting-with-windows)
   to turn that off.

### Step 6 — Start it

Double-click **`run-clock.cmd`**.

Windows asks **"Do you want to allow this app to make changes to your
device?"** — click **Yes**. Reading the CPU temperature needs administrator
rights, just like LCD Control. If you click **No**, it runs anyway and the CPU
temperature shows `--`.

Within a few seconds the case screen shows the GIF with the clock, date and
stats. **Keep the black window open** — you can minimise it. Closing it stops
the display, and the screen goes blank a moment later.

That's it.

---

## Everyday use

| To… | Do this |
|---|---|
| Start the clock and stats | Double-click `run-clock.cmd` |
| Stop it | Close its black window (or press `Ctrl+C` in it) |
| Show just the GIF, no clock or stats | Double-click `run-gif.cmd` |
| Go back to the vendor's app | Stop this first, then open LCD Control |

---

## Start automatically with Windows (optional)

This starts the clock every time you log in — hidden, with the temperatures,
and **without the admin prompt**. It's the same trick LCD Control uses to start
itself.

**To turn it on:** double-click **`autostart-on.cmd`** and click **Yes** on the
Windows prompt (setting it up needs administrator rights once). It takes
effect from your next login.

If LCD Control is also set to start at login, it asks whether to turn that off
— press **Y**, or the two will fight over the screen. LCD Control still works
if you open it yourself later.

**To turn it off:** double-click **`autostart-off.cmd`** and click **Yes**. It
also stops the hidden clock straight away.

- If you move the project folder, run `autostart-on.cmd` again from the new
  place.
- To stop the hidden clock just for now, end **pythonw.exe** in Task Manager
  (Details tab). It comes back at your next login.
- While the hidden clock runs, `run-clock.cmd` can't start — the screen is
  already in use.

---

## Change what the screen shows (optional)

The bottom box shows seven readings by default: CPU usage and temperature, GPU
usage and temperature, RAM usage, disk usage (how busy the drives are) and
network speed. You can pick others.

1. Right-click **`run-clock.cmd`** and choose **Edit** (it opens in Notepad).
2. Find the line starting with `venv-win\Scripts\python.exe clock_win.py`.
3. Add `--slots` and the readings you want at the end of that line, separated by
   commas. For example:

   ```
   venv-win\Scripts\python.exe clock_win.py "retro_pixel_guy_smoking_on_rooftop.gif" --slots cpu,clock,ram,vram,uptime %*
   ```

   Inside this file, write a percent sign twice: `ram%%` for RAM usage in
   percent, `disk%%` for disk usage.

4. Save, close the black window if it's running, and double-click
   `run-clock.cmd` again.

| Name | Shows |
|---|---|
| `cpu` | CPU usage in % |
| `cputemp` | CPU temperature (needs PawnIO and the admin prompt answered Yes) |
| `clock` | Current CPU speed, including boost |
| `gpu` | GPU usage in % |
| `gputemp` | GPU temperature |
| `vram` | Video memory in use |
| `ram` | RAM in use, in GB |
| `ram%` | RAM in use, in % |
| `disk%` | How hard the drives are working right now |
| `diskio` | Drive read + write speed |
| `storage` | Space used across all drives, in % |
| `disk` | Free space on the Windows drive |
| `net` | Download / upload speed together |
| `netdown`, `netup` | Download or upload speed on its own |
| `procs` | Number of running programs and processes |
| `uptime` | Time since Windows started |

Readings update every 2 seconds. They fill the box two per row, and an odd one
at the end is centred. A `-` leaves a spot empty (its partner moves to the
middle), and `--columns 1` puts one reading on each row, with bigger numbers.

**Using your own picture:** add `--fit fill` (crop to cover the screen),
`--fit blur` (whole picture, blurred edges) or `--fit color --fit-color #203040`
(whole picture, solid edges) and put your file name in place of the rooftop
GIF. `--zoom 2 --pan-x 0` zooms in and shows the left side.

Everything the app's window can change works here too — 24-hour time
(`--time-format 24h`), seconds, the date format (`--date-format "YYYY-MM-DD"`),
sizes, colours, positions, renamed stats, fonts. See
[Command-line options](HOW-IT-WORKS.md#command-line-options).

---

## If something goes wrong

**"Python 3.10 or newer was not found"** (from `setup.cmd`)
Python isn't installed, or "Add python.exe to PATH" wasn't ticked. Do
[Step 1](#step-1--install-python) again, then run `setup.cmd` again.

**"Python 3.13.0 was found, but it has a bug…"** or **"The venv-win folder
was made with Python 3.13.0…"** (from `setup.cmd`)
Install the latest Python from python.org, delete the `venv-win` folder if the
message says so, and run `setup.cmd` again.

**A black window appears at login, and the screen stays black**
Your `venv-win` was made with Python 3.13.0 (see the message above). Clicking
into that window pauses the clock — press **Esc** to get it going again, then
fix it for good by installing a newer Python, deleting `venv-win` and running
`setup.cmd` again.

**"venv-win is missing. Double-click setup.cmd first."**
Setup hasn't been run in this folder yet. Do [Step 3](#step-3--run-setup).

**"COM5 is busy -- close LCD Control.exe"** (the number may differ)
Another program is using the screen — usually LCD Control, the installed app,
or another copy of these scripts (including the autostart one). Close it, or
end **LCD Control** / **Python** / **pythonw.exe** in Task Manager, then try
again.

**"panel 33c3:f101 not found -- is the case plugged in?"**
Windows can't see the screen. Check the LCD's USB cable is connected to the
motherboard. In Device Manager, under **Ports (COM & LPT)**, you should see
**USB Serial Device (COMx)**.

**The screen shows the picture for a moment, then goes blank**
The program stopped. The screen only shows a picture while something keeps
sending it. Start `run-clock.cmd` again and read any message in its window.

**The picture is torn or scrambled**
The picture being sent is too big — this happens if you changed `--width`
without `--fit`. Go back to the default settings.

**CPU Temp shows `--`**
The first line in the black window says why. Usually one of these:
- **"needs administrator rights"** — you clicked No on the Windows prompt.
  Close the window and start `run-clock.cmd` again.
- **"needs the PawnIO driver"** — do [Step 4](#step-4--install-pawnio-for-cpu-temperature).

**"temperatures unavailable: … run setup.cmd again"**
The temperature parts aren't installed or are blocked by Windows — common
after downloading a new version. Double-click `setup.cmd` again.

**A reading shows `--`**
That reading isn't available on your PC (for example, no GPU counters), or it's
the first two seconds after starting — speed readings need two samples.

**Autostart is on but nothing shows after login**
Something else took the screen first — usually LCD Control starting itself.
See [Stop LCD Control starting with Windows](../README.md#stop-lcd-control-starting-with-windows).

**Nothing on the screen and no error**
Try `run-gif.cmd`. If the plain GIF works, the problem is in the clock and
stats part, not the connection to the screen.
