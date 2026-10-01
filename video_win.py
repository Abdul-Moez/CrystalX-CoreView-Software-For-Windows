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
"""Video for the panel: making a clip from a video file, and playing one.

A video cannot be streamed to the panel as it is. The panel wants 320 x 1476
pictures, upside down, and a camera's 4K file would cost far too much to
shrink 30 times a second, all day. So a video is converted once, when it is
applied, into a *clip*: the same pictures a GIF would become (placed with
fill or fit, zoomed and moved, turned upside down), packed as H.264.

  convert()      video file -> clip (the app's window does this)
  ClipReader     plays a clip back (the service and the preview do this)
  check_clip()   is this file a clip we can safely play? (the service asks
                 before keeping one it was sent)

The clip file is our own, not MP4: just the H.264 packets, one per picture,
with an index. Playing one therefore needs only FFmpeg's H.264 decoder, not
its file readers -- the service runs as SYSTEM and plays files other programs
may have sent it, so the less of FFmpeg it touches, the better.

    magic "CXV1" | u32 header length | JSON header (padded)
    one record per picture: u32 size | u8 flags (1 = key picture) | H.264 packet
    index: u64 offset of every record | footer: u64 index offset | "CXVE"

FFmpeg comes with PyAV, imported only when a video is used, so pictures and
GIFs never pay for it.

    video_win.py convert VIDEO CLIP [--fit fill|blur|color] [--max-fps 30|60]
    video_win.py info CLIP
"""

import collections
import json
import os
import struct
import sys

from PIL import Image

from lcd_win import FIT_MODES, FIT_SIZE, MAX_ZOOM, fit_frame

CLIP_EXT = ".cxv"
MAGIC, FOOTER = b"CXV1", b"CXVE"
HEADER_BYTES = 2048                 # room reserved for the JSON header
MAX_SECONDS = 300                   # a video may be five minutes long
MAX_FPS = 60
MAX_FRAMES = (MAX_SECONDS + 2) * MAX_FPS
MAX_CLIP_BYTES = 600 * 2**20
CRF = 21                            # x264 quality: lower is better and bigger
WORK_LIMIT = 1920                   # sources are scaled down to this before placing
HDR_TRANSFERS = ("smpte2084", "arib-std-b67")
_RECORD = struct.Struct("<IB")


class VideoError(ValueError):
    """Something wrong with a video, worded for the person who chose it."""


class NotAVideo(VideoError):
    """The file is not something FFmpeg can read at all."""


class Cancelled(Exception):
    """The conversion was cancelled."""


# Everything heavy is imported where it is used: pictures and GIFs load this
# module too (through clock_win), and must not pay for the video code's needs.
def _av():
    import av
    return av


# -- looking at a video ----------------------------------------------------------

def _seconds(value):
    return None if value is None else float(value)


def probe(path):
    """What `path` is, as a dict -- or a VideoError saying why it can't be used.

    The keys: width, height, fps, duration (None when the file doesn't say),
    codec, rotation (degrees; 0 for most), hdr and frames (an estimate).
    """
    av = _av()
    try:
        container = av.open(path)
    except Exception as e:
        raise NotAVideo("That file is not a video this app can read.") from e
    with container:
        if not container.streams.video:
            raise VideoError("That file has no picture in it, only sound or data.")
        stream = container.streams.video[0]
        ctx = stream.codec_context
        fps = float(stream.average_rate or stream.guessed_rate or 0)
        if not 0 < fps <= 1000:
            fps = 30.0
        duration = _seconds(container.duration / 1e6) if container.duration else None
        if duration is None and stream.duration:
            duration = float(stream.duration * stream.time_base)
        if not ctx.width or not ctx.height:
            raise VideoError("That video's size could not be read.")
        sar = float(stream.sample_aspect_ratio or ctx.sample_aspect_ratio or 1) or 1.0
        info = {"width": ctx.width, "height": ctx.height, "sar": sar, "fps": fps,
                "duration": duration, "codec": ctx.name,
                "hdr": str(getattr(ctx, "color_trc", "")).lower().split(".")[-1] in HDR_TRANSFERS,
                "rotation": 0}
        for frame in container.decode(stream):
            info["rotation"] = frame.rotation or 0
            if str(frame.color_trc).lower().split(".")[-1] in HDR_TRANSFERS:
                info["hdr"] = True
            break
    if duration is not None and duration > MAX_SECONDS + 0.5:
        raise VideoError(f"That video is {duration / 60:.1f} minutes long. "
                         f"The longest the screen takes is {MAX_SECONDS // 60} minutes -- "
                         "please trim it first.")
    info["frames"] = round((duration or 0) * fps)
    return info


