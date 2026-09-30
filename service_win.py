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

The tray never passes a file path. It reads the picture itself and sends the
bytes; the service checks them and keeps its own copy in DATA_DIR, which only
SYSTEM and administrators can write. Otherwise any program could get SYSTEM to
open files on its behalf.

Command line (from an administrator prompt, for development):
  service_win.py install | remove | start | stop | debug
"""

import base64
import io
import json
import logging
import os
import shutil
import sys
import threading
import time
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
from ipc_win import (CONFIG_FILE, DATA_DIR, DISPLAY_NAME, LOG_FILE, MAX_FRAMES,
                     MAX_PICTURE_BYTES, PIPE_NAME, SERVICE_NAME, read_message)
from settings_win import (LAYOUTS_DIR, MAX_LAYOUTS, clean_layout_name,
                          clean_look, engine_argv, find_layout, layout_file,
                          list_layouts, load_config, look_of, picture_path,
                          read_layout)

PICTURE_FORMATS = {"GIF": ".gif", "PNG": ".png", "JPEG": ".jpg",
                   "WEBP": ".webp", "BMP": ".bmp"}

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
    os.replace(tmp, path)


def save_config(config):
    _write_json(CONFIG_FILE, config)


def clock_argv(config):
    return engine_argv(look_of(config), picture_path(config))


def _remove_matching(folder, prefix, keep=None):
    """Delete the files in `folder` named prefix.* -- old copies of a picture."""
    for name in os.listdir(folder):
        if name.startswith(prefix) and name != keep:
            os.remove(os.path.join(folder, name))


def store_picture(config, data, label):
    """Keep the service's own copy of a picture the window sent."""
    name = "picture" + check_picture(data)
    _remove_matching(DATA_DIR, "picture.", keep=name)
    with open(os.path.join(DATA_DIR, name), "wb") as f:
        f.write(data)
    config["picture"] = name
    config["picture_label"] = os.path.basename(str(label or name))[:100]


def save_layout(name):
    """Save the current settings, picture included, as layout `name`.

    A layout with the same name (in any case) is replaced.
    """
    name = clean_layout_name(name)
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
    picture = None
    if config["picture"]:
        picture = f"{number}{os.path.splitext(config['picture'])[1]}"
        shutil.copyfile(os.path.join(DATA_DIR, config["picture"]),
                        os.path.join(LAYOUTS_DIR, picture))
    _write_json(layout_file(number), {
        "name": name, "picture": picture,
        "picture_label": config["picture_label"] if picture else None,
        "look": look_of(config)})
    return name


def load_layout(name):
    """Make layout `name` the current settings; returns the new config."""
    number = find_layout(clean_layout_name(name))
    layout = read_layout(number) if number else None
    if layout is None:
        raise ValueError(f"there is no layout called {name!r}")
    _, picture, label, look = layout
    config = {"picture": None, "picture_label": None, **look}
    if picture:
        current = "picture" + os.path.splitext(picture)[1]
        _remove_matching(DATA_DIR, "picture.", keep=current)
        shutil.copyfile(os.path.join(LAYOUTS_DIR, picture),
                        os.path.join(DATA_DIR, current))
        config["picture"], config["picture_label"] = current, label
    return config


def delete_layout(name):
    number = find_layout(clean_layout_name(name))
    if number is None:
        raise ValueError(f"there is no layout called {name!r}")
    _remove_matching(LAYOUTS_DIR, f"{number}.")


def check_picture(data):
    """Validate picture bytes from the tray; return the file extension to use."""
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
    return PICTURE_FORMATS[kind]


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


class Display:
    """Runs clock_win in a thread, restarting it on settings changes and
    retrying while the panel is busy or unplugged."""

    def __init__(self, stopping):
        self.stopping = stopping            # the whole service is stopping
        self.restart = threading.Event()    # ends the current clock run
        self.state, self.detail, self.temps = "starting", "", ""
        self.thread = threading.Thread(target=self._run, name="display", daemon=True)

    def start(self):
        self.thread.start()

    def reload(self):
        self.restart.set()

    def stop(self):
        self.restart.set()
        self.thread.join(15)

    def _log(self, message):
        log.info("clock: %s", message)
        if message.startswith(("temperatures", "temperatures unavailable")):
            self.temps = message
        elif " frames at " in message:
            self.state, self.detail = "showing", message.split(" on ", 1)[-1].split(",")[0]

    def _run(self):
        while not self.stopping.is_set():
            self.restart.clear()
            self.state, self.detail = "starting", ""
            try:
                clock_win.main(clock_argv(load_config()), stop=self.restart, log=self._log)
                continue            # stopped or restarted on purpose
            except SystemExit as e:
                # The engine's own error messages: panel missing, port busy,
                # unreadable picture. All of these can clear up by themselves.
                self.state, self.detail = "waiting", str(e.code)
                log.warning("waiting: %s", e.code)
            except Exception as e:
                self.state, self.detail = "waiting", f"{type(e).__name__}: {e}"
                log.exception("clock stopped with an error")
            self.restart.wait(RETRY_SECONDS)


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

    # -- service control ------------------------------------------------

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.stopping.set()

    def SvcDoRun(self):
        _setup_logging()
        log.info("service starting%s", " (idle: settings only)" if self.idle else "")
        self.pipe = PipeServer(self)
        self.pipe.start()
        if not self.idle:
            self.display = Display(self.stopping)
            self.display.start()
        self.last_request = time.monotonic()
        while not self.stopping.wait(1):
            if self.idle and time.monotonic() - self.last_request > IDLE_GRACE_SECONDS:
                log.info("idle start finished")
                break
        self.stopping.set()
        if self.display:
            self.display.stop()
        self.pipe.wake()
        self.pipe.thread.join(5)
        log.info("service stopped")

    # -- requests from the tray -----------------------------------------

    def handle(self, req):
        self.last_request = time.monotonic()
        cmd = req.get("cmd")
        if cmd == "status":
            return self._status()
        if cmd == "apply":
            # Everything the window's Apply button sends: the look, and
            # optionally a new picture ({"name", "data"}) or "default".
            config = load_config()
            picture = req.get("picture")
            what = "settings applied"
            if isinstance(picture, dict):
                store_picture(config, base64.b64decode(picture.get("data") or ""),
                              picture.get("name"))
                what = f"picture set: {config['picture_label']}"
            elif picture == "default":
                config["picture"] = config["picture_label"] = None
                what = "back to the default picture"
            config.update(clean_look(req.get("look")))
            return self._saved(config, what)
        if cmd == "save_layout":
            name = save_layout(req.get("name"))
            log.info("layout saved: %s", name)
            return self._status()
        if cmd == "load_layout":
            config = load_layout(req.get("name"))
            return self._saved(config, f"layout loaded: {req.get('name')}")
        if cmd == "delete_layout":
            delete_layout(req.get("name"))
            log.info("layout deleted: %s", req.get("name"))
            return self._status()
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
        if self.display:
            state, detail, temps = self.display.state, self.display.detail, self.display.temps
        else:
            state, detail, temps = "idle", "", ""
        return {"ok": True, "state": state, "detail": detail, "temps": temps,
                "autostart": start_type()}


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
