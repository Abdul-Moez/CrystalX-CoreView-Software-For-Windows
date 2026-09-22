# CrystalX CoreView LCD — Windows driver

Drives the LCD panel in a CrystalX CoreView case (V-950) from Windows, without
the vendor's software. Plays a GIF on the panel with a clock, date and live
system stats overlaid.

**Read this file before changing anything.** The panel has several undocumented
quirks that took a long session of trial and error to find, first on Linux and
then here. They are written down so nobody has to rediscover them.

```cmd
run-clock.cmd
```

Close the vendor's **LCD Control.exe** first — it holds the COM port open and
only one program can have it.

> A Linux build of this exists separately (`lcd.py` / `clock.py`, using pyusb
> and a detached `cdc_acm` driver). This folder is Windows-only; a copy of the
> Linux version is kept at `E:\crystalx-lcd-BACKUP`.

---

## Hardware

| | |
|---|---|
| Device | `33c3:f101` — "HL VMAX", sold as CrystalX CoreView V-950 |
| Vendor software | `lcd.crystalx.io` (Windows only, .NET/WPF, by SOEYI Technology) |
| Panel | 480 × 1920, portrait, mounted **upside down** in the case |
| Enumerates as | CDC-ACM composite → `USB Serial Device (COMx)` via `usbser.sys` |
| Transport | writes to that COM port = USB bulk, interface 1, endpoint `0x02` |

---

## How it works

Each frame is a plain JPEG with a 12-byte header, written to the port. There is
no handshake, no authentication and no acknowledgement — the device never sends
anything back.

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

## THINGS THAT MUST NOT CHANGE

These were each found the hard way. Changing any of them breaks the display in
ways that look like random corruption and are very hard to diagnose.

The first three are properties of the **panel**, not of the operating system,
and are equally true of the Linux build.

### 1. Images are rotated 180° before encoding

The panel is physically mounted upside down. Every frame goes through
`transpose(Image.ROTATE_180)` **before** being encoded to JPEG. The panel does
not do this for you.

### 2. Never send more than ~500,000 pixels per frame

This is the single most important constraint and the least obvious.

The panel is 480 × 1920 (921,600 px), but **it cannot decode a frame that
large**. Sending a full-panel image produces a flickering, torn, scrambled mess.

Measured on this hardware:

| Size | Pixels | Result |
|---|---|---|
| 234 × 1079 | 252,486 | clean |
| 300 × 1383 | 414,900 | clean |
| 320 × 1476 | 472,320 | clean — **what we use** |
| 334 × 1540 | 514,360 | corrupt |
| 480 × 1920 | 921,600 | badly corrupt |

`MAX_PIXELS = 500_000` in `lcd_win.py` enforces this. The scripts refuse to run
rather than let you produce a broken display. **Do not raise it.**

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
the service, or the vendor app, before running a script by hand.

### 8. `%-I` crashes on Windows

The Linux build uses glibc's `%-I` / `%-d` no-padding flags. Windows uses the
MSVC C runtime, where those raise `ValueError: Invalid format string`. The
Windows equivalent is `%#I` / `%#d`, which produces byte-identical output.

---

## Things that are safe to change

- Font, font sizes, text positions, margins
- Which GIF is played, its FPS, JPEG quality
- Clock and date formats (`TIME_FMT`, `DATE_FMT` in `clock_win.py`)
- Which stats are shown (`--slots`) and how often they refresh
- Width, **as long as the result stays under `MAX_PIXELS`**

---

## The important discovery

**The vendor's Windows software drives the panel through a COM port, not
through libusb.** This makes the Windows build far simpler than the Linux one:
no libusb, no Zadig, no driver replacement, no administrator rights.

The evidence, gathered on this machine:

| Check | Result |
|---|---|
| `libusb0.sys` on the system | **absent** — no driver file, no service, no filter |
| Device enumeration | plain CDC-ACM composite; interfaces 0+1 owned by Microsoft's `usbser.sys` |
| Children of the composite device | exactly one, `USB Serial Device (COM5)` |
| Opening COM5 while LCD Control.exe runs | `Access to the port 'COM5' is denied` |

That last line is the proof: the vendor app holds the COM port exclusively.

The Linux notes claim the vendor app "talks to the panel through `libusb0.sys`
and looks for `\\.\libusb0-000X` device nodes". That was observed **under
Wine**. The real Windows build does not use libusb at all.

### Why the COM port is the same wire

The device is a standard CDC-ACM composite:

- **interface 0** — CDC control
- **interface 1** — CDC data, bulk OUT endpoint `0x02`

Endpoint `0x02` on interface 1 is exactly what the Linux driver writes to after
detaching `cdc_acm`. On Windows, `usbser.sys` already owns that endpoint and
exposes it as a COM port. Writing bytes to the port puts them on the same
endpoint. Same wire, different door.

This also explains the Linux note that writing to `/dev/ttyACM0` "does display
an image, but is unreliable". On Linux that path goes through the tty line
discipline, which mangles binary data unless the port is put in raw mode.
Windows COM ports have no line discipline — bytes pass through untouched.

---

## Files

| File | Purpose |
|---|---|
| `lcd_win.py` | Plain player. Also defines `Panel`, `MAGIC`, `MAX_PIXELS`, `load_frames`, which `clock_win.py` imports. **The core driver — treat with care.** |
| `clock_win.py` | Overlay player: GIF + clock + date + configurable stats |
| `run-clock.cmd` | Runs `clock_win.py` with the known-good settings |
| `run-gif.cmd` | Runs `lcd_win.py` — plain GIF, no overlay. **Known-good fallback.** |
| `retro_pixel_guy_smoking_on_rooftop.gif` | The wallpaper GIF |
| `venv-win/` | Python environment: Pillow, pyserial, psutil, pywin32 |

If you break `clock_win.py`, `run-gif.cmd` still works and proves the hardware,
the port and the frame format are all fine.

---

## Setup

One-time. Needs Python 3 on `PATH` and nothing else — no ImageMagick, no
driver, no elevation.

```cmd
cd /d E:\crystalx-lcd
python -m venv venv-win
venv-win\Scripts\python.exe -m pip install Pillow pyserial psutil pywin32
```

---

## Everyday use

```cmd
run-clock.cmd                      REM GIF + clock + stats
run-gif.cmd                        REM plain GIF, known-good fallback
```

Press `Ctrl-C` to stop. Extra arguments pass through:

```cmd
run-clock.cmd --fps 15
run-clock.cmd --font segoeuib.ttf
run-clock.cmd --text-fill 0.9
run-clock.cmd --no-stats
run-clock.cmd --slots cpu,gpu,ram%,vram
```

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

They are sized against the widest string each can ever produce — `12:00 PM`
beats `1:05 AM`, and a two-digit day beats the one-digit day `%#d` can produce —
so neither can outgrow the frosted panel at runtime.

`--time-size` and `--date-size` still cap either line individually if you want
them sized independently rather than matched.

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

If it refuses with an "over the pixel limit" message, lower `--width` until it
fits. The limit is not negotiable — see above.

Images and GIFs only. Video would need ffmpeg; the Linux build got it free from
ImageMagick, which is not used here.

---

## Stats slots

`--slots` takes a comma-separated list, filling the grid **two per row**. An odd
one at the end is **centred across the full width**.

Default: `cpu,gpu,ram%,disk%,net`

```
CPU Usage      GPU Usage
     6%             1%

RAM Usage    Drive Usage
    49%            29%

      Net D/U MB/s
        0.0/0.1
```