# -- converting a video into a clip -------------------------------------------------

# What a video file's colour matrix (FFmpeg's AVColorSpace numbers) is called
# in PyAV's reformatter.
_MATRICES = {1: "ITU709", 4: "FCC", 5: "ITU601", 6: "ITU601", 7: "SMPTE240M",
             9: "BT2020", 10: "BT2020"}


def _colorspace(frame):
    """The colour matrix to turn this frame's YUV into RGB with. A file that
    doesn't say gets what players assume: BT.709 for HD, BT.601 below it."""
    from av.video.reformatter import Colorspace
    name = _MATRICES.get(int(frame.colorspace))
    if name is None:
        name = "ITU709" if frame.height >= 720 else "ITU601"
    return getattr(Colorspace, name)


def _to_picture(frame, sar, rotation, limit=WORK_LIMIT):
    """One decoded frame as an RGB picture the right way up, no larger than
    `limit`: the same thing a GIF's frame is before it is placed.

    `rotation` is what the file asks for, in degrees counter-clockwise (phones
    store a portrait video sideways and say so)."""
    from av.video.reformatter import Interpolation
    width = round(frame.width * sar) if sar > 1 else frame.width
    height = round(frame.height / sar) if sar < 1 else frame.height
    scale = min(1.0, limit / max(width, height))
    width, height = max(2, round(width * scale)), max(2, round(height * scale))
    # Four bytes a pixel (RGB0) lets Pillow use the frame's memory as it is;
    # three-byte RGB is copied, which costs more than the conversion itself and
    # keeps every thread but one waiting.
    rgb = frame.reformat(width=width, height=height, format="rgb0",
                         src_colorspace=_colorspace(frame), interpolation=Interpolation.AREA)
    plane = rgb.planes[0]
    img = Image.frombuffer("RGBX", (rgb.width, rgb.height), plane, "raw", "RGBX",
                           plane.line_size, 1)
    img._frame = rgb                    # the picture borrows the frame's memory
    rotation %= 360
    if rotation:
        turns = {90: Image.ROTATE_90, 180: Image.ROTATE_180, 270: Image.ROTATE_270}
        img = img.transpose(turns[rotation]) if rotation in turns else img.rotate(rotation, expand=True)
    return img


def _place(frame, sar, rotation, fit, color, zoom, pan):
    """A decoded frame, placed on the panel's canvas, upside down (the way
    every frame sent to the panel is), as an H.264 input frame."""
    av = _av()
    picture = _to_picture(frame, sar, rotation)
    if fit == "blur":                   # the blurred edges are made in plain RGB
        picture = picture.convert("RGB")
    panel = fit_frame(picture, FIT_SIZE, fit, color, zoom, pan)
    if panel.mode != "RGB":
        panel = panel.convert("RGB")
    return av.VideoFrame.from_image(panel.transpose(Image.ROTATE_180)).reformat(format="yuv420p")


