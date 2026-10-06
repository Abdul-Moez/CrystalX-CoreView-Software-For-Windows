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
"""The CrystalX LCD app: one window, plus an icon in the notification area.

Runs as the signed-in user, without admin rights, and controls the service
(service_win.py). Everything lives in the main window: status and start/stop,
then tabs for the picture (a playlist of pictures, GIFs and videos, each
filled or fitted, dragged and zoomed), the clock and date, the stats, the
extras (weather, text, countdown, to-do list, calendar), the style (brightness, font,
frosted panel) and options (start with Windows, saved layouts and rotating
them). The preview beside them is drawn by the engine itself. The tray icon
only has Show and Quit; clicking it opens the window, and closing the window
hides it back to the tray.

A video is converted when it is applied (video_win), by a helper process: the
same program started with "--video-worker". The window never loads FFmpeg.

  Start / Stop / Quit   start or stop the service through Windows' service
                        manager. Stopped, the screen is free for other apps.
                        Quit also closes the app.
  settings              go to the service over the pipe (ipc_win.py). When
                        the display is off, the service is started with
                        "--idle" just long enough to take them.

  CrystalXLCD.exe            open the window (from the Start menu)
  CrystalXLCD.exe --hidden   start in the tray only (at login)
"""

import calendar
import copy
import ctypes
import datetime
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from io import BytesIO
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

import pywintypes
import win32api
import win32con
import win32event
import win32gui
import win32service
import winerror
from PIL import Image, ImageDraw, ImageFont, ImageTk

import clock_win
import ipc_win
import video_win
import weather_win
from clock_win import MAX_SPOTS, SLOTS, countdown_text, date_strftime
from ipc_win import (DISPLAY_NAME, LOG_FILE, MAX_FRAMES, MAX_PICTURE_BYTES,
                     SERVICE_NAME, VERSION, app_dir)
from lcd_win import FIT_SIZE, MAX_ZOOM, fit_geometry
from settings_win import (DEFAULT_FILE, DEFAULT_FONT, DEFAULT_LOOK, DEFAULT_ROTATION,
                          EXTRA_BLOCKS, FONT_TYPES, FONTS_DIR, FRAMING,
                          MAX_COUNTDOWN_LABEL, MAX_DATE_PATTERN, MAX_ITEMS, MAX_LABEL,
                          MAX_LAYOUT_NAME, MAX_LAYOUTS, MAX_NOTE, MAX_NOTE_LINES,
                          MAX_TODO_ITEMS, MAX_TODO_TEXT, PLAYS, ROTATION_MINUTES,
                          STILL_SECONDS, clean_look, engine_argv, item_path,
                          list_layouts, load_config, new_item)

AUTHOR = "Abdul Moez"
GITHUB = "https://github.com/Abdul-Moez/CrystalX-CoreView-Software-For-Windows"
ICON = os.path.join(app_dir(), "assets", "crystalx-lcd.ico")
PICTURE_FORMATS = ("GIF", "PNG", "JPEG", "WEBP", "BMP")
_PICTURES = "*.gif *.png *.jpg *.jpeg *.webp *.bmp"
_VIDEOS = "*.mp4 *.m4v *.mov *.mkv *.webm *.avi *.wmv *.flv *.mpg *.mpeg *.ts *.3gp"
FILE_TYPES = [("Pictures and videos", f"{_PICTURES} {_VIDEOS}"), ("Pictures", _PICTURES),
              ("Videos", _VIDEOS), ("All files", "*.*")]
POLL_SECONDS = 3
PREVIEW_SCALE = 0.43            # with the play controls under it, the window's old height
# The preview scales bigger photos down to this before placing them: plenty
# for a preview under half the panel's size, even zoomed in.
PREVIEW_SOURCE_LIMIT = 2048
# While the preview plays a video it is drawn from pictures this size, ten
# times a second; paused, from what the conversion itself will use.
PLAY_SOURCE_LIMIT = 960
PLAY_INTERVAL_MS = 100
# What a playlist item carries in the window only, until it is applied.
WINDOW_KEYS = ("path", "pending", "source_fps", "hdr")
WRAP = 300                      # width of wrapped text on the right, in px
PREVIEW_HINT = "Drag to move - scroll to zoom"
OVERLAP_HINT = "Some blocks overlap"

TIME_CHOICES = [("12h-zero", "12-hour  (03:25 PM)"),
                ("12h", "12-hour  (3:25 PM)"),
                ("12h-plain", "12-hour, no AM/PM  (3:25)"),
                ("24h", "24-hour  (15:25)")]
# Date patterns offered ready-made (codes: clock_win.DATE_CODES). The first
# two differ only in the day's leading zero (see date_examples).
DATE_PRESETS = ["(ddd) DD-MMM-YYYY", "(ddd) D-MMM-YYYY", "YYYY-MMM-DD (ddd)",
                "ddd D MMM YYYY", "ddd, MMM D YYYY", "dddd, D MMMM", "D MMMM YYYY",
                "ddd D MMM", "DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD"]
CUSTOM_DATE = "Custom pattern..."
# The stats as the dropdowns name them: clearer than their short on-screen
# labels ("RAM" and "RAM Usage" are GB and %).
STAT_CHOICES = [
    (None, "Empty"),
    ("cpu", "CPU usage"), ("cputemp", "CPU temperature"), ("clock", "CPU clock speed"),
    ("gpu", "GPU usage"), ("gputemp", "GPU temperature"), ("vram", "GPU memory used"),
    ("ram%", "RAM usage (%)"), ("ram", "RAM used (GB)"),
    ("net", "Network down/up"), ("netdown", "Network download"),
    ("netup", "Network upload"),
    ("disk%", "Disk usage (busy)"), ("diskio", "Disk read/write"),
    ("storage", "Storage used"),
    ("disk", f"Free space ({os.environ.get('SystemDrive', 'C:')})"),
    ("procs", "Processes"), ("uptime", "Uptime"),
]
STAT_KEYS = [key for key, _ in STAT_CHOICES]
COUNTDOWN_CHOICES = [("to", "Days to a date"), ("to-hours", "Days and hours to a date"),
                     ("since", "Days since a date")]
# What the free-space readout calls each block.
BLOCK_NAMES = {"clock": "clock", "stats": "stats", "weather": "weather", "note": "text",
               "countdown": "countdown", "todo": "to-do list"}
BOX, TICKED = "\u2610", "\u2611"            # the to-do list's boxes in the window
# What the user wrote or chose, which Reset to defaults keeps: the text,
# countdown and to-do list, the weather's city and unit, and the playlist of
# pictures.
CONTENT_KEYS = ("note_text", "countdown_mode", "countdown_date", "countdown_time",
                "countdown_label", "todo_title", "todo_items",
                "weather_place", "weather_unit",
                "playlist", "playlist_shuffle")
MAX_CITY = 60                               # characters in a city being looked for

ID_SHOW, ID_QUIT = 1001, 1002
WM_TRAY = win32con.WM_USER + 20
TRAY_CLASS = "CrystalXLCDTray"
# Sent by a second launch (Start menu) to the running app: open the window.
WM_SHOW_APP = win32gui.RegisterWindowMessage("CrystalXLCD.Show")

GREEN, AMBER, GREY, BLUE, RED = "#1a7f37", "#9a6700", "#6e7781", "#0969da", "#cf222e"


# -- the service, through Windows' service manager ----------------------------

def _with_service(access, action):
    """Open the service with only the rights `action` needs, and run it.

    Ask for no more than that: pywin32's win32serviceutil helpers open the
    service manager itself with full access, which only administrators have,
    so they fail for a normal user even where the service allows the action.
    """
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        svc = win32service.OpenService(scm, SERVICE_NAME, access)
        try:
            return action(svc)
        finally:
            win32service.CloseServiceHandle(svc)
    finally:
        win32service.CloseServiceHandle(scm)


def service_state():
    """The service's state (win32service.SERVICE_*), or None if not installed."""
    try:
        return _with_service(win32service.SERVICE_QUERY_STATUS,
                             lambda svc: win32service.QueryServiceStatus(svc)[1])
    except pywintypes.error as e:
        if e.winerror == winerror.ERROR_SERVICE_DOES_NOT_EXIST:
            return None
        raise


def service_autostart():
    return _with_service(
        win32service.SERVICE_QUERY_CONFIG,
        lambda svc: win32service.QueryServiceConfig(svc)[1] == win32service.SERVICE_AUTO_START)


def start_service(*args):
    try:
        _with_service(win32service.SERVICE_START,
                      lambda svc: win32service.StartService(svc, list(args) or None))
    except pywintypes.error as e:
        if e.winerror != winerror.ERROR_SERVICE_ALREADY_RUNNING:
            raise


def stop_service():
    try:
        _with_service(win32service.SERVICE_STOP,
                      lambda svc: win32service.ControlService(
                          svc, win32service.SERVICE_CONTROL_STOP))
    except pywintypes.error as e:
        if e.winerror != winerror.ERROR_SERVICE_NOT_ACTIVE:
            raise


def wait_while(state, seconds=15):
    for _ in range(int(seconds * 10)):
        if service_state() != state:
            return
        win32api.Sleep(100)


def send_settings(cmd, **fields):
    """Send a settings command, starting the service idle first if it is off."""
    state = service_state()
    if state == win32service.SERVICE_STOP_PENDING:
        wait_while(win32service.SERVICE_STOP_PENDING)
        state = service_state()
    if state == win32service.SERVICE_STOPPED:
        start_service("--idle")
    reply = ipc_win.request(cmd, timeout_ms=15000, **fields)
    if not reply.get("ok"):
        raise RuntimeError(reply.get("error", "the service refused"))
    return reply


def layout_names():
    return [name for _, name in list_layouts()]


def read_status(settings=True):
    """The service's state, and with `settings` its settings and layouts too.

    The settings come from the service's files, so they are known even while
    the display is stopped. While the window is hidden only the tray tooltip
    needs updating, so the files are left alone.
    """
    state = service_state()
    if state is None:
        return {"installed": False}
    status = {"installed": True, "scm": state}
    if settings:
        status.update(autostart=service_autostart(), **load_config(),
                      layouts=layout_names())
    if state == win32service.SERVICE_RUNNING:
        try:
            live = ipc_win.request("status", timeout_ms=2000)
            status.update(state=live.get("state"), detail=live.get("detail"),
                          temps=live.get("temps"), playing=live.get("playing"))
        except ConnectionError:
            status["state"] = "starting"
    return status


def is_showing(status):
    return (status.get("scm") in (win32service.SERVICE_RUNNING,
                                  win32service.SERVICE_START_PENDING)
            and status.get("state") != "idle")


def describe(status):
    """(text, colour) for the status line and the tray tooltip."""
    if not status.get("installed"):
        return "The CrystalX LCD service is not installed.", RED
    scm = status.get("scm")
    if scm == win32service.SERVICE_START_PENDING:
        return "Starting...", BLUE
    if scm == win32service.SERVICE_STOP_PENDING:
        return "Stopping...", BLUE
    if not is_showing(status):
        return "Stopped - the screen is free for other apps", GREY
    state = status.get("state")
    if state == "showing":
        playing = status.get("playing") or {}
        if playing.get("count", 1) > 1:     # a playlist: say where it is
            return (f"Showing on {status.get('detail')} - item "
                    f"{playing.get('position')} of {playing['count']}"), GREEN
        return f"Showing on {status.get('detail')}", GREEN
    if state == "waiting":
        return f"Waiting: {status.get('detail')}", AMBER
    return "Starting...", BLUE


def temps_problem(status):
    """A short note when a temperature can't be read, else None."""
    temps = status.get("temps") or ""
    if "unavailable" in temps:
        return "Temperatures: " + temps.split(":", 1)[-1].strip()
    if "CPU --" in temps:
        return "CPU temperature: " + temps.split("CPU --", 1)[1].split(")")[0].strip(" (")
    return None


def check_media_file(path, probe):
    """What the file at `path` is, after the checks the service would make, so
    a problem shows up before anything is sent: ("picture", info) for a
    picture or GIF, ("video", info) for a video (info as video_win.probe).
    `probe(path)` looks at a video; the window's asks its helper process, so
    that FFmpeg is never loaded into the window itself.
    Raises ValueError with a message for the person."""
    try:
        with Image.open(path) as img:
            kind, frames, size = img.format, getattr(img, "n_frames", 1), img.size
    except Exception:
        kind = None
    if kind is not None:
        file_size = os.path.getsize(path)
        if file_size > MAX_PICTURE_BYTES:
            raise ValueError(f"The file is {file_size / 2**20:.0f} MB; the limit for "
                             f"a picture is {MAX_PICTURE_BYTES // 2**20} MB.")
        if kind not in PICTURE_FORMATS:
            raise ValueError(f"{kind} pictures are not supported. Use GIF, PNG, "
                             "JPEG, WEBP or BMP.")
        if frames > MAX_FRAMES:
            raise ValueError(f"The GIF has {frames} frames; the limit is {MAX_FRAMES}.")
        return "picture", {"width": size[0], "height": size[1], "frames": frames}
    try:
        return "video", probe(path)
    except video_win.NotAVideo:
        raise ValueError("That file is not a picture or video this app can show.") from None
    except video_win.VideoError as e:
        raise ValueError(str(e)) from None


# -- the notification-area icon (its own thread and message loop) -------------

class TrayIcon:
    def __init__(self, app):
        self.app = app
        self.hwnd = None
        self.hicon = None
        self.tip = DISPLAY_NAME
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name="tray-icon", daemon=True)

    def start(self):
        self.thread.start()
        self.ready.wait(5)

    def _run(self):
        hinst = win32api.GetModuleHandle(None)
        # Explorer broadcasts this after it restarts; the icon must be re-added.
        taskbar_created = win32gui.RegisterWindowMessage("TaskbarCreated")
        wc = win32gui.WNDCLASS()
        wc.hInstance = hinst
        wc.lpszClassName = TRAY_CLASS
        wc.lpfnWndProc = {WM_TRAY: self._on_tray,
                          WM_SHOW_APP: self._on_show_app,
                          win32con.WM_COMMAND: self._on_command,
                          win32con.WM_DESTROY: self._on_destroy,
                          taskbar_created: self._on_taskbar_created}
        atom = win32gui.RegisterClass(wc)
        self.hwnd = win32gui.CreateWindow(atom, DISPLAY_NAME, win32con.WS_OVERLAPPED,
                                          0, 0, 0, 0, 0, 0, hinst, None)
        try:
            self.hicon = win32gui.LoadImage(
                hinst, ICON, win32con.IMAGE_ICON, 0, 0,
                win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
        except pywintypes.error:
            self.hicon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
        self._notify(win32gui.NIM_ADD)
        self.ready.set()
        win32gui.PumpMessages()

    def _notify(self, action):
        win32gui.Shell_NotifyIcon(action, (
            self.hwnd, 0,
            win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
            WM_TRAY, self.hicon, self.tip[:127]))

    def set_tip(self, text):
        if text != self.tip and self.hwnd:
            self.tip = text
            try:
                self._notify(win32gui.NIM_MODIFY)
            except pywintypes.error:
                pass

    def close(self):
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)

    def _on_tray(self, hwnd, msg, wparam, lparam):
        if lparam == win32con.WM_LBUTTONUP:
            self.app.post(ID_SHOW)
        elif lparam == win32con.WM_RBUTTONUP:
            self._show_menu()
        return True

    def _show_menu(self):
        menu = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_SHOW, f"Show {DISPLAY_NAME}")
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_QUIT, "Quit")
        win32gui.SetMenuDefaultItem(menu, ID_SHOW, False)
        x, y = win32gui.GetCursorPos()
        # Without these two calls the menu does not close when you click away.
        win32gui.SetForegroundWindow(self.hwnd)
        win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON
                                | win32con.TPM_BOTTOMALIGN, x, y, 0, self.hwnd, None)
        win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)
        win32gui.DestroyMenu(menu)

    def _on_show_app(self, hwnd, msg, wparam, lparam):
        self.app.post(ID_SHOW)
        return True

    def _on_command(self, hwnd, msg, wparam, lparam):
        self.app.post(win32api.LOWORD(wparam))
        return True

    def _on_taskbar_created(self, hwnd, msg, wparam, lparam):
        self._notify(win32gui.NIM_ADD)
        return True

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self.hwnd, 0))
        except pywintypes.error:
            pass
        win32gui.PostQuitMessage(0)
        return True


# -- small widgets ----------------------------------------------------------------

