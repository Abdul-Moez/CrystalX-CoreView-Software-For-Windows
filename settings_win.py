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
"""The display settings: their defaults, their checks, and the clock_win
arguments they turn into.

Shared by the service, which stores and applies them, and the window, which
edits them and draws its preview from the very same arguments -- so the
preview always matches the panel.

The settings are split in two: the picture (a file the service keeps) and the
"look" (everything else that decides what the screen shows). A saved layout
is both together.

Everything read from a file or received from the window goes through
clean_look(): the service runs as SYSTEM, so nothing unchecked may reach it.
"""

import copy
import datetime
import json
import os

from clock_win import (ALIGN, COUNTDOWN_MODES, DATE_FORMAT, DEFAULT_SLOTS, EMPTY_SLOT,
                       MAX_SPOTS, SLOTS, TIME_FORMATS)
from ipc_win import CONFIG_FILE, DATA_DIR, DEFAULT_PICTURE, app_dir
from lcd_win import FIT_MODES, MAX_ZOOM

# Fonts come only from here: the service runs as SYSTEM and never reads files
# from anyone's user folder, so fonts installed for one user are not offered.
FONTS_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
FONT_TYPES = (".ttf", ".ttc", ".otf")
DEFAULT_FONT = "consolab.ttf"

SIZE_RANGE = (8, 400)           # text sizes in px; None means automatic
MAX_LABEL = 24                  # characters in a renamed stat
MAX_DATE_PATTERN = 40
MAX_LAYOUTS = 10
MAX_LAYOUT_NAME = 40
MAX_NOTE = 600                  # characters in the text block
MAX_NOTE_LINES = 16
MAX_COUNTDOWN_LABEL = 40
MAX_TODO_ITEMS = 20
MAX_TODO_TEXT = 100
LAYOUTS_DIR = os.path.join(DATA_DIR, "layouts")

DEFAULT_LOOK = {
    # The picture's placement (lcd_win.fit_geometry).
    "fit": "fill", "fit_color": "#000000", "zoom": 1.0, "pan_x": 0.5, "pan_y": 0.5,
    # The clock block. Sizes and positions of None are automatic.
    "show_time": True, "time_format": "12h", "seconds": False,
    "time_size": None, "time_color": "#ffffff",
    "show_date": True, "date_format": DATE_FORMAT,
    "date_size": None, "date_color": "#ffffff",
    "clock_pos": None,
    # The stats block: MAX_SPOTS spots, None for an empty one. Hiding it
    # keeps the spots for when it is shown again.
    "show_stats": True,
    "slots": DEFAULT_SLOTS.split(","), "columns": 2, "labels": {},
    "label_size": None, "label_color": "#ffffff",
    "value_size": None, "value_color": "#ffffff",
    "stats_pos": None,
    # Style. A font of None is DEFAULT_FONT. Brightness dims the pixels in
    # percent; the panel's backlight itself cannot be controlled.
    "font": None, "frost": True, "frost_dark": 0.5, "frost_blur": 9.0,
    "brightness": 100,
    # The calendar, in the clock block: None, "month" or "week".
    "calendar": None, "week_start": "mon", "calendar_size": None,
    "calendar_color": "#ffffff",
    # The extra blocks. Each is off until switched on, and keeps its content
    # while off. A to-do item is {"id", "text", "done"}; ids let a tick from
    # the window reach the right item.
    "show_note": False, "note_text": "",
    "show_countdown": False, "countdown_mode": "to", "countdown_date": "",
    "countdown_time": "", "countdown_label": "",
    "show_todo": False, "todo_title": "", "todo_items": [],
}
EXTRA_BLOCKS = ("note", "countdown", "todo")
for _block, _align in zip(EXTRA_BLOCKS, ("center", "center", "left")):
    DEFAULT_LOOK.update({f"{_block}_size": None, f"{_block}_color": "#ffffff",
                         f"{_block}_align": _align, f"{_block}_pos": None,
                         f"{_block}_frost": True})
DEFAULT_CONFIG = {"picture": None, "picture_label": None, **DEFAULT_LOOK}


def _color(value):
    """#rrggbb, or None if `value` is not one."""
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
            return value.lower()
        except ValueError:
            pass
    return None


