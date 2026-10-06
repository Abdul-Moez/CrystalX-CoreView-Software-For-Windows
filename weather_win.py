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
"""The weather block: its readings, from Open-Meteo, and its icons.

  readings   one small HTTPS request to api.open-meteo.com for the place that
             was chosen, no more often than every REFRESH seconds, and only
             while the block is shown. Weather data by Open-Meteo.com, under
             CC BY 4.0; no key or account is needed. This is the only part of
             the project that uses the network.
  icons      drawn here in code, so there is no font or picture file to ship.

The service fetches as SYSTEM, so a reply is treated as untrusted: it is only
ever parsed as JSON, and each value taken from it is checked (_reading). The
place itself is two numbers; the window finds them by name (search) and the
service is never given anything else.

The modules that talk to the network load only when a reading is fetched
(_get), so a display without the weather block never loads them.
"""

import collections
import datetime
import functools
import math
import threading
import time

from PIL import Image, ImageDraw, ImageFilter

API = "https://api.open-meteo.com/v1/forecast"
SEARCH = "https://geocoding-api.open-meteo.com/v1/search"
CREDIT = "Weather data by Open-Meteo.com"
CREDIT_LINK = "https://open-meteo.com/"

REFRESH = 15 * 60           # Open-Meteo's current conditions change every 15 minutes
RETRY = (60, 120, 300)      # after a fetch that failed; from then on, every REFRESH
MAX_AGE = 3 * 3600          # a reading older than this is no longer shown
TIMEOUT = 10                # seconds a request may take
MAX_BYTES = 64 * 1024       # of a reply; a real one is about 1 KB
FORECAST_DAYS = 3           # the days after today the block can show
MAX_NAME = 80               # characters in a place's name
NO_READING = "No reading yet"


class WeatherError(Exception):
    """A reading (or a search) could not be had; str() says why."""


# What Open-Meteo said about one place: the temperature now, the WMO weather
# code, whether it is day there, the place's time as seconds east of UTC, each
# day from today on as (date, code, high, low), and when it was fetched.
Reading = collections.namedtuple("Reading", "temp code is_day offset days at")

# What the block shows, ready to draw: the temperature, the icon (None: no
# reading) and whether it is the night one, the conditions in words, today's
# high and low, and each further day as (name, icon, high, low). Two views
# are equal when the block would look the same.
View = collections.namedtuple("View", "temp kind night text high low days")

# WMO weather codes, as Open-Meteo gives them -> (icon, what to call it).
CODES = {
    0: ("clear", "Clear"), 1: ("clear", "Mostly clear"),
    2: ("partly", "Partly cloudy"), 3: ("cloudy", "Overcast"),
    45: ("fog", "Fog"), 48: ("fog", "Freezing fog"),
    51: ("drizzle", "Light drizzle"), 53: ("drizzle", "Drizzle"),
    55: ("drizzle", "Heavy drizzle"),
    56: ("sleet", "Freezing drizzle"), 57: ("sleet", "Freezing drizzle"),
    61: ("rain", "Light rain"), 63: ("rain", "Rain"), 65: ("rain", "Heavy rain"),
    66: ("sleet", "Freezing rain"), 67: ("sleet", "Freezing rain"),
    71: ("snow", "Light snow"), 73: ("snow", "Snow"), 75: ("snow", "Heavy snow"),
    77: ("snow", "Snow grains"),
    80: ("showers", "Light showers"), 81: ("showers", "Showers"),
    82: ("showers", "Heavy showers"),
    85: ("snow", "Snow showers"), 86: ("snow", "Snow showers"),
    95: ("thunder", "Thunderstorm"),
    96: ("thunder", "Thunder and hail"), 99: ("thunder", "Thunder and hail"),
}
UNKNOWN = ("cloudy", "Cloudy")      # a code this version does not know
# Every text the conditions line can hold, for sizing the block.
TEXTS = tuple(sorted({text for _, text in CODES.values()} | {UNKNOWN[1], NO_READING}))


