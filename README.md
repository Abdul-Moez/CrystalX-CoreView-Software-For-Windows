# CrystalX CoreView LCD for Windows

Show an animated GIF with a clock, the date and live PC stats (CPU, GPU, RAM,
drives, network) on the LCD screen of a **CrystalX CoreView V-950** PC case —
without the vendor's LCD Control app.

This is an unofficial project. It is not made by or affiliated with CrystalX or
the makers of LCD Control.

- [What you need](#what-you-need)
- [Setup, step by step](#setup-step-by-step)
- [Everyday use](#everyday-use)
- [Start automatically with Windows](#start-automatically-with-windows-optional)
- [Change what the screen shows](#change-what-the-screen-shows-optional)
- [If something goes wrong](#if-something-goes-wrong)
- [Want to know how it works?](#want-to-know-how-it-works)
- [License](#license)

---

## What you need

- **A CrystalX CoreView V-950 case**, with the LCD's USB cable plugged into the
  motherboard, as it comes from the factory.
- **Windows 10 or 11**, 64-bit.
- **Python 3.10 or newer.** Step 1 below shows how to install it. Tested with
  Python 3.13.
- **An internet connection**, only during setup.

> **Coming soon — CPU and GPU temperatures.** When temperature readings are
> added, they will also need the free **PawnIO 2.2.0** driver, installed once
> from **https://pawnio.eu/**. You don't need it yet.

---

## Setup, step by step

You only do this once. It takes about five minutes.

### Step 1 — Install Python

Skip this step if you already have Python 3.10 or newer.

1. Go to **https://www.python.org/downloads/windows/** and download the latest
   **Windows installer (64-bit)**.
2. Run it. On the first screen, tick **"Add python.exe to PATH"**, then click
   **Install Now**.

### Step 2 — Download this project

1. At the top of this page on GitHub, click the green **Code** button, then
   **Download ZIP**.
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

### Step 4 — Close the vendor's LCD Control app

Only one program can drive the screen at a time.

1. If **LCD Control** is running, close it. If you can't find its window, look
   for its icon in the system tray (bottom-right, near the clock), or end
   **LCD Control** in Task Manager.
2. If it starts with Windows, turn off its auto-start setting so it doesn't
   take the screen back after a restart.

### Step 5 — Start it

Double-click **`run-clock.cmd`**.

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

**The "Welcome" screen at power-on** is built into the screen itself and can't
be changed. It shows until something starts sending pictures, then goes blank
if nothing does. Autostart (below) makes the clock take over at login.

---

## Start automatically with Windows (optional)

This starts the clock every time you log in, with no black window.

1. Open the project folder in File Explorer.
2. Click the address bar at the top, type `cmd` and press Enter. A black window
   opens, already in the project folder.
3. Paste this and press Enter:

   ```cmd
   schtasks /create /tn "CrystalX LCD" /sc onlogon /rl limited /f /tr "\"%cd%\venv-win\Scripts\pythonw.exe\" \"%cd%\clock_win.py\" \"%cd%\retro_pixel_guy_smoking_on_rooftop.gif\""
   ```

   It should say `SUCCESS`. No administrator rights are needed.

It takes effect from your next login. To stop the hidden clock without logging
out, end **pythonw.exe** in Task Manager (Details tab).

To remove autostart, paste this the same way:

```cmd
schtasks /delete /tn "CrystalX LCD" /f
```

- If you get **"cannot be more than 261 character(s)"**, the folder path is too
  long. Move the folder somewhere shorter, such as `C:\crystalx-lcd`, and run
  the command again.
- If you move the folder later, run the create command again from the new
  place.
- Make sure LCD Control doesn't also start with Windows, or the two will fight
  over the screen.

---

## Change what the screen shows (optional)

The bottom box shows five readings by default: CPU usage, GPU usage, RAM usage,
drive activity and network speed. You can pick others.

1. Right-click **`run-clock.cmd`** and choose **Edit** (it opens in Notepad).
2. Find the line starting with `venv-win\Scripts\python.exe clock_win.py`.
3. Add `--slots` and the readings you want at the end of that line, separated by
   commas. For example:

   ```
   venv-win\Scripts\python.exe clock_win.py "retro_pixel_guy_smoking_on_rooftop.gif" --slots cpu,clock,ram,vram,uptime %*
   ```

   Inside this file, write a percent sign twice: `ram%%` for RAM usage in
   percent, `disk%%` for drive activity.

4. Save, close the black window if it's running, and double-click
   `run-clock.cmd` again.

| Name | Shows |
|---|---|
| `cpu` | CPU usage in % |
| `clock` | Current CPU speed, including boost |
| `gpu` | GPU usage in % |
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
at the end is centred.

**Using your own GIF, fonts, sizes or frame rate:** see
[Command-line options](docs/HOW-IT-WORKS.md#command-line-options). Be careful
with bigger pictures: the screen can only take about 472,000 pixels per frame,
and anything larger comes out scrambled.

---

## If something goes wrong

**"Python 3.10 or newer was not found"** (from `setup.cmd`)
Python isn't installed, or "Add python.exe to PATH" wasn't ticked. Do
[Step 1](#step-1--install-python) again, then run `setup.cmd` again.

**"venv-win is missing. Double-click setup.cmd first."**
Setup hasn't been run in this folder yet. Do [Step 3](#step-3--run-setup).

**"COM5 is busy -- close LCD Control.exe"** (the number may differ)
Another program is using the screen — usually LCD Control, sometimes another
copy of this project (including the autostart one). Close it, or end
**LCD Control** / **Python** / **pythonw.exe** in Task Manager, then try again.

**"panel 33c3:f101 not found -- is the case plugged in?"**
Windows can't see the screen. Check the LCD's USB cable is connected to the
motherboard. In Device Manager, under **Ports (COM & LPT)**, you should see
**USB Serial Device (COMx)**.

**The screen shows the picture for a moment, then goes blank**
The program stopped. The screen only shows a picture while something keeps
sending it. Start `run-clock.cmd` again and read any message in its window.

**The picture is torn or scrambled**
The picture being sent is too big — this happens if you changed `--width` or
used another GIF. Go back to the default settings.

**A reading shows `--`**
That reading isn't available on your PC (for example, no GPU counters), or it's
the first two seconds after starting — speed readings need two samples.

**Nothing on the screen and no error**
Try `run-gif.cmd`. If the plain GIF works, the problem is in the clock and
stats part, not the connection to the screen.

---

## Want to know how it works?

- **[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md)** — the protocol, the
  screen's hardware limits and how they were found, how each reading is
  measured, what the vendor app revealed, and what didn't work.
- **[AGENTS.md](AGENTS.md)** — rules for AI coding assistants working on this
  project.

---

## License

Copyright © 2026 **Abdul Moez** — [github.com/Abdul-Moez](https://github.com/Abdul-Moez)

This project is free software under the
**[GNU General Public License v3.0](LICENSE)** (or any later version).

In plain words — you may use, copy, change and share it, including for
commercial purposes, as long as:

- **you credit the original author** — keep the name and GitHub link above in
  every copy and modified version (an additional term under section 7(b) of
  the license, stated at the top of each code file);
- **you share your changes under GPL-3.0 as well**, with their source code, if
  you distribute a modified version;
- you keep the license and copyright notices intact.

The [LICENSE](LICENSE) file is the legally binding text; this summary is only a
guide.
