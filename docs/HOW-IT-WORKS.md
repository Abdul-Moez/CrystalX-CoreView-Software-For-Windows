# How it works

The technical side of this project: how the panel is driven, the hardware limits
that were found by trial and error, how each reading is measured, and what did
not work. **If you just want the screen running, go back to the
[README](../README.md).**

**Read this before changing any code.** The panel has several undocumented
quirks. They are written down so nobody has to rediscover them.

- [Hardware](#hardware)
- [The protocol](#the-protocol)
- [What the panel does on its own](#what-the-panel-does-on-its-own)
- [Things that must not change](#things-that-must-not-change)
- [Things that are safe to change](#things-that-are-safe-to-change)
- [Why a COM port is all it takes](#why-a-com-port-is-all-it-takes)
- [What the vendor app revealed](#what-the-vendor-app-revealed)
- [Files](#files)
- [Command-line options](#command-line-options)
- [Stats slots](#stats-slots)
- [Temperatures](#temperatures)
- [The app: service, window and installer](#the-app-service-window-and-installer)
- [Making a release](#making-a-release)
- [Admin rights and autostart (scripts)](#admin-rights-and-autostart-scripts)
- [Performance](#performance)
- [Approaches that did not work](#approaches-that-did-not-work)

---

## Hardware

| | |
|---|---|
| Device | `33c3:f101` — "HL VMAX", sold as CrystalX CoreView V-950 |
| USB name | `HL-VMAX-USB-Device`, firmware revision 1.30 (`REV_0130`) |
| Vendor software | `lcd.crystalx.io` — LCD Control (Windows only, .NET/WPF, by SOEYI Technology / Hailian Zhixin) |
| Panel | 480 × 1920, portrait, mounted **upside down** in the case |
| Enumerates as | CDC-ACM composite → `USB Serial Device (COMx)` via `usbser.sys` |
| Transport | writes to that COM port = USB bulk, interface 1, endpoint `0x02` |

---

## The protocol

Each frame is a plain JPEG with a 12-byte header, written to the port. There is
no handshake and no authentication, and the panel never acknowledges a frame.

The panel is not completely silent, though: the vendor app asks it for a serial
number when it connects, and the panel answers. The request bytes are unknown
(see [What the vendor app revealed](#what-the-vendor-app-revealed)). Displaying
frames does not need it, so our scripts never ask and discard anything the
panel sends.

### The 12-byte header

Little-endian, prepended to every JPEG:

| Offset | Size | Field | Value |
|---|---|---|---|
| `0x00` | 4 | magic | `0x0008100A` |
| `0x04` | 2 | width | width of the JPEG being sent |
| `0x06` | 2 | height | height of the JPEG being sent |
| `0x08` | 2 | stride | `0` |
| `0x0A` | 2 | flag | `1` |

Width and height describe **the image you are sending**, not the panel.

In Python: `struct.pack("<IHHHH", 0x0008100A, w, h, 0, 1)`

### Sending a frame

1. Find the COM port by `VID:PID=33C3:F101`
2. Open it with **all flow control off**
3. Write `header + jpeg_bytes`
4. Repeat, forever

---

## What the panel does on its own

**The "Welcome" screen at power-on comes from the panel's own firmware.** The
panel is powered over USB as soon as the PC turns on, shows its built-in
Welcome image and waits for a program to start streaming frames. If nothing
arrives, it switches itself off after a while. It also goes blank shortly after
frames stop (see constraint 4).

**The Welcome image and the timeout cannot be changed from software**, as far
as anyone has found. The vendor app has no command for either (see below), so
they are fixed in the firmware. The only thing you can do is start streaming
earlier — the app's service takes over as soon as Windows starts, before
anyone logs in.
Nothing can run before Windows starts, so Welcome always shows briefly.

---

## Things that must not change

These were each found the hard way. Changing any of them breaks the display in
ways that look like random corruption and are very hard to diagnose.

The first four are properties of the **panel** itself. The rest are about how
Windows and Python talk to it.

### 1. Images are rotated 180° before encoding

The panel is physically mounted upside down. Every frame goes through
`transpose(Image.ROTATE_180)` **before** being encoded to JPEG. The panel does
not do this for you.

### 2. Never send more than ~472,000 pixels per frame

This is the single most important constraint and the least obvious.

"Pixels" means width × height of the frame being sent — the total number of
dots in the picture. The panel is 480 × 1920 (921,600 px), but **it cannot
decode a frame that large**. Sending a full-panel image produces a flickering,
torn, scrambled mess.

Measured on this hardware:

| Size | Pixels | Result |
|---|---|---|
| 234 × 1079 | 252,486 | clean |
| 300 × 1383 | 414,900 | clean |
| 320 × 1476 | 472,320 | clean — **what we use, and the maximum** |
| 324 × 1494 | 484,056 | corrupt |
| 328 × 1512 | 495,936 | corrupt |
| 332 × 1531 | 508,292 | corrupt |
| 334 × 1540 | 514,360 | corrupt |
| 480 × 1920 | 921,600 | badly corrupt |

The ceiling lies somewhere between **472,320 (clean) and 484,056 (corrupt)**.
For the rooftop GIF that makes **320 px the widest usable width** — the height
follows the width to keep the GIF's shape, so even 4 px wider already breaks.
Widths 321–323 were not tested; they would gain at most 3 px, which is not
worth it.

The 324–332 rows were measured on 2026-09-29, after this section had claimed a
~500k limit. The method: a labelled test pattern (grid, corner-to-corner
diagonals, coloured corners, numbered bands, with the size printed on it) cycled
through 320/324/328/332/334 on the real panel while someone watched and reported
which looked right. Only 320 was clean. **Do not repeat this experiment hoping
for more width — it has been done.**

> **Known gap:** `MAX_PIXELS = 500_000` in `lcd_win.py` was set before the
> 324–332 rows were measured, so it is looser than the real ceiling. The scripts
> refuse to run above 500,000 px, but **widths 324–329 pass that check and still
> show a scrambled screen.** Treat 320 as the maximum regardless of what the
> check allows. Lowering `MAX_PIXELS` to about `475_000` would close the gap.
> **Never raise it.**

### 3. The panel does not scale images up

A smaller image is displayed at its actual size, centred, with the rest of the
panel black. This is why we use 320 × 1476 — the largest image that both fits
under the pixel limit and looks reasonably large on the panel.

Because the source GIF is 234 × 1079 (aspect 1 : 4.61) and the panel is 1 : 4,
the image can never fill the panel completely without cropping or distorting it.

### 4. The panel needs a continuous stream

**A single frame flashes and disappears.** This cost a debugging round: the
first test wrote one frame, the port closed, and the panel went blank within a
second. It was not a transport failure — the frame arrived and was displayed.

The panel blanks shortly after writes stop. `lcd_win.py` and `clock_win.py`
both loop forever for this reason. `--once` exists only for diagnostics.

### 5. Flow control must be off

Frames are binary JPEG. Software flow control would eat any `0x11` or `0x13`
byte in the stream, and hardware flow control would stall forever on a device
that drives none of those lines. `Panel.__init__` sets `xonxoff=False`,
`rtscts=False`, `dsrdtr=False` explicitly. **Do not remove them.**

The line rate (115200) is ignored — there is no real UART behind a CDC ACM
endpoint — but Windows requires a valid value.

### 6. The port is found by VID:PID, never by number

Windows assigns COM numbers per physical USB port, so the panel moves from COM5
to something else the moment it is plugged into a different header.
`find_port()` locates it by `VID:PID=33C3:F101`. Pass `--port COMx` to override.

### 7. Only one process may drive the panel at a time

Opening the port fails with "Access is denied" if something else holds it. Stop
the vendor app, the autostart task or any other running copy before starting a
script by hand.

### 8. Use `%#I`, not `%-I`, to drop leading zeros

`TIME_FORMATS` and the date codes (`DATE_CODES`) use `%#I` / `%#d` so the
clock reads `3:25`, not `03:25`. The `%-I` / `%-d` form found in many Python examples is not supported
on Windows and raises `ValueError: Invalid format string`.

---

## Things that are safe to change

- Font, font sizes, text positions, margins
- Which GIF is played, its FPS, JPEG quality
- Clock and date formats (`--time-format`, `--date-format`; `TIME_FORMATS`
  and `DATE_CODES` in `clock_win.py`)
- Which stats are shown (`--slots`) and how often they refresh
- Width, **downwards only** — 320 is the maximum for the rooftop GIF, even
  though `MAX_PIXELS` currently lets 324–329 through (see constraint 2)

---

## Why a COM port is all it takes

**The vendor's Windows software drives the panel through a COM port, not
through libusb.** So this project needs no libusb, no Zadig, no driver
replacement and no administrator rights.

The evidence:

| Check | Result |
|---|---|
| `libusb0.sys` on the system | **absent** — no driver file, no service, no filter |
| Device enumeration | plain CDC-ACM composite; interfaces 0+1 owned by Microsoft's `usbser.sys` |
| Children of the composite device | exactly one, `USB Serial Device (COM5)` |
| Opening COM5 while LCD Control.exe runs | `Access to the port 'COM5' is denied` |

That last line is the proof: the vendor app holds the COM port exclusively. It
ships a `libusb` folder in its install directory, but never installs or uses
that driver for this panel.

The device is a standard CDC-ACM composite:

- **interface 0** — CDC control
- **interface 1** — CDC data, bulk OUT endpoint `0x02`

Endpoint `0x02` on interface 1 is where the frame data goes. `usbser.sys` owns
that endpoint and exposes it as a COM port, so writing bytes to the port puts
them straight on the endpoint. Windows COM ports pass binary data through
untouched, as long as flow control is off (constraint 5).

---

## What the vendor app revealed

Inspected on 2026-09-29, LCD Control version 1.0.0.47 (.NET Framework 4.8).

- **Its code cannot be read.** An obfuscator encrypts every method body and
  every text string; only class and method names survive. So the exact bytes it
  sends beyond the frame format above are unknown, and this project was written
  from scratch instead.
- **The class that drives this panel is small.** `DLSDevice` can open the port,
  send frames, read the panel's serial number and reconnect. It has no command
  for a boot image, brightness or sleep timeout — which is why the Welcome
  screen and the timeout look fixed in the firmware.
- **The panel answers a serial-number request.** The app's log says
  "waiting 1 s to receive the SN" after opening the port. The serial is a
  string beginning with `VMAX`; the app uses it to keep per-panel settings.
- **It bundles an old LibreHardwareMonitor** (0.9.3) for its own sensor
  readouts. That version predates the switch to the PawnIO driver and uses
  WinRing0 instead (see [Temperatures](#temperatures)).
- **It always runs as administrator.** Its manifest says
  `requireAdministrator`, so starting it by hand shows the admin prompt.
- **It starts itself at login through a scheduled task**, `LCD ControlPowerBoot`
  (logon trigger, "run with highest privileges"). A task like that runs as
  admin with no prompt, which is why it can show temperatures at startup
  without asking. The `AutoStar` value in its settings file
  (`%APPDATA%\Hailian Zhixin (Shenzhen) Technology Co., Ltd\LCD Control\Config.json`)
  said `false` on the test machine while the task was active and running, so
  don't trust that file; check Task Scheduler.

---

## Files

| File | Purpose |
|---|---|
| `README.md` | Installing and using the app, for people who just want it running |
| `LICENSE` | GNU GPL v3.0, the project's license (see the README's License section) |
| `docs/HOW-IT-WORKS.md` | This file |
| `docs/SCRIPTS.md` | Running from source with the `.cmd` scripts instead of the app |
| `AGENTS.md`, `CLAUDE.md` | Rules for AI coding assistants working on the repo |
| **The engine** | |
| `lcd_win.py` | Plain player. Also defines `Panel`, `MAGIC`, `MAX_PIXELS`, `load_frames`, `fit_frame`, which `clock_win.py` imports. **The core driver — treat with care.** |
| `clock_win.py` | Overlay player: picture + clock + date + configurable stats. Its `main()` is also what the service runs, and its `preview()` draws the app's preview. |
| `temps_win.py` | CPU and GPU temperatures through LibreHardwareMonitor; starts the PawnIO driver when needed |
| `lib/LibreHardwareMonitor/` | The unmodified LibreHardwareMonitor 0.9.6 DLLs, their licenses, and `THIRD-PARTY-NOTICES.md` with sources and checksums |
| `retro_pixel_guy_smoking_on_rooftop.gif` | The default picture |
| **The app** | |
| `service_win.py` | The Windows service: runs the engine from boot, answers the window over a pipe |
| `tray_win.py` | The window and the notification-area icon |
| `ipc_win.py` | Shared by both: the version, names, paths and pipe protocol |
| `settings_win.py` | Shared by both: the display settings, their defaults and checks, the `clock_win` options they become, and saved layouts |
| `assets/` | The app icon and the script that draws it |
| `packaging/crystalx-lcd.spec` | PyInstaller recipe: both programs, one folder, one bundled Python |
| `packaging/installer.iss` | Inno Setup script for `CrystalX-LCD-Setup-<version>.exe` |
| `packaging/collect_licenses.py`, `packaging/licenses/` | Gathers the licence of everything bundled into the app's `licenses` folder |
| `packaging/requirements-build.txt` | Exact versions of the build-only tools |
| `.github/workflows/release.yml` | Builds the installer and publishes the GitHub release when a version tag is pushed |
| **The scripts** | |
| `requirements.txt` | The exact library versions the project is tested with |
| `setup.cmd` | One-time setup: creates `venv-win` and installs `requirements.txt` into it |
| `run-clock.cmd` | Runs `clock_win.py` with the known-good settings, asking for admin rights first |
| `run-gif.cmd` | Runs `lcd_win.py` — plain GIF, no overlay, no admin. **Known-good fallback.** |
| `autostart-on.cmd`, `autostart-off.cmd` | Create or remove the login task that runs the clock hidden, as admin, without a prompt |
| `venv-win/` | Python environment created by `setup.cmd`. Not in git. |

If you break `clock_win.py`, `run-gif.cmd` still works and proves the hardware,
the port and the frame format are all fine.

---

## Command-line options

Extra arguments to `run-clock.cmd` pass straight through to `clock_win.py`:

```cmd
run-clock.cmd --fps 15
run-clock.cmd --font segoeuib.ttf
run-clock.cmd --text-fill 0.9
run-clock.cmd --no-stats
run-clock.cmd --slots cpu,gpu,ram%,vram
```

For the full list:

```cmd
venv-win\Scripts\python.exe clock_win.py --help
```

Inside a `.cmd` file a `%` must be written twice, so `ram%` becomes `ram%%`
(see the comment at the top of `run-clock.cmd`). On the command line a single
`%` is fine.

Everything the app's window can set is an option here too. The app turns its
settings into exactly these options (`settings_win.engine_argv`):

| Option | What it does |
|---|---|
| `--fit fill\|blur\|color`, `--fit-color` | Place the picture on a panel-shaped canvas (see [Fitting any picture](#the-app-service-window-and-installer)) |
| `--zoom 1–4`, `--pan-x 0–1`, `--pan-y 0–1` | With `--fit`: enlarge the picture, and choose which part shows — 0 lines up its left (top) edge with the screen's, 1 its right (bottom) edge, 0.5 centres it |
| `--time-format 12h\|12h-plain\|24h`, `--seconds` | `3:25 PM`, `3:25` or `15:25`, optionally with seconds |
| `--date-format PATTERN` | The date, from the codes below (default `(ddd) D-MMM-YYYY`) |
| `--no-time`, `--no-date`, `--no-stats` | Leave that part out |
| `--time-size`, `--date-size`, `--label-size`, `--value-size` | Text sizes in px; each shrinks to fit the width if too big |
| `--time-color`, `--date-color`, `--label-color`, `--value-color` | Text colours (default white); the black outline always stays |
| `--clock-pos 0–1`, `--stats-pos 0–1` | Height of the clock block and the stats block, from top (0) to bottom (1) |
| `--slots`, `--columns 1\|2`, `--label SLOT=TEXT` | The stats, their layout and their names — see [Stats slots](#stats-slots) |
| `--font`, `--no-frost`, `--frost-dark 0–1`, `--frost-blur` | Font file, and the frosted panel behind the text |
| `--brightness 10–100` | Dim the whole screen to this percentage (see below) |
| `--calendar month\|week`, `--week-start mon\|sun`, `--calendar-size`, `--calendar-color` | A calendar in the clock block, under the date (or in its place with `--no-date`); today is marked |
| `--note-line TEXT` (repeatable) | Your own text, one option per line; long lines wrap |
| `--countdown-date YYYY-MM-DD[THH:MM]`, `--countdown-mode to\|to-hours\|since`, `--countdown-label` | A countdown to (or count-up from) a date — see [The extra blocks](#the-extra-blocks) |
| `--todo-title`, `--todo-item "[x] TEXT"` (repeatable) | A to-do list; items starting `[x] ` are done (ticked, crossed out and dimmed) |
| `--note-…`, `--countdown-…`, `--todo-…` `-size`, `-color`, `-align left\|center\|right`, `-pos 0–1`, `-no-frost` | Each extra block's size, colour, alignment, height and panel |

**Brightness is done in software.** The panel's backlight has no known
control: the vendor app's panel class has no brightness command (see
[What the vendor app revealed](#what-the-vendor-app-revealed)) and the device
has no other interface. So `--brightness` scales every pixel's colour
(`clock_win.dim`); the backlight stays at full, so it saves no power and black
stays the same black, but the screen looks dimmer. Tested on the panel at
100/70/40/15% on 2026-09-30: every level showed cleanly. It costs nothing per
frame: frames are dimmed once when they load (`prepare`) and the text once per
change (`text_bands`). Don't look for a hardware brightness by sending the
panel guessed commands — an unknown command could leave its firmware in a bad
state.

**Date codes.** Everything else in the pattern is printed as written; put text
in `[square brackets]` to keep a letter such as the D in `[Today]` from turning
into a day number.

| Code | Gives | Code | Gives |
|---|---|---|---|
| `ddd` | Wed | `dddd` | Wednesday |
| `D` | 5 | `DD` | 05 |
| `MMM` | Sep | `MMMM` | September |
| `M` | 9 | `MM` | 09 |
| `YY` | 26 | `YYYY` | 2026 |

Day and month names are always in English.

### Clock and date sizing

Both lines are **centred** and sized automatically: they grow until they span
`--text-fill` of the available width, so the clock and the date always come out
the same width as each other and scale together.

| `--text-fill` | clock | date |
|---|---|---|
| `1.0` | 59px | 28px |
| `0.92` | 53px | 26px |
| `0.88` | 51px | 24px |
| **`0.82`** | **48px** | **22px** — the default |
| `0.75` | 44px | 20px |

They are sized against the widest string each can ever produce: every hour of
the day for the clock, every day of a year for the date. So `12:00 PM` beats
`1:05 AM`, and a two-digit day beats the one-digit day `D` can produce, and
neither can outgrow the frosted panel at runtime.

`--time-size` and `--date-size` size either line on its own instead, up to the
full width. How big that can be depends on the format:

| Format | Largest that fits (Consolas Bold) |
|---|---|
| `12:00 PM`, or `23:59:59` | 59px |
| `12:00` (no AM/PM, or 24-hour) | 95px |
| `12:00:00 PM` | 42px |
| `(Wed) 30-Sep-2026` | 28px |
| `2026-09-30` | 48px |
| `Wednesday, 30 September 2026` | 17px |

The app's size sliders stop at exactly these limits (`Layout.limits`).

### The extra blocks

Besides the clock block and the stats, three blocks can be switched on: your
own **text**, a **countdown** and a **to-do list**. The clock block can also
hold a **calendar** (this month, or just this week) under the date or instead
of it.

- **Placement.** Unless given a `-pos`, they stack under the clock block in
  that order, 12 px apart (`BLOCK_GAP`); a block that is moved takes the
  unmoved ones after it along. Each has its own frosted panel, which can be
  switched off.
- **They cost nothing while running.** They only change at known moments, so
  they are drawn into the frames once, when the frames are prepared
  (`Layout.static`, `prepare`), not on every frame like the clock and the
  stats. Measured: a 6-line to-do list drawn this way made no measurable
  difference to CPU use and added about 1 MB and 0.4 s to preparing the
  frames; redrawing it with the stats every 2 seconds would have cost about
  0.3% of a core.
- **When they change.** The calendar and a countdown in days change at
  midnight; a countdown in days and hours changes when the hours figure drops.
  The layout records that moment (`valid_until`); when it comes, the player
  builds the frames afresh in the background and swaps them in, so the new day
  appears within a couple of seconds, without a pause.

The countdown reads *12 days to Launch*, then *Launch is today!* on the day,
and disappears after it (`to`); *12 days 5 h to Launch* (`to-hours`, to a date
and time); or *214 days since I quit* (`since`, shown from the date on).
Without a name: *12 days to go*, *Today!*, *214 days*.

The to-do list draws its boxes and ticks as shapes rather than characters, so
they look the same in any font. Ticked items are crossed out and dimmed.

### Tuning the layout without stopping the panel

`--preview` renders one frame to a PNG and exits **without opening the port**,
so the panel keeps running while you iterate:

```cmd
venv-win\Scripts\python.exe clock_win.py retro_pixel_guy_smoking_on_rooftop.gif --preview test.png --stats-y 1150
```

### A different GIF

```cmd
run-clock.cmd --width 280
```

To play another GIF, change the file name in `run-clock.cmd`. A GIF with a
different shape has a different maximum width. Pick a width where the startup
line reports **472,320 px or fewer**. Don't rely on the "over the pixel limit"
refusal: it only triggers above 500,000 px, and frames between ~480,000 and
500,000 px pass it and still come out scrambled (constraint 2).

Images and GIFs only. Video would need ffmpeg.

---

## Stats slots

`--slots` takes a comma-separated list, filling the grid **two per row**. An odd
one at the end is **centred across the full width**. `-` is an **empty spot**:
in a row with one, the other stat is centred, and a row with both empty is left
out, so the screen never shows a hole. The app always has seven spots (three
rows of two and one centred, `MAX_SPOTS`), each a reading or `-`.

`--columns 1` gives every stat its own centred row instead, which lets the
values grow to 68 px. `--label cputemp=CPU` renames a stat on screen; the name
counts towards the width like any other.

Default: `cpu,cputemp,gpu,gputemp,ram%,disk%,net`

```
CPU Usage       CPU Temp
4%                  47°C

GPU Usage       GPU Temp
8%                  51°C

RAM Usage     Disk Usage
53%                   0%

      Net D/U MB/s
        0.0/0.1
```

| Slot | Label | Example | Notes |
|---|---|---|---|
| `cpu` | CPU Usage | `6%` | |
| `cputemp` | CPU Temp | `47°C` | needs PawnIO and admin rights — see [Temperatures](#temperatures) |
| `gpu` | GPU Usage | `1%` | busiest GPU engine type, as Task Manager reports it |
| `gputemp` | GPU Temp | `51°C` | see [Temperatures](#temperatures) |
| `ram` | RAM | `7.5G` | |
| `ram%` | RAM Usage | `49%` | |
| `vram` | GPU VRAM | `0.9G` | dedicated video memory in use |
| `clock` | CPU Clock | `4.02G` | **live**, including boost |
| `net` | Net D/U MB/s | `12/1.4` | down and up together |
| `netdown` | Net Down | `12.4M/s` | |
| `netup` | Net Up | `1.4M/s` | |
| `diskio` | Disk I/O | `1.1M/s` | read + write throughput |
| `disk%` | Disk Usage | `29%` | how hard the drives are working — see below |
| `storage` | Storage Used | `66%` | space consumed, every fixed drive pooled |
| `disk` | Disk Free | `64G` | system drive only |
| `procs` | Processes | `229` | |
| `uptime` | Uptime | `1h 41m` | |

Both fonts are sized against the **widest plausible value** of every chosen
slot, so a reading can never outgrow its column once running. All names share
one size, and so do all values, so **one long name shrinks every name**: a
side column holds 125 px, which fits `Disk Usage` at up to 22 px but
`Drive Activity` at only 15 px. That is why `disk%` is called *Disk Usage*
(it was *Drive Usage* before v1.1.0).

### How often readings update

| What | How often | Where it's set |
|---|---|---|
| Frames sent to the panel | 10 per second | `--fps` |
| Every slot except the two below | every 2 seconds | `interval=2.0` in `Metrics.__init__` |
| `storage`, `disk` | every 30 seconds | `_cached(...)` calls in `Metrics` |
| Clock and date | checked on every frame | — |

CPU load and the throughput slots are averages over the 2-second window. A
shorter window reads as noise, and every changed value forces the affected
frames to be re-encoded.

### `disk%` is activity, `storage` is space

These are easy to confuse and they measure completely different things.

**`disk%` — Disk Usage.** How hard the drives are working right now. Idles at
0–2% and spikes when they are actually being read or written. Averaged across
the physical disks so all of them together make 100%, meaning one disk pinned
out of three reads about 33%:

```
idle                    0%
320MB write to C:      29%   (C: 87%, E: 0%, F: 0%)
back to idle            0%
```

It inverts `\PhysicalDisk(*)\% Idle Time`, which is how Task Manager derives
its "Active time" column. **The obvious-looking `% Disk Time` counter does not
work** — it is queue-length based, reads a flat `0.0` on these drives and can
exceed 100% on others.

**`storage` — Storage Used.** Space consumed: total used over total capacity
across every fixed drive, as though they were one pool. Drives weigh by size,
not one share each. This number barely moves from day to day. Cached for 30
seconds rather than 2, so a spinning disk is not woken every other second.

### `net` follows the active adapter by itself

It reports the **busiest single physical adapter**, re-evaluated every tick.

Summing all interfaces would count VPN traffic twice, once on the tunnel and
again on the physical NIC beneath it. Taking the busiest avoids that, and
`VIRTUAL_NICS` filters out loopback, Hyper-V, VMware, Tailscale, WireGuard and
similar.

The choice is **sticky while idle**, so the reading does not flicker between
adapters at rest. Unplug the ethernet and wifi starts winning on traffic, so
the display follows it with no configuration.

### `clock` is the only live CPU frequency available

`psutil.cpu_freq()` is useless on Windows: it returns the **base** clock
(3501 MHz on a Ryzen 5 5600) no matter what the CPU is doing. The live figure
comes from the `\Processor Information(_Total)\% Processor Performance`
counter, scaled against the base clock — that one tracks boost, reading
~4.0 GHz at idle and higher under load.

---

## Temperatures

Windows gives CPU temperatures **only to kernel-mode code** — there is no
performance counter or normal API for them, which is why every temperature
tool needs a driver.

### How they are read

- **[LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)**
  0.9.6 (MPL-2.0) reads the sensors. `temps_win.py` loads its DLLs from
  `lib/LibreHardwareMonitor/` through **pythonnet**, on the .NET Framework 4.8
  that ships with Windows 10 and 11. It is loaded only when a temperature slot
  is shown, so it costs nothing otherwise.
- **CPU:** LibreHardwareMonitor reaches the sensor through the
  **[PawnIO](https://pawnio.eu/)** driver (2.2.0, signed by Microsoft), and
  only with administrator rights. On a Ryzen the sensor used is
  `Core (Tctl/Tdie)`, the figure AMD's own tools show; on Intel it is
  `CPU Package`. See `CPU_SENSORS` for the fallbacks.
- **GPU:** `GPU Core`. On AMD cards this needs neither PawnIO nor admin rights,
  so the GPU temperature still shows when the admin prompt is refused. A
  discrete card is preferred over an integrated one.
- An unreadable sensor reports **0**, not nothing, so 0 and below are shown as
  `--`. The first line the clock prints says which sensors it found, or why
  one is missing.

### PawnIO gotchas

PawnIO replaced the old WinRing0 driver in LibreHardwareMonitor 0.9.5.
WinRing0 has a known vulnerability,
[CVE-2020-14979](https://nvd.nist.gov/vuln/detail/CVE-2020-14979), and
antivirus software flags it. Without PawnIO, LibreHardwareMonitor cannot read
any CPU values. PawnIO is **never stored in this repo**: the release build
downloads the official, signed installer and checks its fingerprint, and the
app's installer runs it only if PawnIO is missing. Script users install it
themselves ([SCRIPTS.md](SCRIPTS.md), step 4).

Two things were found on the test machine:

- **LibreHardwareMonitor only talks to PawnIO while its driver is running**,
  and never starts it. Even the official installer registers the driver as a
  *manual-start* service, so after a reboot it can be stopped.
  `ensure_pawnio()` starts it when the clock runs as admin.
- **LibreHardwareMonitor checks PawnIO's install entry** (`DisplayVersion` under
  `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO`). A copy
  installed silently by another program (here, probably FurMark) had the
  driver but no install entry, and the CPU read 0 even as admin. Running the
  official installer fixed it.

### Files downloaded in a ZIP are blocked

Windows marks every file extracted from a downloaded ZIP as "from the
internet", and the .NET Framework refuses to load DLLs carrying that mark
(`An attempt was made to load an assembly from a network location…`).
`setup.cmd` clears the mark on `lib\` with `Unblock-File`. If temperatures say
"Windows is blocking the DLLs", run `setup.cmd` again. (The installed app is not
affected: files an installer writes carry no such mark.)

---

## The app: service, window and installer

The installed app is the same engine (`clock_win.py`, `lcd_win.py`,
`temps_win.py`) wrapped in two programs:

```
   CrystalXLCD.exe (tray_win.py)                CrystalXLCD-Service.exe (service_win.py)
   the signed-in user, no admin                 SYSTEM, from boot
  +---------------------------+   named pipe   +------------------------------+
  | window + tray icon        | -------------> | pipe server                  |
  | status, start/stop,       |  JSON, local   | Display thread:              |
  | picture, options          |  users only    |   clock_win.main(stop=...)   |
  +-------------+-------------+                |   retries when the panel is  |
                | start / stop                 |   busy or missing            |
                v                              +--------------+---------------+
      Windows service manager  ------------------------------>|
                                                              v
                                              COM port -> panel,  LibreHardwareMonitor
                                              C:\ProgramData\CrystalX LCD\ (settings, picture, log)
```

**Why a service.** It starts at boot, so the clock is up on the login screen,
and it runs as SYSTEM, so the CPU temperature (admin only) always works and
there is never an admin prompt.

**Start / Stop / Quit** start and stop the service itself. Stopped, it holds
nothing — the COM port, LibreHardwareMonitor and PawnIO are all free for other
programs, which is the point of Stop. **Start display with Windows** switches
the service between automatic and manual start.

**Settings** travel over the pipe `\\.\pipe\CrystalXLCD` as one JSON message
each way (`ipc_win.request`). If the display is off, the window starts the
service with the argument `--idle`: it then only applies settings, touches no
hardware, and stops itself 20 seconds after the last request.

The settings (`settings_win.py`) are the **picture** — a file the service keeps
— and the **look**: everything else that decides what the screen shows (fit,
zoom and pan, clock and date formats, sizes, colours, positions, the seven stat
spots and their names, font, frosted panel). Hiding the time, the date or the
stats (`show_stats`) keeps their other settings for when they are shown again.
They live in `config.json`; a v1.0
file, with only the picture and fit, loads with defaults for the rest.

| Command | What the service does |
|---|---|
| `apply` | Stores the look, and optionally a new picture (sent as bytes) or `"default"`, then restarts the display. The window's one **Apply** button sends everything in this one message |
| `save_layout`, `load_layout`, `delete_layout` | A **saved layout** is `layouts\<n>.json` (n = 1–10) with the look, plus `<n>.<ext>`, its own copy of its picture. Saving takes what is on the screen, so the window applies unsaved changes first |
| `todo` | Ticks, unticks or removes one to-do item (found by its `id`), or removes every ticked one — straight from the window, without the rest of the form. New and edited items go with `apply` |
| `set_autostart` | Switches the service between automatic and manual start |
| `status` | What the display is doing, for the window's status line |

**Changing settings never pauses the screen.** Preparing the frames for a new
look takes a second or two for a GIF. Up to v1.1.0 the display stopped
meanwhile, so the screen froze on its last frame. Now the service builds the
new look (`clock_win.Show`) in the background while the old one keeps playing,
then hands it to the player (`Player.swap`): measured, the longest gap between
frames during a swap was about 140 ms against the usual 100–110 ms. The same
swap redraws the calendar and countdown at midnight (see
[The extra blocks](#the-extra-blocks)). The service keeps one
LibreHardwareMonitor open across swaps rather than reopening it each time.

The window reads `config.json` and the layout list straight from the folder
(users may read it), so it shows the settings even while the service is
stopped.

**Security.** The service runs as SYSTEM, so everything a normal user can reach
is kept narrow:

| Rule | Why |
|---|---|
| The app lives in Program Files; the installer offers no folder choice | A SYSTEM service's files must not be writable by normal users, or anyone could swap the program |
| Users may **start and stop** the service, nothing more (`SERVICE_SDDL`) | Changing its configuration would let a user point a SYSTEM service at their own program |
| The pipe accepts local clients only: SYSTEM, administrators and the signed-in user | No remote access |
| The first pipe instance claims the name (`FILE_FLAG_FIRST_PIPE_INSTANCE`) | Nothing else can create the pipe first and pose as the service |
| The window never sends a file path — it reads the picture and sends the bytes | Otherwise any program could get SYSTEM to open files on its behalf |
| The service checks every picture (format, size ≤ 50 MB, ≤ 300 frames) and keeps its own copy | A long GIF held in memory at panel size could otherwise eat gigabytes |
| `C:\ProgramData\CrystalX LCD` is writable only by SYSTEM and administrators | A user can't plant a picture or settings file for the service to read |
| Every setting, from the pipe or from a file, goes through `settings_win.clean_look` | Only known keys, types and ranges reach the engine; anything else falls back to its default |
| Fonts are only taken from `C:\Windows\Fonts`, by file name | The service never opens a path the user chose; fonts installed for one user only are therefore not offered |

**Fitting any picture.** Pictures chosen in the app are placed on a canvas of
the tested 320 × 1476 frame (`fit_frame`): *fill* crops to cover it, *blur* and
*color* show the whole picture with blurred or solid edges. So every picture,
whatever its shape, stays inside the pixel ceiling (constraint 2).

**Zoom and pan.** `--zoom` enlarges the fitted picture (1–4×), and `--pan-x`
/ `--pan-y` choose where it sits: 0 lines its left (top) edge up with the
screen's, 1 its right (bottom) edge, 0.5 centres it (`fit_geometry`). Both are
proportions, so they mean the same on the panel and in the smaller preview.
Only the part of the picture that lands on the screen is resampled, so zooming
into a large photo costs no more than showing it whole.

**The window** is tkinter: status and Start/Stop at the top, the settings in
five tabs (Picture, Clock, Stats, Style, Options), and one Apply / Undo for all
of them. Its preview is drawn by `clock_win.preview()` — the engine itself, with
the same options the service will get (`settings_win.engine_argv`) and example
readings instead of the sensors — so it shows exactly what the panel will show.
It redraws on every change in 15–50 ms, and never queues redraws: while one
runs, only the newest waits.

- **Dragging** on the preview moves the clock or stats block when grabbed by
  its panel, and the picture anywhere else; the wheel zooms around the
  pointer. The preview works from the picture's first frame, opened once and
  scaled down to 2048 px at most (placement is in proportions, so it looks the
  same).
- **Size sliders** stop at the largest size that fits (`Layout.limits`);
  sizes and positions left automatic show what the engine chose.
- **The free space** between the clock and stats blocks is shown in panel
  pixels on the Picture tab (from the layout's `clock_box` and `stats_box`),
  for anyone making a picture to fit it. Messages that come and go sit in
  fixed-size boxes, so the window never changes size.
- **Fonts**: the Style tab lists every font in `C:\Windows\Fonts`, each drawn
  in its own style. Reading their names takes about a second, so it happens
  in the background, only once the Style tab is first opened.

It starts hidden with `--hidden` (at login); starting it again (Start menu)
tells the running copy to show its window. While hidden it only keeps the
tray tooltip up to date: it reads neither the settings nor the layouts, draws
nothing, and lets go of the preview's picture.

**The installer** (`packaging/installer.iss`, Inno Setup):

- stops the app and service, and removes the script version's
  `CrystalX LCD` login task if present, so an upgrade can replace every file;
- copies the PyInstaller folder to `C:\Program Files\CrystalX LCD`;
- runs `PawnIO_setup.exe -install -silent` only when PawnIO is missing or
  older than 2.0, before the service first starts;
- installs the service (`CrystalXLCD-Service.exe --startup auto install`, which
  also applies `SERVICE_SDDL`) on a fresh install, then starts it;
- adds the Start menu entry and `CrystalXLCD.exe --hidden` under `HKLM\...\Run`;
- offers to disable LCD Control's `LCD ControlPowerBoot` task;
- on uninstall, stops everything, deletes the service and
  `C:\ProgramData\CrystalX LCD`. If there are saved layouts it first asks
  whether to delete them too (No is the default; a silent uninstall keeps
  them); kept, only the `layouts` folder stays, and a reinstall picks them up.
  This is done in `CurUninstallStepChanged`, because `[UninstallDelete]` cannot
  depend on a question. PawnIO is left in place: it is a shared driver with its
  own uninstaller.

Its `AppId` must never change — Windows uses it to recognise upgrades.

**File properties.** Both programs carry a version resource (company, product,
version), so Task Manager lists them as *CrystalX LCD* and *CrystalX LCD
service* rather than as nameless Python processes.

---

## Making a release

1. Change `VERSION` in `ipc_win.py` (the only place the version lives) and
   commit.
2. Tag the commit with the same version and push the tag:

   ```cmd
   git tag v1.0.1
   git push origin v1.0.1
   ```

3. `.github/workflows/release.yml` then, on a fresh Windows machine:
   - checks the tag matches `VERSION`, and that Python is not 3.13.0;
   - installs the exact build tools from `packaging/requirements-build.txt`;
   - builds the two programs with PyInstaller and collects the licences;
   - downloads PawnIO 2.2.0 and Inno Setup 7.1.0, and refuses to continue
     unless both match their SHA-256 fingerprints and carry valid signatures;
   - builds `CrystalX-LCD-Setup-<version>.exe` and its `.sha256` file, and
     publishes both as a GitHub release.

Running the workflow by hand (Actions tab → Release → Run workflow) builds the
installer as a downloadable artifact without publishing anything — a safe way
to test a change to the build.

**The build toolchain has no EULA.** Inno Setup is open source under a
permissive licence. WiX 7 was considered for a `.msi`, but its prebuilt tools
require accepting an Open Source Maintenance Fee EULA; Inno Setup was chosen to
avoid that, and because running PawnIO's own installer is a normal step for it.

To build the installer locally, see the header of `packaging/installer.iss`.

---

## Admin rights and autostart (scripts)

This section is about the `.cmd` scripts (see [SCRIPTS.md](SCRIPTS.md)); the
app handles admin rights with its service instead.

Reading the CPU temperature needs administrator rights. The scripts handle
this the same way LCD Control does:

| Started… | How | Prompt? |
|---|---|---|
| By hand | `run-clock.cmd` re-launches itself elevated with `Start-Process -Verb RunAs`. Answering No carries on unelevated; CPU temperature shows `--`. | Yes, each time |
| At login | `autostart-on.cmd` registers the task `CrystalX LCD`: logon trigger, current user, **run with highest privileges**, `pythonw.exe` (no console), working directory = project folder. | No |

A task with highest privileges runs elevated without asking because an
administrator approved it once, when it was created — that is the admin prompt
`autostart-on.cmd` shows.

Two details that matter:

- **Scheduled tasks stop after 3 days by default.** The task is created with
  no execution time limit (`PT0S`); without that, a PC left on for days would
  lose the clock. The same task also allows running on battery.
- **The task is built with PowerShell's `Register-ScheduledTask`, not
  `schtasks /create`**, which caps the command at 261 characters and so failed
  for long folder paths.
- **Python 3.13.0 breaks the hidden start.** In a venv made by 3.13.0,
  `Scripts\pythonw.exe` launches the console `python.exe` instead of
  `pythonw.exe` ([CPython #126084](https://github.com/python/cpython/issues/126084),
  fixed in 3.13.1). At login a black console window appeared, and clicking
  into it put it in QuickEdit "Select" mode, which **blocks the process at its
  next `print()`** — before the port was opened, so the panel stayed black.
  Pressing Esc released it. Checked side by side on the test machine: a 3.13.5
  venv's `pythonw.exe` starts `pythonw.exe`; a 3.13.0 one starts `python.exe`.
  `setup.cmd` therefore refuses 3.13.0, both for creating `venv-win` and for
  reusing one.

`autostart-on.cmd` also offers to disable LCD Control's own login task
(`LCD ControlPowerBoot`), since only one program can hold the COM port.

---

## Performance

The service runs all day and the window app sits in the tray at every login,
so both are kept as light as they can be. Measured on the test machine (Ryzen 5
5600, 12 threads) on 2026-09-30, v1.0.0 against v1.1.0, same settings, 60
seconds after settling. "% of one core" is one CPU thread; divide by 12 for the
whole CPU.

**The display (the service's engine)**, rooftop GIF, all seven stats with live
sensors, 10 FPS:

| Picture | v1.0.0 CPU | v1.1.0 CPU | v1.0.0 RAM | v1.1.0 RAM |
|---|---|---|---|---|
| Rooftop GIF (59 frames), the default | 11.9% of one core | **4.9%** | 179 MB | **79 MB** |
| A 300-frame GIF, the most allowed | 12.0% | **4.6%** | 636 MB | **121 MB** |
| A still picture | 1.0% | **0.7%** | 68 MB | 72 MB |

The installed v1.0.0 service, measured live on the same PC, used 10.9% of one
core and 171 MB, which matches the first row. Neither version uses the GPU at
all (0% in Windows' GPU counters): everything is drawn in software.

**The window app:**

| Moment | v1.0.0 CPU | v1.1.0 CPU | v1.0.0 RAM | v1.1.0 RAM |
|---|---|---|---|---|
| First 10 s after login, hidden | 6.4% | **0.0%** | 41 MB | 39 MB |
| Hidden in the tray | 0.0% | 0.0% | 41 MB | 39 MB |
| Window open, idle | 0.7% | **0.0%** | 41 MB | 56 MB |
| Window hidden again | 0.0% | 0.0% | 41 MB | 44 MB |

v1.0.0 drew its preview at login even while hidden; v1.1.0 draws nothing until
the window opens. The window itself takes more memory while open (more
controls, and the picture its preview is made from), and lets go of the
picture when hidden.

**v1.2.0** (same machine and method, 2026-09-30) adds brightness, the text,
countdown and to-do blocks and the calendar, and swaps settings without a
pause — at no measurable cost:

| | CPU | RAM |
|---|---|---|
| Display, v1.1.0 | 4.6% of one core | 79 MB |
| Display, v1.2.0, nothing new switched on | 4.2% | 79 MB |
| Display, v1.2.0, **everything on** (text, countdown, to-do list, month calendar, 80% brightness) | 4.1% | 83 MB |
| Window app hidden, v1.1.0 / v1.2.0 | 0.0% / 0.0% | 39 / 40 MB |
| Window app open and idle, v1.1.0 / v1.2.0 | 0.0% / 0.0% | 56 / 57 MB |

Runs vary by about ±0.4% of a core, so the three display rows are the same
within noise: the new blocks are drawn into the frames when they are
prepared, never per frame. With everything on, preparing the frames takes
0.5 s longer (2.8 s) and 9 MB more at its peak, once per Apply and at midnight.

### Where the display's time goes

v1.0.0 did everything for every frame sent: frost the two panels, draw the
text, turn the frame upside down, encode it. v1.1.0 does per frame only what
has to be:

| Step | v1.0.0 | v1.1.0 |
|---|---|---|
| Frost the panels (blur 9) | every frame, ~4.6 ms | once per frame at load (`prepare`) |
| Turn upside down | every frame, 0.7 ms | once per frame at load |
| Draw the text (20 strings with outlines) | every frame, ~4.4 ms | once per change, ~7 ms every 2 s (`text_bands`) |
| Unpack the frame | — | every frame, ~2.5 ms (zlib) |
| Lay the text over it | — | ~0.3 ms |
| JPEG encode (q88, no subsampling) | 1.6 ms | 1.6 ms |
| **Total per frame** | **~12.6 ms** | **~4.9 ms** |

The text is drawn on a transparent layer and laid over each frame. Every glyph
has a black outline, so this matches drawing on the frame itself, bar about 20
of 472,320 pixels a few shades apart (see `text_bands`).

**Memory** is mostly the frames. v1.0.0 held every frame at panel size, 1.4 MB
each: 80 MB for the rooftop GIF, over 400 MB for a 300-frame GIF (636 MB in
all). v1.1.0 keeps them zlib-compressed, which is lossless (80 MB → under
9 MB), makes and packs them one at a time as they load so the full set never
exists at once, and drops finished JPEGs as soon as the text changes (they can
never be sent again). The rest is Python with Pillow (~21 MB) and the sensor
library on .NET (~40 MB).

Also in the loop: the panel still gets 10 frames a second; a still picture is
kept unpacked, since there is nothing to gain; and a frame's JPEG is reused
while the text stays the same (a clock without stats changes once a minute).

### The port

USB write of a frame (≈107 KB) takes about 6.7 ms of waiting, not CPU. The
port sustains **15.5 MB/s**; at 10 FPS the GIF needs 0.67 MB/s, so there is an
enormous margin — frame rate is not a constraint on this transport.

### How to measure

Run the loop exactly as the service does, with the same options
(`clock_win.main(argv, stop, log)`), but with `clock_win.Panel` replaced by a stand-in whose
`send` only counts frames: the real panel stays untouched and the numbers
include the sensors. Wait for frames to flow, let it settle 10 s, then take
the process's CPU time and memory over 60 s (`psutil.Process().cpu_times()`,
`memory_info()`). Compare versions on the same picture and settings. To measure
the window app, run it hidden with a stand-in service (see `AGENTS.md`,
Testing) and sample the same way while it sits hidden, then open.

To use less still: lower `--fps`, raise the stats interval, or use
`--no-stats`.

---

## Approaches that did not work

Recorded so they are not attempted again.

**Zadig / replacing the driver with WinUSB or libusb-win32.** Unnecessary, and
it would *break* things — it unbinds `usbser.sys`, destroying the COM port that
both this and the vendor software depend on. There is no reason to touch the
driver on Windows.

**Blaming frame rate, JPEG size in bytes, chroma subsampling, or the header's
`flag` field.** All were tested on this panel and ruled out. Frame rate from
0.33 to 120 FPS behaves identically. The real variable is always **pixel count**.

**Chasing frame pacing, chunked writes or DTR handling when the panel was
blank.** All were suspected; none was the cause. The transport worked on the
very first attempt — the frame simply needed to keep being sent. Measure before
changing anything: if `write()` returns at ~15 MB/s with `out_waiting == 0`,
the bytes reached the device and the fault is elsewhere.

**Raising the width past 320 because `MAX_PIXELS` seemed to leave room.** The
500k figure was a rounded guess; the panel already corrupts at 324 × 1494
(484,056 px). Tested on the hardware on 2026-09-29 — see constraint 2.

**Reading the vendor app's code to learn its commands.** It is obfuscated; see
[What the vendor app revealed](#what-the-vendor-app-revealed).

**Assuming "PawnIO is installed" means CPU temperatures will work.** The driver
must also be running and registered the official way; see
[PawnIO gotchas](#pawnio-gotchas). Running as admin alone does not help.

**`schtasks /create` for autostart.** Its default 3-day time limit stops the
clock on a PC left running, and its 261-character command limit breaks long
folder paths. See [Admin rights and autostart](#admin-rights-and-autostart-scripts).

**pywin32's `win32serviceutil.StartService` / `StopService` from the window.**
They open the service manager with full access, which only administrators
have, so a normal user got "Access is denied" even though the service allows
them to start and stop it. `tray_win._with_service` asks for only the rights
each action needs.

**Testing the service in-process while the app is installed.** The installed
service owns `\\.\pipe\CrystalXLCD`, so a test copy could not create it — and
its requests silently reached the real service and changed its settings. Tests
must use their own pipe name and data folder.

**Sizing an empty tkinter `Label` in pixels.** With no image yet, `width` and
`height` count text characters and lines, so the preview first opened huge,
then shrank when the picture arrived. The preview is a `Canvas`, which is
always sized in pixels.

**Using `psutil.cpu_freq()` for a live clock, or `% Disk Time` for drive
activity.** Both look right and both are wrong on Windows. See
[Stats slots](#stats-slots).
