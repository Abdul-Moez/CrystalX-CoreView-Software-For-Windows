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
"""Play images or GIFs on the CrystalX CoreView case LCD, from Windows.

The panel is a plain CDC-ACM composite device, so Windows' in-box usbser.sys
driver exposes it as a COM port: interface 1's bulk OUT endpoint (0x02) *is*
the serial data endpoint. The vendor's own LCD Control.exe drives the panel
this way (it holds the COM port open and no libusb driver is installed
anywhere), so we do the same: open the port, write header + JPEG.

That means no libusb, no Zadig, no driver replacement and no admin rights.
The only rule is that nothing else may hold the port, so close LCD Control.exe
before running this.

Every hardware quirk in docs/HOW-IT-WORKS.md applies -- the 180 degree
rotation, the ~472k pixel ceiling, and the fact that the panel never scales
an image up.

Usage:
    lcd_win.py <source> [--width 320] [--fps 10] [--quality 88]
"""

import argparse
import atexit
import itertools
import signal
import struct
import sys
import time
from io import BytesIO

import serial
import serial.tools.list_ports
from PIL import Image, ImageFilter, ImageSequence

VID, PID = 0x33C3, 0xF101
MAGIC = 0x0008100A

# Empirically determined on a CoreView V-950: 320x1476 (472k px) is stable,
# 324x1494 (484k px) already tears, so this check is looser than the real
# ceiling. See docs/HOW-IT-WORKS.md, constraint 2.
MAX_PIXELS = 500_000

# The largest frame measured clean on the panel. Pictures placed with a fit
# mode go on a canvas of this shape, so any picture ends up within the limit.
FIT_SIZE = (320, 1476)
FIT_MODES = ("fill", "blur", "color")

# CDC ACM ignores the line rate -- there is no real UART behind it -- but
# Windows still wants a valid value.
BAUD = 115200


def find_port(explicit=None):
    """The panel's COM port, located by USB VID:PID rather than by number.

    Windows hands out COM numbers per physical port, so hardcoding COM5 would
    break as soon as the case is plugged into a different header.
    """
    if explicit:
        return explicit
    want = f"VID:PID={VID:04X}:{PID:04X}"
    found = [p.device for p in serial.tools.list_ports.comports()
             if want in (p.hwid or "").upper()]
    if not found:
        sys.exit(f"panel {VID:04x}:{PID:04x} not found -- is the case plugged in?")
    return found[0]


class Panel:
    """The LCD, driven through its CDC serial port.

    Closing the port is the only cleanup it needs.
    """

    def __init__(self, port=None):
        self.port = find_port(port)
        try:
            self.ser = serial.Serial(
                self.port, BAUD, timeout=1, write_timeout=10,
                # Frames are binary JPEG. Software flow control would eat any
                # 0x11/0x13 byte in the stream and hardware flow control would
                # stall on a device that drives none of those lines.
                xonxoff=False, rtscts=False, dsrdtr=False)
        except serial.SerialException as e:
            msg = str(e).lower()
            if "access" in msg or "denied" in msg:
                sys.exit(f"{self.port} is busy -- close LCD Control.exe (and any "
                         "other program using the panel) and try again")
            sys.exit(f"could not open {self.port}: {e}")
        self.ser.dtr = True
        self.ser.reset_input_buffer()

    def send(self, frame):
        self.ser.write(frame)

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


