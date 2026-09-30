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
"""What the service and the tray app share: names, paths and the pipe.

The tray app talks to the service over a local named pipe. Each exchange is
one request and one reply, each a single pipe message holding a JSON object:

    request   {"cmd": "status"}
    reply     {"ok": true, ...}   or   {"ok": false, "error": "..."}

The pipe only accepts local clients, and only SYSTEM, administrators and the
signed-in (interactive) user may open it.
"""

import json
import os
import sys
import time

import pywintypes
import win32file
import win32pipe

# The one place the version lives. The installer, the programs' file
# properties and the About box all read it; the release build checks that
# the git tag (v1.0.0) matches.
VERSION = "1.1.0"

SERVICE_NAME = "CrystalXLCD"
DISPLAY_NAME = "CrystalX LCD"
PIPE_NAME = r"\\.\pipe\CrystalXLCD"

# Written only by the service (SYSTEM); readable by everyone for the log.
DATA_DIR = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                        "CrystalX LCD")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
LOG_FILE = os.path.join(DATA_DIR, "service.log")

# Limits on a picture sent by the tray. Every GIF frame is held in memory at
# panel size (about 1.4 MB each), so the frame cap keeps a long GIF from
# eating gigabytes.
MAX_PICTURE_BYTES = 50 * 2**20
MAX_FRAMES = 300

_CHUNK = 64 * 1024
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PIPE_BUSY = 231
_ERROR_MORE_DATA = 234


DEFAULT_PICTURE = "retro_pixel_guy_smoking_on_rooftop.gif"


def app_dir():
    """Where the program's own files (default GIF, icon) live: the project
    folder when run from source, the bundle's data folder once packaged."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def read_message(handle):
    """One whole message from a message-mode pipe, however long."""
    chunks = []
    while True:
        hr, data = win32file.ReadFile(handle, _CHUNK)
        chunks.append(data)
        if hr != _ERROR_MORE_DATA:
            return b"".join(chunks)


def _connect(timeout_ms):
    """Open the pipe, waiting up to `timeout_ms` for the service to offer it.

    WaitNamedPipe gives up at once when no instance exists yet -- as while the
    service is still starting -- and another client can take the instance
    between the wait and the open, so both are retried until the deadline.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        try:
            win32pipe.WaitNamedPipe(PIPE_NAME, max(1, int(timeout_ms)))
            return win32file.CreateFile(
                PIPE_NAME, win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None, win32file.OPEN_EXISTING, 0, None)
        except pywintypes.error as e:
            if (e.winerror not in (_ERROR_FILE_NOT_FOUND, _ERROR_PIPE_BUSY)
                    or time.monotonic() >= deadline):
                raise ConnectionError(
                    f"the service is not answering ({e.strerror})") from e
            time.sleep(0.1)


def request(cmd, timeout_ms=3000, **fields):
    """Send one command to the service and return its reply as a dict.

    Raises ConnectionError when the service is not running or not answering.
    """
    handle = _connect(timeout_ms)
    try:
        win32pipe.SetNamedPipeHandleState(
            handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        win32file.WriteFile(handle, json.dumps({"cmd": cmd, **fields}).encode())
        return json.loads(read_message(handle))
    except pywintypes.error as e:
        raise ConnectionError(f"lost the connection to the service ({e.strerror})") from e
    finally:
        win32file.CloseHandle(handle)