# -- readings ---------------------------------------------------------------------

def _number(value, low, high):
    """`value` as a float if it is a number from `low` to `high`, else None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) \
            and math.isfinite(value) and low <= value <= high:
        return float(value)
    return None


def _get(url, params):
    """The JSON object at `url`: over HTTPS, from that host and no other (a
    redirect is refused), within TIMEOUT seconds and MAX_BYTES."""
    import http.client
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    request = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": "CrystalX-LCD"})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=TIMEOUT) as reply:
            data = reply.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise WeatherError(f"Open-Meteo answered with error {e.code}") from None
    except (OSError, http.client.HTTPException) as e:
        raise WeatherError(f"Open-Meteo could not be reached ({getattr(e, 'reason', e)})") from None
    if len(data) > MAX_BYTES:
        raise WeatherError("Open-Meteo's answer was too long")
    try:
        data = json.loads(data)
    except (ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        raise WeatherError("Open-Meteo's answer could not be read")
    return data


def _reading(data, at):
    """The Reading in a forecast reply, each value checked: a number in a
    sane range, a code from 0 to 99, a date. Days stop at the first one that
    is not all there."""
    try:
        current, daily = data["current"], data["daily"]
        temp = _number(current["temperature_2m"], -150, 200)
        code = _number(current["weather_code"], 0, 99)
        offset = _number(data["utc_offset_seconds"], -18 * 3600, 18 * 3600)
        if temp is None or code is None or offset is None or current["is_day"] not in (0, 1):
            raise ValueError
        days = []
        for date, day_code, high, low in zip(daily["time"], daily["weather_code"],
                                             daily["temperature_2m_max"],
                                             daily["temperature_2m_min"]):
            day_code = _number(day_code, 0, 99)
            high, low = _number(high, -150, 200), _number(low, -150, 200)
            if not isinstance(date, str) or None in (day_code, high, low):
                break
            days.append((datetime.date.fromisoformat(date), int(day_code), high, low))
            if len(days) > FORECAST_DAYS + 1:
                break
    except (KeyError, TypeError, ValueError):
        raise WeatherError("Open-Meteo's answer was not a forecast") from None
    return Reading(temp, int(code), current["is_day"] == 1, int(offset), tuple(days), at)


def fetch(place, unit):
    """A new Reading for `place`, (latitude, longitude), in "c" or "f"."""
    data = _get(API, {
        "latitude": f"{place[0]:.2f}", "longitude": f"{place[1]:.2f}",
        "current": "temperature_2m,weather_code,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "temperature_unit": "fahrenheit" if unit == "f" else "celsius",
        "timezone": "auto", "forecast_days": FORECAST_DAYS + 2})
    return _reading(data, time.time())


def search(name):
    """The places called `name`, likeliest first: [{"name", "lat", "lon"}],
    the name with its region and country so that namesakes can be told apart.
    For the window, when someone looks for their city; the service never
    searches. Positions are kept to two decimal places, about a kilometre:
    close enough for the weather, and no closer than that is sent anywhere."""
    data = _get(SEARCH, {"name": name, "count": 8, "language": "en", "format": "json"})
    results, places = data.get("results"), []
    for entry in results if isinstance(results, list) else []:
        if not isinstance(entry, dict):
            continue
        lat = _number(entry.get("latitude"), -90, 90)
        lon = _number(entry.get("longitude"), -180, 180)
        parts = [part.strip() for part in (entry.get(key) for key in ("name", "admin1", "country"))
                 if isinstance(part, str) and part.strip() and part.isprintable()]
        if lat is None or lon is None or not isinstance(entry.get("name"), str) or not parts:
            continue
        # "Singapore, Singapore" says it once.
        label = ", ".join(dict.fromkeys(parts))[:MAX_NAME]
        places.append({"name": label, "lat": round(lat, 2), "lon": round(lon, 2)})
    return places


_readings, _due, _failures = {}, {}, {}     # by (latitude, longitude, unit)
_fetching = threading.Lock()


def latest(place, unit, now=None):
    """The last Reading fetched for `place`, or None when there is none or it
    is too old to show."""
    reading = _readings.get((*place, unit))
    now = time.time() if now is None else now
    return None if reading is None or now - reading.at > MAX_AGE else reading


def due(place, unit):
    """When the next reading for `place` is due, in epoch seconds (0: none has
    been asked for yet)."""
    return _due.get((*place, unit), 0.0)


def refresh(place, unit, log=None):
    """Fetch a new reading for `place` if one is due; otherwise do nothing.

    This can take many seconds (TIMEOUT for each step, and a slow name lookup
    on top of that), so it is for a background thread, never the display
    loop. A fetch that fails is not an error: the last
    reading stays (see MAX_AGE) and the next try comes sooner (RETRY). `log`
    is told when readings start coming and when they stop, not every time.
    """
    key = (*place, unit)
    with _fetching:
        now = time.time()
        if now < _due.get(key, 0.0):
            return
        try:
            reading = fetch(place, unit)
        except WeatherError as e:
            failures = _failures[key] = _failures.get(key, 0) + 1
            _due[key] = now + (RETRY[failures - 1] if failures <= len(RETRY) else REFRESH)
            if failures == 1 and log:
                log(f"weather: {e}; trying again in a minute")
            return
        except Exception as e:      # nothing here may ever stop the display
            _failures[key] = _failures.get(key, 0) + 1
            _due[key] = now + REFRESH
            if log:
                log(f"weather: {type(e).__name__}: {e}")
            return
        if log and (_failures.get(key) or key not in _readings):
            log(f"weather: reading from Open-Meteo for {place[0]:.2f},{place[1]:.2f}, "
                f"every {REFRESH // 60} minutes")
        _readings[key], _failures[key], _due[key] = reading, 0, now + REFRESH


def example(unit, now=None):
    """A made-up Reading for previews, like the stats' example readings."""
    now = time.time() if now is None else now
    offset = datetime.datetime.fromtimestamp(now).astimezone().utcoffset().total_seconds()
    today = datetime.date.fromtimestamp(now)

    def temp(celsius):
        return celsius * 9 / 5 + 32 if unit == "f" else celsius

    days = ((2, 27, 18), (63, 26, 17), (0, 28, 18), (95, 25, 16), (3, 24, 15))
    return Reading(temp(23), 2, True, int(offset),
                   tuple((today + datetime.timedelta(n), code, temp(high), temp(low))
                         for n, (code, high, low) in enumerate(days)), now)


