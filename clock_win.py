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
  stats       load and memory from psutil and Windows performance counters;
              temperatures from temps_win (see Metrics below)

Usage:
    clock_win.py <source> [--width 320] [--fps 10] [--font consolab.ttf]
"""

import argparse
import atexit
import calendar
import datetime
import functools
import os
import re
import struct
import sys
import threading
import time
import zlib
from io import BytesIO

import psutil
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from lcd_win import (FIT_MODES, MAGIC, MAX_PIXELS, MAX_ZOOM, Panel, fit_frame,
                     fit_size, install_handlers, load_frames)

# The clock's formats: (without seconds, with seconds). %#I / %#d drop the
# leading zero on Windows. The %-I form found in many Python examples is not
# supported here and raises ValueError.
TIME_FORMATS = {
    "12h":       ("%#I:%M %p", "%#I:%M:%S %p"),     # 3:25 PM
    "12h-plain": ("%#I:%M", "%#I:%M:%S"),           # 3:25
    "24h":       ("%H:%M", "%H:%M:%S"),             # 15:25
}
# The date is written as a pattern of these codes (see date_strftime).
DATE_FORMAT = "(ddd) D-MMM-YYYY"                    # (Sun) 20-Sep-2026
DATE_CODES = {
    "dddd": "%A", "ddd": "%a",                      # Sunday, Sun
    "DD": "%d", "D": "%#d",                         # 05, 5
    "MMMM": "%B", "MMM": "%b", "MM": "%m", "M": "%#m",   # September, Sep, 09, 9
    "YYYY": "%Y", "YY": "%y",                       # 2026, 26
}
_DATE_TOKEN = re.compile(r"\[([^\]]*)\]|dddd|ddd|DD|D|MMMM|MMM|MM|M|YYYY|YY")

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


@functools.lru_cache(maxsize=64)
def _font(path, size):
    return ImageFont.truetype(path, size)


def fit_font(path, samples, max_width, start):
    """Largest size at or below `start` that keeps every sample inside max_width.

    Takes a list so a stats column can be sized against the widest reading any
    of its slots could produce, rather than against whichever one happens to
    be showing when it starts.
    """
    if isinstance(samples, str):
        samples = [samples]
    return _font(path, _fit_size(path, tuple(samples), max_width, start))


@functools.lru_cache(maxsize=512)
def _fit_size(path, samples, max_width, start):
    # Cached: the app's preview asks the same questions on every redraw.
    if not samples:
        return start

    def fits(size):
        font = _font(path, size)
        return all(font.getlength(s) <= max_width for s in samples)

    # Width grows monotonically with size, so this can bisect rather than step
    # down one point at a time -- which matters now that the clock starts its
    # search from the full panel width rather than a small cap.
    if fits(start):
        return start
    low, high = 8, start
    while low < high:
        mid = (low + high + 1) // 2
        if fits(mid):
            low = mid
        else:
            high = mid - 1
    return low


@functools.lru_cache(maxsize=64)
def _widest(path, samples, keep=4):
    """The few widest of many candidate strings, measured at one size.

    A date has 365 possible strings; sizing against only the widest handful
    gives the same answer far faster.
    """
    font = _font(path, 48)
    return tuple(sorted(set(samples), key=font.getlength, reverse=True)[:keep])


def date_strftime(pattern):
    """Turn a date pattern such as "ddd D-MMM-YYYY" into a strftime format.

    The codes are in DATE_CODES; text in [square brackets] is kept as it is,
    so "[Today:] D MMM" does not turn the D of "Today" into a day number.
    """
    out, pos = [], 0
    for m in _DATE_TOKEN.finditer(pattern):
        out.append(pattern[pos:m.start()].replace("%", "%%"))
        literal = m.group(1)
        out.append(literal.replace("%", "%%") if literal is not None
                   else DATE_CODES[m.group(0)])
        pos = m.end()
    out.append(pattern[pos:].replace("%", "%%"))
    return "".join(out)


@functools.lru_cache(maxsize=16)
def time_samples(fmt):
    """Every width the clock can take: each hour, with a spread of minutes."""
    return tuple(datetime.datetime(2026, 1, 1, hour, minute, minute).strftime(fmt)
                 for hour in range(24) for minute in (0, 8, 44, 58))


@functools.lru_cache(maxsize=16)
def date_samples(fmt):
    """Every date of a year, so the widest day and month names are covered."""
    day = datetime.date(2026, 1, 1)
    return tuple((day + datetime.timedelta(n)).strftime(fmt) for n in range(365))


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

    def close(self):
        if self.query is not None:
            try:
                self.pdh.CloseQuery(self.query)
            except Exception:
                pass
            self.query = self.counter = None


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
    "disk%":    ("Disk Usage",   "100%",    lambda m, r: _pct(m.diskact.read())),
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
# temperature. "-" marks an empty spot (see stat_rows).
DEFAULT_SLOTS = "cpu,cputemp,gpu,gputemp,ram%,disk%,net"
EMPTY_SLOT = "-"
# The app offers this many spots: three rows of two and one centred.
MAX_SPOTS = 7


def parse_spots(text):
    """--slots as a list of slot names, with None for each empty spot."""
    spots = []
    for name in text.split(","):
        name = name.strip().lower()
        if name == EMPTY_SLOT:
            spots.append(None)
        elif name:
            spots.append(name)
    return spots


def stat_rows(spots, columns=2):
    """Group the spots into screen rows.

    With two columns, spots pair up in order; a row with one empty spot centres
    the other stat, and a row with both empty is left out, so the screen never
    shows a hole. With one column, each stat gets a centred row of its own.
    """
    size = 2 if columns == 2 else 1
    rows = []
    for i in range(0, len(spots), size):
        row = [name for name in spots[i:i + size] if name]
        if row:
            rows.append(row)
    return rows


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

    def __init__(self, slots, interval=2.0, get_temps=None):
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
        # The service shares one Temperatures between settings changes
        # (get_temps): opening LibreHardwareMonitor takes about a second.
        self.temps, self._own_temps = None, False
        if any(s in TEMP_SLOTS for s in slots):
            if get_temps:
                self.temps = get_temps()
            else:
                from temps_win import Temperatures
                self.temps, self._own_temps = Temperatures(), True

    def close(self):
        for counter in (self.gpu, self.vram, self.diskact, self.perf):
            counter.close()
        if self.temps and self._own_temps:
            self.temps.close()

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

    def close(self):
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


class Shape:
    """A drawn item that isn't text: a to-do box or tick, a strike-through,
    the calendar's mark on today. Edged in black like the text's outline, so
    it reads over any picture.

      box   outline of a rounded rectangle      mark  filled rounded rectangle
      line  a line through the points in `xy`
    """

    def __init__(self, kind, xy, fill, width=2, radius=0):
        self.kind, self.xy, self.fill = kind, xy, fill
        self.width, self.radius = width, radius

    def draw(self, draw):
        if self.kind == "line":
            draw.line(self.xy, fill="black", width=self.width + 2, joint="curve")
            draw.line(self.xy, fill=self.fill, width=self.width, joint="curve")
        elif self.kind == "box":
            draw.rounded_rectangle(self.xy, self.radius, outline="black", width=self.width + 2)
            x0, y0, x1, y1 = self.xy
            draw.rounded_rectangle((x0 + 1, y0 + 1, x1 - 1, y1 - 1), self.radius,
                                   outline=self.fill, width=self.width)
        else:
            draw.rounded_rectangle(self.xy, self.radius, fill=self.fill,
                                   outline="black", width=1)


def compose(frame, items, panels=(), blur=6, darken=0.5, corner=24):
    """Frost each panel box and draw the text over it, the right way up.

    This is what --preview saves: the panel's picture without the 180 degree
    flip that makes it readable in the case. Each item is a Shape or
    (text, font, x, y, anchor, colour), optionally with a seventh element,
    False, for text drawn without its black outline.
    """
    img = frame.copy()
    for box in panels:
        frost(img, box, blur, darken, corner)
    draw = ImageDraw.Draw(img)
    for item in items:
        if isinstance(item, Shape):
            item.draw(draw)
            continue
        text, font, x, y, anchor, fill = item[:6]
        outline = item[6] if len(item) > 6 else True
        # "la" = left edge / "ra" = right edge, both on the ascender line.
        draw.text((x, y), text, font=font, fill=fill,
                  stroke_width=max(2, font.size // 10) if outline else 0,
                  stroke_fill="black", anchor=anchor)
    return img


def dim(img, percent):
    """Scale an image's colours to `percent` of their brightness.

    The panel's backlight can't be controlled (see docs/HOW-IT-WORKS.md), so
    --brightness darkens the pixels instead. A lookup table, so it's cheap;
    an RGBA image keeps its alpha.
    """
    if percent >= 100:
        return img
    lut = [round(v * percent / 100) for v in range(256)] * 3
    if img.mode == "RGBA":
        lut += list(range(256))
    return img.point(lut)


def text_bands(items, size, brightness=100):
    """One tick's text, drawn once to be laid over every frame.

    Returns (y, strip) pairs: the rows of the upside-down frame that hold
    text, each an RGBA strip whose alpha is the text's coverage. Every glyph
    has a black outline, so laying this over a frame gives the pixels that
    drawing on the frame itself would -- bar a handful (20 of 472,320 in
    tests) where the outline and the fill both only partly cover a pixel,
    which come out a few shades darker. JPEG changes pixels by more.

    The strips are dimmed like the frames (see prepare), so the whole screen
    comes out at `brightness`.
    """
    layer = compose(Image.new("RGBA", size, (0, 0, 0, 0)), items)
    layer = dim(layer, brightness).transpose(Image.ROTATE_180)
    rows = layer.getchannel("A").getprojection()[1]
    # Runs of rows with text in them, joined across small gaps.
    runs, start = [], None
    for y, inked in enumerate(list(rows) + [0]):
        if inked and start is None:
            start = y
        elif not inked and start is not None:
            if runs and start - runs[-1][1] < 16:
                runs[-1] = (runs[-1][0], y)
            else:
                runs.append((start, y))
            start = None
    return [(y0, layer.crop((0, y0, size[0], y1))) for y0, y1 in runs]


def encode_with(frame, size, bands, quality):
    """Lay the text strips over a prepared frame (see prepare) and encode it
    as JPEG."""
    img = unpack(frame, size) if isinstance(frame, bytes) else frame.copy()
    for y, strip in bands:
        img.paste(strip, (0, y), strip)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality, subsampling=0)
    return buf.getvalue()


BLOCK_GAP = 12                      # between blocks stacked automatically
EXTRA_BLOCKS = ("note", "countdown", "todo")    # in their stacking order
ALIGN = {"left": "la", "center": "ma", "right": "ra"}
WEEKDAY_LETTERS = "MTWTFSS"         # Monday first
COUNTDOWN_MODES = ("to", "to-hours", "since")
DONE_SHADE = 0.55                   # how bright a ticked to-do item stays


def wrap(text, font, width):
    """`text` broken into lines no wider than `width`: between words, or
    inside a word too long for a line of its own. Blank lines are kept."""
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}" if line else word
            if font.getlength(candidate) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            while len(word) > 1 and font.getlength(word) > width:
                cut = len(word) - 1
                while cut > 1 and font.getlength(word[:cut]) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def countdown_text(mode, target, label, now):
    """The countdown's line at `now` (None when it has nothing to show), and
    when that line next changes (a datetime, or None if it never will).

      to        "12 days to Launch", "Launch is today!", then nothing
      to-hours  "12 days 5 h to Launch", "3 h to Launch", "Launch is today!"
      since     "214 days since I quit" -- from the date on
    """
    midnight = datetime.datetime.combine(now.date() + datetime.timedelta(1),
                                         datetime.time.min)
    today = f"{label} is today!" if label else "Today!"
    if mode == "since":
        days = (now.date() - target.date()).days
        if days < 0:
            return None, datetime.datetime.combine(target.date(), datetime.time.min)
        return (f"{_plural(days, 'day')} since {label}" if label
                else _plural(days, "day")), midnight
    if mode == "to-hours":
        left = (target - now).total_seconds()
        if left > 0:
            days, hours = int(left // 86400), int(left % 86400 // 3600)
            if days or hours:
                span = " ".join(([_plural(days, "day")] if days else []) + [f"{hours} h"])
            else:
                span = "Under an hour"
            text = f"{span} to {label}" if label else f"{span} to go"
            # The hours figure drops each time `left` passes a whole hour.
            return text, now + datetime.timedelta(seconds=(left % 3600) or 3600)
    else:
        days = (target.date() - now.date()).days
        if days > 0:
            return (f"{_plural(days, 'day')} to {label}" if label
                    else f"{_plural(days, 'day')} to go"), midnight
    if now.date() == target.date():
        return today, midnight
    return None, None


def calendar_weeks(style, week_start, today):
    """The calendar's content for `today`: (header or None, weekday letters,
    weeks), each week seven cells of (date, faint) or None for a blank.

      month  "September 2026" over the month, blank cells outside it
      week   this week only, other months' days faint
    """
    first = 0 if week_start == "mon" else 6
    letters = WEEKDAY_LETTERS[first:] + WEEKDAY_LETTERS[:first]
    weeks = calendar.Calendar(first).monthdatescalendar(today.year, today.month)
    if style == "week":
        week = next(w for w in weeks if today in w)
        return None, letters, [[(d, d.month != today.month) for d in week]]
    return (today.strftime("%B %Y"), letters,
            [[(d, False) if d.month == today.month else None for d in w] for w in weeks])


class Layout:
    """Where everything goes on a w x h frame, and the fonts it is drawn in.

    Two kinds of content:
      items()  the clock, the date and the stats, which change as it runs;
               drawn once per change and laid over each frame (text_bands)
      static   the text, countdown and to-do blocks and the calendar, which
               only change at a known moment (`valid_until`); drawn into the
               frames once, when they are prepared

    It also records every block's box, how large each kind of text could grow
    (`limits`), and where each block sits as a share of the height it can move
    through (`positions`) -- what the app's sliders and dragging use.
    """

    def __init__(self, args, w, h, rows, labels, now=None):
        now = now or datetime.datetime.now()
        self.w, self.h, self.rows, self.labels = w, h, rows, labels
        self.show_time, self.show_date = not args.no_time, not args.no_date
        self.colors = {"time": args.time_color, "date": args.date_color,
                       "label": args.label_color, "value": args.value_color}
        self.time_fmt = TIME_FORMATS[args.time_format][1 if args.seconds else 0]
        self.date_fmt = date_strftime(args.date_format)
        self.limits, self.boxes, self.positions = {}, {}, {}
        self.sizes = {}                 # each extra block's text size, as drawn
        self.static = []
        changes = []                    # when static content next changes

        # Screen edge -> inset -> panel edge -> pad -> text.
        inset, padx, pady = args.frost_inset, args.frost_pad, args.frost_pad_y
        self.col_left, self.col_right = inset + padx, w - inset - padx
        avail = max(1, self.col_right - self.col_left)
        path = self.font_path = resolve_font(args.font)
        align_x = {"left": self.col_left, "center": w // 2, "right": self.col_right}

        # No default size cap: both lines grow until they span --text-fill of
        # the panel, so the clock and the date come out the same width as each
        # other and scale together. They are sized against the widest string
        # each can ever produce -- "12:00 PM" beats "1:05 AM", and a two-digit
        # day beats a one-digit one -- so neither can outgrow its box at
        # runtime. --time-size / --date-size size them individually instead,
        # up to the full width.
        text_width = max(1, round(avail * args.text_fill))

        def line_font(kind, samples, size):
            widest = _widest(path, samples)
            self.limits[kind] = fit_font(path, widest, avail, 2 * avail).size
            if size:
                return fit_font(path, widest, avail, size)
            return fit_font(path, widest, text_width, avail)

        self.time_font = (line_font("time", time_samples(self.time_fmt), args.time_size)
                          if self.show_time else None)
        self.date_font = (line_font("date", date_samples(self.date_fmt), args.date_size)
                          if self.show_date else None)

        # Each column gets half the width, less a gap so they never collide,
        # but a centred stat gets the lot. The two groups are therefore sized
        # against different limits and whichever comes out smaller wins.
        # Sizing against every slot's widest plausible value means a reading
        # can never outgrow its column once it is running.
        half = (avail - 10) // 2
        paired = [name for row in rows if len(row) == 2 for name in row]
        single = [name for row in rows if len(row) == 1 for name in row]

        def stat_font(kind, text_of, size, default):
            def sized(start):
                fonts = [fit_font(path, [text_of(n) for n in names], width, start)
                         for names, width in ((paired, half), (single, avail)) if names]
                return min(fonts, key=lambda f: f.size) if fonts else _font(path, start)
            self.limits[kind] = sized(avail).size
            return sized(size or default)

        self.label_font = stat_font("label", lambda n: labels[n], args.label_size, w // 20)
        self.value_font = stat_font("value", lambda n: SLOTS[n][1], args.value_size, w // 11)

        # The calendar, under the date (or in its place) in the clock block.
        calendar_h, draw_calendar = 0, None
        if args.calendar:
            calendar_h, draw_calendar = self._calendar(args, path, avail, now)
            changes.append(datetime.datetime.combine(now.date() + datetime.timedelta(1),
                                                     datetime.time.min))

        # The clock block: at the top unless --clock-pos moves it.
        self.clock_box, self.clock_pos = None, 0.0
        date_off = round(self.time_font.size * 1.15) if self.show_time else 0
        lines_h = (date_off + self.date_font.size if self.show_date
                   else self.time_font.size if self.show_time else 0)
        calendar_off = lines_h + (round(self._calendar_size * 0.8)
                                  if lines_h and draw_calendar else 0)
        if draw_calendar:
            lines_h = calendar_off + calendar_h
        if lines_h:
            panel_h = lines_h + 2 * pady
            travel = h - 2 * inset - panel_h
            top = inset + round(max(0, travel) * (args.clock_pos or 0))
            self.clock_pos = (top - inset) / travel if travel > 0 else 0.0
            self.clock_box = (inset, top, w - inset, top + panel_h)
            self.time_y = top + pady
            self.date_y = self.time_y + date_off
            if draw_calendar:
                self.static += draw_calendar(self.time_y + calendar_off)
            self._place("clock", self.clock_box, self.clock_pos)

        # The stats block.
        self.label_h = round(self.label_font.size * 1.3)
        self.value_h = round(self.value_font.size * 1.35)
        self.block_h = self.label_h + self.value_h + round(self.value_font.size * 0.55)
        self.stats_box, self.stats_pos, self.overflow = None, 0.0, 0
        if rows:
            stats_h = (len(rows) - 1) * self.block_h + self.label_h + self.value_font.size
            panel_h = stats_h + 2 * pady
            travel = h - 2 * inset - panel_h
            if args.stats_pos is not None:
                y = inset + pady + round(max(0, travel) * args.stats_pos)
            else:
                y = args.stats_y or round(h * 0.74)
                # Keep a tall block on screen: at the default y, six rows would
                # run off the bottom of the frame and the last reading would be
                # cut in half.
                overflow = y + stats_h + pady + inset - h
                if overflow > 0:
                    y -= overflow
            self.stats_y = y
            top = y - pady
            self.stats_pos = (min(1.0, max(0.0, (top - inset) / travel))
                              if travel > 0 else 0.0)
            # How far a block too tall for the screen runs off it.
            self.overflow = max(0, -travel)
            self.stats_box = (inset, top, w - inset, top + panel_h)
            self._place("stats", self.stats_box, self.stats_pos)

        self.panels = [] if args.no_frost else [
            box for box in (self.clock_box, self.stats_box) if box]

        # The extra blocks, stacked under the clock unless moved.
        builders = {"note": self._note, "countdown": self._countdown, "todo": self._todo}
        stack = (self.clock_box[3] if self.clock_box else inset - BLOCK_GAP) + BLOCK_GAP
        for name in EXTRA_BLOCKS:
            content_h, draw, change = builders[name](args, path, avail, align_x, now)
            if change:
                changes.append(change)
            if not draw:
                continue
            panel_h = content_h + 2 * pady
            travel = h - 2 * inset - panel_h
            pos = getattr(args, f"{name}_pos")
            if pos is None:
                top = min(stack, h - inset - panel_h) if travel > 0 else inset
            else:
                top = inset + round(max(0, travel) * pos)
            stack = top + panel_h + BLOCK_GAP
            box = (inset, top, w - inset, top + panel_h)
            self._place(name, box, (top - inset) / travel if travel > 0 else 0.0)
            self.static += draw(top + pady)
            if not args.no_frost and not getattr(args, f"{name}_no_frost"):
                self.panels.append(box)

        # When the static content goes out of date, as epoch seconds.
        self.valid_until = min(changes).timestamp() if changes else None

    def _place(self, name, box, pos):
        self.boxes[name] = box
        self.positions[name] = min(1.0, max(0.0, pos))

    def _calendar(self, args, path, avail, now):
        """(height, draw(top)) for the calendar."""
        header, letters, weeks = calendar_weeks(args.calendar, args.week_start, now.date())
        col_w = avail / 7
        cell = max(1, int(col_w * 0.8))
        samples = ["30", "31", "28", *letters]
        wide = [header] if header else []
        self.limits["calendar"] = min([fit_font(path, samples, cell, 400).size]
                                      + [fit_font(path, wide, avail, 400).size] * bool(wide))
        font = fit_font(path, samples, cell, args.calendar_size or self.w // 16)
        if header:
            font = min(font, fit_font(path, header, avail, font.size), key=lambda f: f.size)
        self._calendar_size = size = self.sizes["calendar"] = font.size
        row_h = round(size * 1.5)
        n_rows = (1 if header else 0) + 1 + len(weeks)
        color = args.calendar_color
        faint = tuple(round(c * 0.45) for c in color)
        box_top, box_bottom = font.getbbox("30", anchor="ma")[1::2]
        pad = max(2, size // 6)
        today = now.date()

        def x_of(col):
            return round(self.col_left + col_w * (col + 0.5))

        def draw(top):
            items, y = [], top
            if header:
                items.append((header, font, self.w // 2, y, "ma", color))
                y += row_h
            items += [(letter, font, x_of(c), y, "ma", faint) for c, letter in enumerate(letters)]
            y += row_h
            for week in weeks:
                for c, cell_ in enumerate(week):
                    if cell_ is None:
                        continue
                    day, dim_it = cell_
                    x = x_of(c)
                    if day == today:
                        half = col_w * 0.42
                        items.append(Shape("mark", (x - half, y + box_top - pad,
                                                    x + half, y + box_bottom + pad),
                                           color, radius=pad + 2))
                        # Dark on the mark, and without the outline, which
                        # would swallow a dark number.
                        items.append((str(day.day), font, x, y, "ma", (0, 0, 0), False))
                    else:
                        items.append((str(day.day), font, x, y, "ma", faint if dim_it else color))
                y += row_h
            return items

        return (n_rows - 1) * row_h + size, draw

    def _note(self, args, path, avail, align_x, now):
        """(height, draw(top), None) for the free text block."""
        text = "\n".join(args.note_line).strip("\n")
        if not text.strip():
            return 0, None, None
        words = [w for w in text.split() if w] or [text]
        self.limits["note"] = min(200, fit_font(path, max(words, key=len), avail, 400).size)
        font = _font(path, min(args.note_size or self.w // 16, self.limits["note"]))
        self.sizes["note"] = font.size
        lines = wrap(text, font, avail)
        line_h = round(font.size * 1.3)
        x, anchor, color = align_x[args.note_align], ALIGN[args.note_align], args.note_color

        def draw(top):
            return [(line, font, x, top + i * line_h, anchor, color)
                    for i, line in enumerate(lines) if line]

        return (len(lines) - 1) * line_h + font.size, draw, None

    def _countdown(self, args, path, avail, align_x, now):
        """(height, draw(top), when it changes) for the countdown line."""
        if not args.countdown_date:
            return 0, None, None
        text, change = countdown_text(args.countdown_mode, args.countdown_date,
                                      args.countdown_label, now)
        if not text:
            return 0, None, change
        self.limits["countdown"] = fit_font(path, text, avail, 400).size
        font = fit_font(path, text, avail, args.countdown_size or self.w // 13)
        self.sizes["countdown"] = font.size
        x, anchor = align_x[args.countdown_align], ALIGN[args.countdown_align]
        return font.size, lambda top: [(text, font, x, top, anchor, args.countdown_color)], change

    def _todo(self, args, path, avail, align_x, now):
        """(height, draw(top), None) for the to-do list: an optional title,
        then each item with its box; ticked items are ticked, crossed out and
        dimmed."""
        items = [parse_todo(item) for item in args.todo_item]
        title = args.todo_title.strip()
        if not items and not title:
            return 0, None, None
        size = args.todo_size or self.w // 16
        box = round(size * 0.8)
        gap = round(size * 0.45)
        words = [w for _, text in items for w in text.split()] + title.split() or ["x"]
        self.limits["todo"] = min(200, fit_font(path, max(words, key=len),
                                                max(1, avail - box - gap), 400).size)
        font = _font(path, min(size, self.limits["todo"]))
        size = self.sizes["todo"] = font.size
        box, gap = round(size * 0.8), round(size * 0.45)
        line_h = round(size * 1.3)
        # Boxes sit on the middle of a capital, strike-throughs on the middle
        # of a small letter -- measured, since fonts differ.
        cap = font.getbbox("H", anchor="la")
        small = font.getbbox("x", anchor="la")
        cap_mid, small_mid = (cap[1] + cap[3]) / 2, (small[1] + small[3]) / 2
        color = args.todo_color
        done_color = tuple(round(c * DONE_SHADE) for c in color)
        title_lines = wrap(title, font, avail) if title else []
        rows = [(done, wrap(text, font, max(1, avail - box - gap))) for done, text in items]
        height = (len(title_lines) + sum(len(lines) for _, lines in rows)) * line_h - (line_h - size)
        align = args.todo_align
        edge = max(2, size // 10)

        def draw(top):
            out, y = [], top
            for line in title_lines:
                out.append((line, font, align_x[align], y, ALIGN[align], color))
                y += line_h
            for done, lines in rows:
                row_w = box + gap + max(font.getlength(line) for line in lines)
                x0 = (self.col_left if align == "left" else
                      self.col_right - row_w if align == "right" else self.w / 2 - row_w / 2)
                x0 = round(x0)
                by = y + round(cap_mid - box / 2)
                out.append(Shape("box", (x0, by, x0 + box, by + box), color, edge, radius=2))
                if done:
                    out.append(Shape("line", [(x0 + box * 0.2, by + box * 0.52),
                                              (x0 + box * 0.43, by + box * 0.76),
                                              (x0 + box * 0.82, by + box * 0.24)],
                                     color, max(2, size // 8)))
                tx = x0 + box + gap
                for j, line in enumerate(lines):
                    ly = y + j * line_h
                    out.append((line, font, tx, ly, "la", done_color if done else color))
                    if done and line:
                        mid = ly + round(small_mid)
                        out.append(Shape("line", [(tx, mid), (tx + font.getlength(line), mid)],
                                         done_color, max(1, size // 12)))
                y += len(lines) * line_h
            return out

        return height, draw, None

    def clock(self):
        """The clock and date strings for right now."""
        now = time.localtime()
        return (time.strftime(self.time_fmt, now) if self.show_time else "",
                time.strftime(self.date_fmt, now) if self.show_date else "")

    def items(self, clock, vals):
        """One tick's worth of (text, font, x, y, anchor, colour) tuples."""
        w, color = self.w, self.colors
        items = []
        # Centred: "ma" is middle horizontally, ascender line vertically.
        if self.show_time:
            items.append((clock[0], self.time_font, w // 2, self.time_y, "ma", color["time"]))
        if self.show_date:
            items.append((clock[1], self.date_font, w // 2, self.date_y, "ma", color["date"]))
        values = iter(vals)
        for n, row in enumerate(self.rows):
            top = self.stats_y + n * self.block_h
            below = top + self.label_h
            if len(row) == 2:
                # A full row: left column anchored "la", right "ra".
                left, right = self.labels[row[0]], self.labels[row[1]]
                items += [
                    (left, self.label_font, self.col_left, top, "la", color["label"]),
                    (right, self.label_font, self.col_right, top, "ra", color["label"]),
                    (next(values), self.value_font, self.col_left, below, "la", color["value"]),
                    (next(values), self.value_font, self.col_right, below, "ra", color["value"]),
                ]
            else:
                # A stat on its own row, centred across the full width.
                items += [
                    (self.labels[row[0]], self.label_font, w // 2, top, "ma", color["label"]),
                    (next(values), self.value_font, w // 2, below, "ma", color["value"]),
                ]
        return items

    def describe(self):
        """The text sizes, for the log: "clock 48px / date 22px"."""
        parts = [f"clock {self.time_font.size}px" if self.show_time else "no clock"]
        if self.show_date:
            parts.append(f"date {self.date_font.size}px")
        return " / ".join(parts)


def parse_todo(text):
    """--todo-item "[x] Call mum" -> (True, "Call mum"); "[ ] ..." or no
    box at all is not done yet."""
    stripped = text.strip()
    if stripped[:3].lower() == "[x]":
        return True, stripped[3:].strip()
    if stripped[:3] == "[ ]":
        return False, stripped[3:].strip()
    return False, stripped


def _fraction(text):
    value = float(text)
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError(f"{text} is not between 0 and 1")
    return value


def _when(text):
    """A countdown's date: YYYY-MM-DD, optionally with a time (THH:MM)."""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(text.strip(), fmt)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"{text!r} is not a date like 2026-12-25 "
                                     "or 2026-12-25T18:30")


def _brightness(text):
    value = int(text)
    if not 10 <= value <= 100:
        raise argparse.ArgumentTypeError(f"{text} is not between 10 and 100")
    return value


def _zoom(text):
    value = float(text)
    if not 1 <= value <= MAX_ZOOM:
        raise argparse.ArgumentTypeError(f"{text} is not between 1 and {MAX_ZOOM:g}")
    return value


def _label(text):
    slot, equals, label = text.partition("=")
    slot = slot.strip().lower()
    if not equals or slot not in SLOTS:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not SLOT=TEXT with a slot from: {', '.join(SLOTS)}")
    return slot, label


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
    ap.add_argument("--zoom", type=_zoom, default=1.0,
                    help=f"with --fit: enlarge the picture, 1 to {MAX_ZOOM:g} (default 1)")
    ap.add_argument("--pan-x", type=_fraction, default=0.5,
                    help="with --fit: which part shows when the picture is wider "
                         "than the screen, from 0 (its left edge) to 1 (its right "
                         "edge); default 0.5, centred")
    ap.add_argument("--pan-y", type=_fraction, default=0.5,
                    help="the same from top (0) to bottom (1)")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--quality", type=int, default=88)
    ap.add_argument("--port", help="COM port (default: located by VID:PID)")
    ap.add_argument("--font", default="consolab.ttf",
                    help="font file in the Windows font directory (default consolab.ttf)")
    ap.add_argument("--time-format", choices=TIME_FORMATS, default="12h",
                    help="12h (3:25 PM), 12h-plain (3:25) or 24h (15:25); default 12h")
    ap.add_argument("--seconds", action="store_true", help="show seconds on the clock")
    ap.add_argument("--date-format", default=DATE_FORMAT, metavar="PATTERN",
                    help=f"the date, written with the codes {' '.join(DATE_CODES)}; "
                         f"[text in brackets] is kept as it is "
                         f"(default {DATE_FORMAT!r})")
    ap.add_argument("--no-time", action="store_true", help="no clock line")
    ap.add_argument("--no-date", action="store_true", help="no date line")
    ap.add_argument("--time-size", type=int, default=0)
    ap.add_argument("--date-size", type=int, default=0)
    ap.add_argument("--text-fill", type=float, default=0.82,
                    help="how much of the panel width the clock and date span, "
                         "0-1 (default 0.82; 1.0 runs right up to the padding)")
    ap.add_argument("--label-size", type=int, default=0)
    ap.add_argument("--value-size", type=int, default=0)
    for part in ("time", "date", "label", "value"):
        ap.add_argument(f"--{part}-color", type=ImageColor.getrgb, default="white",
                        help=f"{part} text colour (default white)")
    ap.add_argument("--clock-pos", type=_fraction,
                    help="height of the clock block, from 0 (top, the default) "
                         "to 1 (bottom)")
    ap.add_argument("--stats-pos", type=_fraction,
                    help="height of the stats block, from 0 (top) to 1 (bottom); "
                         "overrides --stats-y")
    ap.add_argument("--stats-y", type=int, default=0,
                    help="top of the stats block; default sits below the figure")
    ap.add_argument("--no-stats", action="store_true", help="clock and date only")
    # argparse formats help text with %, so the % in ram% must be doubled.
    ap.add_argument("--slots", default=DEFAULT_SLOTS,
                    help=(f"comma-separated readings, filling the grid two per "
                          f"row; {EMPTY_SLOT} leaves a spot empty (default "
                          f"{DEFAULT_SLOTS}). Available: {', '.join(SLOTS)}"
                          ).replace("%", "%%"))
    ap.add_argument("--columns", type=int, choices=(1, 2), default=2,
                    help="stats per row (default 2)")
    ap.add_argument("--label", type=_label, action="append", default=[],
                    metavar="SLOT=TEXT",
                    help="rename a stat on screen, e.g. cputemp=CPU; repeatable")
    ap.add_argument("--preview", metavar="FILE",
                    help="save one frame to FILE and exit, without opening the "
                         "panel -- lets you tune layout while it keeps running")
    ap.add_argument("--sample-stats", action="store_true",
                    help="with --preview: show example readings instead of "
                         "this PC's, without loading any sensors")
    ap.add_argument("--calendar", choices=("month", "week"),
                    help="a calendar in the clock block, under the date (or in "
                         "its place, with --no-date): this month, or this week")
    ap.add_argument("--week-start", choices=("mon", "sun"), default="mon",
                    help="the calendar's first weekday (default mon)")
    ap.add_argument("--calendar-size", type=int, default=0)
    ap.add_argument("--calendar-color", type=ImageColor.getrgb, default="white")
    ap.add_argument("--note-line", action="append", default=[], metavar="TEXT",
                    help="a line of your own text; repeat for more lines. Long "
                         "lines wrap")
    ap.add_argument("--countdown-date", type=_when, metavar="YYYY-MM-DD[THH:MM]",
                    help="show a countdown to (or count up from) this date")
    ap.add_argument("--countdown-mode", choices=COUNTDOWN_MODES, default="to",
                    help="to (days to the date), to-hours (days and hours) or "
                         "since (days since it); default to")
    ap.add_argument("--countdown-label", default="", metavar="TEXT",
                    help='what it counts to, e.g. "Launch" -> "12 days to Launch"')
    ap.add_argument("--todo-title", default="", metavar="TEXT",
                    help="a heading over the to-do list")
    ap.add_argument("--todo-item", action="append", default=[], metavar="TEXT",
                    help='a to-do item; start it with "[x] " when it is done. '
                         "Repeat for more")
    for block, align in (("note", "center"), ("countdown", "center"), ("todo", "left")):
        ap.add_argument(f"--{block}-size", type=int, default=0)
        ap.add_argument(f"--{block}-color", type=ImageColor.getrgb, default="white")
        ap.add_argument(f"--{block}-align", choices=ALIGN, default=align)
        ap.add_argument(f"--{block}-pos", type=_fraction,
                        help=f"height of the {block} block, 0 (top) to 1 (bottom); "
                             "default: stacked under the clock")
        ap.add_argument(f"--{block}-no-frost", action="store_true",
                        help=f"no frosted panel behind the {block} block")
    ap.add_argument("--brightness", type=_brightness, default=100, metavar="10-100",
                    help="dim the whole screen to this percentage (default 100); "
                         "the pixels are darkened, the backlight stays as it is")
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


def _frames(args, limit=None, picture=None, each=None):
    """The picture's frames, sized for the panel.

    `picture` is the source's first frame, already opened; the app's preview
    passes it so that it need not read the file on every redraw. `each` is
    applied to each frame as it loads (see lcd_win.load_frames).
    """
    if not args.fit and (args.zoom != 1 or args.pan_x != 0.5 or args.pan_y != 0.5):
        sys.exit("--zoom, --pan-x and --pan-y need --fit")
    pan = (args.pan_x, args.pan_y)
    if picture is None:
        return load_frames(args.source, args.width, args.fit, args.fit_color,
                           args.zoom, pan, limit, each)
    if args.fit:
        return [fit_frame(picture, fit_size(args.width), args.fit, args.fit_color,
                          args.zoom, pan)]
    height = round(picture.height * args.width / picture.width)
    return [picture.resize((args.width, height), Image.LANCZOS)]


def _panel_size(args):
    """The size _frames will make the frames, without decoding any."""
    if args.fit:
        return fit_size(args.width)
    try:
        with Image.open(args.source) as img:
            return args.width, round(img.height * args.width / img.width)
    except OSError as e:
        sys.exit(f"cannot read {args.source}: {e}\n"
                 "(images and GIFs only -- video would need ffmpeg)")


def _stats_setup(args):
    """The stat rows (see stat_rows) and the label of every stat."""
    if args.no_stats:
        return [], {}
    spots = parse_spots(args.slots)
    unknown = [name for name in spots if name and name not in SLOTS]
    if unknown:
        sys.exit(f"unknown slot(s) {', '.join(unknown)} -- available: "
                 f"{', '.join(SLOTS)}")
    if not spots:
        sys.exit("--slots needs at least one reading")
    labels = {name: slot[0] for name, slot in SLOTS.items()}
    labels.update(args.label)
    return stat_rows(spots, args.columns), labels


def preview(argv, picture=None):
    """One frame as the panel will show it, with example readings.

    For the app's preview: returns (image, layout), where the layout says how
    large each text could grow and where the blocks sit. `picture` is the
    source's first frame, already opened (see _frames). Never reads a sensor
    or opens the port.
    """
    args = build_parser().parse_args(argv)
    frame = _frames(args, limit=1, picture=picture)[0]
    rows, labels = _stats_setup(args)
    layout = Layout(args, frame.width, frame.height, rows, labels)
    stats = SampleMetrics([name for row in rows for name in row])
    img = compose(frame, layout.static + layout.items(layout.clock(), stats.values()),
                  layout.panels, args.frost_blur, args.frost_dark, args.frost_corner)
    return dim(img, args.brightness), layout


class Show:
    """One set of options, made ready to stream: the frames (prepared and
    packed), the layout and the readings.

    Building one takes a second or two for a GIF, so a change of settings
    builds the new Show while the old one keeps playing (Player.swap).
    `get_temps` lets the service share one Temperatures between Shows.
    """

    def __init__(self, args, log, get_temps=None):
        w, h = _panel_size(args)
        if w * h > MAX_PIXELS:
            sys.exit(f"{w}x{h} is {w * h:,} px, over the {MAX_PIXELS:,} px limit")
        rows, labels = _stats_setup(args)
        self.args, self.size = args, (w, h)
        self.layout = layout = Layout(args, w, h, rows, labels)
        # Each frame is frosted, given the static blocks, flipped and packed as
        # it loads (see prepare).
        frames = _frames(args, each=lambda frame: prepare(frame, layout, args))
        if len(frames) == 1:
            frames = [unpack(frames[0], (w, h))]    # a still picture: nothing to save
        self.frames = frames
        self.slots = [name for row in rows for name in row]
        self.stats = Metrics(self.slots, get_temps=get_temps) if self.slots else None
        if self.stats and self.stats.temps:
            log(self.stats.temps.status())
        self.header = struct.pack("<IHHHH", MAGIC, w, h, 0, 1)
        self.valid_until = layout.valid_until

    def tick(self):
        """The clock strings and stat values for right now."""
        return self.layout.clock(), self.stats.values() if self.stats else ()

    def describe(self, port):
        """The line logged when it starts playing (the service reads " on ")."""
        w, h = self.size
        layout = self.layout
        return (f"{len(self.frames)} frames at {w}x{h} ({w * h:,} px), "
                f"{self.args.fps:g} FPS, on {port}, "
                f"font {os.path.basename(layout.font_path)}, {layout.describe()}"
                + ("" if self.stats is None else
                   f", slots {'/'.join(self.slots)} at y={layout.stats_y} "
                   f"(labels {layout.label_font.size}px, values {layout.value_font.size}px)"))

    def close(self):
        if self.stats:
            self.stats.close()


class Player:
    """Sends a Show's frames to the panel until stopped.

    This runs all day, so it does as little as it can: the text is drawn once
    each time it changes (text_bands) rather than onto every frame, and each
    frame's JPEG is kept until the text changes. JPEGs made for older text
    can never be sent again, so they are dropped at once rather than held --
    with stats changing every 2 seconds, a long GIF would otherwise keep a
    JPEG of every frame for nothing.

    swap() hands over a new Show, built elsewhere; the old one plays until
    then, so changing settings never freezes the screen. When the Show's
    static blocks go out of date (the calendar at midnight, a countdown),
    `on_stale` is called once, to build a fresh Show and swap it in.
    """

    def __init__(self, panel, show, on_stale=None):
        self.panel, self.show, self.on_stale = panel, show, on_stale
        self._next = None

    def swap(self, show):
        self._next = show

    def run(self, stop=None):
        cache = {}
        shown, bands = None, []
        stale_reported = False
        show = self.show
        i = 0
        while stop is None or not stop.is_set():
            start = time.time()
            if self._next is not None:
                old, show, self._next = show, self._next, None
                self.show = show
                old.close()
                cache.clear()
                shown, stale_reported = None, False
            if show.valid_until and not stale_reported and start >= show.valid_until:
                stale_reported = True
                if self.on_stale:
                    self.on_stale()
            clock, vals = show.tick()
            stamp = clock + vals
            if stamp != shown:
                shown, bands = stamp, text_bands(show.layout.items(clock, vals),
                                                 show.size, show.args.brightness)
                cache.clear()

            idx = i % len(show.frames)
            payload = cache.get(idx)
            if payload is None:
                payload = cache[idx] = show.header + encode_with(
                    show.frames[idx], show.size, bands, show.args.quality)
            self.panel.send(payload)
            i += 1
            wait = max(0, 1.0 / show.args.fps - (time.time() - start))
            if stop is None:
                time.sleep(wait)
            else:
                stop.wait(wait)


def main(argv=None, stop=None, log=None):
    """Run the clock until stopped.

    From the command line this parses sys.argv and runs until Ctrl+C.
    Errors end it with SystemExit(message). (The service drives Show and
    Player itself, so that it can swap settings without a pause.)
    """
    log = log or (lambda message: print(message, flush=True))
    args = build_parser().parse_args(argv)

    if args.preview:
        _write_preview(args, log)
        return

    show = Show(args, log)
    try:
        panel = Panel(args.port)
    except BaseException:
        show.close()
        raise
    if stop is None:
        # Command line only: signal handlers can only be set from the main
        # thread; the service stops the loop through `stop` instead.
        atexit.register(panel.close)
        install_handlers()
    log(show.describe(panel.port))

    def rebuild():
        """At midnight (or a countdown's next change): fresh static blocks."""
        def build():
            try:
                player.swap(Show(args, log))
            except (SystemExit, Exception) as e:
                log(f"could not refresh the blocks: {e}")
        threading.Thread(target=build, name="rebuild", daemon=True).start()

    player = Player(panel, show, on_stale=rebuild)
    try:
        player.run(stop)
    finally:
        panel.close()
        player.show.close()


def _write_preview(args, log):
    """--preview: one frame to a PNG, with this PC's readings (or examples)."""
    frames = _frames(args, limit=1)
    w, h = frames[0].size
    if w * h > MAX_PIXELS:
        sys.exit(f"{w}x{h} is {w * h:,} px, over the {MAX_PIXELS:,} px limit")
    rows, labels = _stats_setup(args)
    layout = Layout(args, w, h, rows, labels)
    slots = [name for row in rows for name in row]
    stats = (None if not slots else
             SampleMetrics(slots) if args.sample_stats else Metrics(slots))
    try:
        if stats and stats.temps:
            log(stats.temps.status())
        if isinstance(stats, Metrics):
            # Prime, wait, then force a fresh sample: CPU load and the
            # throughput slots are deltas and read as 0 or "--" until
            # there are two samples to subtract.
            stats.values()
            time.sleep(0.6)
            stats.refresh()
        vals = stats.values() if stats else ()
        dim(compose(frames[0], layout.static + layout.items(layout.clock(), vals),
                    layout.panels, args.frost_blur, args.frost_dark, args.frost_corner),
            args.brightness).save(args.preview)
    finally:
        if stats:
            stats.close()
    following = (f", following {stats.nic()}" if stats
                 and any(s.startswith("net") for s in slots) else "")
    log(f"preview written to {args.preview} ({w}x{h}), {layout.describe()}"
        f"{following} -- panel untouched")


def prepare(frame, layout, args):
    """One frame made ready for the loop, once: frosted, given the static
    blocks (text, countdown, to-do list, calendar), dimmed, turned upside
    down and packed.

    None of that depends on the changing text, so doing it here rather than
    on every redraw roughly halves the loop's CPU time, and the static blocks
    cost nothing at all once the frames are made.

    Packing is zlib, which is lossless: the rooftop GIF's 59 frames take 80 MB
    as they are and under 9 MB packed (a 300-frame GIF, over 400 MB), for
    about 2.5 ms of unpacking per frame sent.
    """
    img = compose(frame, layout.static, layout.panels, args.frost_blur,
                  args.frost_dark, args.frost_corner)
    img = dim(img, args.brightness).transpose(Image.ROTATE_180)
    return zlib.compress(img.tobytes(), 1)


def unpack(data, size):
    return Image.frombytes("RGB", size, zlib.decompress(data))


if __name__ == "__main__":
    main()