class Swatch(tk.Button):
    """A colour button: shows the colour, click to pick another."""

    def __init__(self, parent, title, on_change):
        super().__init__(parent, width=3, relief="solid", bd=1, command=self._pick)
        self.title, self.on_change, self.color = title, on_change, "#ffffff"

    def set(self, color):
        self.color = color
        self.configure(bg=color, activebackground=color)

    def _pick(self):
        _, hex_color = colorchooser.askcolor(color=self.color, parent=self,
                                             title=self.title)
        if hex_color:
            self.set(hex_color.lower())
            self.on_change(self.color)


class Slider(ttk.Frame):
    """A slider with its value written beside it."""

    def __init__(self, parent, low, high, show, on_change, length=150):
        super().__init__(parent)
        self.show, self.on_change = show, on_change
        self.var = tk.DoubleVar(value=low)
        self.scale = ttk.Scale(self, from_=low, to=high, variable=self.var,
                               command=self._moved, length=length)
        self.scale.pack(side="left")
        self.text = ttk.Label(self, width=10)
        self.text.pack(side="left", padx=(6, 0))
        self.note = ""

    def _moved(self, _value):
        self.note = ""
        self._write()
        self.on_change(self.var.get())

    def _write(self):
        self.text.configure(text=self.show(self.var.get()) + self.note)

    def set(self, value, note=""):
        """Move it without calling on_change."""
        self.var.set(value)
        self.note = note
        self._write()

    def set_range(self, low, high):
        self.scale.configure(from_=low, to=high)

    def enable(self, on):
        self.scale.state(["!disabled"] if on else ["disabled"])


