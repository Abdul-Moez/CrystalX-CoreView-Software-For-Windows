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
then tabs for the picture (fill or fit, drag and zoom), the clock and date,
the stats, the font and frosted panel, and options (start with Windows,
saved layouts). The preview beside them is drawn by the engine itself. The
tray icon only has Show and Quit; clicking it opens the window, and closing
the window hides it back to the tray.

  Start / Stop / Quit   start or stop the service through Windows' service
                        manager. Stopped, the screen is free for other apps.
                        Quit also closes the app.
  settings              go to the service over the pipe (ipc_win.py). When
                        the display is off, the service is started with
                        "--idle" just long enough to take them.

  CrystalXLCD.exe            open the window (from the Start menu)
  CrystalXLCD.exe --hidden   start in the tray only (at login)
"""

import base64
import copy
import ctypes
import datetime
import json
import os
import queue
import sys
import threading
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
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
from clock_win import MAX_SPOTS, SLOTS, date_strftime
from ipc_win import (DEFAULT_PICTURE, DISPLAY_NAME, LOG_FILE, MAX_FRAMES,
                     MAX_PICTURE_BYTES, SERVICE_NAME, VERSION, app_dir)
from lcd_win import FIT_SIZE, MAX_ZOOM, fit_geometry
from settings_win import (DEFAULT_FONT, DEFAULT_LOOK, FONT_TYPES, FONTS_DIR,
                          MAX_DATE_PATTERN, MAX_LABEL, MAX_LAYOUT_NAME,
                          MAX_LAYOUTS, clean_look, engine_argv, list_layouts,
                          load_config, picture_path)

AUTHOR = "Abdul Moez"
GITHUB = "https://github.com/Abdul-Moez/CrystalX-CoreView-Software-For-Windows"
ICON = os.path.join(app_dir(), "assets", "crystalx-lcd.ico")
DEFAULT_PICTURE_PATH = os.path.join(app_dir(), DEFAULT_PICTURE)
DEFAULT_PICTURE_NAME = "Default (rooftop GIF)"
PICTURE_FORMATS = ("GIF", "PNG", "JPEG", "WEBP", "BMP")
PICTURE_TYPES = [("Pictures", "*.gif *.png *.jpg *.jpeg *.webp *.bmp"),
                 ("All files", "*.*")]
POLL_SECONDS = 3
PREVIEW_SCALE = 0.45
# The preview scales bigger photos down to this before placing them: plenty
# for a preview under half the panel's size, even zoomed in.
PREVIEW_SOURCE_LIMIT = 2048
WRAP = 300                      # width of wrapped text on the right, in px
PREVIEW_HINT = "Drag to move - scroll to zoom"
OVERLAP_HINT = "Clock and stats overlap"

TIME_CHOICES = [("12h", "12-hour  (3:25 PM)"),
                ("12h-plain", "12-hour, no AM/PM  (3:25)"),
                ("24h", "24-hour  (15:25)")]
# Date patterns offered ready-made (codes: clock_win.DATE_CODES).
DATE_PRESETS = ["(ddd) D-MMM-YYYY", "YYYY-MMM-DD (ddd)", "ddd D MMM YYYY",
                "ddd, MMM D YYYY", "dddd, D MMMM", "D MMMM YYYY", "ddd D MMM",
                "DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD"]
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
                          temps=live.get("temps"))
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


def check_picture_file(path):
    """The same checks the service makes, so problems show up before sending."""
    size = os.path.getsize(path)
    if size > MAX_PICTURE_BYTES:
        raise ValueError(f"The file is {size / 2**20:.0f} MB; the limit is "
                         f"{MAX_PICTURE_BYTES // 2**20} MB.")
    try:
        with Image.open(path) as img:
            kind, frames = img.format, getattr(img, "n_frames", 1)
    except Exception as e:
        raise ValueError("That file is not a picture this app can show.") from e
    if kind not in PICTURE_FORMATS:
        raise ValueError(f"{kind} pictures are not supported. Use GIF, PNG, "
                         "JPEG, WEBP or BMP.")
    if frames > MAX_FRAMES:
        raise ValueError(f"The GIF has {frames} frames; the limit is {MAX_FRAMES}.")


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


def _px(value):
    return f"{round(value)} px"


def _height(value):
    return "top" if value <= 0.005 else "bottom" if value >= 0.995 else f"{value * 100:.0f}%"


def _enable(widgets, on):
    for widget in widgets:
        if isinstance(widget, Slider):
            widget.enable(on)
        elif isinstance(widget, tk.Button):
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
        self.chosen = None                  # a new file the user picked
        self.use_default = False
        self._current = DEFAULT_PICTURE_PATH
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
        self._raw = (None, None, None)      # (path, first frame, original size)
        self._raw_size = None
        self._drag = None
        self._custom_date = False           # "Custom pattern" chosen for the date
        self._controls_key = None           # what _update_controls last acted on
        self._status_key = None             # what refresh last showed
        self.visible = False
        self.fonts = FontCatalog()
        self.picture_name = tk.StringVar(value="")
        self.autostart = tk.BooleanVar(value=False)
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
        ttk.Label(left, text="Example readings", style="Hint.TLabel").pack(pady=(6, 0))
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
                       if self.tabs.index("current") == 3 else None)
        self._build_picture_tab()
        self._build_clock_tab()
        self._build_stats_tab()
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
        tab = self._tab("Picture")
        ttk.Label(tab, textvariable=self.picture_name, wraplength=WRAP).pack(anchor="w")
        row = ttk.Frame(tab)
        row.pack(anchor="w", pady=(6, 6))
        self.choose_btn = ttk.Button(row, text="Choose file...", command=self.choose)
        self.choose_btn.pack(side="left")
        self.default_btn = ttk.Button(row, text="Use default", command=self.pick_default)
        self.default_btn.pack(side="left", padx=6)
        self.picture_info = self._fixed_label(tab, 2, WRAP, style="Hint.TLabel",
                                              wraplength=WRAP, justify="left")
        # For anyone making a picture to fit the gap between the text blocks.
        self.space_info = self._fixed_label(tab, 2, WRAP, style="Hint.TLabel",
                                            justify="left")
        self.picture_warn = self._fixed_label(tab, 2, WRAP, style="Note.TLabel",
                                              wraplength=WRAP, justify="left")

        self.mode = tk.StringVar(value="fill")
        self.edge = tk.StringVar(value="blur")
        ttk.Radiobutton(tab, text="Fill - crop to cover the whole screen", variable=self.mode,
                        value="fill", command=self._fit_changed).pack(anchor="w", pady=(10, 0))
        ttk.Radiobutton(tab, text="Fit - show the whole picture", variable=self.mode,
                        value="fit", command=self._fit_changed).pack(anchor="w")
        sub = ttk.Frame(tab)
        sub.pack(anchor="w", padx=(22, 0))
        self.blur_radio = ttk.Radiobutton(sub, text="Blurred edges", variable=self.edge,
                                          value="blur", command=self._fit_changed)
        self.blur_radio.grid(row=0, column=0, sticky="w")
        self.color_radio = ttk.Radiobutton(sub, text="Solid colour", variable=self.edge,
                                           value="color", command=self._fit_changed)
        self.color_radio.grid(row=1, column=0, sticky="w")
        self.fit_swatch = Swatch(sub, "Edge colour", lambda c: self._set("fit_color", c))
        self.fit_swatch.grid(row=1, column=1, padx=8)

        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(12, 0))
        ttk.Label(row, text="Zoom", width=6).pack(side="left")
        self.zoom = Slider(row, 1.0, MAX_ZOOM, lambda v: f"{v * 100:.0f}%",
                           lambda v: self._set("zoom", round(v, 3)))
        self.zoom.pack(side="left")
        ttk.Label(tab, text="Drag the preview to move the picture, and scroll on it "
                            "to zoom.", style="Hint.TLabel", wraplength=WRAP,
                  justify="left").pack(anchor="w", pady=(6, 0))
        ttk.Button(tab, text="Reset position", command=self.recentre).pack(anchor="w", pady=(6, 0))

    def _build_clock_tab(self):
        tab = self._tab("Clock")
        self.show_time = tk.BooleanVar()
        self.show_date = tk.BooleanVar()
        self.seconds = tk.BooleanVar()

        frame = ttk.LabelFrame(tab, padding=(10, 4, 10, 8), labelwidget=ttk.Checkbutton(
            tab, text="Time", variable=self.show_time,
            command=lambda: self._set("show_time", self.show_time.get())))
        frame.pack(fill="x")
        self.time_format = ttk.Combobox(frame, state="readonly", width=26,
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
        self.date_format = ttk.Combobox(frame, state="readonly", width=26)
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
        ttk.Checkbutton(tab, text="Show the stats", variable=self.show_stats,
                        command=lambda: self._set("show_stats", self.show_stats.get())
                        ).pack(anchor="w", pady=(0, 6))
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
        frame = ttk.LabelFrame(tab, text=" Font ", padding=10)
        frame.pack(fill="x")
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
        ttk.Label(frame, text=f"A layout keeps everything on the screen, its picture "
                              f"too. Up to {MAX_LAYOUTS}.",
                  style="Hint.TLabel", wraplength=WRAP, justify="left").pack(anchor="w", pady=(8, 0))

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
        self.dirty = True
        self._show_look()
        self._update_controls()
        self.render_preview()

    def _show_look(self):
        """Put self.look into every control."""
        look = self.look
        self._loading = True
        try:
            fit = look["fit"]
            self.mode.set("fill" if fit == "fill" else "fit")
            if fit != "fill":
                self.edge.set(fit)
            self.fit_swatch.set(look["fit_color"])
            self.zoom.set(look["zoom"])
            self.show_time.set(look["show_time"])
            self.show_date.set(look["show_date"])
            self.seconds.set(look["seconds"])
            self.time_format.current([k for k, _ in TIME_CHOICES].index(look["time_format"]))
            today = datetime.date.today()
            examples = [today.strftime(date_strftime(p)) for p in DATE_PRESETS]
            self.date_format.configure(values=examples + [CUSTOM_DATE])
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
            self.show_stats.set(look["show_stats"])
            self.frost_dark.set(look["frost_dark"])
            self.frost_blur.set(look["frost_blur"])
            self._show_font()
        finally:
            self._loading = False

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
        key = (look["fit"], look["show_time"], look["show_date"], custom,
               tuple(bool(s) for s in look["slots"]), look["show_stats"], look["frost"],
               self.dirty,
               self.installed, self.busy, self.layout_list.curselection())
        if key == self._controls_key:
            return
        self._controls_key = key
        fit = look["fit"] != "fill"
        _enable([self.blur_radio, self.color_radio], fit)
        _enable([self.fit_swatch], fit and look["fit"] == "color")
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
                 self.stats_pos], any_stats)
        _enable([self.frost_dark, self.frost_blur], look["frost"])
        settled = self.installed and not self.busy
        _enable([self.apply_btn, self.undo_btn], self.dirty and settled)
        _enable([self.reset_btn, self.choose_btn, self.default_btn, self.save_btn], settled)
        chosen = bool(self.layout_list.curselection())
        _enable([self.load_btn, self.delete_btn], chosen and settled)

    def _fit_changed(self):
        self._set("fit", "fill" if self.mode.get() == "fill" else self.edge.get())

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
        # Let go of the picture the preview was made from (a big photo is
        # megabytes); it is read again the next time the window opens.
        self._raw = (None, None, None)

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
            _enable([self.auto_check], self.installed and not self.busy)
            if autostart is not None:
                self.autostart.set(bool(autostart))
        if "layouts" in s:
            self._show_layouts(s["layouts"])
        if self.installed and not self.dirty and "fit" in s:
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
        config = {"picture": s.get("picture"), "picture_label": s.get("picture_label"),
                  **{key: s[key] for key in DEFAULT_LOOK if key in s}}
        key = json.dumps(config, sort_keys=True)
        if key == self._form_key:
            return
        self._form_key = key
        self.look = clean_look(config)
        self._custom_date = self.look["date_format"] not in DATE_PRESETS
        self.chosen, self.use_default = None, False
        self.picture_name.set((s.get("picture_label") or "Your picture")
                              if s.get("picture") else DEFAULT_PICTURE_NAME)
        self._current = picture_path(config)
        self._show_look()
        self._place_spots()
        self._update_controls()
        self.render_preview()

    # -- the picture ----------------------------------------------------------

    def source(self):
        if self.chosen:
            return self.chosen
        if self.use_default:
            return DEFAULT_PICTURE_PATH
        return self._current

    def choose(self):
        path = filedialog.askopenfilename(parent=self.root, title="Choose a picture",
                                          filetypes=PICTURE_TYPES)
        if not path:
            return
        try:
            check_picture_file(path)
        except ValueError as e:
            messagebox.showerror(DISPLAY_NAME, str(e), parent=self.root)
            return
        self.chosen, self.use_default = path, False
        self.picture_name.set(os.path.basename(path))
        # A new picture starts centred.
        self.look.update(zoom=1.0, pan_x=0.5, pan_y=0.5)
        self.changed()

    def pick_default(self):
        self.chosen, self.use_default = None, True
        self.picture_name.set(DEFAULT_PICTURE_NAME)
        self.look.update(zoom=1.0, pan_x=0.5, pan_y=0.5)
        self.changed()

    def recentre(self):
        self.look.update(zoom=1.0, pan_x=0.5, pan_y=0.5)
        self.changed()

    def _show_picture_info(self, size, scale):
        """The picture's own size, the best size, and a warning if it is
        being enlarged enough to look soft."""
        self.picture_info.configure(
            text=f"This picture: {size[0]} × {size[1]} px\n"
                 f"Best size: {FIT_SIZE[0]} × {FIT_SIZE[1]} px, or larger in the "
                 "same tall shape.")
        custom = self.source() != DEFAULT_PICTURE_PATH
        self.picture_warn.configure(
            text=f"Shown {scale:.1f}× larger than it is, so it may look soft."
            if custom and scale > 1.05 else "")

    def _show_space(self, layout):
        """The empty band between the clock and the stats, in panel pixels --
        or above or below the one block that is shown."""
        w, h = layout.w, layout.h
        blocks = [(name, box) for name, box in (("clock", layout.clock_box),
                                                ("stats", layout.stats_box)) if box]
        if len(blocks) == 2:
            (upper, a), (lower, b) = sorted(blocks, key=lambda item: item[1][1])
            if b[1] <= a[3]:
                text = "The clock and the stats overlap: no space between them."
            else:
                text = (f"Space between the {upper} and the {lower}:\n"
                        f"{w} × {b[1] - a[3]} px, from {a[3]} to {b[1]} px down")
        elif blocks:
            name, box = blocks[0]
            if box[1] >= h - box[3]:
                text = (f"Space above the {name}:\n"
                        f"{w} × {box[1]} px, from the top to {box[1]} px down")
            else:
                text = (f"Space below the {name}:\n"
                        f"{w} × {h - box[3]} px, from {box[3]} px down to the bottom")
        else:
            text = f"Nothing is drawn over the picture:\nall {w} × {h} px show."
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
        source, look = self.source(), copy.deepcopy(self.look)

        def work():
            try:
                path, raw, size = self._raw
                if path != source:
                    raw, size = first_frame(source)
                    self._raw = (source, raw, size)
                img, layout = clock_win.preview(engine_argv(look, source), picture=raw)
                result = (img.resize((self.pw, self.ph), Image.LANCZOS), layout, raw.size, size, look)
            except BaseException as e:      # SystemExit carries the engine's message
                result = e
            self.root.after(0, lambda: self._rendered(result))

        threading.Thread(target=work, daemon=True).start()

    def _rendered(self, result):
        self._rendering = False
        if self._want_render:
            self._start_render()            # the form changed meanwhile
        if isinstance(result, BaseException):
            self._layout = None
            self.canvas.itemconfigure(self.canvas_img, image="")
            self.canvas.itemconfigure(self.canvas_text, text=f"No preview:\n{result}")
            return
        img, layout, raw_size, size, look = result
        self._layout, self._raw_size = layout, raw_size
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.itemconfigure(self.canvas_img, image=self._photo)
        self.canvas.itemconfigure(self.canvas_text, text="")
        scale = fit_geometry(raw_size, FIT_SIZE, look["fit"], look["zoom"],
                             (look["pan_x"], look["pan_y"]))[0] * raw_size[0] / size[0]
        self._show_picture_info(size, scale)
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
        for block, pos, box in (("clock", layout.clock_pos, layout.clock_box),
                                ("stats", layout.stats_pos, layout.stats_box)):
            if box and self.look[f"{block}_pos"] is None:
                getattr(self, f"{block}_pos").set(pos)
        self.stats_warn.configure(
            text="The stats are taller than the screen: make the text smaller or "
                 "show fewer." if layout.overflow else "")
        clock, stats = layout.clock_box, layout.stats_box
        overlap = clock and stats and clock[1] < stats[3] and stats[1] < clock[3]
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
        for block, box in (("clock", layout.clock_box), ("stats", layout.stats_box)):
            if box and box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                target = (block, box)
        return target or ("picture", None)

    def _hover(self, event):
        target = self._target(event)
        cursor = ""
        if target:
            cursor = "fleur" if target[0] == "picture" else "sb_v_double_arrow"
        self.canvas.configure(cursor=cursor)

    def _press(self, event):
        target = self._target(event)
        if not target:
            return
        block, box = target
        if block == "picture":
            self._drag = ("picture", event.x, event.y, self.look["pan_x"], self.look["pan_y"])
        else:
            start = getattr(self._layout, f"{block}_pos")
            travel = FIT_SIZE[1] - 2 * box[0] - (box[3] - box[1])
            self._drag = (block, event.x, event.y, start, travel)

    def _motion(self, event):
        if not self._drag or not self._raw_size:
            return
        kind, x0, y0, a, b = self._drag
        dx, dy = (event.x - x0) / self.k, (event.y - y0) / self.k
        if kind == "picture":
            look = self.look
            _, _, _, width, height = fit_geometry(self._raw_size, FIT_SIZE, look["fit"],
                                                  look["zoom"], (a, b))
            # pan 0..1 slides the picture across its overhang (or its gap).
            if abs(FIT_SIZE[0] - width) > 0.5:
                look["pan_x"] = round(_clamp(a + dx / (FIT_SIZE[0] - width)), 4)
            if abs(FIT_SIZE[1] - height) > 0.5:
                look["pan_y"] = round(_clamp(b + dy / (FIT_SIZE[1] - height)), 4)
            self.changed()
        elif b > 0:
            self._set(f"{kind}_pos", round(_clamp(a + dy / b), 3))

    def _wheel(self, event):
        """Zoom, keeping the spot under the pointer where it is."""
        if not self._raw_size:
            return
        look = self.look
        old = look["zoom"]
        zoom = min(MAX_ZOOM, max(1.0, old * 1.1 ** (event.delta / 120)))
        if zoom == old:
            return
        pan = (look["pan_x"], look["pan_y"])
        cx, cy = event.x / self.k, event.y / self.k
        scale, x, y, _, _ = fit_geometry(self._raw_size, FIT_SIZE, look["fit"], old, pan)
        u, v = (cx - x) / scale, (cy - y) / scale
        scale, _, _, width, height = fit_geometry(self._raw_size, FIT_SIZE, look["fit"], zoom, pan)
        if abs(FIT_SIZE[0] - width) > 0.5:
            look["pan_x"] = round(_clamp((cx - u * scale) / (FIT_SIZE[0] - width)), 4)
        if abs(FIT_SIZE[1] - height) > 0.5:
            look["pan_y"] = round(_clamp((cy - v * scale) / (FIT_SIZE[1] - height)), 4)
        look["zoom"] = round(zoom, 3)
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
        """Everything back to how it comes, except the picture itself."""
        self.look = clean_look({})
        self._custom_date = False
        self._show_look()
        self._place_spots()
        self.changed()

    def undo(self):
        self.dirty = False
        self._form_key = None
        self._load_form(self.app.status)

    def _apply_work(self):
        """What Apply sends, packed now so later edits can't change it."""
        look, chosen, use_default = copy.deepcopy(self.look), self.chosen, self.use_default

        def work():
            fields = {"look": look}
            if chosen:
                with open(chosen, "rb") as f:
                    fields["picture"] = {"name": os.path.basename(chosen),
                                         "data": base64.b64encode(f.read()).decode()}
            elif use_default:
                fields["picture"] = "default"
            send_settings("apply", **fields)
        return work

    def apply(self):
        self._send(self._apply_work(), "apply the settings", applied=True)

    def _send(self, work, what, applied=False):
        self._set_busy(True)

        def done(error):
            self._set_busy(False)
            if error:
                messagebox.showerror(DISPLAY_NAME, f"Could not {what}:\n\n{error}",
                                     parent=self.root)
                return
            # Show what the service saved straight away, rather than waiting
            # for the next status poll (which would first show the old one).
            self.app.status = {**self.app.status, **load_config(), "layouts": layout_names()}
            if applied:
                self.dirty = False
                self._form_key = None
                self._load_form(self.app.status)
            self._show_layouts(self.app.status["layouts"])
            self.app.refresh_soon()

        self.app.background(work, done)

    # -- saved layouts --------------------------------------------------------------

    def load_layout(self):
        name = self.selected_layout()
        if not name:
            return
        if self.dirty and not messagebox.askyesno(
                DISPLAY_NAME, f"Load \"{name}\"? Your changes that are not applied "
                              "yet will be lost.", parent=self.root):
            return
        self._send(lambda: send_settings("load_layout", name=name),
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

        def work():
            if apply_first:
                apply_first()
            send_settings("save_layout", name=name)
        self._send(work, f"save \"{name}\"", applied=bool(apply_first))

    def delete_layout(self):
        name = self.selected_layout()
        if name and messagebox.askyesno(DISPLAY_NAME, f"Delete the layout \"{name}\"?",
                                        parent=self.root):
            self._send(lambda: send_settings("delete_layout", name=name), f"delete \"{name}\"")

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


def first_frame(path):
    """The first frame of a picture, as RGB, and the picture's own size.

    Big photos are scaled down: every placement is worked out in proportions,
    so the preview looks the same, and dragging stays quick.
    """
    with Image.open(path) as img:
        img.seek(0)
        frame = img.convert("RGB")
    size = frame.size
    if max(size) > PREVIEW_SOURCE_LIMIT:
        frame.thumbnail((PREVIEW_SOURCE_LIMIT, PREVIEW_SOURCE_LIMIT), Image.LANCZOS)
    return frame, size


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

        def work():
            if service_state() not in (None, win32service.SERVICE_STOPPED):
                stop_service()

        def done(error):
            if error:
                messagebox.showerror(DISPLAY_NAME, f"Could not stop the display:\n\n{error}")
            self.icon.close()
            self.root.quit()

        self.background(work, done)


def main():
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