def _today(reading, now):
    """The date at the reading's place (this PC's, without a reading)."""
    if reading is None:
        return datetime.date.fromtimestamp(now)
    there = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    return (there + datetime.timedelta(seconds=reading.offset)).date()


def next_day(reading, now):
    """When the date next changes at the reading's place, in epoch seconds:
    the block's days all move on then."""
    tomorrow = _today(reading, now) + datetime.timedelta(1)
    if reading is None:
        return datetime.datetime.combine(tomorrow, datetime.time.min).timestamp()
    midnight = datetime.datetime.combine(tomorrow, datetime.time.min, datetime.timezone.utc)
    return midnight.timestamp() - reading.offset


def _degrees(value):
    return "--°" if value is None else f"{round(value)}°"


def view(reading, unit, days, now):
    """What the block shows for `reading` (None: there is none) at the time
    `now`: today, and the `days` days after it."""
    today = _today(reading, now)
    by_date = {} if reading is None else {day[0]: day[1:] for day in reading.days}
    nothing = (None, None, None)
    following = []
    for n in range(1, days + 1):
        date = today + datetime.timedelta(n)
        code, high, low = by_date.get(date, nothing)
        following.append((date.strftime("%a"), None if code is None else CODES.get(code, UNKNOWN)[0],
                          _degrees(high), _degrees(low)))
    if reading is None:
        return View(f"--°{unit.upper()}", None, False, NO_READING, "--°", "--°", tuple(following))
    kind, text = CODES.get(reading.code, UNKNOWN)
    _, high, low = by_date.get(today, nothing)
    return View(f"{round(reading.temp)}°{unit.upper()}", kind, not reading.is_day, text,
                _degrees(high), _degrees(low), tuple(following))


