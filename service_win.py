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
"""The CrystalX LCD Windows service: the clock, from boot, as SYSTEM.

Running as a service means the clock is up before anyone logs in, the CPU
temperature (which needs administrator rights) always works, and there is
never an admin prompt. The tray app (tray_win.py) controls it:

  Start / Stop   starts or stops this service. Stopped, it holds nothing --
                 the COM port, LibreHardwareMonitor and PawnIO are all free
                 for other programs. Users may start and stop it without admin
                 rights; the installer grants exactly that.
  settings       sent over the pipe (ipc_win.py). If the display is off, the
                 tray starts the service with "--idle": it then only applies
                 the settings, touches no hardware, and stops again.

The tray never passes a file path. It reads each picture, GIF or converted
video itself and sends the bytes, in pieces; the service checks them and keeps
its own copy in the media pool inside DATA_DIR, which only SYSTEM and
administrators can write. Otherwise any program could get SYSTEM to open files
on its behalf.

Command line (from an administrator prompt, for development):
  service_win.py install | remove | start | stop | debug
"""

import base64
import binascii
import hashlib
import io
import json
import logging
import os
import random
import shutil
import sys
import threading
import time
import uuid
from logging.handlers import RotatingFileHandler

import pywintypes
import servicemanager
import win32con
import win32file
import win32pipe
import win32security
import win32service
import win32serviceutil
from PIL import Image, UnidentifiedImageError

import clock_win
import video_win
from ipc_win import (CONFIG_FILE, DATA_DIR, DISPLAY_NAME, INCOMING_DIR, LOG_FILE,
                     MAX_FRAMES, MAX_PICTURE_BYTES, MEDIA_DIR, PIPE_NAME,
                     SERVICE_NAME, UPLOAD_CHUNK, read_message)
from settings_win import (LAYOUTS_DIR, MAX_LAYOUTS, MEDIA_FILE, clean_layout_name,
                          clean_look, clean_rotation, engine_argv, find_layout,
                          layout_file, list_layouts, load_config, look_of,
                          media_in_use, read_layout)

PICTURE_FORMATS = {"GIF": ".gif", "PNG": ".png", "JPEG": ".jpg",
                   "WEBP": ".webp", "BMP": ".bmp"}
# The media pool may hold this much; a file is only accepted if it fits and the
# disk keeps this much free besides.
MAX_POOL_BYTES = 4 * 2**30
MIN_FREE_BYTES = 512 * 2**20
# A pool file nothing uses is kept this long before it is deleted, so a file
# sent a moment ago and not applied yet survives.
POOL_GRACE_SECONDS = 600

# SYSTEM and administrators: full control. Everyone else: read only -- enough
# for the tray to open the log, not enough to plant a file the service reads.
DATA_DIR_SDDL = ("D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
                 "(A;OICI;0x1200a9;;;BU)")
# Local clients only; SYSTEM, administrators and the interactive user.
PIPE_SDDL = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;IU)"
# Who may do what to the service itself. Windows' default, except that the
# interactive user may also start (RP) and stop (WP) it -- that is what lets
# the tray's Start/Stop work without an admin prompt. Changing its
# configuration (DC) stays with administrators: a user who could change the
# program a SYSTEM service runs could take over the PC.
SERVICE_SDDL = ("D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)"
                "(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)"
                "(A;;CCLCSWRPWPLOCRRC;;;IU)"
                "(A;;CCLCSWLOCRRC;;;SU)")
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000

# An idle start (settings only) stops itself once the tray has gone quiet.
IDLE_GRACE_SECONDS = 20
# How long to wait before retrying when the panel is busy or unplugged.
RETRY_SECONDS = 5

log = logging.getLogger("crystalx-lcd")


def _setup_logging():
    os.makedirs(DATA_DIR, exist_ok=True)
    _protect(DATA_DIR)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=512 * 1024, backupCount=1,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def _protect(path):
    """Give DATA_DIR its locked-down permissions, even if someone made it first."""
    sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        DATA_DIR_SDDL, win32security.SDDL_REVISION_1)
    win32security.SetNamedSecurityInfo(
        path, win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, sd.GetSecurityDescriptorDacl(), None)


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # The window reads these files, and Windows refuses to replace one
            # that is open at that moment. It is only open for a millisecond.
            if attempt == 9:
                raise
            time.sleep(0.03)


# The settings file is written by the pipe thread (apply, to-do ticks, layouts)
# and by the display's conductor (when layouts rotate): one at a time.
CONFIG_LOCK = threading.RLock()


def save_config(config):
    """Write the settings: the look, and whether saved layouts rotate."""
    with CONFIG_LOCK:
        _write_json(CONFIG_FILE, {**look_of(config), "rotation": config["rotation"]})


def clock_argv(config):
    """The engine's options for the playlist's first item, for the log."""
    return engine_argv(look_of(config), config["playlist"][0])


def _remove_matching(folder, prefix, keep=None):
    """Delete the files in `folder` named prefix.* -- a layout's files."""
    for name in os.listdir(folder):
        if name.startswith(prefix) and name != keep:
            os.remove(os.path.join(folder, name))