def _number(value, low, high):
    if isinstance(value, (int, float)) and not isinstance(value, bool) \
            and low <= value <= high:
        return float(value)
    return None


def _text(value, limit):
    """Printable text of at most `limit` characters, or None."""
    if isinstance(value, str) and 0 < len(value) <= limit and value.isprintable():
        return value
    return None


def font_path(name):
    """The file of a font setting, or None to use the engine's default."""
    return os.path.join(FONTS_DIR, name) if name else None


def valid_font(name):
    return (isinstance(name, str) and os.path.basename(name) == name
            and name.lower().endswith(FONT_TYPES)
            and os.path.isfile(os.path.join(FONTS_DIR, name)))


def clean_look(raw):
    """A complete, valid look: every usable value from `raw`, defaults for
    the rest. Unknown keys are dropped, bad values replaced by the default."""
    look = copy.deepcopy(DEFAULT_LOOK)
    if not isinstance(raw, dict):
        return look

    def take(key, value):
        if value is not None:
            look[key] = value

    if raw.get("fit") in FIT_MODES:
        look["fit"] = raw["fit"]
    take("zoom", _number(raw.get("zoom"), 1.0, MAX_ZOOM))
    for key in ("pan_x", "pan_y", "frost_dark"):
        take(key, _number(raw.get(key), 0.0, 1.0))
    take("frost_blur", _number(raw.get("frost_blur"), 0.0, 30.0))
    for key in ("fit_color", "time_color", "date_color", "label_color", "value_color",
                "calendar_color", *(f"{b}_color" for b in EXTRA_BLOCKS)):
        take(key, _color(raw.get(key)))
    for key in ("show_time", "show_date", "show_stats", "seconds", "frost",
                *(f"show_{b}" for b in EXTRA_BLOCKS), *(f"{b}_frost" for b in EXTRA_BLOCKS)):
        if isinstance(raw.get(key), bool):
            look[key] = raw[key]
    if raw.get("time_format") in TIME_FORMATS:
        look["time_format"] = raw["time_format"]
    take("date_format", _text(raw.get("date_format"), MAX_DATE_PATTERN))
    if raw.get("columns") in (1, 2):
        look["columns"] = raw["columns"]
    brightness = raw.get("brightness")
    if _int(brightness) and 10 <= brightness <= 100:
        look["brightness"] = brightness
    # Optional values: an explicit None switches back to automatic.
    for key in ("time_size", "date_size", "label_size", "value_size", "calendar_size",
                *(f"{b}_size" for b in EXTRA_BLOCKS)):
        value = raw.get(key)
        if key in raw and (value is None or (
                isinstance(value, int) and not isinstance(value, bool)
                and SIZE_RANGE[0] <= value <= SIZE_RANGE[1])):
            look[key] = value
    for key in ("clock_pos", "stats_pos", *(f"{b}_pos" for b in EXTRA_BLOCKS)):
        if key in raw and raw[key] is None:
            look[key] = None
        else:
            take(key, _number(raw.get(key), 0.0, 1.0))
    slots = raw.get("slots")
    if isinstance(slots, list) and len(slots) <= MAX_SPOTS:
        # A stat this version does not know becomes an empty spot.
        look["slots"] = [s if s in SLOTS else None for s in slots]
    look["slots"] = (look["slots"] + [None] * MAX_SPOTS)[:MAX_SPOTS]
    labels = raw.get("labels")
    if isinstance(labels, dict):
        look["labels"] = {slot: text.strip() for slot, text in labels.items()
                          if slot in SLOTS and _text(text, MAX_LABEL)
                          and text.strip()}
    if valid_font(raw.get("font")):
        look["font"] = raw["font"]
    _clean_extras(raw, look)
    return look


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _clean_extras(raw, look):
    """The calendar and the text, countdown and to-do blocks (see clean_look)."""
    if "calendar" in raw and raw["calendar"] in (None, "month", "week"):
        look["calendar"] = raw["calendar"]
    if raw.get("week_start") in ("mon", "sun"):
        look["week_start"] = raw["week_start"]
    for block in EXTRA_BLOCKS:
        if raw.get(f"{block}_align") in ALIGN:
            look[f"{block}_align"] = raw[f"{block}_align"]
    note = raw.get("note_text")
    if (isinstance(note, str) and len(note) <= MAX_NOTE
            and note.count("\n") < MAX_NOTE_LINES
            and all(line.isprintable() for line in note.split("\n"))):
        look["note_text"] = note
    if raw.get("countdown_mode") in COUNTDOWN_MODES:
        look["countdown_mode"] = raw["countdown_mode"]
    if _date(raw.get("countdown_date"), "%Y-%m-%d"):
        look["countdown_date"] = raw["countdown_date"]
    if raw.get("countdown_time") == "" or _date(raw.get("countdown_time"), "%H:%M"):
        look["countdown_time"] = raw["countdown_time"]
    label = raw.get("countdown_label")
    if label == "" or _text(label, MAX_COUNTDOWN_LABEL):
        look["countdown_label"] = label
    title = raw.get("todo_title")
    if title == "" or _text(title, MAX_TODO_TEXT):
        look["todo_title"] = title
    items = raw.get("todo_items")
    if isinstance(items, list):
        look["todo_items"] = _clean_todo(items)