def convert(source, out_path, fit="fill", fit_color=(0, 0, 0), zoom=1.0, pan=(0.5, 0.5),
            max_fps=30, progress=None, cancel=None, workers=None):
    """Convert the video `source` into the clip `out_path`.

    The pictures are placed on the panel's canvas exactly as a GIF's would be
    (see lcd_win.fit_frame), so fill, fit, zoom and position mean the same.
    The rate is the video's own, but no higher than `max_fps` (30 or 60);
    a video that does not keep a steady rate is made steady, repeating or
    skipping pictures as needed, because the player counts time, not pictures.

    `progress(done, total)` is told how far it is (total is an estimate);
    `cancel` is an Event: set it to stop. Returns the clip's header.
    Raises VideoError for a video that can't be used, Cancelled if stopped.
    """
    from concurrent.futures import ThreadPoolExecutor
    from fractions import Fraction
    av = _av()
    if fit not in FIT_MODES or not 1.0 <= zoom <= MAX_ZOOM or max_fps not in (30, 60):
        raise ValueError("bad conversion settings")
    info = probe(source)
    out_fps = Fraction(min(info["fps"], max_fps)).limit_denominator(1001)
    step = float(1 / out_fps)
    total = max(1, round((info["duration"] or MAX_SECONDS) * float(out_fps)))
    workers = workers or min(8, max(1, os.cpu_count() or 2))
    rotation, sar = info["rotation"], info["sar"]

    encoder = av.codec.CodecContext.create("libx264", "w")
    encoder.width, encoder.height, encoder.pix_fmt = FIT_SIZE[0], FIT_SIZE[1], "yuv420p"
    encoder.time_base, encoder.framerate = 1 / out_fps, out_fps
    keyint = max(1, round(float(out_fps) * 2))
    # No B-frames: every picture comes out in the order it goes in.
    encoder.options = {"crf": str(CRF), "preset": "faster", "bf": "0", "g": str(keyint),
                       "threads": "0"}
    encoder.open()

    tmp = out_path + ".part"
    offsets, written = [], 0
    pool = ThreadPoolExecutor(workers, thread_name_prefix="clip")
    queue = collections.deque()             # futures, in order
    window = workers * 2                    # source frames wait here: a 4K one is 12 MB
    stats = {"errors": 0, "packets": 0, "last": 0.0, "submitted": 0, "reported": 0}

    def check():
        if cancel is not None and cancel.is_set():
            raise Cancelled()

    try:
        with open(tmp, "wb") as f:
            f.write(b"\0" * (8 + HEADER_BYTES))

            def write(packets):
                nonlocal written
                for packet in packets:
                    offsets.append(f.tell())
                    data = bytes(packet)
                    f.write(_RECORD.pack(len(data), 1 if packet.is_keyframe else 0))
                    f.write(data)
                    written += len(data)
                if written > MAX_CLIP_BYTES:
                    raise VideoError("The converted video came out too big -- try a shorter "
                                     "video, or limit it to 30 frames a second.")

            def drain(limit):
                while len(queue) > limit:
                    check()
                    frame = queue.popleft().result()
                    frame.pts, frame.time_base = stats["submitted"], 1 / out_fps
                    stats["submitted"] += 1
                    write(encoder.encode(frame))
                    if progress and len(offsets) >= stats["reported"] + 8:
                        stats["reported"] = len(offsets)
                        progress(len(offsets), max(total, len(offsets)))

            container = av.open(source)
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            ticks, tick_time, t0 = 0, None, None
            pending, pending_future = None, None

            def emit():
                """Output the pending source frame for the next tick."""
                nonlocal pending_future, ticks, tick_time
                if pending_future is None:
                    pending_future = pool.submit(_place, pending, sar, rotation, fit,
                                                 fit_color, zoom, pan)
                queue.append(pending_future)
                ticks += 1
                tick_time += step
                if ticks > MAX_FRAMES:
                    raise VideoError(f"That video is longer than {MAX_SECONDS // 60} minutes.")
                drain(window)

            for packet in container.demux(stream):
                check()
                try:
                    frames = packet.decode()
                except Exception:
                    stats["errors"] += 1
                    continue
                stats["packets"] += 1
                for frame in frames:
                    t = frame.time
                    if t is None:       # no timestamp: one frame after the last
                        t = 0.0 if t0 is None else stats["last"] + 1 / info["fps"]
                    if t0 is None:
                        t0, tick_time = t, t
                    stats["last"] = t
                    # Ticks that fall before this frame still show the one before it.
                    while pending is not None and tick_time < t - 1e-3:
                        emit()
                    pending, pending_future = frame, None
            if pending is None:
                raise VideoError("No pictures could be read from that video.")
            # The last frame holds for its own length.
            end = stats["last"] + 1 / info["fps"]
            while tick_time < end - 1e-3:
                emit()
            drain(0)
            write(encoder.encode(None))
            container.close()

            if info["duration"] and stats["last"] < info["duration"] - max(1.0, info["duration"] * 0.02):
                raise VideoError(f"The video stops at {stats['last']:.0f} seconds although it should "
                                 f"last {info['duration']:.0f}: the file looks cut short or damaged.")
            if stats["errors"] > max(3, stats["packets"] // 50):
                raise VideoError("That video file is damaged in too many places to play.")
            frames_total = len(offsets)
            index_at = f.tell()
            f.write(struct.pack(f"<{frames_total}Q", *offsets))
            f.write(struct.pack("<Q", index_at) + FOOTER)
            header = {"v": 1, "codec": "h264", "width": FIT_SIZE[0], "height": FIT_SIZE[1],
                      "fps_num": out_fps.numerator, "fps_den": out_fps.denominator,
                      "fps": float(out_fps), "frames": frames_total,
                      "duration": frames_total / float(out_fps), "keyint": keyint,
                      "max_fps": max_fps, "crf": CRF, "source_fps": info["fps"],
                      "source_size": [info["width"], info["height"]]}
            raw = json.dumps(header).encode()
            if len(raw) > HEADER_BYTES:
                raise VideoError("clip header too large")
            f.seek(0)
            f.write(MAGIC + struct.pack("<I", HEADER_BYTES) + raw.ljust(HEADER_BYTES, b" "))
        os.replace(tmp, out_path)
        if progress:
            progress(frames_total, frames_total)
        return header
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


# -- reading a clip back ----------------------------------------------------------------

def _layout(f, size):
    """(header, first record offset, record offsets) of the clip in `f`,
    having checked that the file holds together; VideoError otherwise."""
    damaged = VideoError("The prepared video file is damaged.")
    if size < 8 + HEADER_BYTES + 12:
        raise damaged
    f.seek(0)
    if f.read(4) != MAGIC:
        raise damaged
    (length,) = struct.unpack("<I", f.read(4))
    if length != HEADER_BYTES:
        raise damaged
    try:
        header = json.loads(f.read(length))
    except ValueError:
        raise damaged from None
    f.seek(size - 12)
    index_at, tail = struct.unpack("<Q4s", f.read(12))
    frames = header.get("frames")
    if tail != FOOTER or not isinstance(frames, int) or not 1 <= frames <= MAX_FRAMES:
        raise damaged
    first = 8 + HEADER_BYTES
    if not first <= index_at <= size - 12 - 8 * frames or index_at + 8 * frames + 12 != size:
        raise damaged
    f.seek(index_at)
    offsets = struct.unpack(f"<{frames}Q", f.read(8 * frames))
    if offsets[0] != first or any(b <= a for a, b in zip(offsets, offsets[1:])) \
            or offsets[-1] >= index_at:
        raise damaged
    return header, first, offsets, index_at


class ClipReader:
    """Plays a clip: decodes it one picture at a time, round and round.

    Only the H.264 decoder is used, on packets read from our own file.
    """

    def __init__(self, path):
        from fractions import Fraction
        av = _av()
        self._f = open(path, "rb")
        try:
            size = os.fstat(self._f.fileno()).st_size
            self.header, self._first, self.offsets, self._end = _layout(self._f, size)
            self.size = (self.header["width"], self.header["height"])
            self.fps = float(self.header["fps"])
            self.frames = self.header["frames"]
            self.duration = self.header["duration"]
            self._decoder = av.codec.CodecContext.create("h264", "r")
            self._decoder.thread_type = "SLICE"
            self._decoder.thread_count = 1
            self._time_base = Fraction(self.header["fps_den"], self.header["fps_num"])
            self._queue = collections.deque()
            self._next = 0              # the next record to feed to the decoder
            self.position = 0           # the number of the next picture to give out
            self.errors = 0             # damaged pictures skipped so far
        except BaseException:
            self._f.close()
            raise

    def _feed(self):
        """Give the decoder the next record; wrap round at the end."""
        av = _av()
        if self._next >= self.frames:
            self._queue.extend(self._decoder.decode(None))
            self._decoder.flush_buffers()
            self._next = 0
        self._f.seek(self.offsets[self._next])
        length, _flags = _RECORD.unpack(self._f.read(_RECORD.size))
        if length > MAX_CLIP_BYTES:
            raise VideoError("The prepared video file is damaged.")
        packet = av.Packet(self._f.read(length))
        packet.pts = packet.dts = self._next
        packet.time_base = self._time_base
        self._next += 1
        try:
            self._queue.extend(self._decoder.decode(packet))
        except av.error.FFmpegError:
            self.errors += 1            # a damaged picture: skip it, carry on

    def _frame(self):
        tries = 0
        while not self._queue:
            self._feed()
            tries += 1
            if tries > self.frames + 8:     # nothing decodes at all
                raise VideoError("The prepared video file could not be played.")
        self.position = (self.position + 1) % self.frames
        return self._queue.popleft()

    def read(self, skip=0):
        """The next picture (RGB), after skipping `skip` pictures: they are
        decoded, because later ones need them, but not turned into pictures."""
        for _ in range(skip):
            self._frame()
        return self._frame().to_image()

    def seek(self, number):
        """Go to picture `number`: back to the key picture before it, then on."""
        number = max(0, min(self.frames - 1, number))
        key = number
        while key > 0:
            self._f.seek(self.offsets[key] + 4)
            if self._f.read(1)[0] & 1:
                break
            key -= 1
        self._queue.clear()
        self._decoder.flush_buffers()
        self._next, self.position = key, key
        for _ in range(number - key):
            self._frame()

    def close(self):
        self._f.close()


def check_clip(path):
    """Is `path` a clip we can play? Returns its header, or raises VideoError.

    The service calls this on every clip it is sent: the structure must hold
    together, the size must be the panel's, and the first and last pictures
    must decode.
    """
    header = None
    try:
        reader = ClipReader(path)
    except VideoError:
        raise
    except (OSError, ValueError, struct.error, KeyError, TypeError) as e:
        raise VideoError("The prepared video file is damaged.") from e
    try:
        header = reader.header
        if header.get("v") != 1 or header.get("codec") != "h264" \
                or tuple(reader.size) != tuple(FIT_SIZE):
            raise VideoError("That is not a video clip this app made.")
        if not 1 <= reader.fps <= MAX_FPS + 0.01 or reader.duration > MAX_SECONDS + 2:
            raise VideoError("That video clip is outside what the screen takes.")
        for number in (0, reader.frames - 1):
            reader.seek(number)
            if reader.read().size != tuple(FIT_SIZE):
                raise VideoError("The prepared video file is damaged.")
    except VideoError:
        raise
    except Exception as e:
        raise VideoError("The prepared video file could not be played.") from e
    finally:
        reader.close()
    return header


def clip_name(path):
    """A name for the clip's file that depends only on what is in it, so the
    same video applied twice is kept once."""
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:20] + CLIP_EXT