def save_layout(name):
    """Save the current settings as layout `name`: its look, and with it the
    playlist, whose files stay in the media pool for every layout to share.

    A layout with the same name (in any case) is replaced.
    """
    name = clean_layout_name(name)
    with CONFIG_LOCK:
        config = load_config()
    number = find_layout(name)
    if number is None:
        used = {n for n, _ in list_layouts()}
        free = [n for n in range(1, MAX_LAYOUTS + 1) if n not in used]
        if not free:
            raise ValueError(f"you can keep up to {MAX_LAYOUTS} layouts -- "
                             "delete one first")
        number = free[0]
    os.makedirs(LAYOUTS_DIR, exist_ok=True)
    _remove_matching(LAYOUTS_DIR, f"{number}.")
    _write_json(layout_file(number), {"name": name, "look": look_of(config)})
    return name


def load_layout(name):
    """The config that makes layout `name` current: its look, with the app's own
    settings (whether layouts rotate) kept as they are."""
    number = find_layout(clean_layout_name(name))
    layout = read_layout(number) if number else None
    if layout is None:
        raise ValueError(f"there is no layout called {name!r}")
    with CONFIG_LOCK:
        rotation = load_config()["rotation"]
    return {**layout[1], "rotation": rotation}


def delete_layout(name):
    number = find_layout(clean_layout_name(name))
    if number is None:
        raise ValueError(f"there is no layout called {name!r}")
    _remove_matching(LAYOUTS_DIR, f"{number}.")


def check_picture(data):
    """Validate picture bytes from the tray; returns (the file extension to
    use, the number of frames)."""
    if len(data) > MAX_PICTURE_BYTES:
        raise ValueError(f"the picture is over {MAX_PICTURE_BYTES // 2**20} MB")
    try:
        with Image.open(io.BytesIO(data)) as img:
            kind = img.format
            frames = getattr(img, "n_frames", 1)
            img.seek(0)
            img.load()
    except UnidentifiedImageError as e:
        raise ValueError("that file is not a picture this app can show") from e
    except Exception as e:
        raise ValueError(f"the picture could not be read ({e})") from e
    if kind not in PICTURE_FORMATS:
        raise ValueError(f"{kind} pictures are not supported -- use GIF, PNG, "
                         "JPEG, WEBP or BMP")
    if frames > MAX_FRAMES:
        raise ValueError(f"the GIF has {frames} frames; the limit is {MAX_FRAMES}")
    return PICTURE_FORMATS[kind], frames


# -- the media pool ----------------------------------------------------------------
#
# Every picture, GIF and converted video a playlist uses is a file in MEDIA_DIR,
# named after what is in it, so the same file sent twice is kept once and
# every saved layout can use it without a copy of its own.

def pool_bytes():
    try:
        return sum(e.stat().st_size for e in os.scandir(MEDIA_DIR) if e.is_file())
    except OSError:
        return 0