| Slot | Label | Example | Notes |
|---|---|---|---|
| `cpu` | CPU Usage | `6%` | |
| `gpu` | GPU Usage | `1%` | busiest GPU engine type, as Task Manager reports it |
| `ram` | RAM | `7.5G` | |
| `ram%` | RAM Usage | `49%` | |
| `vram` | GPU VRAM | `0.9G` | dedicated video memory in use |
| `clock` | CPU Clock | `4.02G` | **live**, including boost |
| `net` | Net D/U MB/s | `12/1.4` | down and up together |
| `netdown` | Net Down | `12.4M/s` | |
| `netup` | Net Up | `1.4M/s` | |
| `diskio` | Disk I/O | `1.1M/s` | read + write throughput |
| `disk%` | Drive Usage | `29%` | how hard the drives are working — see below |
| `storage` | Storage Used | `66%` | space consumed, every fixed drive pooled |
| `disk` | Disk Free | `64G` | system drive only |
| `procs` | Processes | `229` | |
| `uptime` | Uptime | `1h 41m` | |

Both fonts are sized against the **widest plausible value** of every chosen
slot, so a reading can never outgrow its column once running.

### `disk%` is activity, `storage` is space

These are easy to confuse and they measure completely different things.

**`disk%` — Drive Usage.** How hard the drives are working right now. Idles at
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
(3501 MHz here) no matter what the CPU is doing. The live figure comes from the
`\Processor Information(_Total)\% Processor Performance` counter, scaled
against the base clock — that one tracks boost, reading ~4.0 GHz at idle and
higher under load.

### Why there are no temperatures