class FrameSource:
    """A video kept open, to give pictures of it at chosen moments (for the
    window's preview): the original, not placed on anything, the right way up.

    Asking for a moment a little after the last one carries on decoding from
    there, which is what playing does; anything else seeks to the key picture
    before the moment and decodes up to it.
    """

    def __init__(self, path):
        av = _av()
        try:
            self.container = av.open(path)
        except Exception as e:
            raise NotAVideo("That file is not a video this app can read.") from e
        if not self.container.streams.video:
            self.container.close()
            raise VideoError("That file has no picture in it, only sound or data.")
        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"
        ctx = self.stream.codec_context
        self.sar = float(self.stream.sample_aspect_ratio or ctx.sample_aspect_ratio or 1) or 1.0
        self._frames = self._frame = self._asked = None

    def at(self, seconds, limit=None):
        """(picture, the video's own size) at `seconds`, the picture no larger
        than `limit` (WORK_LIMIT if not given)."""
        ahead = self._asked is not None and self._asked <= seconds <= self._asked + 1.0
        if not ahead:
            self.container.seek(max(0, int(seconds * 1e6)))
            self._frames, self._frame = self.container.decode(self.stream), None
        self._asked = seconds
        frame = self._frame
        if frame is None or (frame.time is not None and frame.time < seconds - 0.001):
            for frame in self._frames:
                self._frame = frame
                if frame.time is None or frame.time >= seconds - 0.001:
                    break
            frame = self._frame
        if frame is None:
            raise VideoError("No picture could be read from that video.")
        picture = _to_picture(frame, self.sar, frame.rotation or 0, limit or WORK_LIMIT)
        return picture.convert("RGB"), (frame.width, frame.height)

    def close(self):
        self.container.close()


