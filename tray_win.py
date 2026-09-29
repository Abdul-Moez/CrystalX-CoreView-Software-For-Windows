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
(service_win.py). Everything lives in the main window: status, start/stop,
the picture and how it fills the screen, and "start with Windows". The tray
icon only has Show and Quit; clicking it opens the window, and closing the
window hides it back to the tray.

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
import ctypes
import os
import queue
import sys
import tempfile
import threading
import webbrowser
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

import pywintypes
import win32api
import win32con
import win32event
import win32gui
import win32service
import winerror
from PIL import Image, ImageTk

import ipc_win
from ipc_win import (DATA_DIR, DEFAULT_PICTURE, DISPLAY_NAME, LOG_FILE,
                     MAX_FRAMES, MAX_PICTURE_BYTES, SERVICE_NAME, VERSION,
                     app_dir, load_config)

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


def read_status():
    state = service_state()
    if state is None:
        return {"installed": False}
    # The settings come from the service's file, so they are known even while
    # the display is stopped.
    status = {"installed": True, "scm": state, "autostart": service_autostart(),
              **load_config()}
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


# -- the main window ------------------------------------------------------------

class MainWindow:
    """Everything in one place: status, start/stop, picture, options."""

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.root.title(DISPLAY_NAME)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        # The picture form. `dirty` means the user changed something that has
        # not been applied yet, so status updates must not overwrite it.
        self.dirty = False
        self.chosen = None                  # a new file the user picked
        self.use_default = False
        self.mode = tk.StringVar(value="fill")
        self.style = tk.StringVar(value="blur")
        self.color = "#000000"
        self.picture_name = tk.StringVar(value="")
        self.autostart = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="Checking...")
        self.note_text = tk.StringVar(value="")
        self.busy = False
        self._render_id = 0
        self._form_key = None
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
        self.pw, self.ph = round(320 * PREVIEW_SCALE), round(1476 * PREVIEW_SCALE)
        self.canvas = tk.Canvas(left, width=self.pw, height=self.ph, bg="#111",
                                highlightthickness=0)
        self.canvas.pack()
        self.canvas_img = self.canvas.create_image(0, 0, anchor="nw")
        self.canvas_text = self.canvas.create_text(
            self.pw // 2, self.ph // 2, text="Rendering...", fill="#bbb",
            width=self.pw - 16, justify="center")
        ttk.Label(left, text="Preview with example readings",
                  style="Hint.TLabel").pack(pady=(6, 0))

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
        ttk.Label(line, textvariable=self.status_text, wraplength=250,
                  justify="left").pack(side="left", padx=(4, 0))
        self.note = ttk.Label(box, textvariable=self.note_text, style="Note.TLabel",
                              wraplength=270, justify="left")
        buttons = ttk.Frame(box)
        buttons.pack(fill="x", pady=(10, 0))
        self.start_btn = ttk.Button(buttons, text="Start display", command=self.start_display)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(buttons, text="Stop display", command=self.stop_display)
        self.stop_btn.pack(side="left", padx=6)
        self.buttons_row = buttons

        # Picture
        box = ttk.LabelFrame(right, text=" Picture ", padding=10)
        box.pack(fill="x", pady=(12, 0))
        ttk.Label(box, textvariable=self.picture_name, wraplength=270).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(6, 10))
        self.choose_btn = ttk.Button(row, text="Choose file...", command=self.choose)
        self.choose_btn.pack(side="left")
        self.default_btn = ttk.Button(row, text="Use default", command=self.pick_default)
        self.default_btn.pack(side="left", padx=6)
        self.fill_radio = ttk.Radiobutton(box, text="Fill - crop to cover the whole screen",
                                          variable=self.mode, value="fill",
                                          command=self.changed)
        self.fill_radio.pack(anchor="w")
        self.fit_radio = ttk.Radiobutton(box, text="Fit - show the whole picture",
                                         variable=self.mode, value="fit",
                                         command=self.changed)
        self.fit_radio.pack(anchor="w")
        sub = ttk.Frame(box)
        sub.pack(anchor="w", padx=(22, 0))
        self.blur_radio = ttk.Radiobutton(sub, text="Blurred edges", variable=self.style,
                                          value="blur", command=self.changed)
        self.blur_radio.grid(row=0, column=0, sticky="w")
        self.color_radio = ttk.Radiobutton(sub, text="Solid colour", variable=self.style,
                                           value="color", command=self.changed)
        self.color_radio.grid(row=1, column=0, sticky="w")
        self.swatch = tk.Button(sub, width=3, relief="solid", bd=1, command=self.pick_color)
        self.swatch.grid(row=1, column=1, padx=8)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(12, 0))
        self.apply_btn = ttk.Button(row, text="Apply", command=self.apply)
        self.apply_btn.pack(side="right")
        self.undo_btn = ttk.Button(row, text="Undo changes", command=self.undo)
        self.undo_btn.pack(side="right", padx=6)

        # Options
        box = ttk.LabelFrame(right, text=" Options ", padding=10)
        box.pack(fill="x", pady=(12, 0))
        self.auto_check = ttk.Checkbutton(box, text="Start display with Windows",
                                          variable=self.autostart,
                                          command=self.toggle_autostart)
        self.auto_check.pack(anchor="w")
        ttk.Label(box, text="The clock appears as soon as the PC starts, even "
                            "before you log in.",
                  style="Hint.TLabel", wraplength=270, justify="left").pack(anchor="w", pady=(4, 0))

        # Footer
        foot = ttk.Frame(right)
        foot.pack(fill="x", side="bottom", pady=(12, 0))
        ttk.Button(foot, text="Open log", command=self.open_log).pack(side="left")
        ttk.Button(foot, text="About", command=self.app.about).pack(side="left", padx=6)
        ttk.Label(foot, text=f"v{VERSION}", style="Hint.TLabel").pack(side="right")

        self._update_controls()

    # -- showing and hiding -------------------------------------------------

    def show(self):
        self.root.deiconify()
        self.root.lift()
        # Windows only lets a window jump to the front briefly on top.
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def hide(self):
        self.root.withdraw()

    # -- status --------------------------------------------------------------

    def refresh(self, s):
        text, colour = describe(s)
        self.status_text.set(text)
        self.dot.configure(fg=colour)
        note = temps_problem(s) if is_showing(s) else None
        self.note_text.set(note or "")
        if note:
            self.note.pack(anchor="w", pady=(6, 0), before=self.buttons_row)
        else:
            self.note.pack_forget()
        installed, showing = s.get("installed"), is_showing(s)
        pending = s.get("scm") in (win32service.SERVICE_START_PENDING,
                                   win32service.SERVICE_STOP_PENDING)
        idle = not installed or self.busy or pending
        self.start_btn.state(["disabled"] if idle or showing else ["!disabled"])
        self.stop_btn.state(["disabled"] if idle or not showing else ["!disabled"])
        if installed and not self.busy:
            self.autostart.set(bool(s.get("autostart")))
        for widget in (self.auto_check, self.choose_btn, self.default_btn,
                       self.fill_radio, self.fit_radio):
            widget.state(["!disabled"] if installed else ["disabled"])
        if installed and not self.dirty:
            self._load_form(s)

    def _load_form(self, s):
        key = (s.get("picture"), s.get("fit"), s.get("fit_color"))
        if key == self._form_key:
            return
        self._form_key = key
        fit = s.get("fit") or "fill"
        self.mode.set("fill" if fit == "fill" else "fit")
        self.style.set(fit if fit in ("blur", "color") else "blur")
        self.color = s.get("fit_color") or "#000000"
        self.chosen, self.use_default = None, False
        self.picture_name.set((s.get("picture_label") or "Your picture")
                              if s.get("picture") else DEFAULT_PICTURE_NAME)
        self._current = (os.path.join(DATA_DIR, s["picture"]) if s.get("picture")
                         else DEFAULT_PICTURE_PATH)
        self._update_controls()
        self.render_preview()

    # -- the picture form ----------------------------------------------------------

    def _update_controls(self):
        fit = self.mode.get() == "fit"
        for widget in (self.blur_radio, self.color_radio):
            widget.state(["!disabled"] if fit else ["disabled"])
        self.swatch.configure(state="normal" if fit and self.style.get() == "color"
                              else "disabled", bg=self.color, activebackground=self.color)
        for widget in (self.apply_btn, self.undo_btn):
            widget.state(["!disabled"] if self.dirty and not self.busy else ["disabled"])

    def fit_mode(self):
        return "fill" if self.mode.get() == "fill" else self.style.get()

    def source(self):
        if self.chosen:
            return self.chosen
        if self.use_default:
            return DEFAULT_PICTURE_PATH
        return getattr(self, "_current", DEFAULT_PICTURE_PATH)

    def changed(self):
        self.dirty = True
        self._update_controls()
        self.render_preview()

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
        self.changed()

    def pick_default(self):
        self.chosen, self.use_default = None, True
        self.picture_name.set(DEFAULT_PICTURE_NAME)
        self.changed()

    def pick_color(self):
        _, hex_color = colorchooser.askcolor(color=self.color, parent=self.root,
                                             title="Edge colour")
        if hex_color:
            self.color = hex_color
            self.changed()

    def undo(self):
        self.dirty = False
        self._form_key = None
        self._load_form(self.app.status)

    def apply(self):
        chosen, use_default = self.chosen, self.use_default
        fit, color = self.fit_mode(), self.color
        self._set_busy(True)

        def work():
            if chosen:
                with open(chosen, "rb") as f:
                    data = base64.b64encode(f.read()).decode()
                send_settings("set_picture", name=os.path.basename(chosen),
                              data=data, fit=fit, fit_color=color)
            else:
                if use_default:
                    send_settings("reset_picture")
                send_settings("set_fit", fit=fit, fit_color=color)

        def done(error):
            self._set_busy(False)
            if error:
                messagebox.showerror(DISPLAY_NAME, f"Could not apply the picture:\n\n{error}",
                                     parent=self.root)
                return
            self.dirty = False
            self._form_key = None           # reload from the saved settings
            self.app.refresh_soon()

        self.app.background(work, done)

    def render_preview(self):
        """Render in the background with clock_win itself, so the preview is
        exactly what the panel will show (with example readings)."""
        self._render_id += 1
        ticket, source, mode, color = self._render_id, self.source(), self.fit_mode(), self.color
        self.canvas.itemconfigure(self.canvas_text, text="Rendering...")

        def work():
            import clock_win
            out = os.path.join(tempfile.gettempdir(), f"crystalx-preview-{os.getpid()}.png")
            try:
                clock_win.main([source, "--fit", mode, "--fit-color", color,
                                "--sample-stats", "--preview", out],
                               log=lambda message: None)
                with Image.open(out) as img:
                    img = img.resize((self.pw, self.ph), Image.LANCZOS)
            except BaseException as e:      # SystemExit carries the engine's message
                img = e
            self.root.after(0, lambda: self._show_preview(ticket, img))

        threading.Thread(target=work, daemon=True).start()

    def _show_preview(self, ticket, img):
        if ticket != self._render_id:
            return                          # a newer render is on its way
        if isinstance(img, BaseException):
            self.canvas.itemconfigure(self.canvas_img, image="")
            self.canvas.itemconfigure(self.canvas_text, text=f"No preview:\n{img}")
            return
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.itemconfigure(self.canvas_img, image=self._photo)
        self.canvas.itemconfigure(self.canvas_text, text="")

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
                self.status = read_status()
            except Exception as e:
                self.status = {"installed": True, "scm": None, "error": str(e)}
            self.icon.set_tip(f"{DISPLAY_NAME} - {describe(self.status)[0]}")
            self.wake.wait(POLL_SECONDS)
            self.wake.clear()

    def _tick(self):
        if not self.quitting:
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
