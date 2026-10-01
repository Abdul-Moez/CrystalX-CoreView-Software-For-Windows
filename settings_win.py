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

Everything that decides what the screen shows is one "look": the playlist of
pictures, GIFs and videos behind the clock (each with its own placement and
timing), and the clock, stats, blocks and style over them. A saved layout is a
look; the files its playlist uses live in the media pool (DATA_DIR\\media),
shared by every layout. The only other setting is whether saved layouts rotate.

Everything read from a file or received from the window goes through
clean_look(): the service runs as SYSTEM, so nothing unchecked may reach it.
"""

import copy
import datetime
import json
import os
import re
import time

from clock_win import (ALIGN, COUNTDOWN_MODES, CRIT_COLOR, DATE_FORMAT, DEFAULT_SLOTS,
                       EMPTY_SLOT, MAX_SPOTS, SLOTS, TIME_FORMATS, WARN_COLOR, WARN_SLOTS)
from ipc_win import CONFIG_FILE, DATA_DIR, DEFAULT_PICTURE, app_dir
from lcd_win import FIT_MODES, MAX_ZOOM
from video_win import CLIP_EXT, MAX_FRAMES, MAX_SECONDS

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

# The playlist. An item's file is "default" (the bundled GIF) or a file in the
# media pool, named after what is in it. Older versions kept the picture
# elsewhere, so those places are accepted too; the service moves such files
# into the pool when it starts. Nothing else is ever a valid file: the service
# runs as SYSTEM and must not be handed a path of someone's choosing.
MAX_ITEMS = 20
DEFAULT_FILE = "default"
PICTURE_TYPES = ("gif", "png", "jpg", "webp", "bmp")
_TYPES = "|".join(PICTURE_TYPES)
MEDIA_FILE = re.compile(rf"media/[0-9a-f]{{20}}\.(?:{_TYPES}|{CLIP_EXT[1:]})"
                        rf"|picture\.(?:{_TYPES})|layouts/[0-9]{{1,2}}\.(?:{_TYPES})")
STILL_SECONDS = (3, 3600)       # how long a still picture is shown
DEFAULT_STILL = 30
PLAYS = (1, 99)                 # how many times a GIF or video plays through
FRAMING = ("fit", "fit_color", "zoom", "pan_x", "pan_y")
ROTATION_MINUTES = (1, 720)


def new_item(file=DEFAULT_FILE, label=None, ident=1):
    """A playlist item with every setting at its default.

    Its placement (lcd_win.fit_geometry) is its own: a wide video and a tall
    picture want different ones. A video's placement is built into the clip
    when it is converted, so it is the choice made then. `seconds` is for
    still pictures, `plays` for GIFs and videos; with only one item, neither
    matters, it just plays on. `max_fps` is a video's frame-rate limit and
    `own_speed` plays a GIF at the speed it was made for rather than at 10
    frames a second; `frames` and `duration` only describe the file, for the
    window.
    """
    return {"id": ident, "file": file,
            "label": label or ("Default (rooftop GIF)" if file == DEFAULT_FILE else file),
            "fit": "fill", "fit_color": "#000000", "zoom": 1.0, "pan_x": 0.5, "pan_y": 0.5,
            "seconds": DEFAULT_STILL, "plays": 1, "max_fps": 30, "own_speed": False,
            "frames": 0, "duration": 0.0}


def item_kind(item):
    """"video" for a converted clip, "pictures" for a picture or GIF."""
    return "video" if item["file"].endswith(CLIP_EXT) else "pictures"


def item_path(item):
    """The file an item shows. A `path` key (the window's preview of a file
    that is not applied yet) wins; otherwise "default" or the pool file."""
    if item.get("path"):
        return item["path"]
    if item["file"] == DEFAULT_FILE:
        return os.path.join(app_dir(), DEFAULT_PICTURE)
    return os.path.join(DATA_DIR, *item["file"].split("/"))


DEFAULT_ROTATION = {"enabled": False, "minutes": 5}

DEFAULT_LOOK = {
    # What is behind the clock: pictures, GIFs and videos, one after another.
    "playlist": [new_item()], "playlist_shuffle": False,
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
    # Warning colours: stat -> [warning level, critical level]. A stat's value
    # is drawn in the warning colour from the first level, the critical colour
    # from the second. Only the stats in clock_win.WARN_SLOTS can have them.
    "warn": {}, "warn_color": WARN_COLOR, "crit_color": CRIT_COLOR,
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
# The config is the look, plus whether saved layouts rotate (that belongs to
# the app, not to a layout, so a layout never saves it).
DEFAULT_CONFIG = {**DEFAULT_LOOK, "rotation": dict(DEFAULT_ROTATION)}


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

    take("frost_dark", _number(raw.get("frost_dark"), 0.0, 1.0))
    take("frost_blur", _number(raw.get("frost_blur"), 0.0, 30.0))
    for key in ("time_color", "date_color", "label_color", "value_color", "warn_color",
                "crit_color", "calendar_color", *(f"{b}_color" for b in EXTRA_BLOCKS)):
        take(key, _color(raw.get(key)))
    warn = raw.get("warn")
    if isinstance(warn, dict):
        for slot, levels in warn.items():
            if (slot in WARN_SLOTS and isinstance(levels, (list, tuple)) and len(levels) == 2
                    and all(_number(level, 0, 1000) is not None for level in levels)
                    and levels[0] <= levels[1]):
                look["warn"][slot] = [float(levels[0]), float(levels[1])]
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
    look["playlist"] = _clean_playlist(raw)
    if isinstance(raw.get("playlist_shuffle"), bool):
        look["playlist_shuffle"] = raw["playlist_shuffle"]
    _clean_extras(raw, look)
    return look


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def clean_item(raw):
    """A valid playlist item from `raw`, or None when it can't be used (its
    file is not one the service may open, or is gone)."""
    if not isinstance(raw, dict):
        return None
    file = raw.get("file")
    if file != DEFAULT_FILE and not (
            isinstance(file, str) and MEDIA_FILE.fullmatch(file)
            and os.path.isfile(os.path.join(DATA_DIR, *file.split("/")))):
        return None
    item = new_item(file)
    label = raw.get("label")
    if isinstance(label, str) and label.strip() and label.isprintable():
        item["label"] = label.strip()[:100]
    if raw.get("fit") in FIT_MODES:
        item["fit"] = raw["fit"]
    color = _color(raw.get("fit_color"))
    if color:
        item["fit_color"] = color
    for key, low, high in (("zoom", 1.0, MAX_ZOOM), ("pan_x", 0.0, 1.0), ("pan_y", 0.0, 1.0)):
        value = _number(raw.get(key), low, high)
        if value is not None:
            item[key] = value
    for key, (low, high) in (("seconds", STILL_SECONDS), ("plays", PLAYS),
                             ("frames", (0, MAX_FRAMES))):
        if _int(raw.get(key)) and low <= raw[key] <= high:
            item[key] = raw[key]
    if raw.get("max_fps") in (30, 60):
        item["max_fps"] = raw["max_fps"]
    if isinstance(raw.get("own_speed"), bool):
        item["own_speed"] = raw["own_speed"]
    duration = _number(raw.get("duration"), 0.0, MAX_SECONDS + 2)
    if duration is not None:
        item["duration"] = duration
    # None means the raw item had no usable id; _clean_playlist numbers those.
    item["id"] = raw["id"] if _int(raw.get("id")) and 0 < raw["id"] < 2**31 else None
    return item


def _clean_playlist(raw):
    """The playlist of `raw`: valid items with unique ids, at least one.

    A look from before playlists has none; its picture's placement (fit, zoom,
    position) becomes that of a single item showing the default picture."""
    items = raw.get("playlist")
    if items is None:
        items = [{"file": DEFAULT_FILE, **{k: raw[k] for k in FRAMING if k in raw}}]
    clean, seen = [], set()
    for entry in (items if isinstance(items, list) else [])[:MAX_ITEMS * 2]:
        item = clean_item(entry)
        if item is None:
            continue
        if item["id"] in seen:
            item["id"] = None
        elif item["id"]:
            seen.add(item["id"])
        clean.append(item)
        if len(clean) == MAX_ITEMS:
            break
    next_id = max(seen, default=0) + 1
    for item in clean:
        if item["id"] is None:
            item["id"], next_id = next_id, next_id + 1
    return clean or [new_item()]


def clean_rotation(raw):
    """The layout-rotation setting from `raw`, with defaults for the rest."""
    rotation = dict(DEFAULT_ROTATION)
    if isinstance(raw, dict):
        if isinstance(raw.get("enabled"), bool):
            rotation["enabled"] = raw["enabled"]
        if _int(raw.get("minutes")) and ROTATION_MINUTES[0] <= raw["minutes"] <= ROTATION_MINUTES[1]:
            rotation["minutes"] = raw["minutes"]
    return rotation


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


_LEGACY_PICTURE = re.compile(rf"picture\.(?:{_TYPES})")


def _with_legacy_picture(raw):
    """`raw` as a settings dict with a playlist.

    Up to v1.2 there was one picture (a file called picture.<ext>) and its
    placement was part of the look. Such a dict becomes a playlist of that one
    picture, placed the same way, so nothing changes for anyone who upgrades."""
    if "playlist" in raw:
        return raw
    picture, item = raw.get("picture"), {"file": DEFAULT_FILE}
    if (isinstance(picture, str) and _LEGACY_PICTURE.fullmatch(picture)
            and os.path.isfile(os.path.join(DATA_DIR, picture))):
        item = {"file": picture, "label": raw.get("picture_label")}
    return {**raw, "playlist": [{**item, **{k: raw[k] for k in FRAMING if k in raw}}]}


def config_from(raw):
    """A complete, valid config from a settings dict: the look, and rotation."""
    raw = raw if isinstance(raw, dict) else {}
    return {**clean_look(_with_legacy_picture(raw)),
            "rotation": clean_rotation(raw.get("rotation"))}


def read_json(path):
    """The JSON in `path`, or None if it isn't there or isn't JSON.

    The service replaces these files while the window may be reading them, and
    Windows then refuses one of the two for a moment: so a refusal is retried
    briefly rather than taken for an empty file (which would show the defaults)."""
    for _ in range(8):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except PermissionError:
            time.sleep(0.02)
        except (OSError, ValueError):
            return None
    return None


def load_config():
    """The saved settings, with anything missing or invalid replaced by defaults.

    The service writes the file; the window reads it too, so it shows the
    current settings even while the display is stopped. A file from before
    playlists (one picture, its placement in the look) loads as a playlist of
    that one picture.
    """
    return config_from(read_json(CONFIG_FILE))


def look_of(config):
    """Just the look from a full settings dict."""
    return {key: config[key] for key in DEFAULT_LOOK}


def media_in_use(look):
    """The pool files a look's playlist needs."""
    return {item["file"] for item in look["playlist"] if item["file"] != DEFAULT_FILE}