def frame_at(path, seconds):
    """One picture of a video, `seconds` in: see FrameSource.at."""
    source = FrameSource(path)
    try:
        return source.at(seconds)
    finally:
        source.close()


def clip_picture(reader, number):
    """Picture `number` of an open clip, as the panel will show it (placement
    is already in it, but it is stored upside down, so it is turned back).
    A picture a little after the last one given is reached by carrying on."""
    number = max(0, min(reader.frames - 1, number))
    ahead = number - reader.position
    if 0 <= ahead <= 2 * reader.header.get("keyint", 60):
        picture = reader.read(skip=ahead)
    else:
        reader.seek(number)
        picture = reader.read()
    return picture.transpose(Image.ROTATE_180)


def clip_frame(path, number):
    """(picture `number` of the clip at `path`, the clip's header)."""
    reader = ClipReader(path)
    try:
        return clip_picture(reader, number), reader.header
    finally:
        reader.close()


def clip_info(path):
    """A clip's header, without loading FFmpeg (the window uses this)."""
    with open(path, "rb") as f:
        return _layout(f, os.fstat(f.fileno()).st_size)[0]


# -- the FFmpeg work, in a process of its own ---------------------------------------------------
#
# The window never loads FFmpeg itself. FFmpeg takes about 45 MB, which would
# stay in the window for as long as the app ran, and a crash inside it would
# take the window along. So the window talks to a helper process, started when
# it first needs a video looked at, and ended when it is done. Requests are
# lines of JSON on its input; each reply is a line of JSON on its output,
# followed by as many bytes as its "length" says (a picture, as JPEG).

