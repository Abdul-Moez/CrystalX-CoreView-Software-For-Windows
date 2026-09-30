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
import json
import os

from clock_win import DATE_FORMAT, DEFAULT_SLOTS, EMPTY_SLOT, MAX_SPOTS, SLOTS, TIME_FORMATS
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
    # Style. A font of None is DEFAULT_FONT.
    "font": None, "frost": True, "frost_dark": 0.5, "frost_blur": 9.0,
}
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
    for key in ("fit_color", "time_color", "date_color", "label_color", "value_color"):
        take(key, _color(raw.get(key)))
    for key in ("show_time", "show_date", "show_stats", "seconds", "frost"):
        if isinstance(raw.get(key), bool):
            look[key] = raw[key]
    if raw.get("time_format") in TIME_FORMATS:
        look["time_format"] = raw["time_format"]
    take("date_format", _text(raw.get("date_format"), MAX_DATE_PATTERN))
    if raw.get("columns") in (1, 2):
        look["columns"] = raw["columns"]
    # Optional values: an explicit None switches back to automatic.
    for key in ("time_size", "date_size", "label_size", "value_size"):
        value = raw.get(key)
        if key in raw and (value is None or (
                isinstance(value, int) and not isinstance(value, bool)
                and SIZE_RANGE[0] <= value <= SIZE_RANGE[1])):
            look[key] = value
    for key in ("clock_pos", "stats_pos"):
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
    return look


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
            f"--frost-dark={look['frost_dark']}", f"--frost-blur={look['frost_blur']}"]
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
    if look["show_stats"] and any(look["slots"]):
        argv += [f"--slots={','.join(s or EMPTY_SLOT for s in look['slots'])}",
                 f"--columns={look['columns']}"]
        argv += [f"--label={slot}={text}" for slot, text in sorted(look["labels"].items())]
    else:
        argv.append("--no-stats")
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