# -- icons ------------------------------------------------------------------------
#
# An icon is a few layers, back to front -- the sun or the moon, a cloud, what
# falls from it -- each edged in black like the text's outline, so it reads
# over any picture. It is drawn SCALE times too large and reduced, which gives
# the smooth edges ImageDraw's own shapes lack. Icons are made once per size
# and kept; the display loop never draws one.

SCALE = 4                   # drawn this many times too large, then reduced
BACK_SHADE = 0.6            # in one colour, how bright the cloud behind stays

KINDS = ("clear", "partly", "cloudy", "fog", "drizzle", "rain", "showers",
         "sleet", "snow", "thunder")
NIGHT_KINDS = ("clear", "partly", "showers")    # the ones the sun is in

COLORS = {
    "sun": (255, 196, 37), "moon": (245, 232, 170),
    "cloud": (240, 244, 250), "back": (160, 172, 190), "storm": (150, 160, 178),
    "rain": (86, 168, 255), "snow": (255, 255, 255),
    "bolt": (255, 214, 48), "fog": (205, 211, 222),
}


class _Pen:
    """One layer of an icon: shapes drawn onto a mask, in units of the icon's
    size (0 to 1 across and down)."""

    def __init__(self, n, margin):
        self.mask = Image.new("L", (n, n), 0)
        self.draw = ImageDraw.Draw(self.mask)
        self.margin, self.span = margin, n - 2 * margin

    def at(self, v):
        return self.margin + v * self.span

    def disc(self, x, y, r, ink=255):
        self.draw.ellipse((self.at(x - r), self.at(y - r), self.at(x + r), self.at(y + r)),
                          fill=ink)

    def bar(self, x0, y0, x1, y1, width):
        """A line with round ends."""
        self.draw.line((self.at(x0), self.at(y0), self.at(x1), self.at(y1)), fill=255,
                       width=max(1, round(width * self.span)))
        self.disc(x0, y0, width / 2)
        self.disc(x1, y1, width / 2)

    def pill(self, x0, y0, x1, y1):
        self.draw.rounded_rectangle((self.at(x0), self.at(y0), self.at(x1), self.at(y1)),
                                    radius=(y1 - y0) * self.span / 2, fill=255)

    def polygon(self, points):
        self.draw.polygon([(self.at(x), self.at(y)) for x, y in points], fill=255)


def _sun(pen, x, y, r):
    pen.disc(x, y, r)
    for i in range(8):
        dx, dy = math.cos(i * math.pi / 4), math.sin(i * math.pi / 4)
        pen.bar(x + dx * r * 1.5, y + dy * r * 1.5, x + dx * r * 1.9, y + dy * r * 1.9,
                r * 0.32)


def _moon(pen, x, y, r):
    pen.disc(x, y, r)
    pen.disc(x + r * 0.45, y - r * 0.3, r * 0.85, ink=0)


def _cloud(pen, x, y, s):
    """A cloud `s` wide: a flat-bottomed base with three bumps on it."""
    pen.pill(x - 0.5 * s, y + 0.02 * s, x + 0.5 * s, y + 0.30 * s)
    pen.disc(x - 0.22 * s, y + 0.02 * s, 0.20 * s)
    pen.disc(x + 0.06 * s, y - 0.08 * s, 0.28 * s)
    pen.disc(x + 0.30 * s, y + 0.08 * s, 0.17 * s)


def _streaks(pen, xs, top, bottom, lean=0.07, width=0.075):
    for x in xs:
        pen.bar(x, top, x - lean, bottom, width)