class TimeField(ttk.Frame):
    """A time of day picked from lists: the hour, the minute and, on a
    12-hour clock, AM or PM. Its value is HH:MM on the 24-hour clock."""

    def __init__(self, parent, on_change):
        super().__init__(parent)
        self.on_change, self.twelve = on_change, None
        self.hour = ttk.Combobox(self, state="readonly", width=3)
        self.minute = ttk.Combobox(self, state="readonly", width=3,
                                   values=[f"{m:02d}" for m in range(60)])
        self.half = ttk.Combobox(self, state="readonly", width=4, values=("AM", "PM"))
        self.boxes = [self.hour, self.minute, self.half]
        self.hour.pack(side="left")
        ttk.Label(self, text=":").pack(side="left", padx=3)
        self.minute.pack(side="left")
        for box in self.boxes:
            box.bind("<<ComboboxSelected>>", lambda e: self.on_change(self.get()))

    def set(self, value, twelve):
        """Show HH:MM (nothing is midnight) on a 12-hour or a 24-hour clock,
        without calling on_change."""
        when = datetime.datetime.strptime(value or "00:00", "%H:%M")
        if twelve != self.twelve:
            self.twelve = twelve
            self.hour.configure(values=[f"{h:02d}" for h in
                                        (range(1, 13) if twelve else range(24))])
            if twelve:
                self.half.pack(side="left", padx=(6, 0))
            else:
                self.half.pack_forget()
        self.hour.set(when.strftime("%I" if twelve else "%H"))
        self.minute.set(when.strftime("%M"))
        self.half.current(when.hour // 12)

    def get(self):
        hour = int(self.hour.get())
        if self.twelve:
            hour = hour % 12 + 12 * self.half.current()
        return f"{hour:02d}:{self.minute.get()}"


def _px(value):
    return f"{round(value)} px"


def _height(value):
    return "top" if value <= 0.005 else "bottom" if value >= 0.995 else f"{value * 100:.0f}%"


def _enable(widgets, on):
    for widget in widgets:
        if isinstance(widget, Slider):
            widget.enable(on)
        elif isinstance(widget, (tk.Button, tk.Text)):
            widget.configure(state="normal" if on else "disabled")
        elif isinstance(widget, ttk.Combobox):
            widget.state(["!disabled", "readonly"] if on else ["disabled"])
        else:
            widget.state(["!disabled"] if on else ["disabled"])


class FontCatalog:
    """The fonts in the Windows font folder and their names.

    Reading all of them takes about a second, so it happens in the background
    and only once someone opens the Style tab -- not at every login.
    """

    def __init__(self):
        self.fonts = []                     # [(name, file)], sorted by name
        self.names = {}                     # file -> name
        self.ready = threading.Event()
        self._started = False

    def start(self):
        if not self._started:
            self._started = True
            threading.Thread(target=self._scan, name="fonts", daemon=True).start()

    def _scan(self):
        found = {}
        try:
            files = sorted(os.listdir(FONTS_DIR))
        except OSError:
            files = []
        for file in files:
            if not file.lower().endswith(FONT_TYPES):
                continue
            try:
                family, style = ImageFont.truetype(os.path.join(FONTS_DIR, file), 12).getname()
            except Exception:
                continue            # not a font Pillow can draw with
            name = family if style in (None, "Regular", "Normal") else f"{family} {style}"
            found.setdefault(name, file)
        self.fonts = sorted(found.items(), key=lambda item: item[0].lower())
        self.names.update({file: name for name, file in self.fonts})
        self.ready.set()

    def name(self, file):
        """A font's name, read from its file if the list isn't ready."""
        if file not in self.names:
            try:
                family, style = ImageFont.truetype(os.path.join(FONTS_DIR, file), 12).getname()
                self.names[file] = (family if style in (None, "Regular", "Normal")
                                    else f"{family} {style}")
            except Exception:
                return file
        return self.names[file]


def font_sample(file, text, size, width, height):
    """`text` drawn in a font file, as an image for the window."""
    img = Image.new("RGB", (width, height), "white")
    try:
        font = ImageFont.truetype(os.path.join(FONTS_DIR, file), size)
        ImageDraw.Draw(img).text((4, height // 2), text, font=font, fill="black", anchor="lm")
    except Exception:
        ImageDraw.Draw(img).text((4, height // 2), "(cannot draw this font)", fill="grey", anchor="lm")
    return img


class FontPicker:
    """A list of every font, each name written in its own font."""

    ROW = 38

    def __init__(self, parent, catalog, current):
        self.catalog, self.result = catalog, None
        self.top = tk.Toplevel(parent)
        self.top.title("Choose a font")
        self.top.transient(parent)
        self.top.resizable(False, True)
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)
        self.search = tk.StringVar()
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, text="Search").pack(side="left")
        entry = ttk.Entry(row, textvariable=self.search)
        entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.search.trace_add("write", lambda *_: self._fill())
        box = ttk.Frame(frame)
        box.pack(fill="both", expand=True, pady=(10, 0))
        self.canvas = tk.Canvas(box, width=380, height=420, bg="white",
                                highlightthickness=1, highlightbackground="#bbb")
        bar = ttk.Scrollbar(box, orient="vertical", command=self._scroll)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="left", fill="y")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.top.destroy).pack(side="right")
        self.ok = ttk.Button(buttons, text="OK", command=self._done)
        self.ok.pack(side="right", padx=6)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Double-Button-1>", lambda e: (self._click(e), self._done()))
        self.canvas.bind("<MouseWheel>", lambda e: self._scroll("scroll", -e.delta // 120, "units"))
        self.canvas.bind("<Configure>", lambda e: self._draw_visible())
        self.images, self.selected, self.shown = {}, current, []
        self._fill()
        entry.focus_set()
        self._reveal(current)
        self.top.wait_visibility()
        self.top.grab_set()                 # modal: the main window waits

    def _fill(self):
        words = self.search.get().lower().split()
        self.shown = [(name, file) for name, file in self.catalog.fonts
                      if all(w in name.lower() or w in file.lower() for w in words)]
        self.canvas.delete("all")
        self.canvas.configure(scrollregion=(0, 0, 380, max(1, len(self.shown)) * self.ROW))
        self.canvas.yview_moveto(0)
        self._draw_visible()

    def _scroll(self, *args):
        self.canvas.yview(*args)
        self._draw_visible()

    def _draw_visible(self):
        """Draw only the rows in view: 340 font samples at once is slow."""
        top = int(self.canvas.canvasy(0)) // self.ROW
        bottom = int(self.canvas.canvasy(self.canvas.winfo_height())) // self.ROW + 1
        for i in range(max(0, top), min(len(self.shown), bottom + 1)):
            name, file = self.shown[i]
            tag = f"row{i}"
            if self.canvas.find_withtag(tag):
                continue
            if file not in self.images:
                self.images[file] = ImageTk.PhotoImage(font_sample(file, name, 20, 370, 26))
            y = i * self.ROW
            fill = "#cce4ff" if file == self.selected else "white"
            self.canvas.create_rectangle(0, y, 380, y + self.ROW, fill=fill, outline="",
                                         tags=(tag, "bg", f"bg{i}"))
            self.canvas.create_image(4, y + 2, image=self.images[file], anchor="nw", tags=tag)
            self.canvas.create_text(8, y + self.ROW - 2, text=name, anchor="sw",
                                    fill="#6e7781", font=("Segoe UI", 8), tags=tag)

    def _reveal(self, file):
        for i, (_, f) in enumerate(self.shown):
            if f == file:
                self.canvas.yview_moveto(max(0, i - 4) / max(1, len(self.shown)))
                self._draw_visible()
                return

    def _click(self, event):
        i = int(self.canvas.canvasy(event.y)) // self.ROW
        if 0 <= i < len(self.shown):
            self.selected = self.shown[i][1]
            self.canvas.itemconfigure("bg", fill="white")
            self.canvas.itemconfigure(f"bg{i}", fill="#cce4ff")

    def _done(self):
        self.result = self.selected
        self.top.destroy()


class DatePicker:
    """A month of days to click, for choosing a date. `result` is the date
    chosen, or None if the window was closed without choosing one."""

    YEARS = (1900, 2199)
    PICKED, HOVER = "#cce4ff", "#e5f1fb"

    def __init__(self, parent, current, week_start, under):
        self.result, self.current = None, current
        self.today = datetime.date.today()
        self.weeks = calendar.Calendar(0 if week_start == "mon" else 6)
        self.view = None                    # (year, month) on show
        self.top = tk.Toplevel(parent)
        self.top.withdraw()                 # until it is in its place
        self.top.title("Choose a date")
        self.top.transient(parent)
        self.top.resizable(False, False)
        frame = ttk.Frame(self.top, padding=12)
        frame.pack()
        head = ttk.Frame(frame)
        head.pack()
        ttk.Button(head, text="\u2039", width=3, command=lambda: self._step(-1)).pack(side="left")
        self.month = ttk.Combobox(head, state="readonly", width=11,
                                  values=list(calendar.month_name)[1:])
        self.month.pack(side="left", padx=(6, 4))
        self.month.bind("<<ComboboxSelected>>", lambda e: self._fill())
        self.year = tk.StringVar()
        ttk.Spinbox(head, width=5, from_=self.YEARS[0], to=self.YEARS[1],
                    textvariable=self.year).pack(side="left", padx=(0, 6))
        self.year.trace_add("write", lambda *_: self._fill())
        ttk.Button(head, text="\u203a", width=3, command=lambda: self._step(1)).pack(side="left")
        self.plain = tkfont.nametofont("TkDefaultFont")
        self.bold = self.plain.copy()
        self.bold.configure(weight="bold")
        days = tk.Frame(frame, bg="white", highlightthickness=1, highlightbackground="#bbb")
        days.pack(pady=(10, 0))
        for column, weekday in enumerate(self.weeks.iterweekdays()):
            tk.Label(days, text=calendar.day_abbr[weekday][:2], bg="white", fg=GREY,
                     width=4).grid(row=0, column=column, pady=(4, 0))
        self.cells = []
        for i in range(42):                 # six weeks: the most a month spans
            cell = tk.Label(days, width=4, pady=4, bg="white")
            cell.grid(row=1 + i // 7, column=i % 7)
            cell.bind("<Button-1>", lambda e, i=i: self._pick(i))
            cell.bind("<Enter>", lambda e, i=i: self._paint(i, hover=True))
            cell.bind("<Leave>", lambda e, i=i: self._paint(i))
            self.cells.append(cell)
        for widget in (days, *days.winfo_children()):
            widget.bind("<MouseWheel>", lambda e: self._step(-1 if e.delta > 0 else 1))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Today", command=lambda: self._show(self.today)).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=self.top.destroy).pack(side="right")
        self.top.bind("<Escape>", lambda e: self.top.destroy())
        self._show(current or self.today)
        # Under the button that opened it, or over it where the screen ends.
        self.top.update_idletasks()
        x, y = under.winfo_rootx(), under.winfo_rooty() + under.winfo_height() + 2
        width = self.top.winfo_reqwidth()
        height = self.top.winfo_reqheight() + 40            # with its title bar
        work = win32api.GetMonitorInfo(win32api.MonitorFromPoint(
            (x, y), win32con.MONITOR_DEFAULTTONEAREST))["Work"]
        if y + height > work[3]:
            y = under.winfo_rooty() - height - 2
        self.top.geometry(f"+{min(x, work[2] - width - 16)}+{y}")
        self.top.deiconify()
        self.top.wait_visibility()
        self.top.grab_set()                 # modal: the main window waits

    def _show(self, date):
        """Turn to the month `date` is in."""
        self.month.current(date.month - 1)
        # Writing the year fills the days in.
        self.year.set(str(min(max(date.year, self.YEARS[0]), self.YEARS[1])))

    def _step(self, months):
        count = self.view[0] * 12 + self.view[1] - 1 + months
        if self.YEARS[0] * 12 <= count < (self.YEARS[1] + 1) * 12:
            self._show(datetime.date(count // 12, count % 12 + 1, 1))

    def _fill(self):
        """Write the days of the month and year picked above into the cells.
        A year half typed is not one yet: the days stay as they are."""
        try:
            year = int(self.year.get())
        except ValueError:
            return
        if not self.YEARS[0] <= year <= self.YEARS[1]:
            return
        self.view = year, self.month.current() + 1
        days = [day for week in self.weeks.monthdayscalendar(*self.view) for day in week]
        self.days = [datetime.date(*self.view, day) if day else None
                     for day in days + [0] * (42 - len(days))]
        for i in range(42):
            self._paint(i)

    def _paint(self, i, hover=False):
        day = self.days[i]
        today = day == self.today
        self.cells[i].configure(
            text=day.day if day else "",
            bg=self.PICKED if day and day == self.current
            else self.HOVER if day and hover else "white",
            fg=BLUE if today else "black", font=self.bold if today else self.plain,
            cursor="hand2" if day else "")

    def _pick(self, i):
        if self.days[i]:
            self.result = self.days[i]
            self.top.destroy()


# -- the main window ------------------------------------------------------------

class MainWindow:
    """Everything in one place: status and start/stop above, the settings in
    tabs, the preview on the left.

    The form edits `self.look` (see settings_win); every change redraws the
    preview, and nothing reaches the screen until Apply.
    """

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.root.title(DISPLAY_NAME)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.look = clean_look({})
        # The playlist item being shown in the preview and edited on the
        # Picture tab. Besides its settings (settings_win.new_item), an item
        # that has not been applied yet carries, for the window only: "path",
        # the file that was picked, and "pending", "picture" or "video".
        self.sel = 0
        self._sources = {}                  # a converted clip -> its video (see _source_of)
        self._positions = {}                # item id -> where its preview is, 0 to 1
        self._media = {}                    # file -> what it is (see _media_info)
        self._frame = (None, None, None)    # (key, picture, original size): the preview's
        self._gif = None                    # (path, open image): a GIF being stepped through
        self._worker = None                 # the helper process that reads videos
        self._play_from = None              # (clock time, position) while the preview plays
        self._play_timer = None             # the pending call that moves it on
        self._cancel = None                # set to stop a conversion or upload under way
        self._progress = None               # the progress window, while one is open
        # `dirty` means the user changed something that has not been applied
        # yet, so status updates must not overwrite it.
        self.dirty = False
        self.busy = False
        self.installed = True
        self._loading = False               # pushing the look into the controls
        self._form_key = None
        self._layouts = None
        self._rendering = self._want_render = False
        self._layout = None                 # the last preview's clock_win.Layout
        self._raw_size = None               # the size of the picture the preview placed
        self._placed = False                # that picture was placed already (a clip's)
        self._drag = None
        self._custom_date = False           # "Custom pattern" chosen for the date
        self._controls_key = None           # what _update_controls last acted on
        self._saved_look = clean_look({})   # what the screen shows (for Undo)
        self._todo_shown = None             # the to-do items in the list box
        self._editing = None                # the id of the to-do item being edited
        self._places = []                   # what the last search for a city found
        self._searching = False             # such a search is under way
        self._place_note = None             # what it has to say (else the usual hint)
        self._status_key = None             # what refresh last showed
        self.visible = False
        self.fonts = FontCatalog()
        self.autostart = tk.BooleanVar(value=False)
        self.rotate = tk.BooleanVar(value=False)
        self.rotate_minutes = tk.StringVar(value=str(DEFAULT_ROTATION["minutes"]))
        self._rotation = dict(DEFAULT_ROTATION)     # what the service has
        self.status_text = tk.StringVar(value="Checking...")
        self.note_text = tk.StringVar(value="")
        self._build()

    # -- layout ------------------------------------------------------------

    def _build(self):
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Hint.TLabel", foreground=GREY)
        style.configure("Note.TLabel", foreground=AMBER)

        outer = ttk.Frame(self.root, padding=14)
        outer.grid(row=0, column=0, sticky="nsew")

        # Left: the preview, sized in pixels from the start.
        left = ttk.Frame(outer)
        left.grid(row=0, column=0, sticky="n", padx=(0, 16))
        self.pw, self.ph = round(FIT_SIZE[0] * PREVIEW_SCALE), round(FIT_SIZE[1] * PREVIEW_SCALE)
        self.k = self.pw / FIT_SIZE[0]
        self.canvas = tk.Canvas(left, width=self.pw, height=self.ph, bg="#111",
                                highlightthickness=0)
        self.canvas.pack()
        self.canvas_img = self.canvas.create_image(0, 0, anchor="nw")
        self.canvas_text = self.canvas.create_text(
            self.pw // 2, self.ph // 2, text="Rendering...", fill="#bbb",
            width=self.pw - 16, justify="center")
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<MouseWheel>", self._wheel)
        # A GIF or video can be looked at anywhere along its length, and
        # played. Nothing plays by itself: playing costs a good part of a
        # processor core, which the open window otherwise never does.
        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=(6, 0))
        self.play_btn = ttk.Button(bar, text="Play", width=6, command=self.toggle_play)
        self.play_btn.pack(side="left")
        self.seek_var = tk.DoubleVar(value=0.0)
        self.seek = ttk.Scale(bar, from_=0.0, to=1.0, variable=self.seek_var,
                              command=self._seeked)
        self.seek.pack(side="left", fill="x", expand=True, padx=(6, 0))
        line = ttk.Frame(left)
        line.pack(fill="x", pady=(6, 0))
        ttk.Label(line, text="Example readings", style="Hint.TLabel").pack(side="left")
        self.seek_text = ttk.Label(line, style="Hint.TLabel", anchor="e")
        self.seek_text.pack(side="right")
        # The hint line doubles as the overlap warning. It is wider than the
        # preview, so its box is sized for the longer of the two texts:
        # otherwise the whole window changed width each time it switched.
        font = tkfont.nametofont("TkDefaultFont")
        width = max(self.pw, *(font.measure(t) for t in (PREVIEW_HINT, OVERLAP_HINT))) + 4
        self.preview_hint = self._fixed_label(left, 1, width, text=PREVIEW_HINT,
                                              style="Hint.TLabel", anchor="center")

        right = ttk.Frame(outer)
        right.grid(row=0, column=1, sticky="nsew")
        outer.rowconfigure(0, weight=1)

        # Display
        box = ttk.LabelFrame(right, text=" Display ", padding=10)
        box.pack(fill="x")
        line = ttk.Frame(box)
        line.pack(fill="x")
        self.dot = tk.Label(line, text="●", font=("Segoe UI", 11), fg=GREY)
        self.dot.pack(side="left", anchor="n")
        ttk.Label(line, textvariable=self.status_text, wraplength=WRAP,
                  justify="left").pack(side="left", padx=(4, 0))
        self.note = ttk.Label(box, textvariable=self.note_text, style="Note.TLabel",
                              wraplength=WRAP, justify="left")
        buttons = ttk.Frame(box)
        buttons.pack(fill="x", pady=(10, 0))
        self.start_btn = ttk.Button(buttons, text="Start display", command=self.start_display)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(buttons, text="Stop display", command=self.stop_display)
        self.stop_btn.pack(side="left", padx=6)
        self.buttons_row = buttons

        # The settings, in tabs.
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True, pady=(12, 0))
        # Read the font list once the Style tab is opened, ready for the picker.
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self.fonts.start()
                       if self.tabs.tab("current", "text").strip() == "Style" else None)
        self._build_picture_tab()
        self._build_clock_tab()
        self._build_stats_tab()
        self._build_extras_tab()
        self._build_style_tab()
        self._build_options_tab()

        row = ttk.Frame(right)
        row.pack(fill="x", pady=(10, 0))
        self.reset_btn = ttk.Button(row, text="Reset to defaults", command=self.reset)
        self.reset_btn.pack(side="left")
        self.apply_btn = ttk.Button(row, text="Apply", command=self.apply)
        self.apply_btn.pack(side="right")
        self.undo_btn = ttk.Button(row, text="Undo changes", command=self.undo)
        self.undo_btn.pack(side="right", padx=6)

        # Footer
        foot = ttk.Frame(right)
        foot.pack(fill="x", side="bottom", pady=(12, 0))
        ttk.Button(foot, text="Open log", command=self.open_log).pack(side="left")
        ttk.Button(foot, text="About", command=self.app.about).pack(side="left", padx=6)
        ttk.Label(foot, text=f"v{VERSION}", style="Hint.TLabel").pack(side="right")

        self._show_look()
        self._update_controls()

    def _tab(self, title):
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text=f" {title} ")
        return tab

    def _build_picture_tab(self):
        """The playlist -- pictures, GIFs and videos, shown one after the other
        -- and, under it, how the one picked in the list is placed and for how
        long it shows. With a single item, it is simply the picture."""
        tab = self._tab("Picture")
        top = ttk.Frame(tab)
        top.pack(fill="x")
        self.items_list = tk.Listbox(top, height=5, activestyle="none", exportselection=False)
        self.items_list.pack(side="left", fill="both", expand=True)
        self.items_list.bind("<<ListboxSelect>>", lambda e: self._item_picked())
        side = ttk.Frame(top)
        side.pack(side="left", padx=(6, 0))
        self.add_btn = ttk.Button(side, text="Add...", width=11, command=self.add_files)
        self.add_default_btn = ttk.Button(side, text="Add default", width=11,
                                          command=self.add_default)
        self.remove_btn = ttk.Button(side, text="Remove", width=11, command=self.remove_item)
        for button in (self.add_btn, self.add_default_btn, self.remove_btn):
            button.pack(pady=(0, 2))
        order = ttk.Frame(side)
        order.pack()
        self.up_btn = ttk.Button(order, text="Up", width=5, command=lambda: self.move_item(-1))
        self.up_btn.pack(side="left")
        self.down_btn = ttk.Button(order, text="Down", width=5, command=lambda: self.move_item(1))
        self.down_btn.pack(side="left")
        self.shuffle = tk.BooleanVar()
        self.shuffle_check = ttk.Checkbutton(
            tab, text="Shuffle the order", variable=self.shuffle,
            command=lambda: self._set("playlist_shuffle", self.shuffle.get()))
        self.shuffle_check.pack(anchor="w", pady=(4, 0))

        self.picture_info = self._fixed_label(tab, 2, WRAP, style="Hint.TLabel",
                                              wraplength=WRAP, justify="left")

        self.mode = tk.StringVar(value="fill")
        self.edge = tk.StringVar(value="blur")
        self.fill_radio = ttk.Radiobutton(
            tab, text="Fill - crop to cover the whole screen", variable=self.mode,
            value="fill", command=self._fit_changed)
        self.fill_radio.pack(anchor="w", pady=(6, 0))
        self.fit_radio = ttk.Radiobutton(
            tab, text="Fit - show the whole picture", variable=self.mode,
            value="fit", command=self._fit_changed)
        self.fit_radio.pack(anchor="w")
        sub = ttk.Frame(tab)
        sub.pack(anchor="w", padx=(22, 0))
        self.blur_radio = ttk.Radiobutton(sub, text="Blurred edges", variable=self.edge,
                                          value="blur", command=self._fit_changed)
        self.blur_radio.grid(row=0, column=0, sticky="w")
        self.color_radio = ttk.Radiobutton(sub, text="Solid colour", variable=self.edge,
                                           value="color", command=self._fit_changed)
        self.color_radio.grid(row=1, column=0, sticky="w")
        self.fit_swatch = Swatch(sub, "Edge colour", lambda c: self._set_item("fit_color", c))
        self.fit_swatch.grid(row=1, column=1, padx=8)

        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Zoom", width=6).pack(side="left")
        self.zoom = Slider(row, 1.0, MAX_ZOOM, lambda v: f"{v * 100:.0f}%",
                           lambda v: self._set_item("zoom", round(v, 3)), length=120)
        self.zoom.pack(side="left")
        self.recentre_btn = ttk.Button(row, text="Reset", width=6, command=self.recentre)
        self.recentre_btn.pack(side="left")

        # How long it shows before the next one: seconds for a still picture,
        # times through for a GIF or video.
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(8, 0))
        self.timing_lead = ttk.Label(row, text="Show it for")
        self.timing_lead.pack(side="left")
        self.timing_var = tk.StringVar()
        self.timing = ttk.Spinbox(row, width=5, from_=1, to=3600, textvariable=self.timing_var,
                                  command=self._timing_typed)
        self.timing.pack(side="left", padx=6)
        self.timing_var.trace_add("write", lambda *_: self._timing_typed())
        self.timing_unit = ttk.Label(row, text="seconds")
        self.timing_unit.pack(side="left")
        # One switch, for what the item is: a video's frame-rate limit, or a
        # GIF's own speed. A still picture has neither.
        self.item_flag = tk.BooleanVar()
        self.item_check = ttk.Checkbutton(tab, variable=self.item_flag,
                                          command=self._flag_changed)
        self.item_check.pack(anchor="w", pady=(6, 0))

        # For anyone making a picture to fit the gap between the text blocks.
        self.space_info = self._fixed_label(tab, 2, WRAP, style="Hint.TLabel",
                                            justify="left")
        self.picture_warn = self._fixed_label(tab, 2, WRAP, style="Note.TLabel",
                                              wraplength=WRAP, justify="left")

    def _build_clock_tab(self):
        tab = self._tab("Clock")
        self.show_time = tk.BooleanVar()
        self.show_date = tk.BooleanVar()
        self.seconds = tk.BooleanVar()

        frame = ttk.LabelFrame(tab, padding=(10, 4, 10, 8), labelwidget=ttk.Checkbutton(
            tab, text="Time", variable=self.show_time,
            command=lambda: self._set("show_time", self.show_time.get())))
        frame.pack(fill="x")
        self.time_format = ttk.Combobox(frame, state="readonly", width=28,
                                        values=[text for _, text in TIME_CHOICES])
        self.time_format.bind("<<ComboboxSelected>>", lambda e: self._set(
            "time_format", TIME_CHOICES[self.time_format.current()][0]))
        self.seconds_check = ttk.Checkbutton(
            frame, text="Seconds", variable=self.seconds,
            command=lambda: self._set("seconds", self.seconds.get()))
        self.time_size = Slider(frame, 8, 60, _px, lambda v: self._set("time_size", round(v)))
        self.time_swatch = Swatch(frame, "Time colour", lambda c: self._set("time_color", c))
        self._grid(frame, [("Format", self.time_format), ("", self.seconds_check),
                           ("Size", self.time_size), ("Colour", self.time_swatch)])
        self.time_widgets = [self.time_format, self.seconds_check, self.time_size, self.time_swatch]

        frame = ttk.LabelFrame(tab, padding=(10, 4, 10, 8), labelwidget=ttk.Checkbutton(
            tab, text="Date", variable=self.show_date,
            command=lambda: self._set("show_date", self.show_date.get())))
        frame.pack(fill="x", pady=(10, 0))
        # (Wide enough for date_examples' longest line.)
        self.date_format = ttk.Combobox(frame, state="readonly", width=28)
        self.date_format.bind("<<ComboboxSelected>>", lambda e: self._date_choice())
        self.date_pattern = tk.StringVar()
        self.date_entry = ttk.Entry(frame, textvariable=self.date_pattern, width=26,
                                    validate="key", validatecommand=(
                                        self.root.register(lambda p: len(p) <= MAX_DATE_PATTERN), "%P"))
        self.date_pattern.trace_add("write", lambda *_: self._date_typed())
        self.date_codes = ttk.Label(
            frame, style="Hint.TLabel", wraplength=WRAP - 70, justify="left",
            text="ddd Wed, dddd Wednesday, D 5, DD 05, MMM Sep, MMMM September, "
                 "MM 09, YYYY 2026, YY 26. [Text in brackets] stays as it is.")
        self.date_size = Slider(frame, 8, 60, _px, lambda v: self._set("date_size", round(v)))
        self.date_swatch = Swatch(frame, "Date colour", lambda c: self._set("date_color", c))
        self._grid(frame, [("Format", self.date_format), ("", self.date_entry),
                           ("", self.date_codes), ("Size", self.date_size),
                           ("Colour", self.date_swatch)])
        self.date_widgets = [self.date_format, self.date_entry, self.date_size, self.date_swatch]

        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(12, 0))
        ttk.Label(row, text="Height on screen").pack(anchor="w")
        self.clock_pos = Slider(row, 0.0, 1.0, _height, lambda v: self._set("clock_pos", round(v, 3)),
                                length=200)
        self.clock_pos.pack(anchor="w")

    def _build_stats_tab(self):
        tab = self._tab("Stats")
        self.show_stats = tk.BooleanVar(value=True)
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(0, 2))
        ttk.Checkbutton(row, text="Show the stats", variable=self.show_stats,
                        command=lambda: self._set("show_stats", self.show_stats.get())
                        ).pack(side="left")
        # (Beside the switch, where there is room: the tab is the tallest one.)
        self.warn_btn = ttk.Button(row, text="Warning colours...", command=self.edit_warnings)
        self.warn_btn.pack(side="right")
        row = ttk.Frame(tab)
        row.pack(fill="x")
        self.columns = tk.IntVar(value=2)
        self.column_radios = [
            ttk.Radiobutton(row, text="Two per row", variable=self.columns, value=2,
                            command=self._columns_changed),
            ttk.Radiobutton(row, text="One per row", variable=self.columns, value=1,
                            command=self._columns_changed)]
        self.column_radios[0].pack(side="left")
        self.column_radios[1].pack(side="left", padx=(12, 0))
        ttk.Label(tab, text="Pick what each spot shows, and the name it has on screen.",
                  style="Hint.TLabel", wraplength=WRAP).pack(anchor="w", pady=(6, 4))

        self.spot_frame = ttk.Frame(tab)
        self.spot_frame.pack(fill="x")
        limit = self.root.register(lambda p: len(p) <= MAX_LABEL)
        self.spots = []
        for i in range(MAX_SPOTS):
            cell = ttk.Frame(self.spot_frame, padding=(0, 0, 0, 6))
            combo = ttk.Combobox(cell, state="readonly", width=19,
                                 values=[text for _, text in STAT_CHOICES])
            combo.bind("<<ComboboxSelected>>", lambda e, i=i: self._spot_changed(i))
            name = tk.StringVar()
            entry = ttk.Entry(cell, textvariable=name, width=21, validate="key",
                              validatecommand=(limit, "%P"))
            name.trace_add("write", lambda *_, i=i: self._name_changed(i))
            self.spots.append((cell, combo, entry, name))
        self._place_spots()

        frame = ttk.LabelFrame(tab, text=" Text size and colour ", padding=(10, 4, 10, 8))
        frame.pack(fill="x", pady=(6, 0))
        self.label_size = Slider(frame, 8, 30, _px, lambda v: self._set("label_size", round(v)),
                                 length=120)
        self.label_swatch = Swatch(frame, "Name colour", lambda c: self._set("label_color", c))
        self.value_size = Slider(frame, 8, 60, _px, lambda v: self._set("value_size", round(v)),
                                 length=120)
        self.value_swatch = Swatch(frame, "Value colour", lambda c: self._set("value_color", c))
        for r, (text, slider, swatch) in enumerate((("Names", self.label_size, self.label_swatch),
                                                    ("Values", self.value_size, self.value_swatch))):
            ttk.Label(frame, text=text).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=2)
            slider.grid(row=r, column=1, sticky="w")
            swatch.grid(row=r, column=2, padx=(4, 0))

        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(10, 0))
        ttk.Label(row, text="Height on screen").pack(anchor="w")
        self.stats_pos = Slider(row, 0.0, 1.0, _height, lambda v: self._set("stats_pos", round(v, 3)),
                                length=200)
        self.stats_pos.pack(anchor="w")
        self.stats_warn = self._fixed_label(tab, 2, WRAP, style="Note.TLabel",
                                            wraplength=WRAP, justify="left")

    def _build_style_tab(self):
        tab = self._tab("Style")
        frame = ttk.LabelFrame(tab, text=" Brightness ", padding=10)
        frame.pack(fill="x")
        self.brightness = Slider(frame, 10, 100, lambda v: f"{round(v)}%",
                                 lambda v: self._set("brightness", round(v)), length=200)
        self.brightness.pack(anchor="w")
        ttk.Label(frame, text="Dims the whole screen. The case screen's backlight "
                              "can't be changed by software, so this darkens the "
                              "picture itself.", style="Hint.TLabel", wraplength=WRAP,
                  justify="left").pack(anchor="w", pady=(4, 0))

        frame = ttk.LabelFrame(tab, text=" Font ", padding=10)
        frame.pack(fill="x", pady=(12, 0))
        self.font_name = ttk.Label(frame, text="")
        self.font_name.pack(anchor="w")
        self.font_image = ttk.Label(frame)
        self.font_image.pack(anchor="w", pady=(6, 6))
        row = ttk.Frame(frame)
        row.pack(anchor="w")
        ttk.Button(row, text="Choose font...", command=self.choose_font).pack(side="left")
        ttk.Button(row, text="Default font", command=lambda: self._set("font", None)).pack(
            side="left", padx=6)
        ttk.Label(frame, text="Fonts installed for all users. Some lack the ° sign; "
                              "the preview shows it before you apply.",
                  style="Hint.TLabel", wraplength=WRAP, justify="left").pack(anchor="w", pady=(8, 0))

        self.frost = tk.BooleanVar()
        frame = ttk.LabelFrame(tab, padding=10, labelwidget=ttk.Checkbutton(
            tab, text="Frosted panel behind the text", variable=self.frost,
            command=lambda: self._set("frost", self.frost.get())))
        frame.pack(fill="x", pady=(12, 0))
        self.frost_dark = Slider(frame, 0.0, 1.0, lambda v: f"{v * 100:.0f}%",
                                 lambda v: self._set("frost_dark", round(v, 2)))
        self.frost_blur = Slider(frame, 0.0, 20.0, lambda v: f"{v:.0f}",
                                 lambda v: self._set("frost_blur", float(round(v))))
        self._grid(frame, [("Darkness", self.frost_dark), ("Blur", self.frost_blur)])

    def _build_options_tab(self):
        tab = self._tab("Options")
        frame = ttk.LabelFrame(tab, text=" Windows ", padding=10)
        frame.pack(fill="x")
        self.auto_check = ttk.Checkbutton(frame, text="Start display with Windows",
                                          variable=self.autostart,
                                          command=self.toggle_autostart)
        self.auto_check.pack(anchor="w")
        ttk.Label(frame, text="The clock appears as soon as the PC starts, even "
                              "before you log in.",
                  style="Hint.TLabel", wraplength=WRAP, justify="left").pack(anchor="w", pady=(4, 0))

        frame = ttk.LabelFrame(tab, text=" Saved layouts ", padding=10)
        frame.pack(fill="x", pady=(12, 0))
        self.layout_list = tk.Listbox(frame, height=7, activestyle="none", exportselection=False)
        self.layout_list.pack(fill="x")
        self.layout_list.bind("<Double-Button-1>", lambda e: self.load_layout())
        self.layout_list.bind("<<ListboxSelect>>", lambda e: self._update_controls())
        row = ttk.Frame(frame)
        row.pack(anchor="w", pady=(8, 0))
        self.load_btn = ttk.Button(row, text="Load", command=self.load_layout)
        self.load_btn.pack(side="left")
        self.save_btn = ttk.Button(row, text="Save as...", command=self.save_layout)
        self.save_btn.pack(side="left", padx=6)
        self.delete_btn = ttk.Button(row, text="Delete", command=self.delete_layout)
        self.delete_btn.pack(side="left")
        ttk.Label(frame, text=f"A layout keeps everything on the screen, its pictures "
                              f"and videos too. Up to {MAX_LAYOUTS}.",
                  style="Hint.TLabel", wraplength=WRAP, justify="left").pack(anchor="w", pady=(8, 0))
        # Rotating belongs to the app, not to a layout, so it takes effect at
        # once, like "Start display with Windows".
        row = ttk.Frame(frame)
        row.pack(anchor="w", pady=(8, 0))
        self.rotate_check = ttk.Checkbutton(row, text="Change layout every", variable=self.rotate,
                                            command=self.set_rotation)
        self.rotate_check.pack(side="left")
        self.rotate_spin = ttk.Spinbox(row, width=4, from_=ROTATION_MINUTES[0],
                                       to=ROTATION_MINUTES[1], textvariable=self.rotate_minutes,
                                       command=self.set_rotation)
        self.rotate_spin.pack(side="left", padx=6)
        self.rotate_spin.bind("<Return>", lambda e: self.set_rotation())
        self.rotate_spin.bind("<FocusOut>", lambda e: self.set_rotation())
        ttk.Label(row, text="minutes").pack(side="left")
        ttk.Label(frame, text="Goes through the saved layouts in turn. It changes when "
                              "the picture or video that is playing has finished.",
                  style="Hint.TLabel", wraplength=WRAP, justify="left").pack(anchor="w", pady=(4, 0))

    def _build_extras_tab(self):
        """Weather, Text, Countdown, To-do and Calendar, each in a tab of its
        own."""
        tab = self._tab("Extras")
        inner = ttk.Notebook(tab)
        inner.pack(fill="both", expand=True)
        self.block_show, self.block_size, self.block_swatch = {}, {}, {}
        self.block_align, self.block_frost, self.block_pos = {}, {}, {}
        self.block_widgets = {}             # what each block's switch enables

        def page(title):
            frame = ttk.Frame(inner, padding=8)
            inner.add(frame, text=f" {title} ")
            return frame

        # Weather. The city is looked up by name, then picked from the places
        # found: only where it is goes into the settings (and to the service).
        frame = page("Weather")
        self._block_switch(frame, "weather", "Show the weather")
        grid = ttk.Frame(frame)
        grid.pack(fill="x", pady=(4, 0))
        find = ttk.Frame(grid)
        self.city = tk.StringVar()
        limit = self.root.register(lambda p: len(p) <= MAX_CITY)
        self.city_entry = ttk.Entry(find, textvariable=self.city, width=22,
                                    validate="key", validatecommand=(limit, "%P"))
        self.city_entry.pack(side="left")
        self.city_entry.bind("<Return>", lambda e: self.find_city())
        self.city_btn = ttk.Button(find, text="Search", width=8, command=self.find_city)
        self.city_btn.pack(side="left", padx=(6, 0))
        self.place_box = ttk.Combobox(grid, state="readonly", width=34)
        self.place_box.bind("<<ComboboxSelected>>", self._place_picked)
        self.weather_unit = tk.StringVar(value="c")
        self.weather_forecast = tk.BooleanVar(value=True)
        self.weather_plain = tk.BooleanVar(value=False)
        units, shows = ttk.Frame(grid), ttk.Frame(grid)
        radios = []
        for parent, var, key, choices in (
                (units, self.weather_unit, "weather_unit",
                 (("c", "Celsius"), ("f", "Fahrenheit"))),
                (shows, self.weather_forecast, "weather_forecast",
                 ((True, "Today and 3 days"), (False, "Today only")))):
            for value, text in choices:
                radio = ttk.Radiobutton(parent, text=text, value=value, variable=var,
                                        command=lambda var=var, key=key: self._set(key, var.get()))
                radio.pack(side="left", padx=(0, 10))
                radios.append(radio)
        plain = ttk.Checkbutton(grid, text="Icons in the block's colour",
                                variable=self.weather_plain,
                                command=lambda: self._set("weather_plain_icons",
                                                          self.weather_plain.get()))
        self._grid(grid, [("Find", find), ("City", self.place_box), ("Units", units),
                          ("Show", shows), ("", plain)])
        self.place_hint = self._fixed_label(frame, 2, WRAP, style="Hint.TLabel",
                                            wraplength=WRAP, justify="left")
        self.block_widgets["weather"] = [self.city_entry, self.city_btn, self.place_box,
                                         *radios, plain]
        self._block_controls(frame, "weather")
        credit = ttk.Label(frame, text=weather_win.CREDIT, foreground=BLUE, cursor="hand2")
        credit.pack(anchor="w", pady=(8, 0))
        credit.bind("<Button-1>", lambda e: webbrowser.open(weather_win.CREDIT_LINK))
        ttk.Label(frame, text="Read every 15 minutes while it is shown. Where your city "
                              "is (to about a kilometre) is sent to Open-Meteo for it.",
                  style="Hint.TLabel", wraplength=WRAP, justify="left").pack(anchor="w")

        # Text
        frame = page("Text")
        self._block_switch(frame, "note", "Show this text")
        self.note_box = tk.Text(frame, height=5, width=36, wrap="word", undo=True,
                                font=tkfont.nametofont("TkDefaultFont"))
        self.note_box.pack(fill="x", pady=(4, 0))
        self.note_box.bind("<<Modified>>", self._note_typed)
        ttk.Label(frame, text=f"A quote, a reminder, anything. Up to {MAX_NOTE_LINES} "
                              "lines; long lines wrap on the screen.",
                  style="Hint.TLabel", wraplength=WRAP, justify="left").pack(anchor="w", pady=(2, 0))
        self.block_widgets["note"] = [self.note_box]
        self._block_controls(frame, "note")

        # Countdown
        frame = page("Countdown")
        self._block_switch(frame, "countdown", "Show a countdown")
        grid = ttk.Frame(frame)
        grid.pack(fill="x", pady=(4, 0))
        self.countdown_mode = ttk.Combobox(grid, state="readonly", width=24,
                                           values=[text for _, text in COUNTDOWN_CHOICES])
        self.countdown_mode.bind("<<ComboboxSelected>>", lambda e: self._set(
            "countdown_mode", COUNTDOWN_CHOICES[self.countdown_mode.current()][0]))
        # The date is picked from a calendar and the time from lists, so
        # neither can be written wrongly.
        self.countdown_date_btn = ttk.Button(grid, width=18, command=self.choose_countdown_date)
        self.countdown_time = TimeField(grid, self._countdown_time_picked)
        self.countdown_label = tk.StringVar()
        limit = self.root.register(lambda p: len(p) <= MAX_COUNTDOWN_LABEL)
        self.countdown_label_entry = ttk.Entry(grid, textvariable=self.countdown_label, width=26,
                                               validate="key", validatecommand=(limit, "%P"))
        self.countdown_label.trace_add("write", lambda *_: self._set_typed(
            "countdown_label", self.countdown_label.get().strip(), self.countdown_label_entry))
        self._grid(grid, [("Counts", self.countdown_mode), ("Date", self.countdown_date_btn),
                          ("Time", self.countdown_time), ("Name", self.countdown_label_entry)])
        self.countdown_example = self._fixed_label(frame, 2, WRAP, style="Hint.TLabel",
                                                   wraplength=WRAP, justify="left")
        self.block_widgets["countdown"] = [self.countdown_mode, self.countdown_date_btn,
                                           self.countdown_label_entry]
        self._block_controls(frame, "countdown")

        # To-do list
        frame = page("To-do")
        self._block_switch(frame, "todo", "Show the to-do list")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, text="Title").pack(side="left")
        self.todo_title = tk.StringVar()
        limit = self.root.register(lambda p: len(p) <= MAX_TODO_TEXT)
        self.todo_title_entry = ttk.Entry(row, textvariable=self.todo_title, width=28,
                                          validate="key", validatecommand=(limit, "%P"))
        self.todo_title_entry.pack(side="left", padx=(8, 0))
        self.todo_title.trace_add("write", lambda *_: self._set_typed(
            "todo_title", self.todo_title.get().strip(), self.todo_title_entry))
        box = ttk.Frame(frame)
        box.pack(fill="x", pady=(6, 0))
        self.todo_tree = ttk.Treeview(box, show="tree", height=5, selectmode="browse")
        self.todo_tree.column("#0", width=290)
        bar = ttk.Scrollbar(box, orient="vertical", command=self.todo_tree.yview)
        self.todo_tree.configure(yscrollcommand=bar.set)
        self.todo_tree.pack(side="left", fill="x", expand=True)
        bar.pack(side="left", fill="y")
        self.todo_tree.bind("<Button-1>", self._todo_click)
        self.todo_tree.bind("<Double-Button-1>", self._todo_edit)
        self.todo_tree.bind("<<TreeviewSelect>>", lambda e: self._update_controls())
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(6, 0))
        self.todo_new = tk.StringVar()
        self.todo_entry = ttk.Entry(row, textvariable=self.todo_new, width=28,
                                    validate="key", validatecommand=(limit, "%P"))
        self.todo_entry.pack(side="left")
        self.todo_entry.bind("<Return>", lambda e: self._todo_add())
        self.todo_add_btn = ttk.Button(row, text="Add", width=6, command=self._todo_add)
        self.todo_add_btn.pack(side="left", padx=(6, 0))
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(6, 0))
        self.todo_tick_btn = ttk.Button(row, text="Tick / untick",
                                        command=lambda: self._todo_selected("toggle"))
        self.todo_tick_btn.pack(side="left")
        self.todo_remove_btn = ttk.Button(row, text="Remove",
                                          command=lambda: self._todo_selected("remove"))
        self.todo_remove_btn.pack(side="left", padx=6)
        self.todo_clear_btn = ttk.Button(row, text="Remove ticked",
                                         command=lambda: self._todo_live("remove_done"))
        self.todo_clear_btn.pack(side="left")
        ttk.Label(frame, text="Ticking and removing show on the screen straight away. "
                              "New or edited items show when you Apply. Double-click "
                              "an item to edit it.",
                  style="Hint.TLabel", wraplength=WRAP, justify="left").pack(anchor="w", pady=(6, 0))
        self.block_widgets["todo"] = [self.todo_title_entry]
        self._block_controls(frame, "todo")

        # Calendar
        frame = page("Calendar")
        self.calendar_mode = tk.StringVar(value="off")
        self.calendar_style = tk.StringVar(value="month")
        self.week_start = tk.StringVar(value="mon")
        for value, text in (("off", "No calendar"), ("under", "Under the date"),
                            ("instead", "Instead of the date")):
            ttk.Radiobutton(frame, text=text, value=value, variable=self.calendar_mode,
                            command=self._calendar_changed).pack(anchor="w")
        grid = ttk.Frame(frame)
        grid.pack(fill="x", pady=(10, 0))
        styles, starts = ttk.Frame(grid), ttk.Frame(grid)
        self.calendar_widgets = []
        for parent, var, choices, command in (
                (styles, self.calendar_style, (("month", "Month grid"), ("week", "Week strip")),
                 self._calendar_changed),
                (starts, self.week_start, (("mon", "Monday"), ("sun", "Sunday")),
                 lambda: self._set("week_start", self.week_start.get()))):
            for value, text in choices:
                radio = ttk.Radiobutton(parent, text=text, value=value, variable=var,
                                        command=command)
                radio.pack(side="left", padx=(0, 10))
                self.calendar_widgets.append(radio)
        self.calendar_size = Slider(grid, 8, 40, _px, lambda v: self._set("calendar_size", round(v)),
                                    length=120)
        self.calendar_swatch = Swatch(grid, "Calendar colour",
                                      lambda c: self._set("calendar_color", c))
        self._grid(grid, [("Style", styles), ("Week starts", starts),
                          ("Size", self.calendar_size), ("Colour", self.calendar_swatch)])
        self.calendar_widgets += [self.calendar_size, self.calendar_swatch]
        ttk.Label(frame, text="The calendar is part of the clock block and moves with it. "
                              "Today is marked.", style="Hint.TLabel", wraplength=WRAP,
                  justify="left").pack(anchor="w", pady=(10, 0))

    def _block_switch(self, parent, block, text):
        var = self.block_show[block] = tk.BooleanVar()
        ttk.Checkbutton(parent, text=text, variable=var,
                        command=lambda: self._set(f"show_{block}", var.get())).pack(anchor="w")

    def _block_controls(self, parent, block):
        """Size, colour, alignment, panel and height: the same for each block."""
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=(8, 0))
        size = self.block_size[block] = Slider(
            frame, 8, 60, _px, lambda v: self._set(f"{block}_size", round(v)), length=120)
        swatch = self.block_swatch[block] = Swatch(
            frame, "Colour", lambda c: self._set(f"{block}_color", c))
        align = self.block_align[block] = tk.StringVar()
        frost = self.block_frost[block] = tk.BooleanVar()
        places = ttk.Frame(frame)
        radios = [ttk.Radiobutton(places, text=text, value=value, variable=align,
                                  command=lambda: self._set(f"{block}_align", align.get()))
                  for value, text in (("left", "Left"), ("center", "Centre"), ("right", "Right"))]
        for radio in radios:
            radio.pack(side="left", padx=(0, 6))
        panel = ttk.Checkbutton(places, text="Panel", variable=frost,
                                command=lambda: self._set(f"{block}_frost", frost.get()))
        panel.pack(side="left", padx=(6, 0))
        pos = self.block_pos[block] = Slider(
            frame, 0.0, 1.0, _height, lambda v: self._set(f"{block}_pos", round(v, 3)),
            length=150)
        ttk.Label(frame, text="Size").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=2)
        size.grid(row=0, column=1, sticky="w")
        swatch.grid(row=0, column=2, padx=(4, 0))
        ttk.Label(frame, text="Place").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=2)
        places.grid(row=1, column=1, columnspan=2, sticky="w")
        ttk.Label(frame, text="Height").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=2)
        pos.grid(row=2, column=1, columnspan=2, sticky="w")
        self.block_widgets[block] += [size, swatch, *radios, panel, pos]

    # -- the extras' own inputs ------------------------------------------------------

    def find_city(self):
        """Look up the city typed in (Open-Meteo's place search, asked from
        here and only when Search is pressed) and offer what it finds in the
        City list."""
        name = self.city.get().strip()
        if len(name) < 2 or self._searching:
            return
        self._searching = True
        self._say_place("Searching...")
        found = []

        def done(error):
            self._searching = False
            if error:
                self._say_place(f"Could not search: {error}")
            elif not found:
                self._say_place("No place found by that name. Check the spelling, or "
                                "try the nearest larger town.")
            else:
                self._places = found
                self.place_box.configure(values=[place["name"] for place in found])
                self._say_place("Now pick your city from the City list.")
                if self.visible:
                    self.place_box.focus_set()
                    self.place_box.event_generate("<Down>")     # opens the list

        self.app.background(lambda: found.extend(weather_win.search(name)), done)

    def _say_place(self, text):
        self._place_note = text
        self.place_hint.configure(text=text)

    def _place_picked(self, _event=None):
        index = self.place_box.current()
        if 0 <= index < len(self._places):
            self._place_note = None
            self._set("weather_place", dict(self._places[index]))

    def _set_typed(self, key, value, entry):
        """A text box changed a setting. Pushing the look back must not
        rewrite the box being typed in."""
        if not self._loading and value != self.look[key]:
            self._set(key, value)

    def _note_typed(self, _event=None):
        if not self.note_box.edit_modified():
            return
        self.note_box.edit_modified(False)
        text = self.note_box.get("1.0", "end-1c")
        lines = text.split("\n")[:MAX_NOTE_LINES]
        text = "\n".join(line.replace("\t", " ") for line in lines)[:MAX_NOTE]
        if not self._loading and text != self.look["note_text"]:
            self._set("note_text", text)

    def choose_countdown_date(self):
        date = self.look["countdown_date"]
        picker = DatePicker(self.root, _as_date(date) if date else None,
                            self.look["week_start"], self.countdown_date_btn)
        self.root.wait_window(picker.top)
        if picker.result:
            self._set("countdown_date", picker.result.isoformat())

    def _countdown_time_picked(self, when):
        # Midnight is the start of the day, which is what no time means.
        self._set("countdown_time", "" if when == "00:00" else when)

    def _show_countdown_example(self):
        look = self.look
        date, when = look["countdown_date"], look["countdown_time"]
        if not date:
            text = "Choose the date to count to, or to count from."
        else:
            timed = when and look["countdown_mode"] == "to-hours"
            target = datetime.datetime.strptime(date + (" " + when if timed else ""),
                                                "%Y-%m-%d %H:%M" if timed else "%Y-%m-%d")
            shows, _ = countdown_text(look["countdown_mode"], target, look["countdown_label"],
                                      datetime.datetime.now())
            text = (f"Shows now: {shows}" if shows else
                    "Shows nothing now: the date has passed (or, counting since it, "
                    "hasn't come yet).")
        self.countdown_example.configure(text=text)

    def _calendar_changed(self):
        mode, style = self.calendar_mode.get(), self.calendar_style.get()
        if self._loading:
            return
        look = self.look
        was_instead = look["calendar"] and not look["show_date"]
        look["calendar"] = None if mode == "off" else style
        if mode == "instead":
            look["show_date"] = False
        elif mode == "under" or was_instead:
            look["show_date"] = True
        self.changed()

    def _todo_items_view(self):
        """Put the form's to-do items into the list box, if they changed."""
        items = self.look["todo_items"]
        key = json.dumps(items)
        if key == self._todo_shown:
            return
        self._todo_shown = key
        selected = self.todo_tree.selection()
        self.todo_tree.delete(*self.todo_tree.get_children())
        for item in items:
            mark = TICKED if item["done"] else BOX
            self.todo_tree.insert("", "end", iid=str(item["id"]), text=f"{mark}  {item['text']}")
        if selected and self.todo_tree.exists(selected[0]):
            self.todo_tree.selection_set(selected[0])

    def _todo_click(self, event):
        """A click on an item's box ticks or unticks it."""
        row = self.todo_tree.identify_row(event.y)
        if row and event.x < 30:
            self._todo_live("toggle", int(row))
            return "break"
        return None

    def _todo_selected(self, action):
        selected = self.todo_tree.selection()
        if selected:
            self._todo_live(action, int(selected[0]))

    def _todo_live(self, action, ident=None):
        """Tick, untick or remove straight away. Items the screen already has
        change there at once (the service's "todo" command); items added and
        not applied yet only change in the form."""
        look = self.look
        if action == "toggle":
            item = next((i for i in look["todo_items"] if i["id"] == ident), None)
            if item is None:
                return
            action = "untick" if item["done"] else "tick"

        def apply_to(items):
            if action == "remove_done":
                return [i for i in items if not i["done"]]
            out = []
            for i in items:
                if i["id"] == ident:
                    if action == "remove":
                        continue
                    i = {**i, "done": action == "tick"}
                out.append(i)
            return out

        on_screen = action == "remove_done" or any(
            i["id"] == ident for i in self._saved_look["todo_items"])
        look["todo_items"] = apply_to(look["todo_items"])
        if on_screen and self.installed:
            self._saved_look["todo_items"] = apply_to(self._saved_look["todo_items"])
            fields = {"action": action} if ident is None else {"action": action, "id": ident}

            def done(error):
                if error:
                    messagebox.showerror(DISPLAY_NAME, f"Could not update the to-do list:\n\n{error}",
                                         parent=self.root)
            self.app.background(lambda: send_settings("todo", **fields), done)
        if action == "remove" and self._editing == ident:
            self._stop_editing()
        self.changed()

    def _todo_add(self):
        text = self.todo_new.get().strip()
        if not text:
            return
        items = [dict(i) for i in self.look["todo_items"]]
        if self._editing is not None:
            for item in items:
                if item["id"] == self._editing:
                    item["text"] = text
            self._stop_editing()
        elif len(items) >= MAX_TODO_ITEMS:
            messagebox.showinfo(DISPLAY_NAME, f"The list holds up to {MAX_TODO_ITEMS} items.",
                                parent=self.root)
            return
        else:
            ids = [i["id"] for i in items + self._saved_look["todo_items"]]
            items.append({"id": max(ids, default=0) + 1, "text": text, "done": False})
            self.todo_new.set("")
        self._set("todo_items", items)

    def _todo_edit(self, event):
        row = self.todo_tree.identify_row(event.y)
        if not row or event.x < 30:
            return
        item = next((i for i in self.look["todo_items"] if i["id"] == int(row)), None)
        if item:
            self._editing = item["id"]
            self.todo_new.set(item["text"])
            self.todo_add_btn.configure(text="Save")
            self.todo_entry.focus_set()

    def _stop_editing(self):
        self._editing = None
        self.todo_new.set("")
        self.todo_add_btn.configure(text="Add")

    @staticmethod
    def _fixed_label(parent, lines, width, anchor="w", **options):
        """A label in a box of fixed size, packed into `parent`, so a warning
        appearing or going never resizes the window."""
        font = tkfont.nametofont("TkDefaultFont")
        box = ttk.Frame(parent, width=width, height=lines * font.metrics("linespace") + 2)
        box.pack_propagate(False)
        box.pack(anchor=anchor, pady=(4, 0))
        label = ttk.Label(box, **options)
        label.pack(anchor=anchor)
        return label

    @staticmethod
    def _grid(frame, rows):
        """Label on the left, control on the right, one row each."""
        for r, (text, widget) in enumerate(rows):
            if text:
                ttk.Label(frame, text=text).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=2)
            widget.grid(row=r, column=1, sticky="w", pady=2)

    def _place_spots(self):
        """Lay the seven spots out like the screen: three rows of two and one
        centred, or one column."""
        two = self.columns.get() == 2
        for i, (cell, combo, entry, _) in enumerate(self.spots):
            cell.grid_forget()
            combo.pack_forget()
            entry.pack_forget()
            side = "top" if two else "left"
            combo.pack(side=side, anchor="w")
            entry.pack(side=side, anchor="w", padx=(0, 0) if two else (6, 0), pady=(2, 0) if two else 0)
            if not two:
                cell.grid(row=i, column=0, columnspan=2, sticky="w")
            elif i == MAX_SPOTS - 1:
                cell.grid(row=i // 2, column=0, columnspan=2)
            else:
                cell.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 8) if i % 2 == 0 else 0)

    # -- the form <-> self.look --------------------------------------------------

    def _set(self, key, value):
        """A control changed one setting."""
        if self._loading:
            return
        self.look[key] = value
        self.changed()

    def changed(self):
        # Changed means different from what the screen shows: undoing a change
        # by hand counts as no change.
        self.dirty = self.look != self._saved_look
        self._show_look()
        self._update_controls()
        self.render_preview()

    def _show_look(self):
        """Put self.look into every control."""
        look = self.look
        self._loading = True
        try:
            self._show_playlist()
            self.show_time.set(look["show_time"])
            self.show_date.set(look["show_date"])
            self.seconds.set(look["seconds"])
            self.time_format.current([k for k, _ in TIME_CHOICES].index(look["time_format"]))
            today = datetime.date.today()
            self.date_format.configure(values=date_examples(today) + [CUSTOM_DATE])
            if look["date_format"] in DATE_PRESETS and not self._custom_date:
                self.date_format.current(DATE_PRESETS.index(look["date_format"]))
            else:
                self.date_format.current(len(DATE_PRESETS))
            focus = self.root.focus_get()
            if self.date_pattern.get() != look["date_format"] and focus is not self.date_entry:
                self.date_pattern.set(look["date_format"])
            for part in ("time", "date", "label", "value"):
                getattr(self, f"{part}_swatch").set(look[f"{part}_color"])
            self.columns.set(look["columns"])
            for i, (_, combo, entry, name) in enumerate(self.spots):
                slot = look["slots"][i]
                combo.current(STAT_KEYS.index(slot))
                text = look["labels"].get(slot, SLOTS[slot][0]) if slot else ""
                if name.get() != text and (focus is not entry or not slot):
                    name.set(text)
            self.frost.set(look["frost"])
            self.brightness.set(look["brightness"])
            self.show_stats.set(look["show_stats"])
            self.frost_dark.set(look["frost_dark"])
            self.frost_blur.set(look["frost_blur"])
            on = [slot for slot in look["warn"] if slot in look["slots"]]
            self.warn_btn.configure(text=f"Warning colours ({len(on)})..." if on
                                    else "Warning colours...")
            self._show_extras(focus)
            self._show_font()
        finally:
            self._loading = False

    def _show_playlist(self):
        """The Picture tab's part of _show_look: the list, and the settings of
        the item picked in it."""
        items = self.look["playlist"]
        texts = [self._item_text(n, item) for n, item in enumerate(items, 1)]
        if texts != list(self.items_list.get(0, "end")):
            self.items_list.delete(0, "end")
            for text in texts:
                self.items_list.insert("end", text)
        item = self._item()
        if self.items_list.curselection() != (self.sel,):
            self.items_list.selection_clear(0, "end")
            self.items_list.selection_set(self.sel)
            self.items_list.see(self.sel)
        self.shuffle.set(self.look["playlist_shuffle"])
        fit = item["fit"]
        self.mode.set("fill" if fit == "fill" else "fit")
        if fit != "fill":
            self.edge.set(fit)
        self.fit_swatch.set(item["fit_color"])
        self.zoom.set(item["zoom"])
        kind = self._kind(item)
        still = kind == "picture"
        self.timing_lead.configure(text="Show it for" if still else "Play it")
        self.timing_unit.configure(text="seconds, then the next" if still
                                   else "time, then the next" if item["plays"] == 1
                                   else "times, then the next")
        low, high = STILL_SECONDS if still else PLAYS
        self.timing.configure(from_=low, to=high)
        value = str(item["seconds"] if still else item["plays"])
        if self.timing_var.get() != value and self.root.focus_get() is not self.timing:
            self.timing_var.set(value)
        if kind == "video":
            self.item_check.configure(text="Limit to 30 frames a second (lighter)")
            self.item_flag.set(item["max_fps"] == 30)
        else:
            self.item_check.configure(text="Play at the GIF's own speed")
            self.item_flag.set(bool(item.get("own_speed")) and kind == "gif")
        position = self._positions.get(item["id"], 0.0)
        if abs(self.seek_var.get() - position) > 1e-6:
            self.seek_var.set(position)
        self._show_position(item)

    def _show_extras(self, focus):
        """The Extras tab's part of _show_look."""
        look = self.look
        for block in EXTRA_BLOCKS:
            self.block_show[block].set(look[f"show_{block}"])
            self.block_swatch[block].set(look[f"{block}_color"])
            self.block_align[block].set(look[f"{block}_align"])
            self.block_frost[block].set(look[f"{block}_frost"])
        # The City list holds what the last search found, and the place in use.
        place = look["weather_place"]
        names = [found["name"] for found in self._places]
        if place and place["name"] not in names:
            names.append(place["name"])
        if list(self.place_box.cget("values")) != names:
            self.place_box.configure(values=names)
        if self.place_box.get() != (place["name"] if place else ""):
            self.place_box.set(place["name"] if place else "")
        self.weather_unit.set(look["weather_unit"])
        self.weather_forecast.set(look["weather_forecast"])
        self.weather_plain.set(look["weather_plain_icons"])
        self.place_hint.configure(text=self._place_note or (
            "To change it, type another city and press Search." if place else
            "Type your city, press Search, then pick it from the City list."))
        if focus is not self.note_box and self.note_box.get("1.0", "end-1c") != look["note_text"]:
            state = self.note_box.cget("state")
            self.note_box.configure(state="normal")
            self.note_box.delete("1.0", "end")
            self.note_box.insert("1.0", look["note_text"])
            self.note_box.edit_modified(False)
            self.note_box.configure(state=state)
        self.countdown_mode.current([k for k, _ in COUNTDOWN_CHOICES].index(look["countdown_mode"]))
        date = look["countdown_date"]
        self.countdown_date_btn.configure(text=_date_text(_as_date(date)) if date
                                          else "Choose a date...")
        # Asked the way the clock shows the time: with AM and PM, or to 24.
        self.countdown_time.set(look["countdown_time"], look["time_format"] != "24h")
        for var, entry, key in ((self.countdown_label, self.countdown_label_entry, "countdown_label"),
                                (self.todo_title, self.todo_title_entry, "todo_title")):
            if focus is not entry and var.get().strip() != look[key]:
                var.set(look[key])
        self._show_countdown_example()
        self._todo_items_view()
        self.calendar_mode.set("off" if not look["calendar"] else
                               "under" if look["show_date"] else "instead")
        if look["calendar"]:
            self.calendar_style.set(look["calendar"])
        self.week_start.set(look["week_start"])
        self.calendar_swatch.set(look["calendar_color"])

    def _show_font(self):
        file = self.look["font"] or DEFAULT_FONT
        self.font_name.configure(text=self.fonts.name(file)
                                 + ("" if self.look["font"] else "  (default)"))
        self._font_photo = ImageTk.PhotoImage(font_sample(file, "10:11 AM  47°C", 24, 280, 34))
        self.font_image.configure(image=self._font_photo)

    def _update_controls(self):
        """Enable what applies to the current choices.

        Called on every status tick, so it does nothing unless something it
        depends on has changed: touching fifty widgets twice a second shows
        up as CPU use while the window sits open.
        """
        look = self.look
        custom = self.date_format.current() == len(DATE_PRESETS)
        item, count = self._item(), len(look["playlist"])
        kind, framing = self._kind(item), self._can_frame(item)
        key = (item["fit"], self.sel, count, kind, framing, self._play_from is not None,
               look["show_time"], look["show_date"], custom,
               tuple(bool(s) for s in look["slots"]), look["show_stats"], look["frost"],
               tuple(look[f"show_{b}"] for b in EXTRA_BLOCKS), look["calendar"],
               look["countdown_mode"], self.todo_tree.selection(), self._editing,
               bool(look["todo_items"]), self.dirty,
               self.installed, self.busy, self.layout_list.curselection())
        if key == self._controls_key:
            return
        self._controls_key = key
        settled = self.installed and not self.busy
        fit = item["fit"] != "fill"
        _enable([self.fill_radio, self.fit_radio, self.zoom, self.recentre_btn], framing)
        _enable([self.blur_radio, self.color_radio], fit and framing)
        _enable([self.fit_swatch], fit and item["fit"] == "color" and framing)
        # With one item there is nothing to take turns with: it just plays on.
        _enable([self.timing], count > 1)
        _enable([self.shuffle_check], count > 2)
        _enable([self.item_check], kind == "gif" or (kind == "video" and framing))
        _enable([self.add_btn, self.add_default_btn], settled and count < MAX_ITEMS)
        _enable([self.remove_btn], settled)
        _enable([self.up_btn], settled and self.sel > 0)
        _enable([self.down_btn], settled and self.sel < count - 1)
        moving = kind != "picture" and self._length(item) > 0
        _enable([self.play_btn], moving)
        self.seek.state(["!disabled"] if moving else ["disabled"])
        self.play_btn.configure(text="Pause" if self._play_from is not None else "Play")
        _enable(self.time_widgets, look["show_time"])
        _enable(self.date_widgets, look["show_date"])
        for widget in (self.date_entry, self.date_codes):
            if custom:
                widget.grid()
            else:
                widget.grid_remove()
        _enable([self.clock_pos], look["show_time"] or look["show_date"])
        for i, (_, _, entry, _) in enumerate(self.spots):
            _enable([entry], look["show_stats"] and bool(look["slots"][i]))
        # Switched off, the stats keep their settings but can't be edited.
        _enable(self.column_radios + [combo for _, combo, _, _ in self.spots],
                look["show_stats"])
        any_stats = look["show_stats"] and any(look["slots"])
        _enable([self.label_size, self.value_size, self.label_swatch, self.value_swatch,
                 self.stats_pos, self.warn_btn], any_stats)
        _enable([self.frost_dark, self.frost_blur], look["frost"])
        for block in EXTRA_BLOCKS:
            _enable(self.block_widgets[block], look[f"show_{block}"])
        _enable(self.countdown_time.boxes,
                look["show_countdown"] and look["countdown_mode"] == "to-hours")
        _enable(self.calendar_widgets, bool(look["calendar"]))
        chosen_item = bool(self.todo_tree.selection())
        _enable([self.todo_tick_btn, self.todo_remove_btn], chosen_item)
        _enable([self.todo_clear_btn], any(i["done"] for i in look["todo_items"]))
        _enable([self.apply_btn, self.undo_btn], self.dirty and settled)
        _enable([self.reset_btn, self.save_btn], settled)
        chosen = bool(self.layout_list.curselection())
        _enable([self.load_btn, self.delete_btn], chosen and settled)

    def _fit_changed(self):
        self._set_item("fit", "fill" if self.mode.get() == "fill" else self.edge.get())

    # -- the playlist ----------------------------------------------------------------

    def _item(self):
        """The playlist item picked in the list."""
        items = self.look["playlist"]
        self.sel = max(0, min(self.sel, len(items) - 1))
        return items[self.sel]

    def _frames_of(self, item):
        """How many pictures an item's file holds: 1 for a still."""
        if item.get("frames"):
            return item["frames"]
        path = item_path(item)
        if path not in self._media:
            try:
                if path.endswith(video_win.CLIP_EXT):
                    self._media[path] = video_win.clip_info(path)["frames"]
                else:
                    with Image.open(path) as img:
                        self._media[path] = getattr(img, "n_frames", 1)
            except Exception:
                self._media[path] = 1
        return self._media[path]

    def _kind(self, item):
        """"video", "gif" or "picture"."""
        if item.get("pending") == "video" or item["file"].endswith(video_win.CLIP_EXT):
            return "video"
        return "gif" if self._frames_of(item) > 1 else "picture"

    def _length(self, item):
        """How long a GIF or video runs, in seconds (a GIF at the engine's 10
        frames a second); 0 for a still, or a video that doesn't say."""
        if self._kind(item) == "video":
            return item.get("duration") or 0.0
        frames = self._frames_of(item)
        return frames / 10 if frames > 1 else 0.0

    def _item_text(self, number, item):
        kind = self._kind(item)
        what = (f"video {_clock(self._length(item))}" if kind == "video"
                else f"GIF, {self._frames_of(item)} frames" if kind == "gif" else "picture")
        return f"{number}. {item['label']}  ({what})" + ("  - new" if item.get("pending") else "")

    def _source_of(self, item):
        """What the app knows of the video an applied clip was made from (its
        path, frame rate, whether it is HDR), if it still knows it (it does
        until it is closed) and the file is still there."""
        source = self._sources.get(item["file"])
        return source if source and os.path.isfile(source["path"]) else None

    def _can_frame(self, item):
        """Can this item be moved, zoomed and refitted? A picture or GIF
        always. A video's placement is built into its clip when it is
        converted, so only while the video itself is at hand to convert again."""
        if not item["file"].endswith(video_win.CLIP_EXT) or item.get("pending"):
            return True
        return self._source_of(item) is not None

    def _view(self, item):
        """The item as the preview should draw it: an applied clip whose video
        is at hand is drawn from the video, placed by its settings, so that it
        can be dragged about like any other picture."""
        if item["file"].endswith(video_win.CLIP_EXT) and not item.get("pending"):
            source = self._source_of(item)
            if source:
                return {**item, **source, "file": "pending", "pending": "video"}
        return item

    def _set_item(self, key, value):
        """A control changed one setting of the item picked in the list."""
        if self._loading:
            return
        item = self._item()
        if item.get(key) == value:
            return
        if (key in FRAMING or key == "max_fps") and not self._reopen(item):
            return
        item[key] = value
        self.changed()

    def _reopen(self, item):
        """A clip's placement and frame rate were fixed when it was made. To
        change them it goes back to being a video waiting to be converted.
        False when that can't be done: the video is no longer at hand."""
        if item["file"].endswith(video_win.CLIP_EXT) and not item.get("pending"):
            source = self._source_of(item)
            if source is None:
                return False
            item.update(source, pending="video", file="pending")
        return True

    def _item_picked(self):
        chosen = self.items_list.curselection()
        if self._loading or not chosen or chosen[0] == self.sel:
            return
        self.stop_play()
        self.sel = chosen[0]
        self._show_look()
        self._update_controls()
        self.render_preview()

    def _probe(self, path):
        return self._video().call("probe", path=path)[0]["info"]

    def _video(self):
        """The helper process that reads videos, started when first needed."""
        if self._worker is None or not self._worker.alive():
            self._worker = video_win.Worker()
        return self._worker

    def add_files(self):
        paths = filedialog.askopenfilenames(parent=self.root, filetypes=FILE_TYPES,
                                            title="Add pictures, GIFs or videos")
        if not paths:
            return
        items = list(self.look["playlist"])
        # The rooftop GIF a fresh install shows is only a stand-in: the first
        # file added takes its place. ("Add default" puts it back.)
        if len(items) == 1 and items[0]["file"] == DEFAULT_FILE and items[0] == new_item():
            items = []
        ids = [i["id"] for i in items + self.look["playlist"] + self._saved_look["playlist"]]
        first = None
        for path in paths:
            if len(items) >= MAX_ITEMS:
                messagebox.showinfo(DISPLAY_NAME, f"The playlist holds up to {MAX_ITEMS} items.",
                                    parent=self.root)
                break
            name = os.path.basename(path)
            try:
                kind, info = check_media_file(path, self._probe)
            except ValueError as e:
                messagebox.showerror(DISPLAY_NAME, f"{name}\n\n{e}", parent=self.root)
                continue
            item = new_item("pending", name[:100], max(ids, default=0) + 1)
            ids.append(item["id"])
            item.update(path=path, pending=kind)
            if kind == "video":
                item.update(duration=info["duration"] or 0.0, frames=info["frames"],
                            source_fps=info["fps"], hdr=info["hdr"])
                # A video's first picture is often black: start a little way in.
                self._positions[item["id"]] = 1 / 3
            else:
                item.update(frames=info["frames"],
                            duration=info["frames"] / 10 if info["frames"] > 1 else 0.0)
            items.append(item)
            first = len(items) - 1 if first is None else first
        if first is None:
            return
        self.stop_play()
        self.look["playlist"], self.sel = items, first
        self.changed()

    def add_default(self):
        items = self.look["playlist"]
        ids = [i["id"] for i in items + self._saved_look["playlist"]]
        items.append(new_item(ident=max(ids, default=0) + 1))
        self.stop_play()
        self.sel = len(items) - 1
        self.changed()

    def remove_item(self):
        items = self.look["playlist"]
        self.stop_play()
        del items[self.sel]
        if not items:                   # there is always something to show
            items.append(new_item())
        self.sel = min(self.sel, len(items) - 1)
        self.changed()

    def move_item(self, step):
        items, target = self.look["playlist"], self.sel + step
        if 0 <= target < len(items):
            items[self.sel], items[target] = items[target], items[self.sel]
            self.sel = target
            self.changed()

    def _timing_typed(self):
        """The seconds a still shows for, or the times a GIF or video plays."""
        if self._loading:
            return
        item = self._item()
        still = self._kind(item) == "picture"
        low, high = STILL_SECONDS if still else PLAYS
        try:
            value = int(self.timing_var.get())
        except ValueError:
            return
        if low <= value <= high:
            self._set_item("seconds" if still else "plays", value)

    def _flag_changed(self):
        item = self._item()
        if self._kind(item) == "video":
            self._set_item("max_fps", 30 if self.item_flag.get() else 60)
        else:
            self._set_item("own_speed", self.item_flag.get())

    # -- looking along a GIF or video, and playing it ---------------------------------

    def _show_position(self, item):
        """The text beside Play: where the preview is in the GIF or video."""
        kind, position = self._kind(item), self._positions.get(item["id"], 0.0)
        if kind == "video" and self._length(item):
            length = self._length(item)
            text = f"{_clock(position * length)} / {_clock(length)}"
        elif kind == "gif":
            frames = self._frames_of(item)
            text = f"{round(position * (frames - 1)) + 1} / {frames}"
        else:
            text = ""
        self.seek_text.configure(text=text)

    def _seeked(self, value):
        """The slider was moved: show that moment."""
        if self._loading:
            return
        item = self._item()
        self._positions[item["id"]] = float(value)
        if self._play_from is not None:     # carry on playing from there
            self._play_from = (time.perf_counter(), float(value))
        self._show_position(item)
        self.render_preview()

    def toggle_play(self):
        if self._play_from is not None:
            self.stop_play()
            return
        item = self._item()
        if self._length(item) <= 0:
            return
        self._play_from = (time.perf_counter(), self._positions.get(item["id"], 0.0))
        self._update_controls()
        self._play_tick()

    def stop_play(self):
        if self._play_from is not None:
            self._play_from = None
            self._update_controls()
            self.render_preview()       # once more, at full quality

    def _play_tick(self):
        """Move the preview on to where the clock says it should be. Pictures
        are skipped rather than queued, so it plays at its real speed however
        long each takes to draw."""
        # One timer at a time: Play pressed again before the last one fired
        # must not leave two running.
        timer, self._play_timer = self._play_timer, None
        if timer is not None:
            self.root.after_cancel(timer)
        if self._play_from is None or not self.visible:
            self._play_from = None
            return
        item = self._item()
        length = self._length(item)
        if length <= 0:
            self.stop_play()
            return
        began, start = self._play_from
        position = (start + (time.perf_counter() - began) / length) % 1.0
        self._positions[item["id"]] = position
        self._loading = True
        try:
            self.seek_var.set(position)
        finally:
            self._loading = False
        self._show_position(item)
        self.render_preview()
        self._play_timer = self.root.after(PLAY_INTERVAL_MS, self._play_tick)

    def _date_choice(self):
        index = self.date_format.current()
        self._custom_date = index == len(DATE_PRESETS)
        if not self._custom_date:
            self._set("date_format", DATE_PRESETS[index])
        else:
            self._update_controls()

    def _date_typed(self):
        pattern = self.date_pattern.get()
        if not self._loading and pattern.strip():
            self._set("date_format", pattern)

    def _columns_changed(self):
        self._place_spots()
        self._set("columns", self.columns.get())

    def _spot_changed(self, i):
        slots = list(self.look["slots"])
        slots[i] = STAT_KEYS[self.spots[i][1].current()]
        self._set("slots", slots)

    def _name_changed(self, i):
        slot = self.look["slots"][i]
        if self._loading or not slot:
            return
        text = self.spots[i][3].get().strip()
        labels = dict(self.look["labels"])
        if text and text != SLOTS[slot][0]:
            labels[slot] = text
        else:
            labels.pop(slot, None)
        if labels != self.look["labels"]:
            self._set("labels", labels)

    # -- showing and hiding -------------------------------------------------

    def show(self):
        self.visible = True
        self.app.refresh_soon()             # read the settings, not just the state
        self.refresh(self.app.status)
        self.root.deiconify()
        self.root.lift()
        # Windows only lets a window jump to the front briefly on top.
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def hide(self):
        self.visible = False
        self.root.withdraw()
        self.release()

    def release(self):
        """Let go of everything the preview holds: the picture it was made
        from (a big photo is megabytes), an open GIF, and the helper process
        that reads videos. Hidden, the app keeps nothing; all of it comes back
        the next time the window opens."""
        self._play_from = None
        self._frame = (None, None, None)
        gif, self._gif = self._gif, None
        if gif:
            gif[1].close()
        worker, self._worker = self._worker, None
        if worker:
            worker.close()

    # -- status --------------------------------------------------------------

    def refresh(self, s):
        """Show a status (twice a second while the window is open). Widgets
        are only touched when what they show has changed."""
        text, colour = describe(s)
        note = temps_problem(s) if is_showing(s) else None
        self.installed, showing = bool(s.get("installed")), is_showing(s)
        pending = s.get("scm") in (win32service.SERVICE_START_PENDING,
                                   win32service.SERVICE_STOP_PENDING)
        idle = not self.installed or self.busy or pending
        # A status read while the window was hidden has no autostart.
        autostart = s.get("autostart") if self.installed and not self.busy else None
        key = (text, colour, note, self.installed, showing, idle, self.busy, autostart)
        if key != self._status_key:
            self._status_key = key
            self.status_text.set(text)
            self.dot.configure(fg=colour)
            self.note_text.set(note or "")
            if note:
                self.note.pack(anchor="w", pady=(6, 0), before=self.buttons_row)
            else:
                self.note.pack_forget()
            self.start_btn.state(["disabled"] if idle or showing else ["!disabled"])
            self.stop_btn.state(["disabled"] if idle or not showing else ["!disabled"])
            _enable([self.auto_check, self.rotate_check, self.rotate_spin],
                    self.installed and not self.busy)
            if autostart is not None:
                self.autostart.set(bool(autostart))
        rotation = s.get("rotation")
        if rotation and rotation != self._rotation and not self.busy:
            self._rotation = dict(rotation)
            self.rotate.set(rotation["enabled"])
            if self.root.focus_get() is not self.rotate_spin:
                self.rotate_minutes.set(str(rotation["minutes"]))
        if "layouts" in s:
            self._show_layouts(s["layouts"])
        if self.installed and not self.dirty and "playlist" in s:
            self._load_form(s)
        self._update_controls()

    def _show_layouts(self, names):
        if names == self._layouts:
            return
        selected = self.selected_layout()
        self._layouts = names
        self.layout_list.delete(0, "end")
        for name in names:
            self.layout_list.insert("end", name)
        if selected in names:
            self.layout_list.selection_set(names.index(selected))

    def selected_layout(self):
        chosen = self.layout_list.curselection()
        return self.layout_list.get(chosen[0]) if chosen else None

    def _load_form(self, s):
        """Show the saved settings, unless they are already on show."""
        config = {key: s[key] for key in DEFAULT_LOOK if key in s}
        key = json.dumps(config, sort_keys=True)
        if key == self._form_key:
            return
        self._form_key = key
        self.stop_play()
        self.look = clean_look(config)
        self._saved_look = copy.deepcopy(self.look)
        self._custom_date = self.look["date_format"] not in DATE_PRESETS
        self.sel = min(self.sel, len(self.look["playlist"]) - 1)
        self._show_look()
        self._place_spots()
        self._update_controls()
        self.render_preview()

    # -- the picture ----------------------------------------------------------

    def recentre(self):
        item = self._item()
        if (item["zoom"], item["pan_x"], item["pan_y"]) != (1.0, 0.5, 0.5) and self._reopen(item):
            item.update(zoom=1.0, pan_x=0.5, pan_y=0.5)
            self.changed()

    def _show_picture_info(self, item, size, scale):
        """What the item picked in the list is: its size, and for a picture
        the best size; a warning if it is enlarged enough to look soft."""
        kind, warn = self._kind(item), ""
        if kind == "video" and item.get("pending"):
            rate = min(item.get("source_fps") or 30.0, item["max_fps"])
            text = (f"This video: {size[0]} × {size[1]} px, {_clock(self._length(item))}\n"
                    f"It will play at {rate:g} frames a second, without sound.")
            if item.get("hdr"):
                warn = "This is an HDR video: its colours may look washed out here."
        elif kind == "video":
            frames, length = self._frames_of(item), self._length(item)
            text = (f"Video: {_clock(length)} at {frames / length if length else 0:g} "
                    "frames a second, without sound.")
            if not self._can_frame(item):
                text += "\nTo move, zoom or refit it, add the video again."
        else:
            text = (f"This picture: {size[0]} × {size[1]} px\n"
                    f"Best size: {FIT_SIZE[0]} × {FIT_SIZE[1]} px, or larger in the "
                    "same tall shape.")
        if not warn and scale and scale > 1.05 and item["file"] != DEFAULT_FILE:
            warn = f"Shown {scale:.1f}× larger than it is, so it may look soft."
        self.picture_info.configure(text=text)
        self.picture_warn.configure(text=warn)

    def _show_space(self, layout):
        """The tallest empty band between the blocks, in panel pixels, for
        anyone making a picture to fit it."""
        w, h = layout.w, layout.h
        bands, y, above = [], 0, None
        for name, box in sorted(layout.boxes.items(), key=lambda item: item[1][1]):
            if box[1] > y:
                bands.append((box[1] - y, y, box[1], above, name))
            if box[3] > y:
                y, above = box[3], name
        if y < h:
            bands.append((h - y, y, h, above, None))
        if not layout.boxes:
            text = f"Nothing is drawn over the picture:\nall {w} × {h} px show."
        elif not bands:
            text = "The blocks cover the whole screen: no free space."
        else:
            height, top, bottom, above, below = max(bands)
            size = f"{w} × {height} px"
            if above and below:
                text = (f"Space between the {BLOCK_NAMES[above]} and the {BLOCK_NAMES[below]}:"
                        f"\n{size}, from {top} to {bottom} px down")
            elif below:
                text = f"Space above the {BLOCK_NAMES[below]}:\n{size}, from the top to {bottom} px down"
            else:
                text = f"Space below the {BLOCK_NAMES[above]}:\n{size}, from {top} px down to the bottom"
        self.space_info.configure(text=text)

    # -- the preview ------------------------------------------------------------

    def render_preview(self):
        """Redraw the preview with clock_win itself, so it is exactly what the
        panel will show (with example readings). Redraws never queue up: while
        one runs, only the newest request waits."""
        self._want_render = True
        if not self._rendering:
            self._start_render()

    def _start_render(self):
        self._want_render, self._rendering = False, True
        look = copy.deepcopy(self.look)
        item = self._view(look["playlist"][min(self.sel, len(look["playlist"]) - 1)])
        position = self._positions.get(item["id"], 0.0)
        kind, playing = self._kind(item), self._play_from is not None
        frames, length = self._frames_of(item), self._length(item)

        def work():
            try:
                picture, size, placed = self._preview_picture(item, kind, position, frames,
                                                              length, playing)
                img, layout = clock_win.preview(engine_argv(look, item), picture=picture,
                                                video=kind == "video")
                result = (img.resize((self.pw, self.ph), Image.LANCZOS), layout,
                          picture.size, size, item, placed)
            except BaseException as e:      # SystemExit carries the engine's message
                result = e
            self.root.after(0, lambda: self._rendered(result))

        threading.Thread(target=work, daemon=True).start()

    def _preview_picture(self, item, kind, position, frames, length, playing):
        """The picture the preview is drawn from: `item`'s, `position` (0 to 1)
        of the way along a GIF or video. Returns (picture, the original's
        size, whether it is placed on the screen's shape already -- a clip's
        pictures are). The last one is kept, since most redraws (a slider, a
        colour, typing) are of the same picture. Called off the window's thread."""
        path = item_path(item)
        if kind == "video" and not item.get("pending"):
            number = round(position * max(0, frames - 1))
            key = (path, number)
            if self._frame[0] != key:
                _, payload = self._video().call("clipframe", path=path, number=number,
                                                quality=85 if playing else 92)
                picture = Image.open(BytesIO(payload))
                picture.load()
                self._frame = (key, picture, picture.size)
            return self._frame[1], self._frame[2], True
        if kind == "video":
            # While it plays, half-size pictures keep it quick; paused, it is
            # drawn from what the conversion itself will use.
            seconds = round(position * max(0.0, length - 0.05), 2)
            limit = PLAY_SOURCE_LIMIT if playing else None
            key = (path, seconds, limit)
            if self._frame[0] != key:
                reply, payload = self._video().call("frame", path=path, seconds=seconds,
                                                    limit=limit, quality=85 if playing else 92)
                picture = Image.open(BytesIO(payload))
                picture.load()
                self._frame = (key, picture, tuple(reply["size"]))
            return self._frame[1], self._frame[2], False
        index = round(position * (frames - 1)) if frames > 1 else 0
        key = (path, index)
        if self._frame[0] != key:
            self._frame = (key, *self._picture_frame(path, index, frames > 1))
        return self._frame[1], self._frame[2], False

    def _picture_frame(self, path, index, animated):
        """Frame `index` of a picture file, as RGB, and the picture's own size.

        Big photos are scaled down: every placement is worked out in
        proportions, so the preview looks the same, and dragging stays quick.
        A GIF is kept open while its frames are looked at, since each frame
        is made from the ones before it; going forwards is then quick.
        """
        gif = self._gif
        if gif is None or gif[0] != path or index < gif[2]:
            if gif:
                gif[1].close()
            gif = [path, Image.open(path), 0]
        img = gif[1]
        img.seek(index)
        gif[2] = index
        frame = img.convert("RGB")
        if animated:
            self._gif = gif
        else:
            img.close()
            self._gif = None
        size = frame.size
        if max(size) > PREVIEW_SOURCE_LIMIT:
            frame.thumbnail((PREVIEW_SOURCE_LIMIT, PREVIEW_SOURCE_LIMIT), Image.LANCZOS)
        return frame, size

    def _rendered(self, result):
        self._rendering = False
        if self._want_render:
            self._start_render()            # the form changed meanwhile
        if isinstance(result, BaseException):
            self._layout = None
            self.canvas.itemconfigure(self.canvas_img, image="")
            self.canvas.itemconfigure(self.canvas_text, text=f"No preview:\n{result}")
            return
        img, layout, raw_size, size, item, placed = result
        self._layout, self._raw_size, self._placed = layout, raw_size, placed
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.itemconfigure(self.canvas_img, image=self._photo)
        self.canvas.itemconfigure(self.canvas_text, text="")
        if self._play_from is not None:
            return                          # playing: the texts beside it can wait
        # How much the original is enlarged on the screen (the picture drawn
        # from may be a scaled-down copy of it).
        scale = None if placed else (
            fit_geometry(raw_size, FIT_SIZE, item["fit"], item["zoom"],
                         (item["pan_x"], item["pan_y"]))[0] * max(raw_size) / max(size))
        self._show_picture_info(item, size, scale)
        self._show_space(layout)
        self._show_limits(layout)

    def _show_limits(self, layout):
        """Size sliders stop at the largest text that fits; automatic sizes
        and positions show what the engine chose. A hidden block's sliders
        are left as they were: there is nothing on screen to measure."""
        fonts = {"time": layout.time_font, "date": layout.date_font,
                 "label": layout.label_font, "value": layout.value_font}
        for part, font in fonts.items():
            slider = getattr(self, f"{part}_size")
            limit = layout.limits.get(part)
            if limit is None or (part in ("label", "value") and not layout.rows):
                continue
            slider.set_range(8, max(9, limit))
            size = self.look[f"{part}_size"]
            if size is None:
                slider.set(font.size, " auto")
            elif not self._drag:
                slider.set(min(size, limit))
        extras = {**{b: self.block_size[b] for b in EXTRA_BLOCKS},
                  "calendar": self.calendar_size}
        for part, slider in extras.items():
            limit, actual = layout.limits.get(part), layout.sizes.get(part)
            if limit is None or actual is None:
                continue                    # hidden: nothing to measure
            slider.set_range(8, max(9, limit))
            size = self.look[f"{part}_size"]
            if size is None:
                slider.set(actual, " auto")
            elif not self._drag:
                slider.set(min(size, limit))
        # Every shown block's height slider shows where it sits now.
        heights = {"clock": self.clock_pos, "stats": self.stats_pos, **self.block_pos}
        for block, pos in layout.positions.items():
            heights[block].set(pos)
        self.stats_warn.configure(
            text="The stats are taller than the screen: make the text smaller or "
                 "show fewer." if layout.overflow else "")
        boxes = sorted(layout.boxes.values(), key=lambda box: box[1])
        overlap = any(a[3] > b[1] for a, b in zip(boxes, boxes[1:]))
        self.preview_hint.configure(text=OVERLAP_HINT if overlap else PREVIEW_HINT,
                                    style="Note.TLabel" if overlap else "Hint.TLabel")

    # Dragging on the preview moves a text block (grabbed by its panel) or,
    # anywhere else, the picture. Coordinates are converted to panel pixels.

    def _target(self, event):
        layout = self._layout
        if not layout:
            return None
        x, y = event.x / self.k, event.y / self.k
        target = None
        for block, box in layout.boxes.items():     # the last one drawn wins
            if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                target = (block, box)
        return target or ("picture", None)

    def _movable(self):
        """Can the picture in the preview be dragged and zoomed? Not a clip
        whose video is no longer at hand: its placement is part of it."""
        return bool(self._raw_size) and not self._placed and self._can_frame(self._item())

    def _hover(self, event):
        target = self._target(event)
        cursor = ""
        if target and target[0] != "picture":
            cursor = "sb_v_double_arrow"
        elif target and self._movable():
            cursor = "fleur"
        self.canvas.configure(cursor=cursor)

    def _press(self, event):
        target = self._target(event)
        if not target:
            return
        block, box = target
        if block == "picture":
            if self._movable():
                item = self._item()
                self._drag = ("picture", event.x, event.y, item["pan_x"], item["pan_y"])
        else:
            start = self._layout.positions[block]
            travel = FIT_SIZE[1] - 2 * box[0] - (box[3] - box[1])
            self._drag = (block, event.x, event.y, start, travel)

    def _motion(self, event):
        if not self._drag or not self._raw_size:
            return
        kind, x0, y0, a, b = self._drag
        dx, dy = (event.x - x0) / self.k, (event.y - y0) / self.k
        if kind == "picture":
            item = self._item()
            _, _, _, width, height = fit_geometry(self._raw_size, FIT_SIZE, item["fit"],
                                                  item["zoom"], (a, b))
            # pan 0..1 slides the picture across its overhang (or its gap).
            pan_x, pan_y = item["pan_x"], item["pan_y"]
            if abs(FIT_SIZE[0] - width) > 0.5:
                pan_x = round(_clamp(a + dx / (FIT_SIZE[0] - width)), 4)
            if abs(FIT_SIZE[1] - height) > 0.5:
                pan_y = round(_clamp(b + dy / (FIT_SIZE[1] - height)), 4)
            if (pan_x, pan_y) != (item["pan_x"], item["pan_y"]) and self._reopen(item):
                item.update(pan_x=pan_x, pan_y=pan_y)
                self.changed()
        elif b > 0:
            self._set(f"{kind}_pos", round(_clamp(a + dy / b), 3))

    def _wheel(self, event):
        """Zoom, keeping the spot under the pointer where it is."""
        if not self._movable():
            return
        item = self._item()
        old = item["zoom"]
        zoom = min(MAX_ZOOM, max(1.0, old * 1.1 ** (event.delta / 120)))
        if zoom == old:
            return
        pan = (item["pan_x"], item["pan_y"])
        cx, cy = event.x / self.k, event.y / self.k
        scale, x, y, _, _ = fit_geometry(self._raw_size, FIT_SIZE, item["fit"], old, pan)
        u, v = (cx - x) / scale, (cy - y) / scale
        scale, _, _, width, height = fit_geometry(self._raw_size, FIT_SIZE, item["fit"], zoom, pan)
        if not self._reopen(item):
            return
        if abs(FIT_SIZE[0] - width) > 0.5:
            item["pan_x"] = round(_clamp((cx - u * scale) / (FIT_SIZE[0] - width)), 4)
        if abs(FIT_SIZE[1] - height) > 0.5:
            item["pan_y"] = round(_clamp((cy - v * scale) / (FIT_SIZE[1] - height)), 4)
        item["zoom"] = round(zoom, 3)
        self.changed()

    # -- font ---------------------------------------------------------------------

    def choose_font(self):
        self.fonts.start()
        if not self.fonts.ready.wait(5):
            messagebox.showinfo(DISPLAY_NAME, "Still reading the fonts; try again "
                                "in a moment.", parent=self.root)
            return
        picker = FontPicker(self.root, self.fonts, self.look["font"] or DEFAULT_FONT)
        self.root.wait_window(picker.top)
        if picker.result:
            self._set("font", None if picker.result == DEFAULT_FONT else picker.result)

    # -- apply, undo, reset -------------------------------------------------------

    def reset(self):
        """Everything back to how it comes, except what the user chose or
        wrote: the playlist (the pictures, and how each is placed and timed)
        and the text, the countdown and the to-do list stay (their blocks are
        switched off, as they come)."""
        kept = {key: self.look[key] for key in CONTENT_KEYS}
        # The playlist is put back as it is: it may hold files not applied
        # yet, which clean_look (rightly, for the service) would not accept.
        self.look = clean_look({key: value for key, value in kept.items()
                                if not key.startswith("playlist")})
        self.look.update({key: value for key, value in kept.items()
                          if key.startswith("playlist")})
        self._custom_date = False
        self._show_look()
        self._place_spots()
        self.changed()

    def undo(self):
        self.dirty = False
        self._form_key = None
        self._load_form(self.app.status)

    def _apply_work(self):
        """What Apply does, with the look as it is now, so that later edits
        can't change it: the videos are converted, the new files sent to the
        service, and then the settings applied. Returns work(report), where
        report(what, done, total) is told how far it has got."""
        look = copy.deepcopy(self.look)
        cancel = self._cancel = threading.Event()
        sources = self._sources

        def work(report):
            items = look["playlist"]
            videos = [item for item in items if item.get("pending") == "video"]
            pictures = [item for item in items if item.get("pending") == "picture"]
            folder = tempfile.mkdtemp(prefix="crystalx-lcd-") if videos else None
            try:
                # Every video is converted before anything is sent, so the
                # files reach the service together, just before they are applied.
                clips = []
                for number, item in enumerate(videos, 1):
                    what = f"Converting {item['label']}" + (
                        f"  ({number} of {len(videos)})" if len(videos) > 1 else "")
                    report(what, 0, 0)
                    clip = os.path.join(folder, f"{number}{video_win.CLIP_EXT}")
                    video_win.convert_in_worker(
                        item["path"], clip, item["fit"], item["fit_color"], item["zoom"],
                        (item["pan_x"], item["pan_y"]), item["max_fps"], cancel=cancel,
                        progress=lambda done, total, what=what: report(what, done, total))
                    clips.append((item, clip))
                for item, path, kind in ([(item, clip, "clip") for item, clip in clips]
                                         + [(item, item["path"], "picture") for item in pictures]):
                    what = f"Sending {item['label']}"
                    reply = ipc_win.upload(
                        send_settings, path, kind, cancel=cancel,
                        progress=lambda done, total, what=what: report(what, done, total))
                    if kind == "clip":
                        sources[reply["file"]] = {key: item.get(key)
                                                  for key in ("path", "source_fps", "hdr")}
                    item.update(file=reply["file"], frames=reply["frames"],
                                duration=reply["duration"])
                for item in items:
                    for key in WINDOW_KEYS:
                        item.pop(key, None)
                if cancel.is_set():
                    raise video_win.Cancelled()
                report("Applying", 1, 1)
                send_settings("apply", look=look)
            finally:
                if folder:
                    shutil.rmtree(folder, ignore_errors=True)
        return work

    def apply(self):
        self._send(self._apply_work(), "apply the settings", applied=True, files=True)

    def _send(self, work, what, applied=False, files=False):
        """Run work(report) off the window's thread, then show what the
        service has. When the work has files to convert or send (`files`, and
        the playlist holds some that are new), a progress window shows how
        far it is and lets it be cancelled."""
        self.stop_play()
        waiting = files and any(item.get("pending") for item in self.look["playlist"])
        self._set_busy(True)
        report = self._open_progress() if waiting else (lambda *args: None)

        def done(error):
            self._close_progress()
            if not error:
                # Show what the service saved straight away, rather than waiting
                # for the next status poll (which would first show the old one).
                self.app.status = {**self.app.status, **load_config(),
                                   "layouts": layout_names()}
            self._set_busy(False)
            if isinstance(error, (video_win.Cancelled, ipc_win.UploadCancelled)):
                return                      # cancelled: everything is as it was
            if error:
                self.app.refresh_soon()
                messagebox.showerror(DISPLAY_NAME, f"Could not {what}:\n\n{error}",
                                     parent=self.root)
                return
            if applied:
                self.dirty = False
                self._form_key = None
                self._frame = (None, None, None)
                self._load_form(self.app.status)
            self._show_layouts(self.app.status["layouts"])
            self.app.refresh_soon()

        self.app.background(lambda: work(report), done)

    def _open_progress(self):
        """The window shown while videos are converted and files sent; returns
        the report(what, done, total) that moves its bar."""
        top = tk.Toplevel(self.root)
        top.title(DISPLAY_NAME)
        top.transient(self.root)
        top.resizable(False, False)
        frame = ttk.Frame(top, padding=14)
        frame.pack()
        text = ttk.Label(frame, text="Getting ready...", width=52, anchor="w")
        text.pack(anchor="w")
        bar = ttk.Progressbar(frame, length=340, maximum=1000, mode="indeterminate")
        bar.pack(pady=(8, 0), fill="x")
        bar.start(40)
        ttk.Label(frame, text="The case screen keeps showing what it shows now until "
                              "this is done. Converting a long video takes a few minutes.",
                  style="Hint.TLabel", wraplength=340, justify="left").pack(anchor="w", pady=(8, 0))
        button = ttk.Button(frame, text="Cancel", command=self._cancel_work)
        button.pack(anchor="e", pady=(10, 0))
        top.protocol("WM_DELETE_WINDOW", self._cancel_work)
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - top.winfo_reqheight()) // 3
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        try:
            top.grab_set()                  # the main window waits meanwhile
        except tk.TclError:
            pass                            # the window was hidden just then
        self._progress = {"top": top, "text": text, "bar": bar, "button": button,
                          "moving": True}
        last = [0.0]

        def report(what, done, total):
            # From the worker's thread, and often: ten updates a second are plenty.
            now = time.monotonic()
            if done < total and now - last[0] < 0.1:
                return
            last[0] = now
            self.root.after(0, lambda: self._show_progress(what, done, total))
        return report

    def _show_progress(self, what, done, total):
        p = self._progress
        if not p or (self._cancel is not None and self._cancel.is_set()):
            return
        if total:
            if p["moving"]:
                p["bar"].stop()
                p["bar"].configure(mode="determinate")
                p["moving"] = False
            p["bar"].configure(value=1000 * done / total)
            p["text"].configure(text=f"{what}  -  {100 * done // total}%")
        else:
            if not p["moving"]:
                p["bar"].configure(mode="indeterminate")
                p["bar"].start(40)
                p["moving"] = True
            p["text"].configure(text=what)

    def _cancel_work(self):
        if self._cancel is not None:
            self._cancel.set()
        if self._progress:
            self._progress["text"].configure(text="Cancelling...")
            self._progress["button"].state(["disabled"])

    def _close_progress(self):
        p, self._progress = self._progress, None
        if p:
            p["bar"].stop()
            p["top"].grab_release()
            p["top"].destroy()

    # -- saved layouts --------------------------------------------------------------

    def load_layout(self):
        name = self.selected_layout()
        if not name:
            return
        if self.dirty and not messagebox.askyesno(
                DISPLAY_NAME, f"Load \"{name}\"? Your changes that are not applied "
                              "yet will be lost.", parent=self.root):
            return
        self._send(lambda report: send_settings("load_layout", name=name),
                   f"load \"{name}\"", applied=True)

    def save_layout(self):
        name = simpledialog.askstring(DISPLAY_NAME, "Name for this layout:",
                                      initialvalue=self.selected_layout() or "",
                                      parent=self.root)
        name = (name or "").strip()
        if not name:
            return
        if len(name) > MAX_LAYOUT_NAME:
            messagebox.showerror(DISPLAY_NAME, f"Use at most {MAX_LAYOUT_NAME} characters.",
                                 parent=self.root)
            return
        if name.lower() in (n.lower() for n in self._layouts or []) and not messagebox.askyesno(
                DISPLAY_NAME, f"Replace the layout \"{name}\"?", parent=self.root):
            return
        # A layout saves what the screen shows, so unapplied changes go first.
        apply_first = self._apply_work() if self.dirty else None

        def work(report):
            if apply_first:
                apply_first(report)
            send_settings("save_layout", name=name)
        self._send(work, f"save \"{name}\"", applied=bool(apply_first),
                   files=bool(apply_first))

    def delete_layout(self):
        name = self.selected_layout()
        if name and messagebox.askyesno(DISPLAY_NAME, f"Delete the layout \"{name}\"?",
                                        parent=self.root):
            self._send(lambda report: send_settings("delete_layout", name=name),
                       f"delete \"{name}\"")

    def set_rotation(self):
        """Switch the rotation of saved layouts, or change how often. Like
        "Start display with Windows", it takes effect at once."""
        try:
            minutes = int(self.rotate_minutes.get())
        except ValueError:
            minutes = self._rotation["minutes"]
        minutes = max(ROTATION_MINUTES[0], min(ROTATION_MINUTES[1], minutes))
        rotation = {"enabled": self.rotate.get(), "minutes": minutes}
        if self.busy:                       # not now: show what the service has
            self.rotate.set(self._rotation["enabled"])
            self.rotate_minutes.set(str(self._rotation["minutes"]))
            return
        if rotation == self._rotation:
            self.rotate_minutes.set(str(minutes))
            return
        self.rotate_minutes.set(str(minutes))
        self._rotation = rotation
        self._send(lambda report: send_settings("set_rotation", rotation=rotation),
                   "change the layout rotation")

    # -- warning colours ----------------------------------------------------------------

    def edit_warnings(self):
        dialog = WarningsDialog(self.root, self.look)
        self.root.wait_window(dialog.top)
        if dialog.result is not None:
            self.look.update(dialog.result)
            self.changed()

    # -- display and options ----------------------------------------------------------

    def _set_busy(self, busy):
        self.busy = busy
        self._update_controls()
        self.refresh(self.app.status)

    def _run(self, work, what):
        self._set_busy(True)

        def done(error):
            self._set_busy(False)
            self.app.refresh_soon()
            if error:
                messagebox.showerror(DISPLAY_NAME, f"Could not {what}:\n\n{error}",
                                     parent=self.root)

        self.app.background(work, done)

    def start_display(self):
        self._run(start_service, "start the display")

    def stop_display(self):
        self._run(stop_service, "stop the display")

    def toggle_autostart(self):
        on = self.autostart.get()
        self._run(lambda: send_settings("set_autostart", on=on),
                  "change Start display with Windows")

    def open_log(self):
        if os.path.exists(LOG_FILE):
            os.startfile(LOG_FILE)
        else:
            messagebox.showinfo(DISPLAY_NAME, "There is no log yet.", parent=self.root)


