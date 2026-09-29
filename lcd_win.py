#!/usr/bin/env python3
"""Play images or GIFs on the CrystalX CoreView case LCD, from Windows.

The panel is a plain CDC-ACM composite device, so Windows' in-box usbser.sys
driver exposes it as a COM port: interface 1's bulk OUT endpoint (0x02) *is*
the serial data endpoint. The vendor's own LCD Control.exe drives the panel
this way (it holds the COM port open and no libusb driver is installed
anywhere), so we do the same: open the port, write header + JPEG.

That means no libusb, no Zadig, no driver replacement and no admin rights.
The only rule is that nothing else may hold the port, so close LCD Control.exe
before running this.

Every hardware quirk in README.md applies -- the 180 degree rotation, the
~472k pixel ceiling, and the fact that the panel never scales an image up.

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
from PIL import Image, ImageSequence

VID, PID = 0x33C3, 0xF101
MAGIC = 0x0008100A

# Empirically determined on a CoreView V-950: 320x1476 (472k px) is stable,
# 334x1540 (514k px) tears. Stay well under the ceiling. See README.md.
MAX_PIXELS = 500_000

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


def load_frames(source, width):
    """Composite every frame of the source and scale it to `width`.

    Seeking a GIF frame and converting it composites that frame over the ones
    before it, so every frame comes out complete even when the GIF only stores
    the pixels that changed.

    Height follows the source aspect ratio.
    """
    try:
        img = Image.open(source)
    except OSError as e:
        sys.exit(f"cannot read {source}: {e}\n"
                 "(images and GIFs only -- video would need ffmpeg)")
    frames = []
    for frame in ImageSequence.Iterator(img):
        rgb = frame.convert("RGB")
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