def fit_frame(frame, size, mode, color=(0, 0, 0)):
    """Place one RGB frame on a canvas of `size`.

    fill  -- scale to cover the whole canvas, cropping what overflows
    blur  -- the whole picture visible, gaps filled with a blurred copy of it
    color -- the whole picture visible, gaps filled with `color`
    """
    w, h = size
    if mode == "fill":
        scale = max(w / frame.width, h / frame.height)
        big = frame.resize((max(w, round(frame.width * scale)),
                            max(h, round(frame.height * scale))), Image.LANCZOS)
        x, y = (big.width - w) // 2, (big.height - h) // 2
        return big.crop((x, y, x + w, y + h))
    if mode == "blur":
        # Blurring a small copy and scaling it back up is far cheaper than a
        # large-radius blur at full size, and looks the same.
        back = fit_frame(frame, (max(1, w // 8), max(1, h // 8)), "fill")
        back = back.filter(ImageFilter.GaussianBlur(3)).resize(size, Image.BILINEAR)
        back = Image.blend(back, Image.new("RGB", size, (0, 0, 0)), 0.35)
    else:
        back = Image.new("RGB", size, color)
    scale = min(w / frame.width, h / frame.height)
    small = frame.resize((max(1, round(frame.width * scale)),
                          max(1, round(frame.height * scale))), Image.LANCZOS)
    back.paste(small, ((w - small.width) // 2, (h - small.height) // 2))
    return back


def load_frames(source, width, fit=None, color=(0, 0, 0)):
    """Composite every frame of the source and scale it to `width`.

    Seeking a GIF frame and converting it composites that frame over the ones
    before it, so every frame comes out complete even when the GIF only stores
    the pixels that changed.

    Without `fit`, height follows the source aspect ratio. With a fit mode
    (see fit_frame), every frame goes on a canvas of FIT_SIZE's shape instead,
    so any picture -- wide, square or tall -- fills the frame the same way.
    """
    try:
        img = Image.open(source)
    except OSError as e:
        sys.exit(f"cannot read {source}: {e}\n"
                 "(images and GIFs only -- video would need ffmpeg)")
    size = (width, round(width * FIT_SIZE[1] / FIT_SIZE[0])) if fit else None
    frames = []
    for frame in ImageSequence.Iterator(img):
        rgb = frame.convert("RGB")
        if size:
            frames.append(fit_frame(rgb, size, fit, color))
        else:
            height = round(rgb.height * width / rgb.width)
            frames.append(rgb.resize((width, height), Image.LANCZOS))
    if not frames:
        sys.exit(f"nothing rendered from {source}")
    return frames


def encode(frame, quality):
    """Rotate 180 degrees, then encode as JPEG.

    The panel is mounted upside down in the case and does not rotate for you.
    subsampling=0 keeps full colour resolution (no chroma subsampling).
    """
    buf = BytesIO()
    frame.transpose(Image.ROTATE_180).save(
        buf, format="JPEG", quality=quality, subsampling=0)
    return buf.getvalue()


def install_handlers():
    """Close the port on exit. Windows has no SIGHUP, but it does have SIGBREAK."""
    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, lambda *_: sys.exit(0))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="image or GIF file")
    ap.add_argument("--width", type=int, default=320, help="output width (default 320)")
    ap.add_argument("--fps", type=float, default=10.0, help="frames per second (default 10)")
    ap.add_argument("--quality", type=int, default=88, help="JPEG quality (default 88)")
    ap.add_argument("--port", help="COM port (default: located by VID:PID)")
    ap.add_argument("--once", action="store_true",
                    help="send each frame once and exit, instead of looping")
    args = ap.parse_args()

    frames = load_frames(args.source, args.width)
    w, h = frames[0].size
    if w * h > MAX_PIXELS:
        sys.exit(f"{w}x{h} is {w * h:,} px, over the {MAX_PIXELS:,} px limit "
                 "-- use a smaller --width")

    header = struct.pack("<IHHHH", MAGIC, w, h, 0, 1)
    payloads = [header + encode(f, args.quality) for f in frames]

    panel = Panel(args.port)
    atexit.register(panel.close)
    install_handlers()
    print(f"{len(payloads)} frames at {w}x{h} ({w * h:,} px), {args.fps:g} FPS, "
          f"on {panel.port}", flush=True)

    period = 1.0 / args.fps
    for i in range(len(payloads)) if args.once else itertools.count():
        start = time.time()
        panel.send(payloads[i % len(payloads)])
        time.sleep(max(0, period - (time.time() - start)))


if __name__ == "__main__":
    main()