def _clamp(value):
    return min(1.0, max(0.0, value))


def date_examples(today):
    """`today` written in each ready-made pattern, for the dropdown. From
    the 10th on, the day reads the same with and without its leading zero, so
    the pattern without one says which it is."""
    examples = [today.strftime(date_strftime(pattern)) for pattern in DATE_PRESETS]
    if examples[1] == examples[0]:
        examples[1] += "  (5, not 05)"
    return examples


def _as_date(text):
    return datetime.datetime.strptime(text, "%Y-%m-%d").date()


def _date_text(date):
    """A date as the countdown's button shows it: Fri 25 Dec 2026."""
    return (f"{calendar.day_abbr[date.weekday()]} {date.day:02d} "
            f"{calendar.month_abbr[date.month]} {date.year}")


def _clock(seconds):
    """A length of time as m:ss."""
    seconds = max(0, round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class WarningsDialog:
    """Which stats change colour when they get high, at what levels, and the
    two colours. `result` is the settings to put into the look, or None if it
    was cancelled."""

    def __init__(self, parent, look):
        self.result = None
        self.top = tk.Toplevel(parent)
        self.top.title("Warning colours")
        self.top.transient(parent)
        self.top.resizable(False, False)
        frame = ttk.Frame(self.top, padding=14)
        frame.pack()
        ttk.Label(frame, text="A stat's number turns the warning colour when it reaches the "
                              "first level, and the critical colour at the second.",
                  wraplength=330, justify="left").grid(row=0, column=0, columnspan=4,
                                                       sticky="w", pady=(0, 8))
        for column, text in ((1, "Warning"), (2, "Critical")):
            ttk.Label(frame, text=text, style="Hint.TLabel").grid(row=1, column=column, padx=4)
        self.rows = {}
        names = dict((key, text) for key, text in STAT_CHOICES if key)
        for r, (slot, suggested) in enumerate(clock_win.WARN_SLOTS.items(), 2):
            levels = look["warn"].get(slot)
            on = tk.BooleanVar(value=levels is not None)
            warn = tk.StringVar(value=f"{(levels or suggested)[0]:g}")
            crit = tk.StringVar(value=f"{(levels or suggested)[1]:g}")
            shown = slot in look["slots"]
            ttk.Checkbutton(frame, text=names[slot] + ("" if shown else "  (not shown)"),
                            variable=on).grid(row=r, column=0, sticky="w", pady=1)
            for column, var in ((1, warn), (2, crit)):
                ttk.Spinbox(frame, width=5, from_=0, to=1000, textvariable=var).grid(
                    row=r, column=column, padx=4)
            ttk.Label(frame, text="°C" if slot.endswith("temp") else "%").grid(
                row=r, column=3, sticky="w")
            self.rows[slot] = (on, warn, crit)
        colours = ttk.Frame(frame)
        colours.grid(row=20, column=0, columnspan=4, sticky="w", pady=(10, 0))
        self.warn_swatch = Swatch(colours, "Warning colour", lambda c: None)
        self.crit_swatch = Swatch(colours, "Critical colour", lambda c: None)
        self.warn_swatch.set(look["warn_color"])
        self.crit_swatch.set(look["crit_color"])
        for text, swatch in (("Warning colour", self.warn_swatch),
                             ("Critical colour", self.crit_swatch)):
            ttk.Label(colours, text=text).pack(side="left")
            swatch.pack(side="left", padx=(6, 14))
        self.problem = ttk.Label(frame, style="Note.TLabel", wraplength=330, justify="left")
        self.problem.grid(row=21, column=0, columnspan=4, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=22, column=0, columnspan=4, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Cancel", command=self.top.destroy).pack(side="right")
        ttk.Button(buttons, text="OK", command=self._done).pack(side="right", padx=6)
        self.top.wait_visibility()
        self.top.grab_set()                 # modal: the main window waits

    def _done(self):
        warn = {}
        for slot, (on, low, high) in self.rows.items():
            if not on.get():
                continue
            try:
                levels = [float(low.get()), float(high.get())]
            except ValueError:
                levels = None
            if not levels or not 0 <= levels[0] <= levels[1] <= 1000:
                self.problem.configure(text="Each level is a number, and the critical "
                                            "level is not below the warning level.")
                return
            warn[slot] = levels
        self.result = {"warn": warn, "warn_color": self.warn_swatch.color,
                       "crit_color": self.crit_swatch.color}
        self.top.destroy()


# -- the app ---------------------------------------------------------------------

class App:
    def __init__(self, show):
        self.root = tk.Tk()
        self.root.withdraw()
        try:
            self.root.iconbitmap(default=ICON)
        except tk.TclError:
            pass
        self.actions = queue.Queue()
        self.status = {"installed": True}
        self.quitting = False
        self.wake = threading.Event()
        self.window = MainWindow(self)
        self.icon = TrayIcon(self)
        self.show_at_start = show

    def run(self):
        self.icon.start()
        threading.Thread(target=self._poll, name="status", daemon=True).start()
        self.root.after(100, self._pump)
        self.root.after(500, self._tick)
        if self.show_at_start:
            self.window.show()
        self.root.mainloop()

    # Actions arrive from the icon thread; windows must be touched on this one.
    def post(self, item_id):
        self.actions.put(item_id)

    def _pump(self):
        while not self.actions.empty():
            item_id = self.actions.get()
            if item_id == ID_SHOW:
                self.window.show()
            elif item_id == ID_QUIT:
                self.quit()
        self.root.after(100, self._pump)

    def _poll(self):
        while not self.quitting:
            try:
                self.status = read_status(settings=self.window.visible)
            except Exception as e:
                self.status = {"installed": True, "scm": None, "error": str(e)}
            self.icon.set_tip(f"{DISPLAY_NAME} - {describe(self.status)[0]}")
            self.wake.wait(POLL_SECONDS)
            self.wake.clear()

    def _tick(self):
        # The app spends nearly all its time hidden in the tray; then only the
        # tooltip is kept up to date, and the window is left alone.
        if not self.quitting:
            if self.window.visible:
                self.window.refresh(self.status)
            self.root.after(500, self._tick)

    def refresh_soon(self):
        self.wake.set()

    def background(self, work, done):
        """Run `work` off the UI thread; call done(error or None) back on it."""
        def run():
            try:
                work()
                error = None
            except Exception as e:
                error = e
            self.root.after(0, lambda: done(error))
        threading.Thread(target=run, daemon=True).start()

    def about(self):
        if messagebox.askyesno(
                DISPLAY_NAME,
                f"{DISPLAY_NAME} {VERSION}\n\n"
                "Shows a clock, the date and PC stats on the screen of a "
                "CrystalX CoreView case.\n\n"
                f"Made by {AUTHOR}.\nFree software under the GNU GPL v3.0.\n\n"
                "Open the project page on GitHub?", parent=self.root):
            webbrowser.open(GITHUB)

    def quit(self):
        """Stop the display and close the app."""
        self.quitting = True
        if self.window._cancel is not None:
            self.window._cancel.set()       # a conversion or upload under way ends here

        def work():
            video_win.kill_all()
            # Give the cancelled work a moment to clear up after itself (a
            # half-made clip in the temporary folder).
            deadline = time.monotonic() + 5
            while self.window.busy and time.monotonic() < deadline:
                time.sleep(0.05)
            if service_state() not in (None, win32service.SERVICE_STOPPED):
                stop_service()

        def done(error):
            if error:
                messagebox.showerror(DISPLAY_NAME, f"Could not stop the display:\n\n{error}")
            self.window.release()
            self.icon.close()
            self.root.quit()

        self.background(work, done)


def main():
    if "--video-worker" in sys.argv[1:]:
        # Not the app, but its helper: the same program started by the window
        # to read and convert videos, so that FFmpeg never loads into the
        # window itself (see video_win.Worker).
        video_win.worker_main()
        return
    hidden = "--hidden" in sys.argv[1:]
    # One app per user session. A second launch -- say from the Start menu --
    # asks the running one to show its window, then exits.
    mutex = win32event.CreateMutex(None, False, "Local\\CrystalXLCDTray")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        if not hidden:
            hwnd = win32gui.FindWindow(TRAY_CLASS, None)
            if hwnd:
                win32gui.PostMessage(hwnd, WM_SHOW_APP, 0, 0)
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # crisp text on high-DPI screens
    except (AttributeError, OSError):
        pass
    App(show=not hidden).run()
    del mutex


if __name__ == "__main__":
    main()