def keep_in_pool(path, extension):
    """Move the checked file at `path` into the media pool, named from its
    content; returns the name an item uses ("media/<name>")."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    name = digest.hexdigest()[:20] + extension
    os.makedirs(MEDIA_DIR, exist_ok=True)
    target = os.path.join(MEDIA_DIR, name)
    if os.path.exists(target):
        os.remove(path)
        os.utime(target)                # in use again: its grace period starts afresh
    else:
        os.replace(path, target)
    return f"media/{name}"


def missing_media(raw_look):
    """The well-named pool files a look from the window asks for that are not
    in the pool. (clean_look would leave such items out without a word; the
    person applying should be told instead.)"""
    items = raw_look.get("playlist") if isinstance(raw_look, dict) else None
    return [item["file"] for item in (items if isinstance(items, list) else [])
            if isinstance(item, dict) and isinstance(item.get("file"), str)
            and item["file"].startswith("media/") and MEDIA_FILE.fullmatch(item["file"])
            and not os.path.isfile(os.path.join(DATA_DIR, *item["file"].split("/")))]


def gc_media():
    """Delete the pool files that no playlist and no saved layout uses, once
    they have sat unused for POOL_GRACE_SECONDS."""
    with CONFIG_LOCK:
        keep = set(media_in_use(load_config()))
    for number, _ in list_layouts():
        layout = read_layout(number)
        if layout:
            keep |= media_in_use(layout[1])
    used = {name.split("/", 1)[1] for name in keep if name.startswith("media/")}
    try:
        names = os.listdir(MEDIA_DIR)
    except OSError:
        return
    now = time.time()
    for name in names:
        path = os.path.join(MEDIA_DIR, name)
        try:
            if name not in used and now - os.path.getmtime(path) >= POOL_GRACE_SECONDS:
                os.remove(path)
                log.info("no longer used, removed: %s", name)
        except OSError:
            pass


def _into_pool(file):
    """The pool name for a file kept the v1.0-v1.2 way (picture.<ext> in the
    data folder, <n>.<ext> beside a layout); None if `file` is not one."""
    if not file.startswith(("picture.", "layouts/")):
        return None
    try:
        return keep_in_pool(os.path.join(DATA_DIR, *file.split("/")),
                            os.path.splitext(file)[1])
    except OSError as e:
        log.warning("could not move %s into the media pool: %s", file, e)
        return None


def migrate_media():
    """Bring settings and layouts made before v1.3 up to date: their pictures
    move into the media pool and the files are rewritten with a playlist.

    Safe to run at every start: it only touches what is old."""
    os.makedirs(MEDIA_DIR, exist_ok=True)
    shutil.rmtree(INCOMING_DIR, ignore_errors=True)         # an interrupted upload
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = None
    if isinstance(raw, dict):
        with CONFIG_LOCK:
            config = load_config()
            moved = False
            for item in config["playlist"]:
                new = _into_pool(item["file"])
                if new:
                    item["file"], moved = new, True
            if moved or "playlist" not in raw or "picture" in raw:
                save_config(config)
                log.info("settings brought up to date for playlists")
    for number, _ in list_layouts():
        try:
            with open(layout_file(number), encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            continue
        layout = read_layout(number)
        if layout is None:
            continue
        name, look = layout
        moved = False
        for item in look["playlist"]:
            new = _into_pool(item["file"])
            if new:
                item["file"], moved = new, True
        if moved or "playlist" not in (raw.get("look") or {}) or "picture" in raw:
            _write_json(layout_file(number), {"name": name, "look": look_of(look)})
            log.info("layout %r brought up to date for playlists", name)


class Uploads:
    """The file the window is sending, one at a time: staged in INCOMING_DIR,
    checked, then kept in the media pool.

    Nothing the window says is used as a path or a name: files are staged
    under a random token, and kept under a name made from their content.
    """

    def __init__(self):
        self.current = None
        self._lock = threading.Lock()

    def _clear(self):
        up, self.current = self.current, None
        if up:
            up["file"].close()
            try:
                os.remove(up["path"])
            except OSError:
                pass

    def begin(self, name, size, kind):
        if kind not in ("picture", "clip"):
            raise ValueError("unknown kind of file")
        limit = MAX_PICTURE_BYTES if kind == "picture" else video_win.MAX_CLIP_BYTES
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= limit:
            raise ValueError(f"the file is empty or over {limit // 2**20} MB")
        if pool_bytes() + size > MAX_POOL_BYTES:
            raise ValueError(f"the stored pictures and videos would take more than "
                             f"{MAX_POOL_BYTES // 2**30} GB: take some out of the playlist "
                             "or the saved layouts first")
        os.makedirs(INCOMING_DIR, exist_ok=True)
        if shutil.disk_usage(DATA_DIR).free < size * 2 + MIN_FREE_BYTES:
            raise ValueError("there is not enough free disk space for that file")
        with self._lock:
            self._clear()
            token = uuid.uuid4().hex[:16]
            path = os.path.join(INCOMING_DIR, token + ".part")
            self.current = {"token": token, "path": path, "size": size, "received": 0,
                            "kind": kind, "file": open(path, "wb")}
        return token

    def chunk(self, token, offset, data):
        with self._lock:
            up = self.current
            if up is None or token != up["token"]:
                raise ValueError("that upload is not in progress")
            if offset != up["received"]:
                raise ValueError("the pieces of the file arrived out of order")
            try:
                raw = base64.b64decode(data, validate=True)
            except (binascii.Error, TypeError, ValueError):
                raise ValueError("a piece of the file was damaged on the way") from None
            if len(raw) > UPLOAD_CHUNK or up["received"] + len(raw) > up["size"]:
                raise ValueError("more data arrived than the file said it had")
            up["file"].write(raw)
            up["received"] += len(raw)

    def end(self, token):
        """Check the finished file and keep it; returns what the window needs
        to know about it."""
        with self._lock:
            up = self.current
            if up is None or token != up["token"]:
                raise ValueError("that upload is not in progress")
            up["file"].close()
            self.current = None
        path = up["path"]
        try:
            if up["received"] != up["size"]:
                raise ValueError("the file did not arrive in full")
            if up["kind"] == "picture":
                with open(path, "rb") as f:
                    extension, frames = check_picture(f.read())
                kind, duration = "pictures", (frames / 10 if frames > 1 else 0.0)
            else:
                try:
                    header = video_win.check_clip(path)
                except video_win.VideoError as e:
                    raise ValueError(str(e)) from e
                extension, frames = video_win.CLIP_EXT, header["frames"]
                kind, duration = "video", header["duration"]
            name = keep_in_pool(path, extension)
        except BaseException:
            try:
                os.remove(path)
            except OSError:
                pass
            raise
        log.info("kept %s (%s, %d frames)", name, kind, frames)
        return {"file": name, "kind": kind, "frames": frames, "duration": duration}

    def abort(self, token=None):
        with self._lock:
            if self.current and (token is None or token == self.current["token"]):
                self._clear()


def start_type():
    """True when the service starts with Windows."""
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        svc = win32service.OpenService(scm, SERVICE_NAME,
                                       win32service.SERVICE_QUERY_CONFIG)
        try:
            return win32service.QueryServiceConfig(svc)[1] == win32service.SERVICE_AUTO_START
        finally:
            win32service.CloseServiceHandle(svc)
    finally:
        win32service.CloseServiceHandle(scm)


def set_start_type(automatic):
    """Switch "start with Windows". The service may do this to itself as SYSTEM;
    ordinary users are deliberately not allowed to change its configuration."""
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        svc = win32service.OpenService(scm, SERVICE_NAME,
                                       win32service.SERVICE_CHANGE_CONFIG)
        try:
            win32service.ChangeServiceConfig(
                svc, win32service.SERVICE_NO_CHANGE,
                win32service.SERVICE_AUTO_START if automatic
                else win32service.SERVICE_DEMAND_START,
                win32service.SERVICE_NO_CHANGE,
                None, None, 0, None, None, None, None)
        finally:
            win32service.CloseServiceHandle(svc)
    finally:
        win32service.CloseServiceHandle(scm)


LEAD_SECONDS = 20       # a playlist's next item is made this long before it is due
MIN_ITEM_SECONDS = 2    # however short a GIF or video is, an item shows at least this long
GUARD_FILE = os.path.join(DATA_DIR, "playing.json")


def _clear_guard():
    try:
        os.remove(GUARD_FILE)
    except OSError:
        pass


class Guard:
    """Keeps one damaged clip from stopping the display for good.

    A clip that crashes the decoder takes the whole service with it, and would
    again at every start. So the clip being played is named in a note on disk
    (GUARD_FILE); a service that starts and finds a clip named there knows what
    it was doing when it ended. Mostly that is innocent -- the PC was switched
    off, and Windows ends a service then without telling it -- so one such end
    counts for nothing. A clip is left out only when the service has ended
    STRIKES times running while playing it, and never once it has been seen
    to play right through (`proven`). Applying settings forgives everything.

    A service that only shows pictures and GIFs never writes the note.
    """

    STRIKES = 2
    REMEMBER = 40           # proven clips kept on record

    def __init__(self):
        self._lock = threading.Lock()
        self.playing = None     # the clip named in the note
        self.struck = {}        # clip -> times running the service ended while playing it
        self.proven = []        # clips seen to play right through
        self.suspects = []      # clips left out
        try:
            with open(GUARD_FILE, encoding="utf-8") as f:
                note = json.load(f)
            if isinstance(note.get("playing"), str):
                self.playing = note["playing"]
            self.struck = {file: count for file, count in note.get("struck", {}).items()
                           if isinstance(count, int)}
            self.proven = [file for file in note.get("proven", []) if isinstance(file, str)]
            self.suspects = [file for file in note.get("suspects", []) if isinstance(file, str)]
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def _write(self):
        try:
            if self.playing or self.struck or self.proven or self.suspects:
                _write_json(GUARD_FILE, {"playing": self.playing, "struck": self.struck,
                                         "proven": self.proven, "suspects": self.suspects})
            else:
                _clear_guard()
        except OSError:
            pass

    def restarted(self):
        """Call once as the service starts. If the note names a clip, the last
        run ended while playing it; returns that clip when this is what gets
        it left out."""
        with self._lock:
            file, self.playing = self.playing, None
            if file is None:
                return None
            condemned = None
            if file not in self.proven and file not in self.suspects:
                self.struck[file] = self.struck.get(file, 0) + 1
                if self.struck[file] >= self.STRIKES:
                    del self.struck[file]
                    self.suspects.append(file)
                    condemned = file
            self._write()
            return condemned

    def note(self, file):
        """`file` is the clip now playing (None: a picture, or nothing)."""
        with self._lock:
            if file != self.playing:
                self.playing = file
                self._write()

    def prove(self, file):
        """`file` has played right through."""
        with self._lock:
            if file not in self.proven:
                self.proven = (self.proven + [file])[-self.REMEMBER:]
                self.struck.pop(file, None)
                self._write()

    def forgive(self):
        """Settings were applied: every clip gets another go."""
        with self._lock:
            if self.struck or self.suspects:
                self.struck, self.suspects = {}, []
                self._write()


class Display:
    """Plays the settings on the panel, from a thread, until the service stops.

    The playlist is played one item after another. A change of settings never
    pauses the screen: the new look is built (clock_win.Show) in the background
    while the old one keeps playing, then swapped in (Player.swap). The same
    happens by itself at midnight, when the calendar or a countdown needs
    redrawing, when the weather has changed (it is read in the background,
    at most every 15 minutes, and only while its block is shown), and
    whenever a playlist item has run its course: the next one is
    made a little before it is due and swapped in at the moment. When saved
    layouts rotate, the same happens with the first item of the next layout.
    While the panel is busy or unplugged, it retries every few seconds.

    With one item and no rotation there is nothing to decide, so no thread is
    started for it: a single picture or GIF plays exactly as it always did.
    """

    def __init__(self, stopping):
        self.stopping = stopping            # the whole service is stopping
        self.retry = threading.Event()      # try again now, rather than wait
        self.wake = threading.Event()       # tells the conductor something changed
        self.player = None
        self.state, self.detail, self.temps = "starting", "", ""
        self._temps = None                  # one Temperatures, shared by every Show
        self._rebuild_lock = threading.Lock()
        self.config = None                  # the settings being played
        self.items, self.order, self.pos = {}, [], 0    # item by id; play order; the place in it
        self.generation = 0                 # which conductor is the current one
        self.prebuilt = None                # the next show, made ahead and waiting
        self.holdoff = 0.0                  # no new attempt to make the next show before this time
        self.last_switch = time.time()      # when layouts last rotated
        self.last_layout = None             # the layout the rotation is up to
        self.guard = Guard()                # the clips the service keeps ending on
        self.failed = set()                 # clips that stopped decoding during this run
        self._proving = None                # the timer that will call a clip proven
        self.thread =threading.Thread(target=self._run, name="display", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.generation += 1
        self.retry.set()
        self.wake.set()
        self.thread.join(15)

    def _log(self, message):
        log.info("clock: %s", message)
        if message.startswith(("temperatures", "temperatures unavailable")):
            self.temps = message
        elif " frames at " in message:
            self.state, self.detail = "showing", message.split(" on ", 1)[-1].split(",")[0]

    def _get_temps(self):
        if self._temps is None:
            from temps_win import Temperatures
            self._temps = Temperatures()
        return self._temps

    # -- the playlist --------------------------------------------------------

    @staticmethod
    def _order(config):
        """The playlist's items by id, and the order they play in (shuffled if
        asked)."""
        items = {item["id"]: item for item in config["playlist"]}
        order = list(items)
        if config["playlist_shuffle"] and len(order) > 1:
            random.shuffle(order)
        return items, order

    def _plan(self, config, keep=None):
        self.config = config
        self.items, self.order = self._order(config)
        self.pos = self.order.index(keep) if keep in self.order else 0

    def _build(self, config, items, item_id, fetch=True):
        item = items[item_id]
        args = clock_win.build_parser().parse_args(engine_argv(look_of(config), item))
        show = clock_win.make_show(args, self._log, self._get_temps, fetch)
        show.item_id, show.pending, show.ends_at = item_id, None, None
        return show

    def _make_show(self, config, items, order, start, fetch=True):
        """The show for position `start` of `order`, or the next one that can
        be made if that one can't (a file that has gone, a damaged clip): one
        bad item must not stop the rest of the playlist. `fetch` off makes it
        without waiting for the weather (see clock_win.make_show)."""
        last = None
        for step in range(len(order)):
            item = items[order[(start + step) % len(order)]]
            if item["file"] in self.guard.suspects or item["file"] in self.failed:
                continue
            try:
                return self._build(config, items, item["id"], fetch)
            except SystemExit as e:
                log.warning("leaving out %s: %s", item["label"], e.code)
                last = e
        raise last or SystemExit("there is nothing in the playlist that can be shown")

    def _cycling(self, config=None, order=None):
        """Does something have to decide what comes next? (Asked of the plan
        being played, or of one about to be.)"""
        config = self.config if config is None else config
        order = self.order if order is None else order
        return len(order) > 1 or config["rotation"]["enabled"]

    def _ends_at(self, show):
        """When `show`, just started, has had its turn: a still picture shows
        for the seconds it was given, a GIF or video plays through as many
        times as it was told to. With one item, the layout rotation is what
        ends it; with nothing to rotate to, it never ends."""
        now = time.time()
        item = self.items.get(show.item_id, {})
        if len(self.order) > 1:
            if show.kind == "video":
                length = item.get("plays", 1) * show.clip.duration
            elif len(show.frames) <= 1:
                length = item.get("seconds", 30)
            else:
                length = item.get("plays", 1) * len(show.frames) / show.fps
            return now + max(MIN_ITEM_SECONDS, length)
        rotation = self.config["rotation"]
        if rotation["enabled"]:
            return max(now, self.last_switch + rotation["minutes"] * 60)
        return None

    def _started(self, show):
        """A show has started playing (the player calls this from its thread)."""
        pending, show.pending = show.pending, None
        if pending is not None:
            self._adopt(pending)
        if show.item_id in self.order:
            self.pos = self.order.index(show.item_id)
        if self.prebuilt is show:
            self.prebuilt = None
        show.ends_at = self._ends_at(show) if self._cycling() else None
        self._guard(show)
        self.wake.set()

    def _adopt(self, pending):
        """Take over the plan that came with a show: new settings, a rotated
        layout, or a playlist that has been shuffled afresh."""
        if pending.get("config") is not None:
            self.config = pending["config"]
        self.items, self.order = pending["items"], pending["order"]
        if pending.get("rotated"):
            try:
                with CONFIG_LOCK:
                    save_config(self.config)    # so the window shows the layout now playing
            except OSError:
                log.warning("could not save the rotated layout; carrying on")
            self.last_switch, self.last_layout = time.time(), pending["layout"]
            log.info("layout rotated: %s", pending["layout"])

    def _guard(self, show):
        """Tell the guard which clip is playing now (see Guard), and have the
        clip called proven once it has all but played through: its last
        pictures were decoded when it was sent here (check_clip)."""
        timer, self._proving = self._proving, None
        if timer is not None:
            timer.cancel()
        file = self.items.get(show.item_id, {}).get("file") if show.kind == "video" else None
        self.guard.note(file)
        if file and file not in self.guard.proven:
            timer = threading.Timer(max(1.0, show.clip.duration - 0.25), self._proved,
                                    (show, file))
            timer.daemon = True
            timer.start()
            self._proving = timer

    def _proved(self, show, file):
        player = self.player
        if player is not None and player.show is show and not self.stopping.is_set():
            self.guard.prove(file)

    def forgive(self):
        """Settings were applied: clips left out get another go."""
        self.failed.clear()
        self.guard.forgive()

    # -- deciding what comes next -----------------------------------------------

    def _arm(self, config=None, order=None):
        """Start the conductor, if the plan needs one (the one given, which is
        about to be swapped in; else the one playing). Any earlier conductor
        sees the new generation and ends."""
        self.generation += 1
        if self._cycling(config, order):
            threading.Thread(target=self._conduct, args=(self.generation,),
                             name="conductor", daemon=True).start()

    def _conduct(self, generation):
        while not self.stopping.is_set() and generation == self.generation:
            self.wake.wait(1.0)
            self.wake.clear()
            player = self.player
            if player is None or player._next is not None or self.prebuilt is not None:
                continue
            ends = getattr(player.show, "ends_at", None)
            now = time.time()
            if ends is None or now < ends - LEAD_SECONDS or now < self.holdoff:
                continue
            with self._rebuild_lock:
                if generation != self.generation or self.prebuilt is not None:
                    continue
                try:
                    show = self._next_show(ends)
                except SystemExit as e:
                    log.warning("the next item can't be shown: %s", e.code)
                    self.holdoff = time.time() + 30
                    continue
                except Exception:
                    log.exception("the next item can't be made")
                    self.holdoff = time.time() + 30
                    continue
                if show is None:           # nothing to change to: look again later
                    player.show.ends_at = time.time() + max(
                        60, self.config["rotation"]["minutes"] * 60)
                    continue
                if generation != self.generation or self.stopping.is_set():
                    show.close()
                    continue
                self.prebuilt = show
                player.swap(show, at=ends)

    def _next_layout(self):
        """(name, look) of the saved layout after the one rotation is up to, or
        None when there is none."""
        layouts = list_layouts()
        if not layouts:
            return None
        names = [name for _, name in layouts]
        after = names.index(self.last_layout) + 1 if self.last_layout in names else 0
        number, name = layouts[after % len(layouts)]
        layout = read_layout(number)
        return (name, layout[1]) if layout else None

    def _next_show(self, ends):
        """The show to swap in when the current one ends at `ends`: the first
        item of the next layout once its turn has come, else the next item of
        this playlist. None when there is nothing to change to."""
        rotation = self.config["rotation"]
        if rotation["enabled"] and ends >= self.last_switch + rotation["minutes"] * 60:
            target = self._next_layout()
            if target is not None:
                name, look = target
                config = {**look, "rotation": rotation}
                items, order = self._order(config)
                show = self._make_show(config, items, order, 0)
                show.pending = {"config": config, "items": items, "order": order,
                                "layout": name, "rotated": True}
                return show
        if len(self.order) <= 1:
            return None                 # one item, and no layout to rotate to
        pos, order, pending = self.pos + 1, self.order, None
        if pos >= len(order):
            pos = 0
            if self.config["playlist_shuffle"]:
                order = list(order)
                random.shuffle(order)
                if order[0] == self.order[-1]:      # not the same one twice running
                    order[0], order[1] = order[1], order[0]
                pending = {"items": self.items, "order": order}
        show = self._make_show(self.config, self.items, order, pos)
        show.pending = pending
        return show

    # -- changing the settings -------------------------------------------------------

    def _stale(self):
        """Something in the picture is out of date (midnight, or the weather
        is due another look): rebuild what is playing, and carry on from the
        same item -- unless the weather was all, and the show can stay (see
        clock_win.weather_kept)."""
        threading.Thread(target=self._refresh, name="rebuild", daemon=True).start()

    def _refresh(self):
        player = self.player
        try:
            if player is not None and clock_win.weather_kept(player.show, self._log):
                return
        except Exception:
            log.exception("the weather could not be looked at; rebuilding")
        self._rebuild(True)

    def reload(self, keep=False):
        """New settings (or the midnight refresh): build them in the
        background and swap them in when ready."""
        threading.Thread(target=self._rebuild, args=(keep,), name="rebuild",
                         daemon=True).start()

    def _rebuild(self, keep):
        with self._rebuild_lock:            # one at a time; the last one wins
            player = self.player
            if player is None:
                self.retry.set()            # not playing: the next try uses them
                return
            try:
                config = load_config()
                items, order = self._order(config)
                current = getattr(player.show, "item_id", None) if keep else None
                show = self._make_show(config, items, order,
                                       order.index(current) if current in order else 0)
            except SystemExit as e:
                log.warning("the new settings can't be shown, keeping the old: %s", e.code)
                return
            except Exception:
                log.exception("the new settings can't be shown, keeping the old")
                return
            if self.player is player and not self.stopping.is_set():
                self.generation += 1        # whoever was waiting to change item stops
                self.prebuilt = None        # (swap closes a next item made ahead)
                show.pending = {"config": config, "items": items, "order": order}
                player.swap(show)
                self._arm(config, order)
                log.info("now showing the new settings")
            else:
                show.close()

    def _run(self):
        while not self.stopping.is_set():
            self.retry.clear()
            self.state, self.detail = "starting", ""
            show = panel = None
            try:
                self._plan(load_config())
                # Without waiting for the weather: the screen comes up at once,
                # and the first reading follows in the background (_stale).
                show = self._make_show(self.config, self.items, self.order, 0, fetch=False)
                panel = clock_win.Panel()
                self._log(show.describe(panel.port))
                self.player = clock_win.Player(panel, show, on_stale=self._stale,
                                               on_started=self._started)
                self._started(show)
                self._arm()
                self.player.run(self.stopping)
                show = self.player.show     # the one playing when it stopped
                continue
            except SystemExit as e:
                # The engine's own error messages: panel missing, port busy,
                # unreadable picture. All of these can clear up by themselves.
                self.state, self.detail = "waiting", str(e.code)
                log.warning("waiting: %s", e.code)
            except video_win.VideoError as e:
                # The clip being played stopped decoding. Leave it out for the
                # rest of this run and carry on at once with what is left.
                playing = self.player.show if self.player is not None else show
                file = self.items.get(getattr(playing, "item_id", None), {}).get("file")
                self.state, self.detail = "waiting", str(e)
                log.warning("leaving out %s until settings are applied again: %s", file, e)
                if file and file not in self.failed:
                    self.failed.add(file)
                    self.retry.set()
            except Exception as e:
                self.state, self.detail = "waiting", f"{type(e).__name__}: {e}"
                log.exception("clock stopped with an error")
            finally:
                self.generation += 1
                if self.player is not None:
                    show = self.player.show
                    if self.player._next is not None:   # swapped in too late
                        self.player._next.close()
                    self.player = None
                self.prebuilt = None
                if panel is not None:
                    panel.close()
                if show is not None:
                    show.close()
            self.retry.wait(RETRY_SECONDS)
        if self._temps is not None:
            self._temps.close()


class PipeServer:
    """Answers the tray app, one request at a time."""

    def __init__(self, service):
        self.service = service
        sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            PIPE_SDDL, win32security.SDDL_REVISION_1)
        self.sa = win32security.SECURITY_ATTRIBUTES()
        self.sa.SECURITY_DESCRIPTOR = sd
        self.thread = threading.Thread(target=self._run, name="pipe", daemon=True)

    def start(self):
        self.thread.start()

    def wake(self):
        """Unblock the waiting ConnectNamedPipe so the thread can exit."""
        try:
            handle = win32file.CreateFile(
                PIPE_NAME, win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None, win32file.OPEN_EXISTING, 0, None)
            win32file.CloseHandle(handle)
        except pywintypes.error:
            pass

    def _create(self, first=False):
        # The first instance claims the name, so nothing else can create the
        # pipe first and pose as the service.
        return win32pipe.CreateNamedPipe(
            PIPE_NAME,
            win32pipe.PIPE_ACCESS_DUPLEX
            | (FILE_FLAG_FIRST_PIPE_INSTANCE if first else 0),
            win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE
            | win32pipe.PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
            win32pipe.PIPE_UNLIMITED_INSTANCES, 64 * 1024, 64 * 1024, 0, self.sa)

    def _run(self):
        handle = self._create(first=True)
        while not self.service.stopping.is_set():
            try:
                win32pipe.ConnectNamedPipe(handle, None)
            except pywintypes.error as e:
                if e.winerror != 535:           # ERROR_PIPE_CONNECTED is fine
                    log.exception("pipe error")
            if self.service.stopping.is_set():
                break
            # Open the next instance before answering this client, so there is
            # never a moment with no pipe for the tray to connect to.
            connected, handle = handle, self._create()
            self._answer(connected)
        win32file.CloseHandle(handle)

    def _answer(self, handle):
        try:
            try:
                reply = self.service.handle(json.loads(read_message(handle)))
            except ValueError as e:
                # A picture or setting the service turned down: the message is
                # meant for the user, and there is nothing to debug.
                log.warning("rejected: %s", e)
                reply = {"ok": False, "error": str(e)}
            except Exception as e:
                log.exception("request failed")
                reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            win32file.WriteFile(handle, json.dumps(reply).encode())
            win32file.FlushFileBuffers(handle)
        except pywintypes.error as e:
            # 109/232: the client hung up before the reply -- nothing to fix.
            if e.winerror not in (109, 232):
                log.exception("pipe error")
        finally:
            try:
                win32pipe.DisconnectNamedPipe(handle)
            except pywintypes.error:
                pass
            win32file.CloseHandle(handle)


class CrystalLcdService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = DISPLAY_NAME
    _svc_description_ = ("Shows a clock, the date and PC stats on the LCD "
                         "screen of a CrystalX CoreView case.")

    def __init__(self, args):
        super().__init__(args)
        # Start parameters follow the service name. The tray passes --idle
        # when it only needs to change settings while the display is off.
        self.idle = "--idle" in args[1:]
        self.stopping = threading.Event()
        self.last_request = None
        self.display = None
        self.pipe = None
        self.uploads = Uploads()

    # -- service control ------------------------------------------------

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.stopping.set()

    def SvcDoRun(self):
        _setup_logging()
        log.info("service starting%s", " (idle: settings only)" if self.idle else "")
        try:
            migrate_media()
            gc_media()
        except Exception:
            log.exception("could not tidy the media pool")
        self.pipe = PipeServer(self)
        self.pipe.start()
        if not self.idle:
            self.display = Display(self.stopping)
            self._check_guard()
            self.display.start()
        self.last_request = time.monotonic()
        while not self.stopping.wait(1):
            if self.idle and time.monotonic() - self.last_request > IDLE_GRACE_SECONDS:
                log.info("idle start finished")
                break
        self.stopping.set()
        if self.display:
            self.display.stop()
            self.display.guard.note(None)   # a clean stop: nothing was playing when it ended
        self.pipe.wake()
        self.pipe.thread.join(5)
        log.info("service stopped")

    def _check_guard(self):
        """Count the last run's end against the clip it was playing, if it was
        playing one (see Guard)."""
        file = self.display.guard.restarted()
        if file:
            log.warning("the service has ended twice running while playing %s; leaving "
                        "it out until settings are applied again", file)

    # -- requests from the tray -----------------------------------------

    def handle(self, req):
        self.last_request = time.monotonic()
        cmd = req.get("cmd")
        if cmd == "status":
            return self._status()
        if cmd == "upload_begin":
            return {"ok": True, "upload": self.uploads.begin(
                req.get("name"), req.get("size"), req.get("kind"))}
        if cmd == "upload_chunk":
            self.uploads.chunk(req.get("upload"), req.get("offset"), req.get("data"))
            return {"ok": True}
        if cmd == "upload_end":
            return {"ok": True, **self.uploads.end(req.get("upload"))}
        if cmd == "upload_abort":
            self.uploads.abort(req.get("upload"))
            return {"ok": True}
        if cmd == "apply":
            # Everything the window's Apply button sends: the look, whose
            # playlist names files already sent with the upload commands.
            if missing_media(req.get("look")):
                raise ValueError("a picture or video in the playlist is no longer "
                                 "stored -- add it to the playlist again")
            with CONFIG_LOCK:
                config = load_config()
                config.update(clean_look(req.get("look")))
            if self.display:
                self.display.forgive()
            else:
                Guard().forgive()
            reply = self._saved(config, "settings applied")
            gc_media()
            return reply
        if cmd == "set_rotation":
            with CONFIG_LOCK:
                config = load_config()
                config["rotation"] = clean_rotation(req.get("rotation"))
            save_config(config)
            log.info("layout rotation: %s", config["rotation"])
            if self.display:
                self.display.reload(keep=True)
            return self._status()
        if cmd == "save_layout":
            name = save_layout(req.get("name"))
            log.info("layout saved: %s", name)
            return self._status()
        if cmd == "load_layout":
            config = load_layout(req.get("name"))
            if self.display:
                self.display.last_layout = clean_layout_name(req.get("name"))
                self.display.last_switch = time.time()
            reply = self._saved(config, f"layout loaded: {req.get('name')}")
            gc_media()
            return reply
        if cmd == "delete_layout":
            delete_layout(req.get("name"))
            log.info("layout deleted: %s", req.get("name"))
            gc_media()
            return self._status()
        if cmd == "todo":
            # A tick, untick or removal from the window: straight to the
            # screen, without the rest of the form. Items are found by id.
            with CONFIG_LOCK:
                config = load_config()
                items, action = config["todo_items"], req.get("action")
                if action == "remove_done":
                    items = [item for item in items if not item["done"]]
                else:
                    item = next((i for i in items if i["id"] == req.get("id")), None)
                    if item is None:
                        raise ValueError("that to-do item is no longer there")
                    if action in ("tick", "untick"):
                        item["done"] = action == "tick"
                    elif action == "remove":
                        items.remove(item)
                    else:
                        raise ValueError(f"unknown to-do action {action!r}")
                config["todo_items"] = items
            return self._saved(config, f"to-do list: {action}")
        if cmd == "set_autostart":
            set_start_type(bool(req.get("on")))
            log.info("start with Windows: %s", bool(req.get("on")))
            return self._status()
        return {"ok": False, "error": f"unknown command {cmd!r}"}

    def _saved(self, config, what):
        save_config(config)
        log.info("%s (%s)", what, " ".join(clock_argv(config)[1:]))
        if self.display:
            self.display.reload()
        return self._status()

    def _status(self):
        playing = None
        if self.display:
            state, detail, temps = self.display.state, self.display.detail, self.display.temps
            player = self.display.player
            item = getattr(player.show, "item_id", None) if player else None
            if item is not None:
                playing = {"item": item, "position": self.display.pos + 1,
                           "count": len(self.display.order)}
        else:
            state, detail, temps = "idle", "", ""
        return {"ok": True, "state": state, "detail": detail, "temps": temps,
                "autostart": start_type(), "playing": playing}


def grant_users_start_stop():
    """Apply SERVICE_SDDL. The installer does the same; this is for installs
    made from the command line."""
    sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        SERVICE_SDDL, win32security.SDDL_REVISION_1)
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        svc = win32service.OpenService(scm, SERVICE_NAME,
                                       win32con.WRITE_DAC | win32con.READ_CONTROL)
        try:
            win32service.SetServiceObjectSecurity(
                svc, win32security.DACL_SECURITY_INFORMATION, sd)
        finally:
            win32service.CloseServiceHandle(svc)
    finally:
        win32service.CloseServiceHandle(scm)


if __name__ == "__main__":
    if len(sys.argv) == 1 and getattr(sys, "frozen", False):
        # Started by Windows as the packaged service executable.
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(CrystalLcdService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(CrystalLcdService)
        if any(arg in ("install", "update") for arg in sys.argv[1:]):
            grant_users_start_stop()
            print("Users may now start and stop the service.")