def _jpeg(img, quality=92):
    from io import BytesIO
    buf = BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def worker_main():
    """The helper process: answers requests on stdin until it is closed."""
    # The pipes the window gave it. (The installed app is a program without a
    # console, where Python leaves sys.stdin and sys.stdout unset; the pipes
    # are there all the same, as file numbers 0 and 1.)
    inp = sys.stdin.buffer if sys.stdin else open(0, "rb", closefd=False)
    out = sys.stdout.buffer if sys.stdout else open(1, "wb", closefd=False)
    opened = {}         # the last video or clip asked about, kept open: path -> source

    def emit(reply, payload=b""):
        reply["length"] = len(payload)
        out.write(json.dumps(reply).encode() + b"\n" + payload)
        out.flush()

    def source(path, make):
        if path not in opened:
            for old in list(opened):
                opened.pop(old).close()
            opened[path] = make(path)
        return opened[path]

    for line in inp:
        req = json.loads(line)
        cmd = req.get("cmd")
        try:
            if cmd == "probe":
                emit({"ok": True, "info": probe(req["path"])})
            elif cmd == "frame":
                picture, size = source(req["path"], FrameSource).at(
                    req["seconds"], req.get("limit"))
                emit({"ok": True, "size": list(size)}, _jpeg(picture, req.get("quality", 92)))
            elif cmd == "clipframe":
                reader = source(req["path"], ClipReader)
                emit({"ok": True, "header": reader.header},
                     _jpeg(clip_picture(reader, req["number"]), req.get("quality", 92)))
            elif cmd == "convert":
                from PIL import ImageColor
                header = convert(req["source"], req["out"], req["fit"],
                                 ImageColor.getrgb(req["fit_color"]), req["zoom"],
                                 tuple(req["pan"]), req["max_fps"],
                                 progress=lambda done, total: emit({"progress": [done, total]}))
                emit({"ok": True, "header": header})
            else:
                emit({"ok": False, "error": f"unknown request {cmd!r}"})
        except VideoError as e:
            emit({"ok": False, "error": str(e), "not_a_video": isinstance(e, NotAVideo)})
        except Exception as e:
            emit({"ok": False, "error": f"{type(e).__name__}: {e}"})