def engine_argv(look, item):
    """The clock_win arguments that show playlist `item` with `look`.

    A still picture or GIF is placed by its item's fit, zoom and position; a
    video's placement is already in the clip (it was made at conversion), so it
    gets none. `item` may also be just a file path, placed by the defaults.

    Values go in --name=value form, so a date pattern or a label that starts
    with "-" cannot be mistaken for an option.
    """
    if isinstance(item, str):
        item = {**new_item(), "path": item}
    argv = [item_path(item)]
    if item_kind(item) != "video":
        argv += [f"--fit={item['fit']}", f"--fit-color={item['fit_color']}",
                 f"--zoom={item['zoom']}", f"--pan-x={item['pan_x']}",
                 f"--pan-y={item['pan_y']}"]
        if item.get("own_speed"):
            argv.append("--own-speed")
    argv += [f"--time-format={look['time_format']}",
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
        warns = [f"--warn={slot}={warn:g}:{crit:g}"
                 for slot, (warn, crit) in sorted(look["warn"].items())
                 if slot in look["slots"]]
        if warns:
            argv += warns + [f"--warn-color={look['warn_color']}",
                             f"--crit-color={look['crit_color']}"]
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
# Each layout is LAYOUTS_DIR\<n>.json (n = 1..MAX_LAYOUTS): its name and its
# look, whose playlist names files in the media pool. A layout therefore
# costs nothing in pictures, however many share them. (v1.1 and v1.2 kept a
# copy of the picture beside the layout as <n>.<ext>; those still load, and
# the service moves them into the pool.) Only the service writes layouts; the
# window reads the list straight from the folder, like the settings file.

def layout_file(number):
    return os.path.join(LAYOUTS_DIR, f"{number}.json")


def read_layout(number):
    """(name, look) of saved layout `number`, or None."""
    raw = read_json(layout_file(number))
    name = _text(raw.get("name"), MAX_LAYOUT_NAME) if isinstance(raw, dict) else None
    if not name:
        return None
    look = raw.get("look") if isinstance(raw.get("look"), dict) else {}
    if "playlist" not in look:
        picture, item = raw.get("picture"), {"file": DEFAULT_FILE}
        if (isinstance(picture, str) and re.fullmatch(rf"{number}\.(?:{_TYPES})", picture)
                and os.path.isfile(os.path.join(LAYOUTS_DIR, picture))):
            item = {"file": f"layouts/{picture}", "label": raw.get("picture_label")}
        look = {**look, "playlist": [{**item, **{k: look[k] for k in FRAMING if k in look}}]}
    return name, clean_look(look)


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