def _date(value, fmt):
    try:
        return isinstance(value, str) and bool(datetime.datetime.strptime(value, fmt))
    except ValueError:
        return False


def _clean_todo(items):
    """Valid to-do items, each with a unique id (new ones numbered on)."""
    clean, seen = [], set()
    for item in items[:MAX_TODO_ITEMS]:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not (_text(text, MAX_TODO_TEXT) and text.strip()):
            continue
        ident = item.get("id")
        if not _int(ident) or not 0 < ident < 2**31 or ident in seen:
            ident = None
        clean.append({"id": ident, "text": text.strip(), "done": item.get("done") is True})
        if ident:
            seen.add(ident)
    next_id = max(seen, default=0) + 1
    for item in clean:
        if item["id"] is None:
            item["id"], next_id = next_id, next_id + 1
    return clean


def split_config(raw):
    """(picture, picture_label, look) from a settings dict, all checked."""
    raw = raw if isinstance(raw, dict) else {}
    picture, label = raw.get("picture"), None
    # Only a picture the service itself stored in DATA_DIR is ever used.
    if not (isinstance(picture, str) and os.path.basename(picture) == picture
            and os.path.isfile(os.path.join(DATA_DIR, picture))):
        picture = None
    elif isinstance(raw.get("picture_label"), str):
        # The name of the file the user picked, for display only.
        label = raw["picture_label"][:100]
    return picture, label, clean_look(raw)


def load_config():
    """The saved settings, with anything missing or invalid replaced by defaults.

    The service writes the file; the window reads it too, so it shows the
    current settings even while the display is stopped. A v1.0 file (picture
    and fit only) loads with defaults for everything newer.
    """
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    picture, label, look = split_config(raw)
    return {"picture": picture, "picture_label": label, **look}


def look_of(config):
    """Just the look from a full settings dict."""
    return {key: config[key] for key in DEFAULT_LOOK}


def picture_path(config):
    """The file the service shows: its stored copy, or the bundled GIF."""
    if config.get("picture"):
        return os.path.join(DATA_DIR, config["picture"])
    return os.path.join(app_dir(), DEFAULT_PICTURE)


def engine_argv(look, picture):
    """The clock_win arguments that show `picture` with `look`.

    Values go in --name=value form, so a date pattern or a label that starts
    with "-" cannot be mistaken for an option.
    """
    argv = [picture, f"--fit={look['fit']}", f"--fit-color={look['fit_color']}",
            f"--zoom={look['zoom']}", f"--pan-x={look['pan_x']}",
            f"--pan-y={look['pan_y']}",
            f"--time-format={look['time_format']}",
            f"--date-format={look['date_format']}",
            f"--frost-dark={look['frost_dark']}", f"--frost-blur={look['frost_blur']}",
            f"--brightness={look['brightness']}"]
    if look["seconds"]:
        argv.append("--seconds")
    if not look["show_time"]:
        argv.append("--no-time")
    if not look["show_date"]:
        argv.append("--no-date")
    if not look["frost"]:
        argv.append("--no-frost")
    if look["font"]:
        argv.append(f"--font={font_path(look['font'])}")
    for part in ("time", "date", "label", "value"):
        argv.append(f"--{part}-color={look[part + '_color']}")
        if look[part + "_size"]:
            argv.append(f"--{part}-size={look[part + '_size']}")
    for block in ("clock", "stats"):
        if look[block + "_pos"] is not None:
            argv.append(f"--{block}-pos={look[block + '_pos']}")
    argv += _extras_argv(look)
    if look["show_stats"] and any(look["slots"]):
        argv += [f"--slots={','.join(s or EMPTY_SLOT for s in look['slots'])}",
                 f"--columns={look['columns']}"]
        argv += [f"--label={slot}={text}" for slot, text in sorted(look["labels"].items())]
    else:
        argv.append("--no-stats")
    return argv