class Worker:
    """The window's end of the helper process. One request at a time."""

    def __init__(self, low_priority=False):
        import subprocess
        import threading
        command = ([sys.executable, "--video-worker"] if getattr(sys, "frozen", False)
                   else [sys.executable, os.path.abspath(__file__), "worker"])
        flags = 0x08000000                              # CREATE_NO_WINDOW
        if low_priority:
            flags |= 0x00004000                         # BELOW_NORMAL_PRIORITY_CLASS
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, creationflags=flags)
        self.lock, self.killed = threading.Lock(), False

    def _read(self):
        line = self.proc.stdout.readline()
        if not line:
            raise Cancelled() if self.killed else VideoError(
                "The helper that reads videos stopped unexpectedly.")
        reply = json.loads(line)
        payload = self.proc.stdout.read(reply.get("length", 0)) if reply.get("length") else b""
        return reply, payload

    def call(self, cmd, on_progress=None, **args):
        """Send one request; returns (reply, payload). Raises VideoError with
        the helper's own message when it refuses."""
        with self.lock:
            try:
                self.proc.stdin.write(json.dumps({"cmd": cmd, **args}).encode() + b"\n")
                self.proc.stdin.flush()
            except OSError:
                raise Cancelled() if self.killed else VideoError(
                    "The helper that reads videos stopped unexpectedly.") from None
            while True:
                reply, payload = self._read()
                if "progress" in reply:
                    if on_progress:
                        on_progress(*reply["progress"])
                    continue
                if not reply.get("ok"):
                    kind = NotAVideo if reply.get("not_a_video") else VideoError
                    raise kind(reply.get("error", "That video could not be read."))
                return reply, payload

    def alive(self):
        return self.proc.poll() is None

    def kill(self):
        self.killed = True
        try:
            self.proc.kill()
        except OSError:
            pass

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(2)
        except Exception:
            self.kill()


def convert_in_worker(source, out_path, fit="fill", fit_color="#000000", zoom=1.0,
                      pan=(0.5, 0.5), max_fps=30, progress=None, cancel=None):
    """convert(), done by a helper process of its own at below-normal priority,
    so the PC stays pleasant to use meanwhile. `fit_color` is "#rrggbb".
    Setting the Event `cancel` ends the helper at once (Cancelled)."""
    import threading
    worker = Worker(low_priority=True)
    _converting.add(worker)
    done = threading.Event()

    def watch():
        while not done.wait(0.2):
            if cancel is not None and cancel.is_set():
                worker.kill()
                return

    threading.Thread(target=watch, daemon=True).start()
    try:
        reply, _ = worker.call("convert", on_progress=progress, source=source, out=out_path,
                               fit=fit, fit_color=fit_color, zoom=zoom, pan=list(pan),
                               max_fps=max_fps)
        return reply["header"]
    except BaseException:
        worker.kill()
        for leftover in (out_path, out_path + ".part"):
            try:
                os.remove(leftover)
            except OSError:
                pass
        raise
    finally:
        done.set()
        worker.close()
        _converting.discard(worker)


_converting = set()         # the helpers converting a video right now


def kill_all():
    """End every conversion under way (the app is closing): a helper left to
    itself would carry on converting, for nobody, until it was done."""
    for worker in list(_converting):
        worker.kill()


# -- the command line ------------------------------------------------------------------------

def _main(argv):
    import argparse
    import time
    from PIL import ImageColor
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert", help="make a clip from a video")
    c.add_argument("video")
    c.add_argument("clip")
    c.add_argument("--fit", choices=FIT_MODES, default="fill")
    c.add_argument("--fit-color", type=ImageColor.getrgb, default="#000000")
    c.add_argument("--zoom", type=float, default=1.0)
    c.add_argument("--pan-x", type=float, default=0.5)
    c.add_argument("--pan-y", type=float, default=0.5)
    c.add_argument("--max-fps", type=int, choices=(30, 60), default=30)
    c.add_argument("--workers", type=int)
    i = sub.add_parser("info", help="describe a clip")
    i.add_argument("clip")
    sub.add_parser("worker", help="the helper process the window uses (reads requests on stdin)")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "worker":
            worker_main()
        elif args.cmd == "convert":
            began = time.perf_counter()
            header = convert(args.video, args.clip, args.fit, args.fit_color, args.zoom,
                             (args.pan_x, args.pan_y), args.max_fps, workers=args.workers,
                             progress=lambda done, total: print(
                                 f"\r{done}/{total} pictures", end="", flush=True))
            took = time.perf_counter() - began
            print(f"\n{header['frames']} pictures at {header['fps']:.3f} fps "
                  f"({header['duration']:.1f} s), {os.path.getsize(args.clip) / 2**20:.1f} MB, "
                  f"made in {took:.1f} s")
        else:
            print(json.dumps(check_clip(args.clip), indent=1))
    except (VideoError, Cancelled) as e:
        raise SystemExit(str(e) or "cancelled")


if __name__ == "__main__":
    _main(sys.argv[1:])