Windows exposes CPU and GPU temperature **only from kernel mode**. There is no
`/sys/class/hwmon` equivalent and no unprivileged API. Reading them means
running something like [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
(MPL 2.0) in the background and reading its WMI namespace.

That is a real trade-off, not just an install: it loads a ring-0 driver whose
lineage (WinRing0) had a known vulnerability, [CVE-2020-14979](https://nvd.nist.gov/vuln/detail/CVE-2020-14979),
allowing any user-mode process arbitrary ring-0 access. Some antivirus flags
it, and Core Isolation / Memory Integrity may block it from loading.

Everything this build shows needs no driver, no elevation and no install.

To add temperatures later: read the WMI namespace in a new `Metrics` method and
add an entry to `SLOTS`. Nothing else changes.

---

## Performance

Measured on a Ryzen 5 5600, at 10 FPS, 320 × 1476, with stats:

**7.6% of one core, 142 MB RAM.**

Per-frame cost:

| Stage | Time |
|---|---|
| copy + frost (2 panels, blur 9) | 4.64 ms |
| draw text | 1.52 ms |
| rotate 180° | 0.74 ms |
| JPEG encode (q88, no subsampling) | 1.58 ms |
| USB write (≈107 KB) | 6.7 ms |

The port sustains **15.5 MB/s**. At 10 FPS the GIF needs 0.67 MB/s, so there is
an enormous margin — frame rate is not a constraint on this transport.

Frames are cached and re-encoded only when the displayed text changes. To reduce
the cost: raise the stats interval, lower `--fps`, or use `--no-stats`.

---

## Autostart

Windows has no systemd. Use Task Scheduler:

```cmd
schtasks /create /tn "CrystalX LCD" /tr "E:\crystalx-lcd\venv-win\Scripts\pythonw.exe E:\crystalx-lcd\clock_win.py E:\crystalx-lcd\retro_pixel_guy_smoking_on_rooftop.gif" /sc onlogon /rl limited /f
```

Note **`pythonw.exe`**, not `python.exe` — it runs without a console window.

Remove it with:

```cmd
schtasks /delete /tn "CrystalX LCD" /f
```

This does **not** need administrator rights. If you also run the vendor's
LCD Control.exe at startup, remove it from startup or the two will fight over
the port and whichever loses simply exits with an error.

---

## Troubleshooting

**`COM5 is busy -- close LCD Control.exe`**

Something holds the port. Usually the vendor app; sometimes a previous run that
did not exit.

```cmd
tasklist | findstr /i "LCD python"
taskkill /f /im "LCD Control.exe"
```

**`panel 33c3:f101 not found`**

The case is unplugged, or Windows has not enumerated it. Check it appears:

```powershell
Get-PnpDevice | Where-Object InstanceId -like "*VID_33C3*"
```

You want `USB Serial Device (COMx)` with status `OK`.

**Panel flashes then goes blank**

The process exited. The panel only holds an image while something keeps
writing. Check the console for a traceback.

**Panel is torn or scrambled**

Almost certainly the frame is too large. Check the reported pixel count on
startup and reduce `--width`.

**Stats show `--`**

That reading is unavailable rather than zero. GPU slots need the GPU
performance counters; network slots read `--` until there are two samples to
subtract, which takes one interval (2 s) after start.

**Nothing on the panel, no error**

Fall back to `run-gif.cmd`. If that works, the fault is in the overlay, not the
transport.

---

## Approaches that did not work

Recorded so they are not attempted again.

**Zadig / replacing the driver with WinUSB or libusb-win32.** Unnecessary, and
it would *break* things — it unbinds `usbser.sys`, destroying the COM port that
both this and the vendor software depend on. There is no reason to touch the
driver on Windows.

**Running the vendor's software under Wine** (the Linux investigation). It
installs and the UI runs, and Wine's `wineusb` can see the device, but the app
talks through `libusb0.sys`, a kernel driver Wine has no equivalent for. Dead
end — and, as it turns out, not what the app does on real Windows anyway.

**Installing ImageMagick.** Pillow coalesces GIF frames correctly on its own —
seeking a frame and converting composites it against the ones before it, which
is what `-coalesce` does. The only thing lost is video input.

**Blaming frame rate, JPEG size in bytes, chroma subsampling, or the header's
`flag` field.** All were tested and ruled out on the Linux side. Frame rate from
0.33 to 120 FPS behaves identically. The real variable is always **pixel count**.

**Chasing frame pacing, chunked writes or DTR handling when the panel was
blank.** All were suspected on Windows; none was the cause. The transport worked
on the very first attempt — the frame simply needed to keep being sent. Measure
before changing anything: if `write()` returns at ~15 MB/s with
`out_waiting == 0`, the bytes reached the device and the fault is elsewhere.

**Using `psutil.cpu_freq()` for a live clock, or `% Disk Time` for drive
activity.** Both look right and both are wrong on Windows. See the slots section.

---

## Notes for an AI agent working on this

- The user is on Windows 10 LTSC, AMD Ryzen 5 5600 + Radeon RX 580, three fixed
  drives (C:, E:, F:), ethernet with Tailscale also installed.
- Before changing rendering, read "THINGS THAT MUST NOT CHANGE" above. Those
  constraints are empirical, verified on the physical hardware, and are not
  guesses.
- **You cannot see the panel.** Only the user can confirm whether output looks
  right. When testing a change, make it produce a visibly distinct result and
  ask — do not assume it worked because the process runs without error.
- Use `--preview FILE` for anything visual. It renders a frame to PNG without
  opening the port, so it neither disturbs a running panel nor risks the
  display. Look at the PNG before sending anything to hardware.
- A test that renders identically to the previous test tells you nothing. Vary
  one thing at a time, and make the variable visible on screen (a number, a
  label) so the user can report which case they are looking at.
- Solid colours and uniform images **hide geometry bugs completely**. A sheared
  or offset red frame still looks red. Always test with structured content:
  grids, diagonals, numbered bands, distinct corners.
- `run-gif.cmd` is the known-good baseline. If something breaks, go back to it
  to establish whether the problem is the hardware or the change.
- Measure before optimising, and check what a measurement actually means —
  `psutil.Process.cpu_percent()` is normalised to *one core*, not to the whole
  CPU, and multiplying it by the thread count gives a figure 12× too high.