def _extras_argv(look):
    """engine_argv's part for the calendar and the extra blocks."""
    argv = []
    if look["calendar"]:
        argv += [f"--calendar={look['calendar']}", f"--week-start={look['week_start']}",
                 f"--calendar-color={look['calendar_color']}"]
        if look["calendar_size"]:
            argv.append(f"--calendar-size={look['calendar_size']}")
    shown = {
        "note": look["show_note"] and look["note_text"].strip(),
        "countdown": look["show_countdown"] and look["countdown_date"],
        "todo": look["show_todo"] and (look["todo_items"] or look["todo_title"]),
    }
    if shown["note"]:
        argv += [f"--note-line={line}" for line in look["note_text"].split("\n")]
    if shown["countdown"]:
        when = look["countdown_date"]
        if look["countdown_mode"] == "to-hours" and look["countdown_time"]:
            when += "T" + look["countdown_time"]
        argv += [f"--countdown-date={when}", f"--countdown-mode={look['countdown_mode']}",
                 f"--countdown-label={look['countdown_label']}"]
    if shown["todo"]:
        argv.append(f"--todo-title={look['todo_title']}")
        argv += [f"--todo-item={'[x]' if item['done'] else '[ ]'} {item['text']}"
                 for item in look["todo_items"]]
    for block in EXTRA_BLOCKS:
        if not shown[block]:
            continue
        argv += [f"--{block}-color={look[block + '_color']}",
                 f"--{block}-align={look[block + '_align']}"]
        if look[block + "_size"]:
            argv.append(f"--{block}-size={look[block + '_size']}")
        if look[block + "_pos"] is not None:
            argv.append(f"--{block}-pos={look[block + '_pos']}")
        if not look[block + "_frost"]:
            argv.append(f"--{block}-no-frost")
    return argv


# -- saved layouts ---------------------------------------------------------------
#
# Each layout is LAYOUTS_DIR\<n>.json (n = 1..MAX_LAYOUTS), plus <n>.<ext>, its
# own copy of its picture, when it has one. Only the service writes them; the
# window reads the list straight from the folder, like the settings file.

def layout_file(number):
    return os.path.join(LAYOUTS_DIR, f"{number}.json")


def read_layout(number):
    """(name, picture file or None, picture label, look), or None."""
    try:
        with open(layout_file(number), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    name = _text(raw.get("name"), MAX_LAYOUT_NAME) if isinstance(raw, dict) else None
    if not name:
        return None
    picture = raw.get("picture")
    if not (isinstance(picture, str) and picture.startswith(f"{number}.")
            and os.path.basename(picture) == picture
            and os.path.isfile(os.path.join(LAYOUTS_DIR, picture))):
        picture = None
    label = raw.get("picture_label") if isinstance(raw.get("picture_label"), str) else None
    return name, picture, label, clean_look(raw.get("look"))


def list_layouts():
    """The saved layouts as [(number, name)], sorted by name."""
    found = []
    for number in range(1, MAX_LAYOUTS + 1):
        layout = read_layout(number)
        if layout:
            found.append((number, layout[0]))
    return sorted(found, key=lambda item: item[1].lower())


def find_layout(name):
    """The number of the layout called `name` (any case), or None."""
    for number, existing in list_layouts():
        if existing.lower() == name.lower():
            return number
    return None


def clean_layout_name(name):
    name = name.strip() if isinstance(name, str) else ""
    if not _text(name, MAX_LAYOUT_NAME):
        raise ValueError(f"a layout name needs 1 to {MAX_LAYOUT_NAME} characters")
    return name