def _flake(pen, x, y, r):
    for degrees in (90, 30, 150):
        dx, dy = r * math.cos(math.radians(degrees)), r * math.sin(math.radians(degrees))
        pen.bar(x - dx, y - dy, x + dx, y + dy, r * 0.42)


def _bolt(pen):
    pen.polygon([(0.55, 0.46), (0.36, 0.72), (0.48, 0.72), (0.40, 0.93),
                 (0.67, 0.61), (0.54, 0.61), (0.63, 0.46)])


def _layers(kind, night):
    """An icon's layers, back to front: (colour name, what to draw with a pen)."""
    def lamp(x, y, sun_r, moon_r):
        if night:
            return "moon", lambda p: _moon(p, x, y, moon_r)
        return "sun", lambda p: _sun(p, x, y, sun_r)

    if kind == "clear":
        return [lamp(0.5, 0.5, 0.2, 0.32)]
    if kind == "partly":
        return [lamp(0.36, 0.36, 0.15, 0.24), ("cloud", lambda p: _cloud(p, 0.56, 0.64, 0.68))]
    if kind == "cloudy":
        return [("back", lambda p: _cloud(p, 0.62, 0.38, 0.54)),
                ("cloud", lambda p: _cloud(p, 0.45, 0.62, 0.74))]
    if kind == "showers":
        return [lamp(0.32, 0.30, 0.125, 0.2), ("cloud", lambda p: _cloud(p, 0.55, 0.47, 0.70)),
                ("rain", lambda p: _streaks(p, (0.38, 0.58, 0.78), 0.77, 0.94))]
    if kind == "fog":
        return [("cloud", lambda p: _cloud(p, 0.5, 0.36, 0.74)),
                ("fog", lambda p: (p.bar(0.14, 0.72, 0.72, 0.72, 0.075),
                                   p.bar(0.28, 0.88, 0.86, 0.88, 0.075)))]
    cloud = ("storm" if kind == "thunder" else "cloud", lambda p: _cloud(p, 0.5, 0.40, 0.80))
    falling = {
        "drizzle": [("rain", lambda p: (_streaks(p, (0.30, 0.52, 0.74), 0.73, 0.79, 0),
                                        _streaks(p, (0.41, 0.63), 0.87, 0.93, 0)))],
        "rain": [("rain", lambda p: _streaks(p, (0.32, 0.54, 0.76), 0.74, 0.94))],
        "sleet": [("rain", lambda p: _streaks(p, (0.32, 0.80), 0.74, 0.94)),
                  ("snow", lambda p: _flake(p, 0.53, 0.84, 0.095))],
        "snow": [("snow", lambda p: (_flake(p, 0.28, 0.79, 0.085), _flake(p, 0.50, 0.87, 0.085),
                                     _flake(p, 0.72, 0.79, 0.085)))],
        "thunder": [("bolt", _bolt)],
    }
    return [cloud] + falling[kind]


@functools.lru_cache(maxsize=64)
def icon(kind, size, night=False, color=None):
    """The icon for `kind` (see KINDS) as a `size` x `size` RGBA picture: in
    its own colours, or all in `color`. `night` shows the moon for the sun."""
    n = size * SCALE
    edge = max(SCALE, round(n / 28))
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    for name, shapes in _layers(kind, night and kind in NIGHT_KINDS):
        pen = _Pen(n, edge + 1)
        shapes(pen)
        if color is None:
            fill = COLORS[name]
        elif name == "back":
            fill = tuple(round(c * BACK_SHADE) for c in color)
        else:
            fill = tuple(color)
        # The black edge: the shape grown by `edge` pixels every way. Growing
        # it a pixel at a time gives what one large MaxFilter would, in a
        # third of the time.
        grown = pen.mask
        for _ in range(edge):
            grown = grown.filter(ImageFilter.MaxFilter(3))
        img.paste((0, 0, 0, 255), (0, 0), grown)
        img.paste(fill + (255,), (0, 0), pen.mask)
    return img.resize((size, size), Image.LANCZOS)
