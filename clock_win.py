#!/usr/bin/env python3
# CrystalX CoreView LCD for Windows
# Copyright (C) 2026 Abdul Moez (https://github.com/Abdul-Moez)
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along
# with this program (the LICENSE file). If not, see
# <https://www.gnu.org/licenses/>.
#
# Additional term under section 7(b) of the GNU GPL version 3: the author
# attribution "Abdul Moez (https://github.com/Abdul-Moez)" must be preserved
# in this file and in all copies and modified versions of it.
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Play a GIF on the CrystalX CoreView panel with a clock and system stats.

Text is drawn over each frame, outlined and sitting on a frosted panel, so
the output never grows past the panel's pixel ceiling.

  transport   the panel's COM port (see lcd_win.py)
  frames      Pillow, so nothing extra to install
  fonts       the Windows font directories
  stats       load and memory from psutil and Windows performance counters,
              no temperatures yet (see Metrics below)

Usage:
    clock_win.py <source> [--width 320] [--fps 10] [--font consolab.ttf]
"""

import argparse
import atexit
import os
import struct
import sys
import time
from io import BytesIO

import psutil
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from lcd_win import (FIT_MODES, MAGIC, MAX_PIXELS, Panel, install_handlers,
                     load_frames)

# %#I / %#d drop the leading zero on Windows. The %-I form found in many
# Python examples is not supported here and raises ValueError.
TIME_FMT = "%#I:%M %p"          # 3:25 PM
DATE_FMT = "(%a) %#d-%b-%Y"     # (Sun) 20-Sep-2026

FONT_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
]
# Friendly names --font accepts, mapped to the bold file of that family.
FONT_ALIASES = {
    "consolas": "consolab.ttf",
    "jetbrains mono": "JetBrainsMono-Bold.ttf",
    "segoe ui": "segoeuib.ttf",
    "arial": "arialbd.ttf",
    "courier new": "courbd.ttf",
}


def resolve_font(spec):
    """Accept a font file, a name in the Windows font directories, or an alias."""
    if os.path.isfile(spec):
        return spec
    base = spec.strip().lower()
    candidates = [spec, FONT_ALIASES.get(base, ""), base, base + ".ttf", base + ".ttc"]
    for name in filter(None, candidates):
        if os.path.isfile(name):
            return name
        for directory in FONT_DIRS:
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                return path
    try:
        ImageFont.truetype(spec, 12)
        return spec
    except OSError:
        pass
    sys.exit(f"no font matching {spec!r} -- pass a .ttf filename from "
             f"{FONT_DIRS[0]} (for example consolab.ttf or segoeuib.ttf)")


def fit_font(path, samples, max_width, start):
    """Largest size at or below `start` that keeps every sample inside max_width.

    Takes a list so a stats column can be sized against the widest reading any
    of its slots could produce, rather than against whichever one happens to
    be showing when it starts.
    """
    if isinstance(samples, str):
        samples = [samples]
    if not samples:
        return ImageFont.truetype(path, start)

    def fits(size):
        font = ImageFont.truetype(path, size)
        return all(font.getlength(s) <= max_width for s in samples)

    # Width grows monotonically with size, so this can bisect rather than step
    # down one point at a time -- which matters now that the clock starts its
    # search from the full panel width rather than a small cap.
    if fits(start):
        return ImageFont.truetype(path, start)
    low, high = 8, start
    while low < high:
        mid = (low + high + 1) // 2
        if fits(mid):
            low = mid
        else:
            high = mid - 1
    return ImageFont.truetype(path, low)


def _pct(v):
    return f"{v:.0f}%" if v is not None else "--"


def _rate(bps):
    """Bytes per second, compactly: 812K/s, 12.4M/s."""
    if bps is None:
        return "--"
    return f"{bps / 2**20:.1f}M/s" if bps >= 2**20 else f"{bps / 1024:.0f}K/s"


def _mbs(bps):
    """Megabytes per second in at most three characters: 0.4, 9.9, 12, 125.

    Dropping the decimal above 10 keeps a combined down/up reading inside
    "999/999", which is what lets it share a column without shrinking the
    value font for every other slot.
    """
    if bps is None:
        return "--"
    mb = bps / 2**20
    return f"{mb:.1f}" if mb < 10 else f"{mb:.0f}"


# Adapters that carry no real traffic, or that shadow a physical one and so
# would otherwise win "busiest interface" against the NIC underneath them.
VIRTUAL_NICS = ("loopback", "pseudo", "vethernet", "virtualbox", "vmware",
                "hyper-v", "tailscale", "wireguard", "zerotier", "tap-",
                "tunnel", "teredo", "isatap", "bluetooth")


def _is_physical(name):
    low = name.lower()
    return not any(hint in low for hint in VIRTUAL_NICS)


def _busiest_engine(data):
    """Task Manager's GPU figure: sum each engine type, take the busiest.

    Windows reports one instance per engine per process, so neither a raw sum
    (which would exceed 100%) nor a single instance is meaningful on its own.
    """
    totals = {}
    for name, value in data.items():
        kind = name.rsplit("engtype_", 1)[-1] if "engtype_" in name else "?"
        totals[kind] = totals.get(kind, 0.0) + value
    return min(100.0, max(totals.values())) if totals else None


def _sum_values(data):
    return sum(data.values())


def _pooled_disk_activity(data):
    """How hard the drives are working, averaged so all of them make 100%.

    One disk pinned out of three therefore reads about 33%, not 100%.

    This inverts "% Idle Time", which is how Task Manager derives its "Active
    time" column. The more obvious "% Disk Time" counter is queue-length based:
    it reads a flat 0.0 on these drives and can exceed 100% on others, so it
    cannot be used here.
    """
    disks = [v for name, v in data.items() if name != "_Total"]
    if not disks:
        return None
    return sum(max(0.0, min(100.0, 100.0 - v)) for v in disks) / len(disks)


class PdhCounter:
    """One Windows performance counter, held open.

    Needs no driver and no elevation, which is what makes it usable here at
    all. A wildcard path is reduced across its instances by `reduce`.
    """

    def __init__(self, path, wildcard=True, reduce=_sum_values):
        self.reduce, self.wildcard = reduce, wildcard
        self.pdh = self.query = self.counter = None
        try:
            import win32pdh
            self.pdh = win32pdh
            self.query = win32pdh.OpenQuery()
            self.counter = win32pdh.AddCounter(self.query, path)
            win32pdh.CollectQueryData(self.query)
        except Exception:
            self.pdh = self.query = self.counter = None

    def read(self):
        if self.counter is None:
            return None
        try:
            self.pdh.CollectQueryData(self.query)
            if not self.wildcard:
                _, value = self.pdh.GetFormattedCounterValue(
                    self.counter, self.pdh.PDH_FMT_DOUBLE)
                return value
            data = self.pdh.GetFormattedCounterArray(
                self.counter, self.pdh.PDH_FMT_DOUBLE)
        except Exception:
            # A process can exit between collections and invalidate one of its
            # instances, which throws. A single bad sample is not worth dying
            # over -- the display just shows "--" for one tick.
            return None
        return self.reduce(data) if data else None


# name -> (label, widest plausible value, reader). The sample string is what
# the value font gets sized against, so a reading can never overflow its
# column once running. Readers take (metrics, rates-since-last-sample).
SLOTS = {
    "cpu":      ("CPU Usage",    "100%",    lambda m, r: _pct(psutil.cpu_percent(interval=None))),
    "gpu":      ("GPU Usage",    "100%",    lambda m, r: _pct(m.gpu.read())),
    "ram":      ("RAM",          "99.9G",   lambda m, r: f"{psutil.virtual_memory().used / 2**30:.1f}G"),
    "ram%":     ("RAM Usage",    "100%",    lambda m, r: _pct(psutil.virtual_memory().percent)),
    "vram":     ("GPU VRAM",     "99.9G",   lambda m, r: m.scaled(m.vram.read(), 2**30, "G")),
    "clock":    ("CPU Clock",    "9.99G",   lambda m, r: m.cpu_clock()),
    "net":      ("Net D/U MB/s", "999/999", lambda m, r: m.net_du(r)),
    "netdown":  ("Net Down",     "99.9M/s", lambda m, r: _rate(r.get("net_down"))),
    "netup":    ("Net Up",       "99.9M/s", lambda m, r: _rate(r.get("net_up"))),
    "diskio":   ("Disk I/O",     "99.9M/s", lambda m, r: _rate(r.get("disk_io"))),
    "disk%":    ("Drive Usage",  "100%",    lambda m, r: _pct(m.diskact.read())),
    "storage":  ("Storage Used", "100%",    lambda m, r: m.drives_used()),
    "disk":     ("Disk Free",    "999G",    lambda m, r: m.disk_free()),
    "procs":    ("Processes",    "9999",    lambda m, r: str(len(psutil.pids()))),
    "uptime":   ("Uptime",       "99h 59m", lambda m, r: m.uptime()),
    "cputemp":  ("CPU Temp",     "100°C",   lambda m, r: m.temp("cpu")),
    "gputemp":  ("GPU Temp",     "100°C",   lambda m, r: m.temp("gpu")),
}
# Only these need LibreHardwareMonitor, so it is loaded only when one is shown.
TEMP_SLOTS = ("cputemp", "gputemp")
# Typical readings, for previews that should not read the PC (--sample-stats).
SAMPLE_VALUES = {
    "cpu": "12%", "gpu": "8%", "ram": "7.5G", "ram%": "49%", "vram": "0.9G",
    "clock": "4.02G", "net": "0.4/0.1", "netdown": "412K/s", "netup": "96K/s",
    "diskio": "1.1M/s", "disk%": "2%", "storage": "66%", "disk": "64G",
    "procs": "229", "uptime": "1h 41m", "cputemp": "47°C", "gputemp": "51°C",
}
# Slots fill the grid two per row; an odd one at the end is centred across the
# full width, which is why this default has seven. Each usage sits beside its
# temperature.
DEFAULT_SLOTS = "cpu,cputemp,gpu,gputemp,ram%,disk%,net"


class Metrics:
    """Every reading on the panel.

    All but the temperatures come from psutil and Windows performance
    counters, which need no driver and no elevation. Temperatures come from
    temps_win (LibreHardwareMonitor), loaded only when a temperature slot is
    shown.

    Values refresh on an interval rather than per frame: CPU load is a delta
    between samples, so a short window reads as noise, and every changed value
    forces the affected frames to be re-encoded.
    """

    def __init__(self, slots, interval=2.0):
        self.slots, self.interval = slots, interval
        self.gpu = PdhCounter(r"\GPU Engine(*)\Utilization Percentage",
                              reduce=_busiest_engine)
        self.vram = PdhCounter(r"\GPU Process Memory(*)\Dedicated Usage")
        self.diskact = PdhCounter(r"\PhysicalDisk(*)\% Idle Time",
                                  reduce=_pooled_disk_activity)
        # psutil.cpu_freq() only ever reports the base clock on Windows -- it
        # returns the same number no matter what the CPU is doing -- so a live
        # figure that reflects boost has to come from this counter instead.
        self.perf = PdhCounter(
            r"\Processor Information(_Total)\% Processor Performance",
            wildcard=False)
        self.base_mhz = (psutil.cpu_freq().max if psutil.cpu_freq() else 0) or 3500
        self.drive = os.environ.get("SystemDrive", "C:") + "\\"
        psutil.cpu_percent(interval=None)       # prime the delta
        self._nics, self._nic = {}, None
        self._disk = self._t = None
        self._slow = {}
        self._cache = None
        self._next = 0.0
        self.temps = None
        if any(s in TEMP_SLOTS for s in slots):
            from temps_win import Temperatures
            self.temps = Temperatures()

    def _cached(self, key, seconds, fn):
        """Memoise a reading that changes slowly or costs real disk I/O."""
        now = time.monotonic()
        hit = self._slow.get(key)
        if hit is None or now >= hit[0]:
            self._slow[key] = (now + seconds, fn())
        return self._slow[key][1]

    @staticmethod
    def scaled(value, unit, suffix):
        return f"{value / unit:.1f}{suffix}" if value is not None else "--"

    def temp(self, which):
        """CPU or GPU temperature in whole degrees: "47°C"."""
        v = self.temps.read(which) if self.temps else None
        return f"{v:.0f}°C" if v is not None else "--"

    def cpu_clock(self):
        """Live core clock, including boost, in GHz."""
        v = self.perf.read()
        return f"{self.base_mhz * v / 100 / 1000:.2f}G" if v is not None else "--"

    def net_du(self, rates):
        """Download and upload together, in MB/s: "12/1.4"."""
        down, up = rates.get("net_down"), rates.get("net_up")
        if down is None or up is None:
            return "--"
        return f"{_mbs(down)}/{_mbs(up)}"

    def disk_free(self):
        return self._cached("free", 30.0, self._disk_free)

    def _disk_free(self):
        try:
            return f"{psutil.disk_usage(self.drive).free / 2**30:.0f}G"
        except OSError:
            return "--"

    def drives_used(self):
        """Every fixed drive pooled into one percentage.

        Windows has no all-disks figure, so this adds them up: total used over
        total capacity, as though the drives were a single pool. Three drives
        of different sizes therefore weigh by size, not one third each.

        Cached far longer than the other readings -- free space barely moves,
        and there is no reason to wake a spinning disk every two seconds.
        """
        return self._cached("drives", 30.0, self._drives_used)

    @staticmethod
    def _drives_used():
        used = total = 0
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue        # unreadable, or disconnected mid-scan
            used, total = used + usage.used, total + usage.total
        return f"{used / total * 100:.0f}%" if total else "--"

    def uptime(self):
        seconds = int(time.time() - psutil.boot_time())
        days, rem = divmod(seconds, 86400)
        hours, minutes = divmod(rem // 60, 60)
        if days:
            return f"{days}d {hours}h"
        return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"

    def _pick_nic(self, nics, dt):
        """Follow whichever real adapter is actually carrying traffic.

        Summing every interface would count a VPN's traffic twice, once on the
        tunnel and again on the physical NIC beneath it, so this takes the
        busiest single adapter instead.

        The choice is sticky while everything is idle, so the reading does not
        flicker between adapters at rest. When the ethernet is unplugged and
        wifi takes over, the wifi adapter starts winning on traffic and this
        follows it with no configuration.
        """
        up = psutil.net_if_stats()
        best, busiest = None, -1.0
        for name, counters in nics.items():
            previous = self._nics.get(name)
            if previous is None or not _is_physical(name):
                continue
            if name in up and not up[name].isup:
                continue
            moved = ((counters.bytes_recv - previous.bytes_recv)
                     + (counters.bytes_sent - previous.bytes_sent)) / dt
            if moved > busiest:
                best, busiest = name, moved
        gone = self._nic not in nics or (self._nic in up and not up[self._nic].isup)
        if best is not None and (busiest > 0 or gone):
            self._nic = best

    def _rates(self):
        """Throughput since the previous sample.

        Taken once per tick rather than per slot, so that "Net Down" and
        "Net Up" cannot consume each other's delta.
        """
        now = time.monotonic()
        nics = psutil.net_io_counters(pernic=True)
        disk = psutil.disk_io_counters()
        rates = {}
        if self._t is not None and now > self._t:
            dt = now - self._t
            self._pick_nic(nics, dt)
            previous = self._nics.get(self._nic)
            if previous is not None and self._nic in nics:
                current = nics[self._nic]
                rates["net_down"] = (current.bytes_recv - previous.bytes_recv) / dt
                rates["net_up"] = (current.bytes_sent - previous.bytes_sent) / dt
            if self._disk is not None:
                rates["disk_io"] = ((disk.read_bytes - self._disk.read_bytes)
                                    + (disk.write_bytes - self._disk.write_bytes)) / dt
        self._nics, self._disk, self._t = nics, disk, now
        return rates

    def nic(self):
        """The adapter currently being reported, for the startup line."""
        return self._nic or "none yet"

    def labels(self):
        return [SLOTS[name][0] for name in self.slots]

    def refresh(self):
        """Force the next values() call to sample afresh rather than cache."""
        self._next = 0.0

    def values(self):
        now = time.monotonic()
        if self._cache is None or now >= self._next:
            rates = self._rates()
            self._cache = tuple(SLOTS[name][2](self, rates) for name in self.slots)
            self._next = now + self.interval
        return self._cache


class SampleMetrics:
    """Stands in for Metrics in a preview: fixed example values, no sensors."""

    temps = None

    def __init__(self, slots):
        self.slots = slots

    def labels(self):
        return [SLOTS[name][0] for name in self.slots]

    def values(self):
        return tuple(SAMPLE_VALUES[name] for name in self.slots)

    def refresh(self):
        pass

    def nic(self):
        return "example values"


def frost(img, box, blur, darken, corner):
    """Blur and dim a rounded region in place, so text over it stays legible."""
    x0, y0, x1, y1 = (int(v) for v in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    if x1 <= x0 or y1 <= y0:
        return
    region = img.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(blur))
    if darken:
        region = Image.blend(region, Image.new("RGB", region.size, (0, 0, 0)), darken)
    mask = Image.new("L", region.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, region.width - 1, region.height - 1), corner, fill=255)
    img.paste(region, (x0, y0), mask)


def compose(frame, items, panels=(), blur=6, darken=0.5, corner=24):
    """Frost each panel box and draw the text over it, the right way up.

    Kept separate from render() so --preview can save what the panel will
    show without the 180 degree flip making it unreadable.
    """
    img = frame.copy()
    for box in panels:
        frost(img, box, blur, darken, corner)
    draw = ImageDraw.Draw(img)
    for text, font, x, y, anchor in items:
        # "la" = left edge / "ra" = right edge, both on the ascender line.
        draw.text((x, y), text, font=font, fill="white",
                  stroke_width=max(2, font.size // 10), stroke_fill="black",
                  anchor=anchor)
    return img


def render(frame, items, quality, panels=(), blur=6, darken=0.5, corner=24):
    """Compose, rotate 180 for the upside-down panel, encode as JPEG."""
    img = compose(frame, items, panels, blur, darken, corner)
    buf = BytesIO()
    img.transpose(Image.ROTATE_180).save(
        buf, format="JPEG", quality=quality, subsampling=0)
    return buf.getvalue()


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="GIF or image to play under the overlay")
    ap.add_argument("--width", type=int, default=320)
    ap.add_argument("--fit", choices=FIT_MODES,
                    help="place the picture on a panel-shaped canvas: fill "
                         "(crop to cover), blur (whole picture, blurred "
                         "edges) or color (whole picture, --fit-color edges). "
                         "Default: scale to --width and keep the shape")
    ap.add_argument("--fit-color", type=ImageColor.getrgb, default="#000000",
                    help="edge colour for --fit color (default #000000)")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--quality", type=int, default=88)
    ap.add_argument("--port", help="COM port (default: located by VID:PID)")
    ap.add_argument("--font", default="consolab.ttf",
                    help="font file in the Windows font directory (default consolab.ttf)")
    ap.add_argument("--time-size", type=int, default=0)
    ap.add_argument("--date-size", type=int, default=0)
    ap.add_argument("--text-fill", type=float, default=0.82,
                    help="how much of the panel width the clock and date span, "
                         "0-1 (default 0.82; 1.0 runs right up to the padding)")
    ap.add_argument("--label-size", type=int, default=0)
    ap.add_argument("--value-size", type=int, default=0)
    ap.add_argument("--stats-y", type=int, default=0,
                    help="top of the stats block; default sits below the figure")
    ap.add_argument("--no-stats", action="store_true", help="clock and date only")
    ap.add_argument("--slots", default=DEFAULT_SLOTS,
                    help="comma-separated readings, filling the grid two per "
                         f"row (default {DEFAULT_SLOTS}). "
                         f"Available: {', '.join(SLOTS)}")
    ap.add_argument("--preview", metavar="FILE",
                    help="save one frame to FILE and exit, without opening the "
                         "panel -- lets you tune layout while it keeps running")
    ap.add_argument("--sample-stats", action="store_true",
                    help="with --preview: show example readings instead of "
                         "this PC's, without loading any sensors")
    ap.add_argument("--no-frost", action="store_true",
                    help="no blurred panel behind the text")
    ap.add_argument("--frost-blur", type=float, default=9.0)
    ap.add_argument("--frost-dark", type=float, default=0.5,
                    help="0 = blur only, 1 = solid black")
    ap.add_argument("--frost-corner", type=int, default=26)
    ap.add_argument("--frost-inset", type=int, default=12,
                    help="gap between the panel and the screen edge")
    ap.add_argument("--frost-pad", type=int, default=18,
                    help="gap between the panel edge and the text, sideways")
    ap.add_argument("--frost-pad-y", type=int, default=24,
                    help="same, above and below")
    return ap


def main(argv=None, stop=None, log=None):
    """Run the clock until stopped.

    From the command line this parses sys.argv and runs until Ctrl+C. The
    service calls it with its own `argv`, a threading.Event as `stop`, and a
    `log` function; it then returns once `stop` is set, with the port closed.
    Errors still end it with SystemExit(message), which the service catches.
    """
    log = log or (lambda message: print(message, flush=True))
    args = build_parser().parse_args(argv)

    frames = load_frames(args.source, args.width, args.fit, args.fit_color)
    w, h = frames[0].size
    if w * h > MAX_PIXELS:
        sys.exit(f"{w}x{h} is {w * h:,} px, over the {MAX_PIXELS:,} px limit")

    slots = [s.strip().lower() for s in args.slots.split(",") if s.strip()]
    unknown = [s for s in slots if s not in SLOTS]
    if unknown:
        sys.exit(f"unknown slot(s) {', '.join(unknown)} -- available: "
                 f"{', '.join(SLOTS)}")
    if not slots:
        sys.exit("--slots needs at least one reading")

    # Screen edge -> inset -> panel edge -> pad -> text.
    inset, padx, pady = args.frost_inset, args.frost_pad, args.frost_pad_y
    col_left, col_right = inset + padx, w - inset - padx
    path = resolve_font(args.font)
    avail = col_right - col_left
    # No default size cap: both lines grow until they span --text-fill of the
    # panel, so the clock and the date come out the same width as each other
    # and scale together. The samples are the widest each can ever be --
    # "12:00 PM" beats "1:05 AM", and a two-digit day beats the one-digit day
    # that %#d can produce, so neither can outgrow its box at runtime.
    # --time-size / --date-size still cap them individually.
    text_width = max(1, round(avail * args.text_fill))
    time_font = fit_font(path, "12:00 PM", text_width, args.time_size or avail)
    date_font = fit_font(path, "(Wed) 30-Sep-2026", text_width,
                         args.date_size or avail)

    if args.no_stats:
        stats = None
    elif args.preview and args.sample_stats:
        stats = SampleMetrics(slots)
    else:
        stats = Metrics(slots)
    temps = stats.temps if stats else None
    if temps:
        log(temps.status())
    # Each column gets half the width, less a gap so they never collide, but a
    # centred odd slot gets the lot. The two groups are therefore sized
    # against different limits and whichever comes out smaller wins. Sizing
    # against every slot's widest plausible value means a reading can never
    # outgrow its column once it is running.
    half = (avail - 10) // 2
    paired = slots[:len(slots) - len(slots) % 2] if stats else []
    centred = slots[len(paired):] if stats else []
    smallest = lambda a, b: a if a.size <= b.size else b
    label_start = args.label_size or w // 20
    value_start = args.value_size or w // 11
    label_font = smallest(
        fit_font(path, [SLOTS[s][0] for s in paired], half, label_start),
        fit_font(path, [SLOTS[s][0] for s in centred], avail, label_start))
    value_font = smallest(
        fit_font(path, [SLOTS[s][1] for s in paired], half, value_start),
        fit_font(path, [SLOTS[s][1] for s in centred], avail, value_start))

    time_y = inset + pady
    date_y = time_y + round(time_font.size * 1.15)
    stats_y = args.stats_y or round(h * 0.74)
    label_h = round(label_font.size * 1.3)
    value_h = round(value_font.size * 1.35)
    block_h = label_h + value_h + round(value_font.size * 0.55)

    if stats:
        rows = (len(slots) + 1) // 2
        stats_bottom = stats_y + (rows - 1) * block_h + label_h + value_font.size
        # Keep a tall block on screen: at the default y, six rows would run off
        # the bottom of the frame and the last reading would be cut in half.
        overflow = stats_bottom + pady + inset - h
        if overflow > 0:
            stats_y -= overflow
            stats_bottom -= overflow

    panels = []
    if not args.no_frost:
        panels.append((inset, time_y - pady, w - inset, date_y + date_font.size + pady))
        if stats:
            panels.append((inset, stats_y - pady, w - inset, stats_bottom + pady))

    def tick():
        """The clock strings and stat values for right now."""
        now = time.localtime()
        return ((time.strftime(TIME_FMT, now), time.strftime(DATE_FMT, now)),
                stats.values() if stats else ())

    def build_items(clock, vals):
        """One tick's worth of (text, font, x, y, anchor) tuples."""
        # Centred: "ma" is middle horizontally, ascender line vertically.
        items = [
            (clock[0], time_font, w // 2, time_y, "ma"),
            (clock[1], date_font, w // 2, date_y, "ma"),
        ]
        if stats:
            labels = stats.labels()
            for n in range(0, len(vals), 2):
                top = stats_y + (n // 2) * block_h
                if n + 1 < len(vals):
                    # A full row: left column anchored "la", right "ra".
                    items += [
                        (labels[n], label_font, col_left, top, "la"),
                        (labels[n + 1], label_font, col_right, top, "ra"),
                        (vals[n], value_font, col_left, top + label_h, "la"),
                        (vals[n + 1], value_font, col_right, top + label_h, "ra"),
                    ]
                else:
                    # An odd slot at the end, centred across the full width.
                    items += [
                        (labels[n], label_font, w // 2, top, "ma"),
                        (vals[n], value_font, w // 2, top + label_h, "ma"),
                    ]
        return items

    if args.preview:
        try:
            if isinstance(stats, Metrics):
                # Prime, wait, then force a fresh sample: CPU load and the
                # throughput slots are deltas and read as 0 or "--" until
                # there are two samples to subtract.
                stats.values()
                time.sleep(0.6)
                stats.refresh()
            clock, vals = tick()
            compose(frames[0], build_items(clock, vals), panels, args.frost_blur,
                    args.frost_dark, args.frost_corner).save(args.preview)
        finally:
            if temps:
                temps.close()
        following = (f", following {stats.nic()}" if stats
                     and any(s.startswith("net") for s in slots) else "")
        log(f"preview written to {args.preview} ({w}x{h}), "
            f"clock {time_font.size}px / date {date_font.size}px"
            f"{following} -- panel untouched")
        return

    header = struct.pack("<IHHHH", MAGIC, w, h, 0, 1)
    try:
        panel = Panel(args.port)
    except BaseException:
        if temps:
            temps.close()
        raise
    if stop is None:
        # Command line only: signal handlers can only be set from the main
        # thread, and the service stops the loop through `stop` instead.
        atexit.register(panel.close)
        install_handlers()
    log(f"{len(frames)} frames at {w}x{h} ({w * h:,} px), {args.fps:g} FPS, "
        f"on {panel.port}, font {os.path.basename(path)}, "
        f"clock {time_font.size}px"
        + ("" if stats is None else
           f", slots {'/'.join(slots)} at y={stats_y} "
           f"(labels {label_font.size}px, values {value_font.size}px)"))
    try:
        _stream(panel, header, frames, tick, build_items, panels, args, stop)
    finally:
        panel.close()
        if temps:
            temps.close()


def _stream(panel, header, frames, tick, build_items, panels, args, stop):
    """Send frames forever, or until `stop` is set."""
    # Re-rendering every frame each tick is wasteful, so cache per frame and
    # only redraw when the displayed text actually changes.
    cache = {}
    period = 1.0 / args.fps
    i = 0
    while stop is None or not stop.is_set():
        start = time.time()
        clock, vals = tick()
        stamp = clock + vals

        idx = i % len(frames)
        cached = cache.get(idx)
        if cached is None or cached[0] != stamp:
            payload = header + render(frames[idx], build_items(clock, vals),
                                      args.quality, panels, args.frost_blur,
                                      args.frost_dark, args.frost_corner)
            cache[idx] = (stamp, payload)
        else:
            payload = cached[1]
        panel.send(payload)
        i += 1
        wait = max(0, period - (time.time() - start))
        if stop is None:
            time.sleep(wait)
        else:
            stop.wait(wait)


if __name__ == "__main__":
    main()
