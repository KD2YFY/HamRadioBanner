"""Ham Radio Banner - a small always-on-top HUD showing callsign, UTC/local time and weather.

Right-click the banner (or its tray icon) for Settings, Refresh Weather, updates and Quit.
"""
import configparser
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import escape

import requests
from PyQt6.QtCore import (
    QDateTime, QFileSystemWatcher, QObject, QPoint, QProcess, QStandardPaths, Qt, QTimer, QTimeZone,
    QUrl, pyqtSignal,
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtGui import QTextDocument
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QMenu, QMessageBox, QSizeGrip, QSpinBox, QSystemTrayIcon, QVBoxLayout,
    QTimeEdit, QWidget,
)

__version__ = "1.6.7-beta.1"  # must match the version typed when publishing a release
APP_NAME = "HamRadioBanner"
RELEASES_API = os.environ.get("HAMRADIOBANNER_RELEASES_API",  # override is for testing only
                              "https://api.github.com/repos/KD2YFY/HamRadioBanner/releases/latest")
RELEASES_PAGE = "https://github.com/KD2YFY/HamRadioBanner/releases/latest"
DEFAULTS = {
    "callsign": "N0CALL",
    "timezone": "",           # blank = use the computer's timezone
    "weather_refresh": "30",  # minutes
    "units": "F",             # F or C
    "font_callsign": "14",    # text sizes in points (like a document), 6-72
    "font_clock": "18",       #   UTC & local clocks (the date and labels scale with it)
    "font_weather": "14",     #   town name and temperatures
    "font_clock_label": "8",  #   the "UTC" and "New York" labels
    "font_weather_label": "8",  # the "WX" and "24h" labels
    "font_date": "10",        #   the date
    "font_conditions": "11",  #   "Cloudy", "Light snow", "Windy"...
    "show_conditions": "no",  # yes = show current conditions (clear, cloudy, rain, snow, windy...)
    "station_current": "yes", # yes = the station (NWS or your own) also gives the current temperature
    "font_ticker": "11",      #   solar & band-conditions ticker
    "show_weather": "yes",    # no = hide the weather completely (and don't download it)
    "show_hilo": "yes",       # yes = today's forecast high/low
    "show_24h": "no",         # yes = observed high/low for the 24 hours ending at day_end
    "day_end": "06:30",       # end of the 24-hour high/low period (local time, HH:MM)
    "wx24_source": "station", # station = NWS (US); model = Open-Meteo; aprs = CWOP via aprs.fi;
                              # synoptic = Synoptic Data; wu / ambient / ecowitt = your own station
    "station": "",            # NWS ID like KGFL (blank = nearest), WU station ID, or your station's MAC
    "api_key": "",            # your own station's API key (Weather Underground / Ambient / Ecowitt)
    "app_key": "",            # Ambient Weather / Ecowitt application key
    "location": "",           # blank = detect from IP; or "City, State" or "lat, lon"
    "screens": "one",         # one = a single banner; all = a banner on every monitor
    "auto_update": "no",      # yes = install new versions without asking
    "solar": "no",            # yes = scrolling solar/propagation ticker under the clock
    "ticker_speed": "normal", # slow, normal or fast
    "dock": "no",             # yes = full-width bar docked at the top; other windows stay below it
    "on_top": "yes",          # yes = stay above other windows; no = normal window with taskbar button
}


# ---------------------------------------------------------------- config ---

def app_dir():
    """Folder holding the program (works for a script and a bundled .exe)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    """Use config.ini next to the program if it is there (portable mode) or if the
    program lives in a normal folder; when installed as a package, use the user's
    config folder instead."""
    local = os.path.join(app_dir(), "config.ini")
    if os.path.exists(local):
        return local
    inside_mac_app = ".app/Contents/" in local
    if "site-packages" not in local and not inside_mac_app and os.access(app_dir(), os.W_OK):
        return local
    folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.ini")


class Config:
    def __init__(self):
        self.path = config_path()
        self.reload()

    def reload(self):
        """Re-read config.ini from disk (picks up edits made in a text editor)."""
        self.parser = configparser.ConfigParser()
        try:
            self.exists = bool(self.parser.read(self.path, encoding="utf-8"))
        except configparser.Error:
            return False  # half-saved or mistyped file: keep using the current settings
        if not self.parser.has_section("SETTINGS"):
            self.parser.add_section("SETTINGS")
        if not self.parser.has_section("WINDOW"):
            self.parser.add_section("WINDOW")
        return True

    def settings(self):
        return {k: self.get(k) for k in DEFAULTS}

    def migrate(self):
        """v1.6.0 stored text sizes as percentages; turn them into point sizes."""
        changed = False
        for part in ("callsign", "clock", "weather", "ticker"):
            old = f"size_{part}"
            if self.parser.has_option("SETTINGS", old):
                pct = max(50, min(250, _int(self.parser.get("SETTINGS", old), 100)))
                if not self.parser.has_option("SETTINGS", f"font_{part}"):
                    self.set(f"font_{part}", round(int(DEFAULTS[f"font_{part}"]) * pct / 100))
                self.parser.remove_option("SETTINGS", old)
                changed = True
        if changed and self.exists:
            try:
                self.save()
            except OSError:
                pass

    def get(self, key):
        return self.parser.get("SETTINGS", key, fallback=DEFAULTS[key]).strip()

    def set(self, key, value):
        self.parser.set("SETTINGS", key, str(value))

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("# Ham Radio Banner settings - edit here or right-click the banner > Settings\n")
            self.parser.write(f)
        self.exists = True


# --------------------------------------------------------------- weather ---

US_STATES = dict(s.split(":") for s in (
    "AL:Alabama AK:Alaska AZ:Arizona AR:Arkansas CA:California CO:Colorado CT:Connecticut "
    "DE:Delaware FL:Florida GA:Georgia HI:Hawaii ID:Idaho IL:Illinois IN:Indiana IA:Iowa "
    "KS:Kansas KY:Kentucky LA:Louisiana ME:Maine MD:Maryland MA:Massachusetts MI:Michigan "
    "MN:Minnesota MS:Mississippi MO:Missouri MT:Montana NE:Nebraska NV:Nevada NH:New_Hampshire "
    "NJ:New_Jersey NM:New_Mexico NY:New_York NC:North_Carolina ND:North_Dakota OH:Ohio "
    "OK:Oklahoma OR:Oregon PA:Pennsylvania RI:Rhode_Island SC:South_Carolina SD:South_Dakota "
    "TN:Tennessee TX:Texas UT:Utah VT:Vermont VA:Virginia WA:Washington WV:West_Virginia "
    "WI:Wisconsin WY:Wyoming DC:District_of_Columbia PR:Puerto_Rico"
).split())

class LocationNotFound(ValueError):
    pass


def _search_candidates(text):
    """Ways to split a place into (name to search, [state/country hints]).
    'Albany, NY' -> ('Albany', ['NY']).  Without commas, try trailing words as the
    hint: 'Warrensburg NY' -> ('Warrensburg NY', []), ('Warrensburg', ['NY'])."""
    if "," in text:
        parts = [p.strip() for p in text.split(",")]
        yield parts[0], [p for p in parts[1:] if p]
        return
    words = text.split()
    yield text, []
    for i in range(len(words) - 1, 0, -1):
        yield " ".join(words[:i]), [" ".join(words[i:])]


def _matches(result, hints):
    fields = {str(result.get(k, "")).lower() for k in ("admin1", "country", "country_code")}
    return all(US_STATES.get(h.upper(), h).replace("_", " ").lower() in fields for h in hints)


_location_cache = {}


def lookup_location_cached(text):
    """lookup_location, remembered for an hour (weather and solar both need it)."""
    hit = _location_cache.get(text)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    result = lookup_location(text)
    _location_cache[text] = (time.time(), result)
    return result


def _place_label(r):
    """'Gansevoort, NY' for US places, 'Albany, Western Australia, AU' elsewhere."""
    if r.get("country_code") == "US":
        abbr = next((k for k, v in US_STATES.items() if v.replace("_", " ") == r.get("admin1")), r.get("admin1", ""))
        return f"{r['name']}, {abbr}" if abbr else r["name"]
    return ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country_code")) if x)


def suggest_locations(text, limit=3):
    """Places with a similar name, for 'Did you mean...?' when a location isn't found
    (e.g. 'Ganesvoort NY' -> 'Gansevoort, NY'). Returns a list of labels."""
    import difflib
    text = " ".join(text.split())
    if "," in text:
        parts = [p.strip() for p in text.split(",")]
        name, hints = parts[0], [p for p in parts[1:] if p]
    else:
        words = text.split()
        tail = words[-1] if len(words) > 1 else ""
        is_place = tail.upper() in US_STATES or tail.lower() in {v.replace("_", " ").lower() for v in US_STATES.values()}
        name, hints = (" ".join(words[:-1]), [tail]) if is_place else (text, [])
    seen, scored = set(), []
    for prefix in dict.fromkeys((name[:3], name[:4], name[:5])):
        if len(prefix) < 3:
            continue
        try:
            res = requests.get("https://geocoding-api.open-meteo.com/v1/search", timeout=5,
                               params={"name": prefix, "count": 100, "language": "en", "format": "json"}).json()
        except (requests.RequestException, ValueError):
            continue
        for r in res.get("results") or []:
            key = (r.get("name"), r.get("admin1"), r.get("country_code"))
            if key in seen or (hints and not _matches(r, hints)):
                continue
            seen.add(key)
            score = difflib.SequenceMatcher(None, name.lower(), str(r.get("name", "")).lower()).ratio()
            if score >= 0.7:
                scored.append((score, r.get("population") or 0, _place_label(r)))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [label for _, _, label in scored[:limit]]


def lookup_location(text):
    """Return (lat, lon, name). text may be blank (IP lookup), 'lat, lon', a place name
    like 'Albany, NY' / 'Warrensburg NY' / 'London, United Kingdom', or a postal code."""
    text = " ".join(text.split())
    if not text:
        loc = requests.get("http://ip-api.com/json/", timeout=5).json()
        return loc["lat"], loc["lon"], loc["city"]
    parts = [p.strip() for p in text.split(",")]
    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1]), ""
        except ValueError:
            pass
    fallback = None
    for name, hints in _search_candidates(text):
        if len(name) < 2:
            continue
        res = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": name, "count": 20, "language": "en", "format": "json"},
            timeout=5,
        ).json()
        results = res.get("results") or []
        match = next((r for r in results if _matches(r, hints)), None)
        if match:
            return match["latitude"], match["longitude"], match["name"]
        if results and fallback is None:
            fallback = results[0]
    if fallback is not None:
        return fallback["latitude"], fallback["longitude"], fallback["name"]
    raise LocationNotFound(text)


def _parse_day_end(text):
    try:
        h, m = (int(x) for x in str(text).strip().split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except ValueError:
        pass
    return 6, 30


def window_end(now_local, day_end):
    """End of the latest completed 24-hour period: today at day_end if that has passed,
    otherwise yesterday at day_end."""
    h, m = _parse_day_end(day_end)
    end = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    if now_local < end:
        end -= timedelta(days=1)
    return end


NWS_API = "https://api.weather.gov"
_NWS_HEADERS = {"User-Agent": f"{APP_NAME}/{__version__} (github.com/KD2YFY/HamRadioBanner)",
                "Accept": "application/geo+json"}
_station_cache = {}


def nearest_nws_station(lat, lon):
    """ID of the NWS observation station nearest a point (US only), remembered for a day."""
    key = (round(lat, 2), round(lon, 2))
    hit = _station_cache.get(key)
    if hit and time.time() - hit[0] < 86400:
        return hit[1]
    point = requests.get(f"{NWS_API}/points/{lat:.4f},{lon:.4f}", headers=_NWS_HEADERS, timeout=10)
    point.raise_for_status()
    stations = requests.get(point.json()["properties"]["observationStations"],
                            headers=_NWS_HEADERS, timeout=10)
    stations.raise_for_status()
    station = stations.json()["features"][0]["properties"]["stationIdentifier"]
    _station_cache[key] = (time.time(), station)
    return station


def nws_latest(station, units):
    """The station's most recent reading if it is less than 2 hours old:
    {'temp': ..., 'text': 'Light Snow'} or None."""
    res = requests.get(f"{NWS_API}/stations/{station}/observations/latest", headers=_NWS_HEADERS, timeout=10)
    res.raise_for_status()
    props = res.json().get("properties", {})
    t = (props.get("temperature") or {}).get("value")
    when = datetime.fromisoformat(props["timestamp"].replace("Z", "+00:00"))
    if t is None or datetime.now(timezone.utc) - when > timedelta(hours=2):
        return None
    return {"temp": t if units == "C" else t * 9 / 5 + 32, "text": (props.get("textDescription") or "").strip()}


def nws_conditions(text, model_text):
    """Use the station's observed description ('Light Snow', 'Mostly Cloudy'), keeping the
    model's 'Windy' note if the station didn't mention wind."""
    text = text[:1].upper() + text[1:].lower()
    if "Windy" in model_text and "wind" not in text.lower():
        text += " · " + model_text[model_text.index("Windy"):]
    return text


def nws_high_low(station, start_utc, end_utc, units):
    """Highest/lowest temperature a real NWS station reported between two UTC times.
    Returns (high, low, number_of_readings)."""
    res = requests.get(f"{NWS_API}/stations/{station}/observations", headers=_NWS_HEADERS, timeout=15,
                       params={"start": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "end": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")})
    res.raise_for_status()
    temps = []
    for f in res.json().get("features", []):
        props = f.get("properties", {})
        t = (props.get("temperature") or {}).get("value")
        if t is None or str(props.get("temperature", {}).get("qualityControl", "")) in ("X", "Q"):
            continue  # missing, or failed the NWS quality checks
        when = datetime.fromisoformat(props["timestamp"].replace("Z", "+00:00"))
        if start_utc <= when <= end_utc:
            temps.append(t if units == "C" else t * 9 / 5 + 32)
    if len(temps) < 12:  # a station that was down most of the day isn't worth showing
        raise ValueError(f"only {len(temps)} readings from {station}")
    return max(temps), min(temps), len(temps)


# --- your own weather station -------------------------------------------------------------
# Each returns {"now": current temp or None, "temps": [readings in the window], "label": text}.
# Temperatures come back in the units asked for ("F" or "C").

PWS_SOURCES = {"wu": "Weather Underground", "ambient": "Ambient Weather", "ecowitt": "Ecowitt"}


def _f_to(units, f):
    return f if units == "F" else (f - 32) * 5 / 9


def _wu_readings(station, api_key, start_utc, end_utc, offset, units):
    """Weather Underground PWS API (api.weather.com). Needs your station ID and the API key
    from wunderground.com > My Profile > API Keys (free for station owners)."""
    if not station or not api_key:
        raise ValueError("Weather Underground needs your station ID and API key")
    base = "https://api.weather.com/v2/pws"
    u, key = ("e", "imperial") if units == "F" else ("m", "metric")
    common = {"stationId": station, "format": "json", "units": u, "apiKey": api_key, "numericPrecision": "decimal"}
    temps = []
    # History is by the station's local date, so ask for each day the window touches.
    day = (start_utc + offset).date()
    while day <= (end_utc + offset).date():
        r = requests.get(f"{base}/history/all", params={**common, "date": day.strftime("%Y%m%d")}, timeout=15)
        if r.status_code == 401:
            raise ValueError("Weather Underground rejected the API key")
        r.raise_for_status()
        for o in (r.json() if r.content else {}).get("observations", []):
            when = datetime.fromisoformat(o["obsTimeUtc"].replace("Z", "+00:00"))
            vals = o.get(key) or {}
            if start_utc <= when <= end_utc:
                temps += [v for v in (vals.get("tempHigh"), vals.get("tempLow")) if v is not None]
        day += timedelta(days=1)
    now = None
    r = requests.get(f"{base}/observations/current", params=common, timeout=15)
    if r.ok and r.content:
        obs = r.json().get("observations") or [{}]
        now = (obs[0].get(key) or {}).get("temp")
    return {"now": now, "temps": temps, "label": station}


def _ambient_readings(mac, api_key, app_key, start_utc, end_utc, units):
    """Ambient Weather API. Needs an API key and an Application key, both created at
    ambientweather.net > Account > API Keys. MAC is only needed with more than one station."""
    if not api_key or not app_key:
        raise ValueError("Ambient Weather needs your API key and Application key")
    base = "https://rt.ambientweather.net/v1/devices"
    keys = {"apiKey": api_key, "applicationKey": app_key}
    r = requests.get(base, params=keys, timeout=15)
    if r.status_code == 401:
        raise ValueError("Ambient Weather rejected the API key or Application key")
    r.raise_for_status()
    devices = r.json()
    dev = next((d for d in devices if not mac or d.get("macAddress", "").upper() == mac.upper()), None)
    if dev is None:
        raise ValueError("no Ambient Weather station found" + (f" with MAC {mac}" if mac else ""))
    time.sleep(1.1)  # Ambient allows one request per second
    end_ms = int(end_utc.timestamp() * 1000)
    r = requests.get(f"{base}/{dev['macAddress']}", params={**keys, "endDate": end_ms, "limit": 290}, timeout=15)
    r.raise_for_status()
    temps = []
    for rec in r.json():
        when = datetime.fromtimestamp(rec.get("dateutc", 0) / 1000, timezone.utc)
        if rec.get("tempf") is not None and start_utc <= when <= end_utc:
            temps.append(_f_to(units, rec["tempf"]))
    last = (dev.get("lastData") or {}).get("tempf")
    name = (dev.get("info") or {}).get("name") or "Ambient"
    return {"now": None if last is None else _f_to(units, last), "temps": temps, "label": name}


def _ecowitt_readings(mac, api_key, app_key, start_utc, end_utc, offset, units):
    """Ecowitt API v3. Needs the Application key and API key from ecowitt.net > User Profile >
    Private Center, and the station's MAC address (shown on ecowitt.net under the device)."""
    if not (mac and api_key and app_key):
        raise ValueError("Ecowitt needs your station's MAC address, API key and Application key")
    base = "https://api.ecowitt.net/api/v3/device"
    common = {"application_key": app_key, "api_key": api_key, "mac": mac,
              "temp_unitid": 2 if units == "F" else 1}
    fmt = lambda t: (t + offset).strftime("%Y-%m-%d %H:%M:%S")  # station-local time
    r = requests.get(f"{base}/history", params={**common, "start_date": fmt(start_utc), "end_date": fmt(end_utc),
                                                 "cycle_type": "5min", "call_back": "outdoor.temperature"},
                     timeout=20)
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 0:
        raise ValueError(f"Ecowitt: {body.get('msg', 'error')}")
    series = (((body.get("data") or {}).get("outdoor") or {}).get("temperature") or {}).get("list") or {}
    temps = [float(v) for ts, v in series.items()
             if v not in (None, "", "-") and start_utc <= datetime.fromtimestamp(int(ts), timezone.utc) <= end_utc]
    now = None
    r = requests.get(f"{base}/real_time", params={**common, "call_back": "outdoor"}, timeout=15)
    if r.ok and r.json().get("code") == 0:
        val = ((((r.json().get("data") or {}).get("outdoor") or {}).get("temperature")) or {}).get("value")
        now = float(val) if val not in (None, "") else None
    return {"now": now, "temps": temps, "label": "Ecowitt"}


# --- citizen weather stations near you (no station of your own needed) ----------------------

CITIZEN_SOURCES = {"aprs": "CWOP / APRS weather (aprs.fi)", "synoptic": "Synoptic Data"}
_history_lock = threading.Lock()


def _history_path():
    return os.path.join(os.path.dirname(config_path()), "station_history.json")


def record_reading(station, epoch, temp_c):
    """Keep a station's readings (last 48 hours) for stations whose service only gives the
    latest value, so the banner can work out the 24-hour high/low itself."""
    import json
    with _history_lock:
        try:
            with open(_history_path(), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        rows = {int(t): v for t, v in data.get(station, [])}
        rows[int(epoch)] = float(temp_c)
        cutoff = time.time() - 48 * 3600
        data[station] = sorted([t, v] for t, v in rows.items() if t >= cutoff)
        try:
            with open(_history_path(), "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass
        return data[station]


def _aprs_latest(call, api_key):
    """Latest weather report from a CWOP/APRS station via the aprs.fi API (free account).
    Returns (epoch, temp_c)."""
    if not call or not api_key:
        raise ValueError("aprs.fi needs a station (callsign or CWOP ID) and your aprs.fi API key")
    r = requests.get("https://api.aprs.fi/api/get", timeout=15,
                     params={"name": call, "what": "wx", "apikey": api_key, "format": "json"},
                     headers={"User-Agent": f"{APP_NAME}/{__version__} (+https://github.com/KD2YFY/HamRadioBanner)"})
    r.raise_for_status()
    body = r.json()
    if body.get("result") != "ok":
        raise ValueError(f"aprs.fi: {body.get('description') or 'request failed'}")
    entries = body.get("entries") or []
    if not entries or entries[0].get("temp") in (None, ""):
        raise ValueError(f"aprs.fi has no temperature reports from {call}")
    return int(entries[0]["time"]), float(entries[0]["temp"])


def _aprs_readings(call, api_key, start_utc, end_utc, units):
    call = call.strip().upper()
    epoch, temp_c = _aprs_latest(call, api_key)
    history = record_reading(call, epoch, temp_c)
    conv = (lambda c: c) if units == "C" else (lambda c: c * 9 / 5 + 32)
    fresh = time.time() - epoch < 2 * 3600
    s, e = start_utc.timestamp(), end_utc.timestamp()
    inside = [(t, v) for t, v in history if s <= t <= e]
    # Only trust the high/low if readings cover the whole day without a long gap (e.g. the
    # computer was off overnight and missed the low).
    stamps = [s] + [t for t, _ in inside] + [e]
    covered = len(inside) >= 12 and max(b - a for a, b in zip(stamps, stamps[1:])) <= 2 * 3600
    # How much unbroken history there is (since the last gap of over 2 hours).
    run_start = history[-1][0] if history else time.time()
    for (t0, _), (t1, _) in zip(reversed(history[:-1]), reversed(history[1:])):
        if t1 - t0 > 2 * 3600:
            break
        run_start = t0
    have_hours = (min(e, time.time()) - max(run_start, s)) / 3600 if history else 0
    return {"now": conv(temp_c) if fresh else None, "label": call,
            "temps": [conv(v) for _, v in inside] if covered else [],
            "collecting": None if covered else max(0, min(24, int(have_hours)))}


def _synoptic_readings(stid, token, lat, lon, start_utc, end_utc, units):
    """Synoptic Data API (free open-access token). Blank station = nearest active station."""
    if not token:
        raise ValueError("Synoptic Data needs your API token (free at synopticdata.com)")
    base = "https://api.synopticdata.com/v2/stations"

    def get(path, **params):
        r = requests.get(f"{base}/{path}", params={"token": token, "vars": "air_temp", **params}, timeout=20)
        r.raise_for_status()
        body = r.json()
        summary = body.get("SUMMARY") or {}
        if summary.get("RESPONSE_CODE") != 1:
            raise ValueError(f"Synoptic Data: {summary.get('RESPONSE_MESSAGE') or 'request failed'}")
        return body.get("STATION") or []

    stid = stid.strip().upper()
    if not stid:  # the nearest active station reporting temperature, within 15 miles
        near = get("latest", radius=f"{lat:.4f},{lon:.4f},15", limit=1, status="active")
        if not near:
            raise ValueError("Synoptic Data found no station within 15 miles")
        stid = near[0]["STID"]
    now_utc = datetime.now(timezone.utc)
    st = get("timeseries", stid=stid, start=start_utc.strftime("%Y%m%d%H%M"),
             end=max(end_utc, now_utc).strftime("%Y%m%d%H%M"), obtimezone="UTC", units="metric")
    if not st:
        raise ValueError(f"Synoptic Data has no readings for {stid}")
    obs = st[0].get("OBSERVATIONS") or {}
    times = obs.get("date_time") or []
    values = next((v for k, v in obs.items() if k.startswith("air_temp_set")), [])
    conv = (lambda c: c) if units == "C" else (lambda c: c * 9 / 5 + 32)
    readings = [(datetime.fromisoformat(t.replace("Z", "+00:00")), v) for t, v in zip(times, values) if v is not None]
    temps = [conv(v) for t, v in readings if start_utc <= t <= end_utc]
    now = None
    if readings and now_utc - readings[-1][0] < timedelta(hours=2):
        now = conv(readings[-1][1])
    return {"now": now, "temps": temps, "label": stid, "collecting": None}


def pws_readings(source, station, api_key, app_key, start_utc, end_utc, offset, units):
    if source == "wu":
        return _wu_readings(station, api_key, start_utc, end_utc, offset, units)
    if source == "ambient":
        return _ambient_readings(station, api_key, app_key, start_utc, end_utc, units)
    if source == "ecowitt":
        return _ecowitt_readings(station, api_key, app_key, start_utc, end_utc, offset, units)
    raise ValueError(f"unknown station type {source}")


_WMO = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy", 45: "Fog", 48: "Freezing fog",
        51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
        61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
        71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Rain showers",
        81: "Rain showers", 82: "Heavy showers", 85: "Snow showers", 86: "Heavy snow showers",
        95: "Thunderstorm", 96: "Thunderstorm, hail", 99: "Thunderstorm, hail"}


def conditions_text(code, is_day, wind, gust, units):
    """Plain words for the weather: 'Sunny', 'Light snow', 'Cloudy · Windy (gusts 34 mph)'."""
    text = _WMO.get(code, "")
    if is_day and code in (0, 1):
        text = "Sunny" if code == 0 else "Mostly sunny"
    windy_at, gusty_at, unit = (20, 30, "mph") if units == "F" else (32, 48, "km/h")
    if (wind or 0) >= windy_at or (gust or 0) >= gusty_at:
        windy = f"Windy (gusts {round(gust)} {unit})" if gust else "Windy"
        text = f"{text} · {windy}" if text else windy
    return text


def fetch_weather(location, units, day_end="06:30", source="station", station="", api_key="", app_key="",
                  station_current=True, want24=True):
    """Current temperature and conditions, today's forecast high/low, and the high/low over the
    24 hours ending at day_end. Model data comes from Open-Meteo; a weather station (NWS or your
    own) can supply the 24-hour high/low and, with station_current, the current temperature."""
    lat, lon, name = lookup_location_cached(location)
    res = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,weather_code,wind_speed_10m,wind_gusts_10m,is_day",
            "wind_speed_unit": "mph" if units == "F" else "kmh",
            "daily": "temperature_2m_max,temperature_2m_min",
            "minutely_15": "temperature_2m",
            "hourly": "temperature_2m",
            "temperature_unit": "celsius" if units == "C" else "fahrenheit",
            "timezone": "auto", "past_days": 2, "forecast_days": 1,
        },
        timeout=10,
    ).json()
    now_local = datetime.fromisoformat(res["current"]["time"])
    today = now_local.date().isoformat()
    days = res["daily"]["time"]
    i = days.index(today) if today in days else len(days) - 1
    out = {
        "name": name, "now": res["current"]["temperature_2m"],
        "hi": res["daily"]["temperature_2m_max"][i], "lo": res["daily"]["temperature_2m_min"][i],
        "utc_offset": res.get("utc_offset_seconds", 0), "hi24": None, "lo24": None,
        "conditions": conditions_text(res["current"].get("weather_code"), res["current"].get("is_day"),
                                      res["current"].get("wind_speed_10m"), res["current"].get("wind_gusts_10m"),
                                      units),
    }
    end = window_end(now_local, day_end)
    start = end - timedelta(hours=24)
    for series in ("minutely_15", "hourly"):
        data = res.get(series) or {}
        temps = [t for when, t in zip(data.get("time", []), data.get("temperature_2m", []))
                 if t is not None and start <= datetime.fromisoformat(when) <= end]
        if len(temps) >= (48 if series == "minutely_15" else 20):  # enough to cover the day
            out["hi24"], out["lo24"] = max(temps), min(temps)
            break
    out["window_end"] = end.isoformat()
    out["source24"] = "model"
    if source == "station":
        # Real thermometer readings from the nearest (or chosen) NWS station.
        sid = None
        try:
            sid = station.strip().upper() or nearest_nws_station(lat, lon)
        except Exception as e:  # outside the US, NWS unreachable... keep the model numbers
            print(f"No NWS station, using model data: {e!r}", file=sys.stderr)
            out["source24"], out["station_error"] = "model (no station)", str(e)
        if sid and station_current:
            try:
                latest = nws_latest(sid, units)
                if latest:
                    out["now"], out["now_source"] = latest["temp"], sid
                    if latest.get("text"):
                        out["conditions"] = nws_conditions(latest["text"], out["conditions"])
            except Exception as e:
                print(f"NWS latest reading unavailable: {e!r}", file=sys.stderr)
        if sid and want24:
            try:
                offset = timedelta(seconds=out["utc_offset"])
                to_utc = lambda local: (local - offset).replace(tzinfo=timezone.utc)
                hi, lo, n = nws_high_low(sid, to_utc(start), to_utc(end), units)
                out["hi24"], out["lo24"], out["source24"], out["readings24"] = hi, lo, sid, n
            except Exception as e:  # station offline most of the day...
                print(f"NWS station data unavailable, using model data: {e!r}", file=sys.stderr)
                out["source24"], out["station_error"] = "model (no station)", str(e)
    elif source in PWS_SOURCES or source in CITIZEN_SOURCES:
        # Your own station, or a citizen station near you: current temperature and 24-hour high/low.
        try:
            offset = timedelta(seconds=out["utc_offset"])
            to_utc = lambda local: (local - offset).replace(tzinfo=timezone.utc)
            if source == "aprs":
                got = _aprs_readings(station, api_key.strip(), to_utc(start), to_utc(end), units)
            elif source == "synoptic":
                got = _synoptic_readings(station, api_key.strip(), lat, lon, to_utc(start), to_utc(end), units)
            else:
                got = pws_readings(source, station.strip(), api_key.strip(), app_key.strip(),
                                   to_utc(start), to_utc(end), offset, units)
            if got["now"] is not None and station_current:
                out["now"], out["now_source"] = got["now"], got["label"]
            tag = "home" if source in PWS_SOURCES else got["label"]
            if got.get("collecting") is not None:
                # aprs.fi only gives the latest reading: the banner is still building a day of history.
                out["source24"], out["collecting"] = f"collecting {tag}", got["collecting"]
            elif len(got["temps"]) >= 12:
                out["hi24"], out["lo24"] = max(got["temps"]), min(got["temps"])
                out["source24"], out["readings24"] = tag, len(got["temps"])
            else:
                out["source24"] = "model (no station)"
                out["station_error"] = f"only {len(got['temps'])} readings in the 24 hours"
        except Exception as e:
            print(f"Station data unavailable, using model data: {e!r}", file=sys.stderr)
            out["source24"] = "model (no station)"
            out["station_error"] = str(e)
    return out


# ----------------------------------------------------------------- solar ---

# N0NBH's solar-terrestrial report, via KD2YFY.net's edge-cached copy (kinder to HamQSL),
# falling back to HamQSL itself.
SOLAR_URLS = ["https://kd2yfy.net/api/hamqsl", "https://www.hamqsl.com/solarxml.php"]
# NOAA aurora model + 30-minute average Bz, pre-digested by KD2YFY.net (functions/api/aurora.js).
AURORA_URL = "https://kd2yfy.net/api/aurora"
KP_FORECAST_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
_UA = {"User-Agent": f"{APP_NAME}/{__version__}"}
GOOD, FAIR, POOR, DIM = "#67C23A", "#E6A23C", "#F56C6C", "#888888"


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _rated(label, value, color):
    return (f"<span style='color:#9aa4b2;'>{label}</span> "
            f"<span style='color:{color};'>{escape(str(value))}</span>")


def _get_hamqsl():
    last = None
    for url in SOLAR_URLS:
        try:
            res = requests.get(url, timeout=10, headers=_UA)
            res.raise_for_status()
            d = ET.fromstring(res.content).find("solardata")
            if d is not None:
                return d
            last = ValueError(f"no <solardata> from {url}")
        except Exception as e:  # try the next source
            last = e
    raise last


def _records(data):
    """NOAA JSON comes either as a list of objects or as rows with a header row."""
    if not isinstance(data, list):
        data = [data] if data else []
    if data and isinstance(data[0], list):
        header = [str(h).lower() for h in data[0]]
        return [dict(zip(header, row)) for row in data[1:]]
    return [{str(k).lower(): v for k, v in o.items()} for o in data if isinstance(o, dict)]


def _kp_forecast_max():
    """Highest NOAA-predicted Kp in the next 24 hours: (kp, datetime) or None."""
    now = time.time()
    best = None
    for r in _records(requests.get(KP_FORECAST_URL, timeout=10, headers=_UA).json()):
        if r.get("observed") != "predicted":
            continue
        kp = _num(r.get("kp"))
        try:
            t = datetime.strptime(str(r.get("time_tag"))[:19].replace("T", " "),
                                  "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if kp is None or not (now - 3 * 3600 <= t.timestamp() <= now + 24 * 3600):
            continue
        if best is None or kp > best[0]:
            best = (kp, t)
    return best


def sun_elevation(when, lat, lon):
    """Sun's elevation in degrees (same formula as KD2YFY.net/solar)."""
    rad = math.pi / 180
    d = when.timestamp() / 86400 - 10957.5                     # days since J2000.0
    g = (357.529 + 0.98560028 * d) * rad                       # mean anomaly
    q = 280.459 + 0.98564736 * d                               # mean longitude
    L = (q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) * rad
    e = (23.439 - 0.00000036 * d) * rad                        # obliquity
    ra = math.atan2(math.cos(e) * math.sin(L), math.cos(L)) / rad
    dec = math.asin(math.sin(e) * math.sin(L))
    gmst = (18.697374558 + 24.06570982441908 * d) % 24
    ha = (gmst * 15 + lon - ra) * rad
    return math.asin(math.sin(lat * rad) * math.sin(dec) +
                     math.cos(lat * rad) * math.cos(dec) * math.cos(ha)) / rad


def vhf_status(k, sfi, speed, bz30, aurora_north, kp_fc, is_day, now):
    """VHF outlook using the same rules as KD2YFY.net/solar. Returns (text, colour)."""
    k = k if k is not None else 0
    sfi = sfi if sfi is not None else 0
    big = float("inf")
    bz = bz30 if bz30 is not None else big
    north = aurora_north if aurora_north is not None else -1
    fc = kp_fc[0] if kp_fc else 0
    if k >= 6 or north >= 20 or (k >= 5 and bz <= -10):
        return "AURORA LIKELY - beam north on 6m/2m", "#c084fc"
    if k >= 5 or north >= 5 or bz <= -8 or (bz <= -5 and (speed or 0) >= 500) or fc >= 6:
        return "AURORA WATCH - monitor 6m/2m to the north", "#d8b4fe"
    month = now.month
    if (month >= 9 or month <= 4) and sfi >= 150 and is_day:
        return "POSSIBLE 6M F2 - check 50.313 FT8", GOOD
    if 5 <= month <= 8:
        return "E-SKIP SEASON - watch 6m & 10m", "#fde047"
    if (month == 12 and now.day >= 15) or (month == 1 and now.day <= 15):
        return "FLAT / TROPO - winter Es peak, short 6m openings", DIM
    return "FLAT / TROPO", DIM


def fetch_solar(location=""):
    """Download the solar/propagation report and return it as coloured ticker HTML."""
    d = _get_hamqsl()
    t = lambda tag: (d.findtext(tag) or "").strip()
    sep = " <span style='color:#4a4a5a;'>│</span> "
    parts = ["<span style='color:#E6A23C;'>☀ SOLAR</span>"]

    sfi, ssn, a, k = _num(t("solarflux")), t("sunspots"), _num(t("aindex")), _num(t("kindex"))
    if sfi is not None:
        parts.append(_rated("SFI", int(sfi), GOOD if sfi >= 120 else FAIR if sfi >= 90 else POOR))
    if ssn:
        parts.append(_rated("SSN", ssn, "#ffffff"))
    if a is not None:
        parts.append(_rated("A", int(a), GOOD if a < 10 else FAIR if a < 30 else POOR))
    if k is not None:
        parts.append(_rated("K", int(k), GOOD if k <= 2 else FAIR if k <= 4 else POOR))
    xray = t("xray")
    if xray:
        parts.append(_rated("X-ray", xray, GOOD if xray[:1] in "AB" else FAIR if xray[:1] == "C" else POOR))
    wind = _num(t("solarwind"))
    if wind is not None:
        parts.append(_rated("Wind", f"{int(wind)} km/s", GOOD if wind < 500 else FAIR if wind < 700 else POOR))
    bz = _num(t("magneticfield"))
    if bz is not None:
        parts.append(_rated("Bz", f"{bz:+.1f}", GOOD if bz > -5 else FAIR if bz > -10 else POOR))
    geo = t("geomagfield")
    if geo:
        g = geo.upper()
        parts.append(_rated("Geomag", geo.title(), POOR if "STORM" in g else FAIR if ("ACTIVE" in g or "UNSET" in g)
                            else GOOD))
    noise = t("signalnoise")
    if noise:
        parts.append(_rated("Noise", noise, "#ffffff"))

    # HF band conditions, day / night
    bands = {}
    for b in d.findall("calculatedconditions/band"):
        bands.setdefault(b.get("name", "?"), {})[b.get("time", "")] = (b.text or "").strip()
    if bands:
        color = lambda v: {"good": GOOD, "fair": FAIR, "poor": POOR}.get(v.lower(), DIM)
        hf = ["<span style='color:#409EFF;'>HF</span> <span style='color:#9aa4b2;'>day/night</span>"]
        for name, times in bands.items():
            day, night = times.get("day", "?"), times.get("night", "?")
            hf.append(f"<span style='color:#9aa4b2;'>{escape(name)}</span> "
                      f"<span style='color:{color(day)};'>{escape(day)}</span>/"
                      f"<span style='color:{color(night)};'>{escape(night)}</span>")
        parts.append(" &nbsp;".join(hf))

    # VHF outlook: aurora (NOAA model + Bz via KD2YFY.net), Kp forecast, 6m F2 / Es season.
    # Each extra source may fail on its own without breaking the ticker.
    now = datetime.now(timezone.utc)
    lat = lon = None
    try:
        lat, lon, _ = lookup_location_cached(location)
    except Exception:
        pass
    aurora = {}
    try:
        params = {"lat": round(lat, 1), "lon": round(lon, 1)} if lat is not None else None
        aurora = requests.get(AURORA_URL, params=params, timeout=10, headers=_UA).json()
    except Exception as e:
        print(f"Aurora data error: {e!r}", file=sys.stderr)
    bz30 = _num((aurora.get("bz30") or {}).get("avg"))
    north = _num((aurora.get("aurora") or {}).get("north"))
    try:
        kp_fc = _kp_forecast_max()
    except Exception as e:
        print(f"Kp forecast error: {e!r}", file=sys.stderr)
        kp_fc = None
    is_day = sun_elevation(now, lat, lon) > 0 if lat is not None else 6 <= now.hour < 18
    text, color = vhf_status(k, sfi, wind, bz30, north, kp_fc, is_day, now)
    detail = []
    if k is not None:
        detail.append(f"Kp {k:.1f}")
    if bz30 is not None:
        detail.append(f"Bz 30m avg {bz30:+.1f} nT")
    if north is not None:
        detail.append(f"Aurora N {int(north)}%")
    if kp_fc:
        detail.append(f"Kp fcst max {kp_fc[0]:.1f} @ {kp_fc[1]:%H}Z")
    parts.append(f"<span style='color:#409EFF;'>VHF</span> <span style='color:{color};'>{escape(text)}</span>"
                 + (f" <span style='color:#9aa4b2;'>({escape(' · '.join(detail))})</span>" if detail else ""))

    # HamQSL's sporadic-E / aurora flags
    names = {("vhf-aurora", "northern_hemi"): "Aurora", ("e-skip", "north_america"): "2m Es NA",
             ("e-skip", "europe"): "2m Es EU", ("e-skip", "europe_6m"): "6m Es EU",
             ("e-skip", "europe_4m"): "4m Es EU"}
    vhf = []
    for ph in d.findall("calculatedvhfconditions/phenomenon"):
        key = (ph.get("name", "").lower(), ph.get("location", "").lower())
        label = names.get(key, f"{ph.get('name', '')} {ph.get('location', '')}".strip())
        value = (ph.text or "").strip()
        is_open = value and "closed" not in value.lower()
        vhf.append(f"<span style='color:#9aa4b2;'>{escape(label)}</span> "
                   f"<span style='color:{GOOD if is_open else DIM};'>"
                   f"{escape(value if is_open else 'Closed')}</span>")
    if vhf:
        parts.append("<span style='color:#409EFF;'>Es</span> " + " &nbsp;".join(vhf))

    updated = t("updated")
    if updated:
        parts.append(f"<span style='color:#666;'>Updated {escape(updated)} · N0NBH hamqsl.com</span>")
    return sep.join(parts)


class Ticker(QWidget):
    """A line of coloured text that scrolls right-to-left and repeats."""
    SPEEDS = {"slow": 30, "normal": 55, "fast": 90}  # pixels per second

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ticker")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # drag the banner through it
        self.doc = QTextDocument()
        self.doc.setDocumentMargin(0)
        self.doc.setDefaultStyleSheet("body { color: white; }")
        self._html = None
        self.offset = 0.0
        self.speed = self.SPEEDS["normal"]
        self.setFixedHeight(26)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._step)
        self.timer.start(33)

    def set_html(self, html, font):
        if (html, font.pointSize()) == self._html:
            return
        self._html = (html, font.pointSize())
        self.doc.setDefaultFont(font)
        self.doc.setHtml(f"<span style='white-space:nowrap;'>{html}</span>")
        self.doc.setTextWidth(-1)
        self.setFixedHeight(int(self.doc.size().height()) + 6)
        self.update()

    def set_speed(self, name):
        self.speed = self.SPEEDS.get(name, self.SPEEDS["normal"])

    def _step(self):
        if self.isVisible() and self.doc.idealWidth() > 0:
            self.offset = (self.offset + self.speed * 0.033) % (self.doc.idealWidth() + 120)
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setClipRect(self.rect())
        width = self.doc.idealWidth() + 120  # gap before the text repeats
        x = -self.offset
        y = (self.height() - self.doc.size().height()) / 2
        while x < self.width():
            p.save()
            p.translate(x, y)
            self.doc.drawContents(p)
            p.restore()
            x += width
        p.end()


# ---------------------------------------------------------------- update ---

def _version_tuple(v):
    """'v1.2.3' -> (1, 2, 3, 0); 'v1.3.0-beta.2' -> (1, 3, 0, -1, 2), which sorts below the
    final 1.3.0; anything unparseable -> ()"""
    m = re.match(r"^[vV]?(\d+(?:\.\d+)*)(?:-[A-Za-z]*\.?(\d*))?$", v.strip())
    if not m:
        return ()
    nums = tuple(int(x) for x in m.group(1).split("."))
    if "-" in v:
        return nums + (-1, int(m.group(2) or 0))
    return nums + (0,)


def _platform_zip():
    if sys.platform.startswith("win"):
        return "HamRadioBanner-Windows.zip"
    if sys.platform == "darwin":
        return "HamRadioBanner-macOS.zip"
    return "HamRadioBanner-Linux.zip"


def _mac_app_bundle():
    """Path of HamRadioBanner.app when running as a bundled Mac app."""
    return os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", ".."))


def self_update_mode():
    """How this copy can update itself: 'app' (downloaded app), 'source' (downloaded
    code run by the launcher), or None (git checkout / pipx install / read-only folder)."""
    folder = app_dir()
    if getattr(sys, "frozen", False):
        target = os.path.dirname(_mac_app_bundle()) if sys.platform == "darwin" else folder
        return "app" if os.access(target, os.W_OK) else None
    if ("site-packages" in folder or os.path.exists(os.path.join(folder, ".git"))
            or not os.access(folder, os.W_OK)):
        return None
    return "source"


def check_for_update():
    """Return {'version', 'page', 'download'} for a newer GitHub release, or None.
    'download' is empty when this copy can't install it by itself."""
    res = requests.get(RELEASES_API, timeout=10,
                       headers={"Accept": "application/vnd.github+json"}).json()
    latest = res.get("tag_name", "")
    if _version_tuple(latest) <= _version_tuple(__version__):
        return None
    download = ""
    mode = self_update_mode()
    if mode == "app":
        download = next((a.get("browser_download_url", "") for a in res.get("assets", [])
                         if a.get("name") == _platform_zip()), "")
    elif mode == "source":
        download = res.get("zipball_url", "")
    return {"version": latest, "page": res.get("html_url") or RELEASES_PAGE, "download": download}


def _extract(zip_path, dest):
    """Unzip, keeping the 'executable' permission on macOS/Linux."""
    if sys.platform == "darwin":  # ditto keeps the symlinks inside a .app bundle
        subprocess.run(["ditto", "-x", "-k", zip_path, dest], check=True)
        return
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            path = z.extract(info, dest)
            mode = info.external_attr >> 16
            if mode and os.name == "posix":
                os.chmod(path, mode & 0o7777)


def _clean_env():
    """Environment for helper programs. A bundled Linux app points LD_LIBRARY_PATH at its
    private libraries, which can crash system tools like cp - put the original back."""
    env = dict(os.environ)
    for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        orig = env.pop(key + "_ORIG", None)
        if orig is not None:
            env[key] = orig
        elif getattr(sys, "frozen", False):
            env.pop(key, None)
    return env


def prepare_update(download_url):
    """Download and unpack an update. Returns a function that finishes the install
    once the banner has quit (or raises if anything looks wrong - nothing is changed
    until then)."""
    work = tempfile.mkdtemp(prefix="hamradiobanner-update-")
    zip_path = os.path.join(work, "update.zip")
    with requests.get(download_url, timeout=60, stream=True) as r:
        r.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                f.write(chunk)
    unpacked = os.path.join(work, "new")
    _extract(zip_path, unpacked)
    mode = self_update_mode()

    if mode == "source":
        tops = [d for d in os.listdir(unpacked) if os.path.isdir(os.path.join(unpacked, d))]
        src = os.path.join(unpacked, tops[0]) if len(tops) == 1 else unpacked
        if not os.path.exists(os.path.join(src, "banner.py")):
            raise RuntimeError("download did not contain banner.py")

        def finish():
            # Plain files can be replaced while running; then start the new copy.
            for root, dirs, files in os.walk(src):
                dirs[:] = [d for d in dirs if d not in (".git", ".github")]
                rel = os.path.relpath(root, src)
                os.makedirs(os.path.join(app_dir(), rel), exist_ok=True)
                for name in files:
                    if name != "config.ini":
                        shutil.copy2(os.path.join(root, name), os.path.join(app_dir(), rel, name))
            QProcess.startDetached(sys.executable, [os.path.join(app_dir(), "banner.py")], app_dir())
            shutil.rmtree(work, ignore_errors=True)
        return finish

    exe_name = os.path.basename(sys.executable)
    if sys.platform == "darwin":
        new_app = os.path.join(unpacked, "HamRadioBanner.app")
        if not os.path.exists(os.path.join(new_app, "Contents", "MacOS", exe_name)):
            raise RuntimeError("download did not contain the app")
        script = os.path.join(work, "update.sh")
        with open(script, "w") as f:
            f.write(f"""#!/bin/sh
while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done
rm -rf "{_mac_app_bundle()}" && mv "{new_app}" "{_mac_app_bundle()}"
open "{_mac_app_bundle()}"
rm -rf "{work}"
""")
        return lambda: subprocess.Popen(["/bin/sh", script], start_new_session=True, env=_clean_env())

    src = os.path.join(unpacked, "HamRadioBanner")
    if not os.path.exists(os.path.join(src, exe_name)):
        raise RuntimeError("download did not contain the program")
    if sys.platform.startswith("win"):
        script = os.path.join(work, "update.bat")
        with open(script, "w") as f:
            # robocopy retries files that are still locked while the old banner exits.
            f.write(f"""@echo off
ping -n 3 127.0.0.1 >nul
robocopy "{src}" "{app_dir()}" /E /R:30 /W:1 /NFL /NDL /NJH /NJS /NP >nul
start "" "{sys.executable}"
(goto) 2>nul & rd /s /q "{work}"
""")
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        return lambda: subprocess.Popen(["cmd.exe", "/c", script], creationflags=flags, close_fds=True,
                                        env=_clean_env())

    script = os.path.join(work, "update.sh")
    with open(script, "w") as f:
        f.write(f"""#!/bin/sh
while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done
cp -Rf "{src}/." "{app_dir()}/"
nohup "{sys.executable}" >/dev/null 2>&1 &
rm -rf "{work}"
""")
    return lambda: subprocess.Popen(["/bin/sh", script], start_new_session=True, env=_clean_env())


# ------------------------------------------------------- start with computer ---

AUTOSTART_NAME = "HamRadioBanner"
_MAC_PLIST = os.path.expanduser("~/Library/LaunchAgents/com.kd2yfy.hamradiobanner.plist")
_LINUX_DESKTOP = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                              "autostart", "hamradiobanner.desktop")


def launch_command():
    """The command that starts this copy of the banner."""
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            return ["/usr/bin/open", _mac_app_bundle()]
        return [sys.executable]
    python = sys.executable
    if sys.platform.startswith("win"):  # pythonw = no console window
        pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
        python = pythonw if os.path.exists(pythonw) else python
    return [python, os.path.abspath(__file__)]


def autostart_enabled():
    try:
        if sys.platform.startswith("win"):
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
        if sys.platform == "darwin":
            return os.path.exists(_MAC_PLIST)
        return os.path.exists(_LINUX_DESKTOP)
    except OSError:
        return False


def set_autostart(enabled):
    """Add or remove the banner from the programs that start when you log in."""
    cmd = launch_command()
    if sys.platform.startswith("win"):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
        return
    path = _MAC_PLIST if sys.platform == "darwin" else _LINUX_DESKTOP
    if not enabled:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if sys.platform == "darwin":
        import plistlib
        with open(path, "wb") as f:
            plistlib.dump({"Label": "com.kd2yfy.hamradiobanner", "ProgramArguments": cmd,
                           "RunAtLoad": True}, f)
    else:
        import shlex
        with open(path, "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Ham Radio Banner\n"
                    f"Exec={' '.join(shlex.quote(c) for c in cmd)}\n"
                    "X-GNOME-Autostart-enabled=true\n")


def refresh_autostart():
    """If start-at-login is on, point it at this copy (in case the folder was moved)."""
    try:
        if autostart_enabled():
            set_autostart(True)
    except OSError:
        pass


# ---------------------------------------------------------------- docking ---
# Docked = a full-width bar at the top of the screen that other windows can't cover:
# Windows "app bar" (like the taskbar) or X11 "struts" (like a Linux panel).

_APPBAR_CALLBACK = 0x0400 + 0x1A7  # WM_USER + n: our private message for app-bar notices
ABN_POSCHANGED, ABN_FULLSCREENAPP = 1, 2


def _win_appbar(message, hwnd, rect=None):
    """Send an app-bar message (ABM_NEW=0, ABM_REMOVE=1, ABM_QUERYPOS=2, ABM_SETPOS=3)."""
    import ctypes
    from ctypes import wintypes

    class APPBARDATA(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                    ("uCallbackMessage", wintypes.UINT), ("uEdge", wintypes.UINT),
                    ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]

    abd = APPBARDATA()
    abd.cbSize = ctypes.sizeof(APPBARDATA)
    abd.hWnd = hwnd
    abd.uCallbackMessage = _APPBAR_CALLBACK
    abd.uEdge = 1  # ABE_TOP
    if rect:
        abd.rc.left, abd.rc.top, abd.rc.right, abd.rc.bottom = rect
    shell32 = ctypes.windll.shell32
    shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(APPBARDATA)]
    shell32.SHAppBarMessage.restype = ctypes.c_size_t
    shell32.SHAppBarMessage(message, ctypes.byref(abd))
    return abd.rc.left, abd.rc.top, abd.rc.right, abd.rc.bottom


def _win_monitor_rect(hwnd):
    """The full rectangle (physical pixels) of the monitor a window is on."""
    import ctypes
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))  # 2 = nearest
    r = info.rcMonitor
    return r.left, r.top, r.right, r.bottom


def _x11_strut(wid, strut=None):
    """Reserve (or with strut=None, release) space at the top of the screen on X11."""
    import ctypes
    import ctypes.util
    xlib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    xlib.XInternAtom.restype = ctypes.c_ulong
    xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    xlib.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    xlib.XDeleteProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
    xlib.XFlush.argtypes = xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    dpy = xlib.XOpenDisplay(None)
    if not dpy:
        return
    try:
        partial = xlib.XInternAtom(dpy, b"_NET_WM_STRUT_PARTIAL", 0)
        simple = xlib.XInternAtom(dpy, b"_NET_WM_STRUT", 0)
        if strut is None:
            xlib.XDeleteProperty(dpy, wid, partial)
            xlib.XDeleteProperty(dpy, wid, simple)
        else:
            top, x0, x1 = strut
            values = (ctypes.c_long * 12)(0, 0, top, 0, 0, 0, 0, 0, x0, x1, 0, 0)
            xlib.XChangeProperty(dpy, wid, partial, 6, 32, 0, values, 12)   # 6 = CARDINAL
            xlib.XChangeProperty(dpy, wid, simple, 6, 32, 0, values, 4)
        xlib.XFlush(dpy)
    finally:
        xlib.XCloseDisplay(dpy)


# ------------------------------------------------------- desktop shortcut ---

SHORTCUT_NAME = "Ham Radio Banner"


def _icon_file(ext):
    """Save the app icon as a file (for shortcuts of the non-bundled program)."""
    folder = os.path.join(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericDataLocation), "icons")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"hamradiobanner.{ext}")
    draw_icon(256).save(path)
    return path


def _desktop_dir():
    if sys.platform.startswith("linux"):
        try:
            out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True,
                                 timeout=5, env=_clean_env()).stdout.strip()
            if out and os.path.isdir(out):
                return out
        except (OSError, subprocess.SubprocessError):
            pass
    return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)


def _ps_quote(text):
    return "'" + str(text).replace("'", "''") + "'"


def create_desktop_shortcut():
    """Put a 'Ham Radio Banner' shortcut with the app icon on the desktop. Returns its path."""
    cmd = launch_command()
    if sys.platform.startswith("win"):
        frozen = getattr(sys, "frozen", False)
        icon = sys.executable if frozen else _icon_file("ico")
        args = subprocess.list2cmdline(cmd[1:])
        workdir = os.path.dirname(cmd[-1] if not frozen else sys.executable)
        # The Desktop can be redirected (e.g. to OneDrive), so ask Windows where it is.
        script = (
            "$d=[Environment]::GetFolderPath('Desktop');"
            f"$l=Join-Path $d {_ps_quote(SHORTCUT_NAME + '.lnk')};"
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($l);"
            f"$s.TargetPath={_ps_quote(cmd[0])};$s.Arguments={_ps_quote(args)};"
            f"$s.WorkingDirectory={_ps_quote(workdir)};$s.IconLocation={_ps_quote(icon + ',0')};"
            "$s.Description='Callsign, UTC/local time, weather and propagation banner';"
            "$s.Save();Write-Output $l")
        out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                             capture_output=True, text=True, timeout=30, env=_clean_env(),
                             creationflags=subprocess.CREATE_NO_WINDOW)
        if out.returncode != 0:
            raise OSError(out.stderr.strip() or "PowerShell could not create the shortcut")
        return out.stdout.strip()

    desktop = _desktop_dir()
    os.makedirs(desktop, exist_ok=True)
    if sys.platform == "darwin":
        if getattr(sys, "frozen", False):  # an alias-style link to the app shows its icon
            link = os.path.join(desktop, SHORTCUT_NAME)
            if os.path.islink(link):
                os.remove(link)
            os.symlink(_mac_app_bundle(), link)
            return link
        import shlex
        link = os.path.join(desktop, SHORTCUT_NAME + ".command")
        with open(link, "w") as f:
            f.write("#!/bin/sh\n" + " ".join(shlex.quote(c) for c in cmd) + " >/dev/null 2>&1 &\n")
        os.chmod(link, 0o755)
        return link

    import shlex
    entry = ("[Desktop Entry]\nType=Application\nName=Ham Radio Banner\n"
             "Comment=Callsign, UTC/local time, weather and propagation banner\n"
             f"Exec={' '.join(shlex.quote(c) for c in cmd)}\nIcon={_icon_file('png')}\n"
             "Terminal=false\nCategories=Utility;HamRadio;\n")
    link = os.path.join(desktop, "hamradiobanner.desktop")
    with open(link, "w", encoding="utf-8") as f:
        f.write(entry)
    os.chmod(link, 0o755)
    try:  # GNOME only launches desktop files marked as trusted
        subprocess.run(["gio", "set", link, "metadata::trusted", "true"], capture_output=True,
                       timeout=5, env=_clean_env())
    except (OSError, subprocess.SubprocessError):
        pass
    apps = os.path.join(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.ApplicationsLocation), "hamradiobanner.desktop")
    try:  # also list it in the applications menu
        os.makedirs(os.path.dirname(apps), exist_ok=True)
        with open(apps, "w", encoding="utf-8") as f:
            f.write(entry)
    except OSError:
        pass
    return link


def ensure_desktop_shortcut_once(config):
    """Create the desktop shortcut automatically one time per install. If the user deletes
    it, it is not recreated (the Settings button brings it back)."""
    if not config.exists or not config.reload():
        return
    if config.parser.has_option("STATE", "desktop_shortcut"):
        return
    try:
        create_desktop_shortcut()
        result = "created"
    except Exception as e:
        print(f"Desktop shortcut failed: {e!r}", file=sys.stderr)
        result = "failed"
    if not config.parser.has_section("STATE"):
        config.parser.add_section("STATE")
    config.parser.set("STATE", "desktop_shortcut", result)
    try:
        config.save()
    except OSError:
        pass


# -------------------------------------------------------------- settings ---

def _ui_images():
    """Draw the check mark and drop-down arrow used by the Settings window once, as PNG
    files (style sheets need image files; this way none have to ship with the program)."""
    folder = os.path.join(tempfile.gettempdir(), f"hamradiobanner-ui-{__version__}")
    check, arrow = os.path.join(folder, "check.png"), os.path.join(folder, "arrow.png")
    if not (os.path.exists(check) and os.path.exists(arrow)):
        os.makedirs(folder, exist_ok=True)
        for path, points in ((check, [(4, 10), (8, 14), (16, 5)]), (arrow, [(5, 8), (10, 13), (15, 8)])):
            pix = QPixmap(20, 20)
            pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = p.pen()
            pen.setColor(QColor("#101015" if path == check else "#e6e6e6"))
            pen.setWidthF(2.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                p.drawLine(x1, y1, x2, y2)
            p.end()
            pix.save(path)
    return check.replace("\\", "/"), arrow.replace("\\", "/")


def settings_style():
    check, arrow = _ui_images()
    return f"""
        QDialog {{ background: #1b1b22; }}
        QLabel, QCheckBox {{ color: #e6e6e6; font-size: 13px; }}
        QLineEdit, QComboBox, QSpinBox, QTimeEdit {{
            background: #2a2a35; color: #ffffff; font-size: 13px;
            border: 1px solid #4a4a5a; border-radius: 5px; padding: 5px 8px; min-height: 18px;
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTimeEdit:focus {{ border-color: #409EFF; }}
        QComboBox::drop-down {{ border: none; width: 24px; }}
        QComboBox::down-arrow {{ image: url("{arrow}"); width: 14px; height: 14px; }}
        QComboBox QAbstractItemView {{
            background: #2a2a35; color: #ffffff; selection-background-color: #409EFF;
            border: 1px solid #4a4a5a;
        }}
        QSpinBox::up-button, QSpinBox::down-button, QTimeEdit::up-button, QTimeEdit::down-button {{
            width: 0; border: none; }}
        QCheckBox {{ spacing: 10px; padding: 3px 0; }}
        QCheckBox::indicator {{
            width: 18px; height: 18px; border: 2px solid #8a8aa0; border-radius: 4px;
            background: #2a2a35;
        }}
        QCheckBox::indicator:hover {{ border-color: #E6A23C; }}
        QCheckBox::indicator:checked {{
            background: #E6A23C; border-color: #E6A23C; image: url("{check}");
        }}
        QCheckBox::indicator:disabled {{ border-color: #4a4a5a; background: #22222a; }}
        QCheckBox:disabled {{ color: #777; }}
        QPushButton {{
            background: #2a2a35; color: #ffffff; font-size: 13px;
            border: 1px solid #4a4a5a; border-radius: 5px; padding: 6px 18px;
        }}
        QPushButton:hover {{ border-color: #409EFF; }}
        QPushButton:default {{ background: #409EFF; border-color: #409EFF; }}
        QToolTip {{ background: #2a2a35; color: #ffffff; border: 1px solid #4a4a5a; }}
        QTabWidget::pane {{ border: 1px solid #3a3a4a; border-radius: 6px; top: -1px; background: #1f1f28; }}
        QTabBar::tab {{
            background: #22222c; color: #9aa4b2; font-size: 13px; padding: 7px 16px;
            border: 1px solid #3a3a4a; border-bottom: none; border-top-left-radius: 6px;
            border-top-right-radius: 6px; margin-right: 2px;
        }}
        QTabBar::tab:selected {{ background: #1f1f28; color: #ffffff; border-bottom: 2px solid #E6A23C; }}
        QTabBar::tab:hover {{ color: #ffffff; }}
        QTabWidget > QWidget > QWidget {{ background: #1f1f28; }}
    """


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None, first_run=False, ctl=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("Ham Radio Banner - Settings")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(settings_style())
        self.setMinimumWidth(640)
        self.config = config
        layout = QVBoxLayout(self)

        if first_run:
            intro = QLabel("Welcome! Enter your callsign to get started.\n"
                           "You can change these later by right-clicking the banner.")
            intro.setWordWrap(True)
            layout.addWidget(intro)

        # Tabs keep the window a sensible height as settings grow.
        from PyQt6.QtWidgets import QTabWidget
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        def tab(title, note=None):
            page = QWidget()
            outer = QVBoxLayout(page)
            outer.setContentsMargins(16, 16, 16, 12)
            if note:
                hint = QLabel(note)
                hint.setWordWrap(True)
                hint.setStyleSheet("color: #9aa4b2; font-size: 11px; margin-bottom: 6px;")
                outer.addWidget(hint)
            f = QFormLayout()
            f.setHorizontalSpacing(14)
            f.setVerticalSpacing(10)
            f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            outer.addLayout(f)
            outer.addStretch(1)
            self.tabs.addTab(page, title)
            return f

        general = tab("General")
        weather = tab("Weather")
        stations = tab("Station data", "Where the 24-hour high/low (and optionally the current temperature) "
                                       "comes from. NWS needs no account.")
        prop = tab("Propagation")
        sizes_form = tab("Text sizes", "Font sizes in points, like in a document (6-72). "
                                       "The banner resizes to fit.")
        updates = tab("Updates")

        self.callsign = QLineEdit(config.get("callsign") if not first_run else "")
        self.callsign.setPlaceholderText("e.g. N0CALL")
        self.callsign.textEdited.connect(lambda t: self.callsign.setText(t.upper()))
        general.addRow("Callsign:", self.callsign)

        self.timezone = QComboBox()
        self.timezone.setEditable(True)
        zones = sorted(z.data().decode() for z in QTimeZone.availableTimeZoneIds())
        self.timezone.addItems(zones)
        self.timezone.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.timezone.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.timezone.setCurrentText(config.get("timezone") or QTimeZone.systemTimeZoneId().data().decode())
        general.addRow("Local timezone:", self.timezone)

        self.show_weather = QCheckBox("Show weather")
        self.show_weather.setChecked(_yes(config.get("show_weather")))
        self.show_weather.setToolTip("Untick to hide the weather completely; it is then not downloaded at all.")
        weather.addRow("Weather:", self.show_weather)

        self.location = QLineEdit(config.get("location"))
        self.location.setPlaceholderText("Blank = detect automatically")
        self.location.setToolTip("Leave blank to guess from your internet connection,\n"
                                 "or enter a place like 'Albany, NY', a ZIP/postal code,\n"
                                 "or coordinates like '42.65, -73.75'.")
        weather.addRow("Weather location:", self.location)

        self.units = QComboBox()
        self.units.addItems(["°F", "°C"])
        self.units.setCurrentIndex(1 if config.get("units").upper() == "C" else 0)
        weather.addRow("Temperature:", self.units)

        self.refresh = QSpinBox()
        self.refresh.setRange(5, 240)
        self.refresh.setSuffix(" min")
        self.refresh.setValue(_int(config.get("weather_refresh"), 30))
        weather.addRow("Weather refresh:", self.refresh)

        self.show_conditions = QCheckBox("Show current conditions (sunny, rain, snow, windy…)")
        self.show_conditions.setChecked(_yes(config.get("show_conditions")))
        weather.addRow("Conditions:", self.show_conditions)

        self.show_hilo = QCheckBox("Show today's forecast high/low")
        self.show_hilo.setChecked(_yes(config.get("show_hilo")))
        weather.addRow("High/low:", self.show_hilo)

        row24 = QHBoxLayout()
        self.show_24h = QCheckBox("Show 24-hour high/low ending at")
        self.show_24h.setChecked(_yes(config.get("show_24h")))
        self.show_24h.setToolTip("The highest and lowest temperature over the 24 hours ending at this time,\n"
                                 "like a daily weather observation. It switches to the new day at this time.")
        h, m = _parse_day_end(config.get("day_end"))
        from PyQt6.QtCore import QTime
        self.day_end = QTimeEdit(QTime(h, m))
        self.day_end.setDisplayFormat("h:mm AP")
        self.day_end.setEnabled(self.show_24h.isChecked())
        self.show_24h.toggled.connect(self.day_end.setEnabled)
        row24.addWidget(self.show_24h)
        row24.addSpacing(6)
        row24.addWidget(self.day_end)
        row24.addStretch(1)
        weather.addRow("24-hour:", row24)

        self.wx24_source = QComboBox()
        self._sources = [("station", "NWS weather station (US)"), ("model", "Weather model (worldwide)"),
                         ("aprs", "CWOP / APRS weather near you (aprs.fi)"),
                         ("synoptic", "Synoptic Data: stations near you"),
                         ("wu", "My station: Weather Underground"), ("ambient", "My station: Ambient Weather"),
                         ("ecowitt", "My station: Ecowitt")]
        self.wx24_source.addItems([label for _, label in self._sources])
        codes = [code for code, _ in self._sources]
        current = config.get("wx24_source").lower()
        self.wx24_source.setCurrentIndex(codes.index(current) if current in codes else 0)
        self.wx24_source.setToolTip(
            "Where the 24-hour high/low (and, if ticked below, the current temperature) comes from:\n"
            "- NWS weather station: official thermometer readings (usually the nearest airport), US only.\n"
            "- Weather model: Open-Meteo's 15-minute model data, anywhere in the world.\n"
            "- My station: your own home weather station. It also supplies the current temperature.")
        stations.addRow("Source:", self.wx24_source)

        self.station = QLineEdit(config.get("station"))
        self.api_key = QLineEdit(config.get("api_key"))
        self.app_key = QLineEdit(config.get("app_key"))
        for w in (self.api_key, self.app_key):
            w.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)  # hidden except while typing
        self.station.textEdited.connect(lambda t: self.station.setText(t.upper()))
        self._form = stations
        stations.addRow("Station ID:", self.station)
        stations.addRow("API key:", self.api_key)
        stations.addRow("Application key:", self.app_key)
        self.station_current = QCheckBox("Also use the station for the current temperature")
        self.station_current.setChecked(_yes(config.get("station_current")))
        self.station_current.setToolTip("On: the current temperature (and, for NWS, the conditions) comes from the\n"
                                        "station's latest reading. Off: the station is only used for the 24-hour\n"
                                        "high/low, and the current temperature comes from the weather model.")
        stations.addRow("", self.station_current)

        test_row = QHBoxLayout()
        self.test_button = QPushButton("Test station")
        self.test_button.clicked.connect(self._test_station)
        self.key_note = QLabel()
        self.key_note.setStyleSheet("color: #9aa4b2; font-size: 11px;")
        self.key_note.setWordWrap(True)
        self.key_note.setOpenExternalLinks(True)  # the "Sign up" links open in the browser

        test_row.addWidget(self.test_button)
        test_row.addStretch(1)
        stations.addRow("", test_row)
        stations.addRow(self.key_note)  # sign-up instructions, full width
        self.wx24_source.currentIndexChanged.connect(lambda _: self._update_enabled())

        from PyQt6.QtWidgets import QGridLayout
        sizes = QGridLayout()
        sizes.setHorizontalSpacing(8)
        self.sizes = {}
        sizes.setVerticalSpacing(10)
        sizes.setColumnMinimumWidth(1, 90)
        sizes.setColumnMinimumWidth(2, 40)
        for i, (key, label) in enumerate((("font_callsign", "Callsign"), ("font_date", "Date"),
                                          ("font_clock", "Clock times"), ("font_clock_label", "Clock labels"),
                                          ("font_weather", "Temperatures"), ("font_weather_label", "WX labels"),
                                          ("font_conditions", "Conditions"), ("font_ticker", "Ticker"))):
            box = QSpinBox()
            box.setRange(6, 72)
            box.setSuffix(" pt")
            box.setValue(_int(config.get(key), int(DEFAULTS[key])))
            box.setToolTip(f"Font size of the {label.lower()} text in points, like in a document.\n"
                           "Type a number or use the arrow keys.")
            self.sizes[key] = box
            sizes.addWidget(QLabel(label + ":"), i // 2, (i % 2) * 3)
            sizes.addWidget(box, i // 2, (i % 2) * 3 + 1)
        sizes.setColumnStretch(5, 1)
        sizes_form.addRow(sizes)

        # Weather options only matter while the weather is shown.
        self.show_weather.toggled.connect(lambda _: self._update_enabled())
        self.show_24h.toggled.connect(lambda _: self._update_enabled())
        self._update_enabled()

        self.screens = QComboBox()
        self.screens.addItems(["One screen", "Every screen"])
        self.screens.setCurrentIndex(1 if config.get("screens").lower() == "all" else 0)
        self.screens.setToolTip("Every screen = a separate banner on each monitor.\n"
                                "Drag each one where you want it; positions are remembered.")
        general.addRow("Show on:", self.screens)

        self.auto_update = QCheckBox("Install updates automatically")
        self.auto_update.setChecked(_yes(config.get("auto_update")))
        if self_update_mode() is None:
            self.auto_update.setEnabled(False)
            self.auto_update.setToolTip("This copy can't update itself (installed with pipx or git).")
        updates.addRow("Automatic:", self.auto_update)

        self.solar = QCheckBox("Show solar && band conditions ticker")
        self.solar.setChecked(_yes(config.get("solar")))
        self.solar.setToolTip("A scrolling line with SFI, A/K index, X-ray, solar wind and\n"
                              "HF/VHF band conditions from N0NBH (hamqsl.com).")
        prop.addRow("Propagation:", self.solar)

        self.ticker_speed = QComboBox()
        self.ticker_speed.addItems(["Slow", "Normal", "Fast"])
        self.ticker_speed.setCurrentText(config.get("ticker_speed").capitalize()
                                         if config.get("ticker_speed").lower() in Ticker.SPEEDS else "Normal")
        self.solar.toggled.connect(self.ticker_speed.setEnabled)
        self.ticker_speed.setEnabled(self.solar.isChecked())
        prop.addRow("Ticker speed:", self.ticker_speed)

        self.on_top = QCheckBox("Always on top of other windows")
        self.on_top.setChecked(_yes(config.get("on_top")))
        self.on_top.setToolTip("When off, the banner acts like a normal window and gets a taskbar button.")
        general.addRow("Window:", self.on_top)

        self.dock = QCheckBox("Dock to top of screen (other windows stay below it)")
        self.dock.setChecked(_yes(config.get("dock")))
        self.dock.setToolTip("A full-width bar across the top of the screen, like a taskbar.\n"
                             "Maximized windows fit underneath instead of covering it.")
        self.dock.toggled.connect(lambda on: (self.on_top.setChecked(True) if on else None,
                                              self.on_top.setEnabled(not on)))
        self.on_top.setEnabled(not self.dock.isChecked())
        general.addRow("", self.dock)

        self.autostart = QCheckBox("Start when the computer starts")
        self.autostart.setChecked(True if first_run else autostart_enabled())
        general.addRow("Startup:", self.autostart)

        tools = QHBoxLayout()
        self.check_button = QPushButton("Check for updates now")
        self.check_button.clicked.connect(self._check_updates)
        self.check_button.setEnabled(ctl is not None)
        shortcut = QPushButton("Create desktop shortcut")
        shortcut.clicked.connect(self._make_shortcut)
        tools.addWidget(self.check_button)
        tools.addWidget(shortcut)
        tools.addStretch(1)
        updates.insertRow(0, "Version:", QLabel(f"<b>{__version__}</b>"))
        updates.addRow("", tools)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _source(self):
        return self._sources[self.wx24_source.currentIndex()][0]

    def _update_enabled(self):
        on = self.show_weather.isChecked()
        for w in (self.location, self.units, self.refresh, self.show_hilo, self.show_24h,
                  self.sizes["font_weather"], self.sizes["font_weather_label"], self.wx24_source,
                  self.show_conditions, self.sizes["font_conditions"]):
            w.setEnabled(on)
        self.day_end.setEnabled(on and self.show_24h.isChecked())
        src = self._source()
        hints = {
            "station": ("auto", "", "", "Blank = the NWS station nearest your weather location, or an ID like KGFL."),
            "model": ("", "", "", "Model data needs no station."),
            "aprs": ("callsign or CWOP ID, e.g. KD2XXX-13", "aprs.fi API key", "",
                     "Backyard stations from the Citizen Weather Observer Program (many also feed Weather "
                     "Underground). <a href='https://aprs.fi/'>Sign up free at aprs.fi</a>, then copy the key "
                     "from Account settings. Find a station near you on the aprs.fi map (weather stations "
                     "have a WX icon). aprs.fi only gives the latest reading, so the 24-hour high/low appears "
                     "after the banner has run for a day."),
            "synoptic": ("blank = nearest", "API token", "",
                         "Combines 100,000+ stations (CWOP, schools, DOT road sensors...). "
                         "<a href='https://synopticdata.com/open-access'>Request a free open-access token</a>, then "
                         "paste it here. Leave Station ID blank for the nearest station, or enter its ID."),
            "wu": ("e.g. KNYWARRE12", "API key", "",
                   "From wunderground.com > My Profile > API Keys (free for station owners). "
                   "Davis, Acurite and others that upload to WU work this way too."),
            "ambient": ("MAC (optional)", "API key", "Application key",
                        "Both keys from ambientweather.net > Account > API Keys. MAC only if you have several stations."),
            "ecowitt": ("MAC address", "API key", "Application key",
                        "From ecowitt.net > User Profile > Private Center; the MAC is shown under your device."),
        }[src]
        self.station.setPlaceholderText(hints[0])
        self.api_key.setPlaceholderText(hints[1])
        self.app_key.setPlaceholderText(hints[2])
        self.key_note.setText(hints[3].replace("<a href", "<a style='color:#409EFF;' href"))
        uses_station = src != "model"
        uses_api = src in PWS_SOURCES or src in CITIZEN_SOURCES
        uses_app = src in ("ambient", "ecowitt")
        for field, used in ((self.station, uses_station), (self.api_key, uses_api), (self.app_key, uses_app),
                            (self.station_current, uses_station)):
            self._form.setRowVisible(field, used)  # only show the fields this source needs
            field.setEnabled(on)
        self.test_button.setEnabled(on and src != "model")

    def _test_station(self):
        """Fetch from the chosen station right now and say what came back."""
        src = self._source()
        from PyQt6.QtGui import QCursor
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            w = fetch_weather(self.location.text(), "C" if self.units.currentIndex() == 1 else "F",
                              self.day_end.time().toString("HH:mm"), src, self.station.text(),
                              self.api_key.text(), self.app_key.text(), True, True)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            if isinstance(e, LocationNotFound):
                QMessageBox.warning(self, "Test station", f"Couldn't find the weather location \"{e}\".\n"
                                    "Check the spelling, or use a ZIP code (e.g. 12831) or coordinates.")
            else:
                QMessageBox.warning(self, "Test station", f"Couldn't get the weather:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        unit = "°C" if self.units.currentIndex() == 1 else "°F"
        if w.get("source24", "").startswith("model"):
            QMessageBox.warning(self, "Test station", "The station didn't answer, so the model data would be used.\n\n"
                                f"Reason: {w.get('station_error') or 'no NWS station data for this location'}")
            return
        if src == "station":
            src_name = f"NWS station {w['source24']}"
        elif src in CITIZEN_SOURCES:
            src_name = f"{w.get('now_source') or w['source24'].split()[-1]} via {CITIZEN_SOURCES[src]}"
        else:
            src_name = PWS_SOURCES[src]
        lines = [f"Connected to {src_name}."]
        if w.get("source24", "").startswith("collecting"):
            if w.get("now_source"):
                lines.append(f"Current temperature: {round(w['now'])}{unit}")
            lines.append(f"24-hour high/low: collecting readings ({w.get('collecting', 0)}h of 24 so far). "
                         "It appears once the banner has a full day of readings.")
            QMessageBox.information(self, "Test station", "\n".join(lines))
            return
        if w.get("now_source"):
            lines.append(f"Current temperature: {round(w['now'])}{unit}"
                         + (f" ({w['conditions']})" if src == "station" and w.get("conditions") else ""))
        lines.append(f"24-hour high/low: {round(w['hi24'])}{unit} / {round(w['lo24'])}{unit} "
                     f"({w.get('readings24', 0)} readings)")
        QMessageBox.information(self, "Test station", "\n".join(lines))

    def _check_updates(self):
        self.check_button.setEnabled(False)
        self.check_button.setText("Checking…")
        self.ctl.check_update(manual=True, done=self._check_done)

    def _check_done(self):
        self.check_button.setEnabled(True)
        self.check_button.setText("Check for updates now")

    def _make_shortcut(self):
        try:
            path = create_desktop_shortcut()
            QMessageBox.information(self, "Desktop shortcut", f"Added a shortcut to your desktop:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "Desktop shortcut", f"Could not create the shortcut:\n{e}")

    def _location_ok(self):
        """Check a newly typed weather location before saving; offer 'Did you mean...?'.
        Returns False to stay in the dialog."""
        text = " ".join(self.location.text().split())
        if not text or text == " ".join(self.config.get("location").split()) or not self.show_weather.isChecked():
            return True
        parts = [p.strip() for p in text.split(",")]
        if len(parts) == 2:
            try:
                float(parts[0]), float(parts[1])
                return True  # coordinates
            except ValueError:
                pass
        from PyQt6.QtGui import QCursor
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            lookup_location_cached(text)
            return True
        except LocationNotFound:
            suggestions = suggest_locations(text)
        except Exception:
            return True  # offline: can't check now, the banner will say if it fails later
        finally:
            QApplication.restoreOverrideCursor()
        box = QMessageBox(QMessageBox.Icon.Warning, "Weather location", f"Couldn't find \"{text}\".", parent=self)
        use = None
        if suggestions:
            box.setInformativeText("Did you mean " + " or ".join(suggestions) + "?\n\n"
                                   "You can also use a ZIP code (e.g. 12831) or coordinates.")
            use = box.addButton(f"Use {suggestions[0]}", QMessageBox.ButtonRole.AcceptRole)
        else:
            box.setInformativeText("Check the spelling, or use a ZIP code (e.g. 12831) or coordinates "
                                   "like 43.10, -73.65.")
        back = box.addButton("Edit location", QMessageBox.ButtonRole.RejectRole)
        box.addButton("Save anyway", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(use or back)
        box.exec()
        if box.clickedButton() is use:
            self.location.setText(suggestions[0])
            return True
        if box.clickedButton() is back:
            self.location.setFocus()
            self.location.selectAll()
            return False
        return True

    def accept(self):
        if not self._location_ok():
            return
        tz = self.timezone.currentText().strip()
        if not QTimeZone(tz.encode()).isValid():
            QMessageBox.warning(self, "Unknown timezone",
                                f"'{tz}' is not a known timezone.\nPick one from the list, e.g. America/New_York.")
            return
        self.config.reload()  # keep window positions saved since the dialog opened
        self.config.set("callsign", self.callsign.text().strip().upper() or DEFAULTS["callsign"])
        self.config.set("timezone", tz)
        self.config.set("location", self.location.text().strip())
        self.config.set("units", "C" if self.units.currentIndex() == 1 else "F")
        self.config.set("weather_refresh", self.refresh.value())
        self.config.set("show_weather", "yes" if self.show_weather.isChecked() else "no")
        self.config.set("show_hilo", "yes" if self.show_hilo.isChecked() else "no")
        self.config.set("show_24h", "yes" if self.show_24h.isChecked() else "no")
        self.config.set("day_end", self.day_end.time().toString("HH:mm"))
        self.config.set("wx24_source", self._source())
        self.config.set("station", self.station.text().strip().upper())
        self.config.set("api_key", self.api_key.text().strip())
        self.config.set("app_key", self.app_key.text().strip())
        self.config.set("station_current", "yes" if self.station_current.isChecked() else "no")
        self.config.set("show_conditions", "yes" if self.show_conditions.isChecked() else "no")
        for key, box in self.sizes.items():
            self.config.set(key, box.value())
        self.config.set("screens", "all" if self.screens.currentIndex() == 1 else "one")
        self.config.set("auto_update", "yes" if self.auto_update.isChecked() else "no")
        self.config.set("on_top", "yes" if self.on_top.isChecked() else "no")
        self.config.set("dock", "yes" if self.dock.isChecked() else "no")
        self.config.set("solar", "yes" if self.solar.isChecked() else "no")
        self.config.set("ticker_speed", self.ticker_speed.currentText().lower())
        if self.autostart.isChecked() != autostart_enabled():
            try:
                set_autostart(self.autostart.isChecked())
            except OSError as e:
                QMessageBox.warning(self, "Startup setting", f"Could not change the startup setting:\n{e}")
        try:
            self.config.save()
        except OSError as e:
            QMessageBox.warning(self, "Could not save", f"Could not write {self.config.path}:\n{e}")
        super().accept()


def _yes(value):
    return str(value).strip().lower() in ("yes", "true", "1", "on")


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------ controller ---

class Controller(QObject):
    """Holds the settings, clock, weather and update state shared by every banner."""
    changed = pyqtSignal()
    _weather_ready = pyqtSignal(object)
    _solar_ready = pyqtSignal(str)
    _update_found = pyqtSignal(dict)
    _manual_check_done = pyqtSignal(object, object)
    _update_ready = pyqtSignal(object)
    _update_failed = pyqtSignal(str)

    def __init__(self, config, icon):
        super().__init__()
        self.config = config
        self.icon = icon
        self.banners = {}
        self.weather = None                          # data from fetch_weather()
        self.weather_error = "Loading WX..."         # shown instead when there's no data
        self.update_info = None
        self.status_text = ""
        self._fetching = False
        self._updating = False
        self.instance_server = None

        # Shared menu used by every banner and the tray icon.
        self.menu = QMenu()
        self.menu.addAction("Settings…", lambda: self.open_settings())
        self.menu.addAction("Refresh Weather", lambda: self.get_weather(force=True))
        self.menu.addAction("Check for Updates…", lambda: self.check_update(manual=True))
        self.menu_banner = None  # the banner that was right-clicked
        self.match_size_action = self.menu.addAction("Make all banners this size", self.match_size)
        self.menu.aboutToShow.connect(
            lambda: self.match_size_action.setVisible(len(self.banners) > 1 and self.menu_banner is not None))
        self.menu.aboutToHide.connect(lambda: QTimer.singleShot(0, self._forget_menu_banner))
        self.update_action = self.menu.addAction("Update", self.on_update_clicked)
        self.update_action.setVisible(False)
        self.menu.addSeparator()
        self.menu.addAction(f"Version {__version__}").setEnabled(False)
        self.menu.addAction("Quit", QApplication.instance().quit)

        self._weather_ready.connect(self._on_weather)
        self._update_found.connect(self._on_update_found)
        self._manual_check_done.connect(self._on_manual_check)
        self._update_ready.connect(self._on_update_ready)
        self._update_failed.connect(self._on_update_failed)

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.changed)
        self.rollover_timer = QTimer(self)
        self.rollover_timer.timeout.connect(self._check_day_rollover)
        self.rollover_timer.start(60 * 1000)
        # aprs.fi only gives a station's latest report, so sample it every 10 minutes to build
        # the 24-hour history (CWOP stations report every 5-10 minutes).
        self.sample_timer = QTimer(self)
        self.sample_timer.timeout.connect(self._sample_aprs)
        self.sample_timer.start(10 * 60 * 1000)
        self.clock_timer.start(1000)
        # Other programs (full-screen apps, other "on top" windows, waking from sleep) can
        # push the banner down, so quietly put it back on top every couple of seconds.
        self.on_top_timer = QTimer(self)
        self.on_top_timer.timeout.connect(self.keep_on_top)
        self.on_top_timer.start(2000)
        self.weather_timer = QTimer(self)
        self.weather_timer.timeout.connect(self.get_weather)
        # Solar data changes every few hours at the source; hourly is plenty.
        self.solar_html = "<span style='color:#888;'>☀ Loading solar &amp; band conditions…</span>"
        self._solar_fetching = False
        self._solar_ready.connect(self._on_solar)
        self.solar_timer = QTimer(self)
        self.solar_timer.timeout.connect(self.get_solar)
        self.solar_timer.start(60 * 60 * 1000)
        # Check GitHub for a newer release shortly after start, then once a day.
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.check_update)
        self.update_timer.start(24 * 60 * 60 * 1000)
        QTimer.singleShot(5000, self.check_update)

        # Apply hand edits to config.ini right away (e.g. another operator's callsign).
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(lambda _p: QTimer.singleShot(200, self._reload_config))

        app = QApplication.instance()
        app.screenAdded.connect(lambda _s: QTimer.singleShot(1000, self.layout_banners))
        app.screenRemoved.connect(lambda _s: QTimer.singleShot(1000, self.layout_banners))
        app.aboutToQuit.connect(self.save_all_geometry)
        app.aboutToQuit.connect(lambda: [b.release_dock() for b in self.banners.values()])

        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(icon)
            self.tray.setToolTip("Ham Radio Banner")
            self.tray.setContextMenu(self.menu)
            self.tray.activated.connect(self.show_banners)
            self.tray.show()

        self.apply_settings()

    # --- settings ---
    def _watch_config(self):
        if os.path.exists(self.config.path) and self.config.path not in self.watcher.files():
            self.watcher.addPath(self.config.path)

    def _reload_config(self):
        # Some editors replace the file on save, so watch it again.
        self._watch_config()
        if self.config.reload() and self.config.settings() != self._applied:
            self.apply_settings()

    def apply_settings(self):
        old = getattr(self, "_applied", {})
        self._applied = self.config.settings()
        self._watch_config()
        self.callsign = self.config.get("callsign")
        tz_name = self.config.get("timezone")
        self.tz = QTimeZone(tz_name.encode()) if tz_name else QTimeZone.systemTimeZone()
        self.tz_bad = not self.tz.isValid()
        if self.tz_bad:
            self.tz = QTimeZone.systemTimeZone()
        self.tz_label = self.tz.id().data().decode().split("/")[-1].replace("_", " ")
        self.weather_timer.start(max(5, _int(self.config.get("weather_refresh"), 30)) * 60000)
        if (any(old.get(k) != self._applied[k] for k in
                ("location", "units", "show_24h", "day_end", "show_weather", "wx24_source", "station",
                 "api_key", "app_key", "station_current"))
                or not old):
            if old and any(old.get(k) != self._applied[k] for k in ("location", "units")):
                self.weather, self.weather_error = None, "Loading WX..."  # don't show the old place
            self.get_weather(force=True)
        if old and any(old.get(k) != self._applied[k] for k in
                       ("font_callsign", "font_clock", "font_weather", "font_ticker", "show_weather",
                        "font_clock_label", "font_weather_label", "font_date", "font_conditions",
                        "show_conditions")):
            QTimer.singleShot(0, lambda: [b.refit() for b in self.banners.values()])
        if _yes(self._applied["solar"]) and (not old or not _yes(old.get("solar"))
                                             or old.get("location") != self._applied["location"]):
            self.get_solar()
        if old.get("screens") != self._applied["screens"]:
            self.layout_banners()
        if old and (old.get("on_top") != self._applied["on_top"] or old.get("dock") != self._applied["dock"]):
            for b in self.banners.values():
                b.apply_window_mode()
                b.apply_dock()
        self.changed.emit()

    def open_settings(self, first_run=False):
        # No parent window: otherwise the banner's dark style leaks into the dialog.
        if SettingsDialog(self.config, None, first_run=first_run, ctl=self).exec():
            self.apply_settings()
            if first_run:
                ensure_desktop_shortcut_once(self.config)

    # --- banners: one, or one per monitor ---
    def layout_banners(self):
        if self.config.get("screens").lower() == "all":
            wanted = {f"WINDOW {s.name()}": s for s in QApplication.screens()}
        else:
            wanted = {"WINDOW": None}
        for key in list(self.banners):
            if key not in wanted:
                self.banners.pop(key).retire()
        for key, screen in wanted.items():
            if key not in self.banners:
                banner = Banner(self, key, screen)
                self.banners[key] = banner
                banner.show()
                banner.apply_dock()

    def _forget_menu_banner(self):
        self.menu_banner = None

    def match_size(self):
        source = self.menu_banner
        if source is None:
            return
        for b in self.banners.values():
            if b is not source and not b.is_docked:
                b.resize(source.size())
                self.save_geometry(b)

    def on_top(self):
        return _yes(self.config.get("on_top")) or self.docked()

    def docked(self):
        return _yes(self.config.get("dock"))

    def keep_on_top(self):
        # Don't cover the Settings window or a message box while one is open.
        if not self.on_top() or QApplication.activeModalWidget() or QApplication.activePopupWidget():
            return
        for b in self.banners.values():
            if not b.fullscreen_app:  # let full-screen games/videos cover a docked bar
                b.raise_without_focus()

    def show_banners(self, *_):
        for b in self.banners.values():
            b.show()
            b.raise_()

    def save_geometry(self, banner):
        if banner.is_docked:
            return  # keep the undocked position for when docking is turned off
        if not self.config.exists or not self.config.reload():
            return  # never overwrite hand edits or a half-saved file
        if not self.config.parser.has_section(banner.key):
            self.config.parser.add_section(banner.key)
        w = self.config.parser[banner.key]
        w["x"], w["y"] = str(banner.x()), str(banner.y())
        w["width"], w["height"] = str(banner.width()), str(banner.height())
        try:
            self.config.save()
        except OSError:
            pass

    def save_all_geometry(self):
        for b in self.banners.values():
            self.save_geometry(b)

    # --- weather (runs in background so the clock never freezes) ---
    def _weather_key(self):
        """The settings a weather reading depends on; if these change, old readings are wrong."""
        return tuple(self.config.get(k) for k in ("location", "units", "wx24_source", "station",
                                                  "station_current", "day_end"))

    def get_weather(self, force=False):
        """Download the weather in the background. Timer refreshes skip if one is already running;
        a forced refresh (settings changed, Refresh Weather) always starts a new one, and only
        the newest request's result is shown."""
        if not _yes(self.config.get("show_weather")) or (self._fetching and not force):
            return
        self._fetching = True
        self._wx_gen = getattr(self, "_wx_gen", 0) + 1
        gen, key = self._wx_gen, self._weather_key()
        location, units = self.config.get("location"), self.config.get("units").upper()
        day_end = self.config.get("day_end")
        source, station = self.config.get("wx24_source").lower(), self.config.get("station")
        keys = self.config.get("api_key"), self.config.get("app_key")
        use_now, want24 = _yes(self.config.get("station_current")), _yes(self.config.get("show_24h"))

        def work():
            try:
                text = fetch_weather(location, units, day_end, source, station, *keys, use_now, want24)
            except LocationNotFound:
                text = ("<span style='color:#888;'>Location not found - check the spelling or use a "
                        "ZIP code (right-click &gt; Settings)</span>")
            except requests.RequestException as e:
                print(f"Weather error: {e}", file=sys.stderr)
                text = "<span style='color:#888;'>WX Unavailable (no connection)</span>"
            except Exception as e:  # unexpected reply from the weather service
                print(f"Weather error: {e!r}", file=sys.stderr)
                text = "<span style='color:#888;'>WX Unavailable</span>"
            self._weather_ready.emit((gen, key, text))  # text = a dict of readings, or an error message

        threading.Thread(target=work, daemon=True).start()

    def _on_weather(self, message):
        gen, key, result = message
        if gen != self._wx_gen:
            return  # an older request finished late: a newer one is on its way
        self._fetching = False
        if isinstance(result, dict):
            self.weather, self.weather_error, self._weather_for = result, None, key
        else:
            self.weather_error = result
            # Keep showing the last good reading only through a brief network hiccup for the
            # same place - never when the location changed or can't be found.
            if "not found" in result or getattr(self, "_weather_for", None) != key:
                self.weather = None
        self.changed.emit()

    def _sample_aprs(self):
        if self.config.get("wx24_source").lower() != "aprs" or not _yes(self.config.get("show_weather")):
            return
        call, key = self.config.get("station").upper(), self.config.get("api_key")

        def work():
            try:
                epoch, temp_c = _aprs_latest(call, key)
                record_reading(call, epoch, temp_c)
            except Exception as e:
                print(f"aprs.fi sample failed: {e!r}", file=sys.stderr)

        threading.Thread(target=work, daemon=True).start()

    def _check_day_rollover(self):
        """Refresh right after the 24-hour period rolls over (e.g. at 6:30 AM)."""
        w = self.weather
        if (not w or self._fetching or not _yes(self.config.get("show_24h"))
                or not _yes(self.config.get("show_weather"))):
            return
        now_local = (datetime.now(timezone.utc) + timedelta(seconds=w.get("utc_offset", 0))).replace(tzinfo=None)
        if window_end(now_local, self.config.get("day_end")).isoformat() != w.get("window_end"):
            self.get_weather()

    def font_pt(self, part):
        """Font size in points for 'callsign', 'clock', 'weather' or 'ticker' (6-72)."""
        return max(6, min(72, _int(self.config.get(f"font_{part}"), int(DEFAULTS[f"font_{part}"]))))

    def weather_html(self, lbl, cond_size="11pt"):
        """lbl = font size for the small labels, e.g. '8.0pt'; cond_size for the conditions."""
        if self.weather_error and not self.weather:
            return self.weather_error
        w = self.weather
        unit = "°"
        html = f"{escape(w['name'])} {round(w['now'])}{unit} " if w["name"] else f"{round(w['now'])}{unit} "
        if _yes(self.config.get("show_conditions")) and w.get("conditions"):
            html += f"<span style='color:#b8c7dd; font-size:{cond_size};'>{escape(w['conditions'])}</span> "
        if _yes(self.config.get("show_hilo")):
            html += (f"<span style='color:#ff5f5f;'>H:{round(w['hi'])}{unit}</span> "
                     f"<span style='color:#5fafff;'>L:{round(w['lo'])}{unit}</span> ")
        if _yes(self.config.get("show_24h")):
            h, m = _parse_day_end(self.config.get("day_end"))
            ends = f"{(h % 12) or 12}:{m:02d}{'a' if h < 12 else 'p'}"
            src = w.get("source24", "model")
            if src == "model":
                tag = f"24h→{ends}"                 # model chosen in Settings
            elif src.startswith("model"):
                tag = f"24h model→{ends}"           # wanted a station, none available
            elif src == "home":
                tag = f"24h home→{ends}"            # your own weather station
            else:
                tag = f"24h {src}→{ends}"           # real station readings, e.g. KGFL
            if src.startswith("collecting"):
                html += (f"<span style='color:#888; font-size:{lbl};'>&nbsp;24h {escape(src.split(' ', 1)[1])}: "
                         f"collecting ({w.get('collecting', 0)}h of 24)</span>")
            elif w.get("hi24") is None:
                html += f"<span style='color:#888; font-size:{lbl};'>24h n/a</span>"
            else:
                html += (f"<span style='color:#888; font-size:{lbl};'>&nbsp;{escape(tag)}</span> "
                         f"<span style='color:#ff5f5f;'>H:{round(w['hi24'])}{unit}</span> "
                         f"<span style='color:#5fafff;'>L:{round(w['lo24'])}{unit}</span>")
        return html

    def get_solar(self):
        if self._solar_fetching or not _yes(self.config.get("solar")):
            return
        self._solar_fetching = True
        location = self.config.get("location")

        def work():
            try:
                html = fetch_solar(location)
            except Exception as e:
                print(f"Solar data error: {e!r}", file=sys.stderr)
                html = ("<span style='color:#888;'>☀ Solar &amp; band conditions unavailable - "
                        "will try again soon</span>")
            self._solar_ready.emit(html)

        threading.Thread(target=work, daemon=True).start()

    def _on_solar(self, html):
        self._solar_fetching = False
        if "unavailable" in html and "SFI" not in self.solar_html:
            QTimer.singleShot(10 * 60 * 1000, self.get_solar)  # retry sooner after a failure
        if "unavailable" not in html or "SFI" not in self.solar_html:
            self.solar_html = html  # keep the last good report if a refresh fails
        self.changed.emit()

    # --- updates ---
    def check_update(self, manual=False, done=None):
        """Look for a newer release. Automatic checks are silent; a manual check (menu or
        Settings button) always answers: up to date, offer to install, or why it failed."""
        def work():
            try:
                found = check_for_update()
            except Exception as e:  # offline, GitHub down, rate-limited... try again tomorrow
                print(f"Update check failed: {e}", file=sys.stderr)
                if manual:
                    self._manual_check_done.emit({"error": str(e)}, done)
                return
            if manual:
                self._manual_check_done.emit({"found": found}, done)
            elif found:
                self._update_found.emit(found)

        threading.Thread(target=work, daemon=True).start()

    def _on_manual_check(self, result, done):
        if done:
            try:
                done()
            except RuntimeError:  # the Settings window was closed meanwhile
                pass
        parent = QApplication.activeModalWidget()
        box = lambda kind, text, buttons=QMessageBox.StandardButton.Ok: kind(
            parent, "Check for updates", text, buttons)
        if "error" in result:
            box(QMessageBox.warning, "Couldn't reach GitHub to check for updates.\n"
                                     "Check your internet connection and try again.")
            return
        info = result["found"]
        if not info:
            box(QMessageBox.information, f"You're up to date - version {__version__} is the latest.")
            return
        if self._updating:
            return
        self.update_info = info
        self.update_action.setVisible(True)
        self.update_action.setText(f"Install update ({info['version']}) now" if info["download"]
                                   else f"Download update ({info['version']})…")
        self.status_text = "⬆ Update available (right-click)"
        self.changed.emit()
        yes_no = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        if info["download"]:
            if box(QMessageBox.question, f"Version {info['version']} is available (you have {__version__}).\n\n"
                   "Install it now? The banner will restart in a few seconds.", yes_no) \
                    == QMessageBox.StandardButton.Yes:
                if parent:
                    parent.reject()  # close Settings first
                self.install_update()
        elif box(QMessageBox.question, f"Version {info['version']} is available (you have {__version__}).\n\n"
                 "Open the download page?", yes_no) == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl(info["page"]))

    def _on_update_found(self, info):
        if self._updating:
            return
        self.update_info = info
        if info["download"]:
            self.update_action.setText(f"Install update ({info['version']}) now")
        else:
            self.update_action.setText(f"Download update ({info['version']})…")
        self.update_action.setVisible(True)
        auto = _yes(self.config.get("auto_update"))
        if info["download"] and auto:
            self.install_update()
        else:
            self.status_text = "⬆ Update available (right-click)"
            self.changed.emit()

    def on_update_clicked(self):
        if self.update_info and self.update_info["download"]:
            self.install_update()
        elif self.update_info:
            QDesktopServices.openUrl(QUrl(self.update_info["page"]))

    def install_update(self):
        if self._updating:
            return
        self._updating = True
        self.update_action.setEnabled(False)
        self.status_text = f"Updating to {self.update_info['version']}…"
        self.changed.emit()
        url = self.update_info["download"]

        def work():
            try:
                self._update_ready.emit(prepare_update(url))
            except Exception as e:
                print(f"Update failed: {e!r}", file=sys.stderr)
                self._update_failed.emit(str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_update_ready(self, finish):
        self.save_all_geometry()
        for b in self.banners.values():
            b.release_dock()  # give the reserved strip back while the new version starts
        if self.instance_server is not None:
            self.instance_server.close()  # let the new version start up as the only copy
        try:
            finish()
        except Exception as e:
            self._on_update_failed(str(e))
            return
        QApplication.instance().quit()

    def _on_update_failed(self, _msg):
        # Fall back to the manual way: open the Releases page.
        self._updating = False
        self.update_info["download"] = ""
        self.update_action.setEnabled(True)
        self.update_action.setText(f"Download update ({self.update_info['version']})…")
        self.status_text = "⬆ Update failed - right-click to download"
        self.changed.emit()


# ---------------------------------------------------------------- banner ---

class Banner(QWidget):
    def __init__(self, ctl, key, screen=None):
        super().__init__()
        self.ctl, self.key, self.screen_ = ctl, key, screen
        self.offset = QPoint()
        self.is_docked = False
        self._appbar = False
        self._appbar_hwnd = None    # the exact window Windows registered our strip for
        self._closing = False       # set when this banner is being removed: never dock again
        self._dock_area = None      # free screen area measured before we reserved our strip
        self._watched_screen = None
        self.fullscreen_app = False

        self.setWindowTitle("Ham Radio Banner")
        self.apply_window_mode(show=False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # macOS hides "tool" windows when you switch to another app; keep it visible.
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setToolTip("Drag to move · drag corner to resize · right-click for options")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(30, 15, 30, 10)
        content_layout.setSpacing(4)
        self.label = QLabel("Initializing HUD...")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        content_layout.addWidget(self.label)
        self.ticker = Ticker()
        content_layout.addWidget(self.ticker)
        main_layout.addWidget(content)

        self.sizegrip = QSizeGrip(self)
        main_layout.addWidget(self.sizegrip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        self.setStyleSheet("""
            QWidget {
                background-color: rgba(10, 10, 15, 230);
                border: 1px solid #3a3a4a;
                border-radius: 12px;
                font-family: 'Consolas', 'Menlo', 'DejaVu Sans Mono', monospace;
            }
            QLabel { border: none; background: transparent; color: white; }
            #ticker { border: none; border-top: 1px solid #2a2a35; border-radius: 0; background: transparent; }
        """)

        ctl.changed.connect(self.update_display)
        self.update_display()
        self.restore_geometry()

    def refit(self):
        """Resize to fit the text after the text size changed."""
        if self.is_docked:
            self.dock()
        else:
            # Let Qt finish recalculating sizes from the new text before shrinking/growing.
            from PyQt6.QtCore import QEvent
            self.label.updateGeometry()
            for _ in range(3):  # label -> content area -> window
                QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest.value)
            self.adjustSize()
            self.ctl.save_geometry(self)

    @staticmethod
    def ticker_font(points=11):
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFamilies(["Consolas", "Menlo", "DejaVu Sans Mono", "monospace"])
        font.setPointSize(points)
        font.setBold(True)
        return font

    def apply_window_mode(self, show=True):
        """On top: stays above other windows, no taskbar button (use the tray icon).
        Not on top: an ordinary window with a taskbar button, so it's easy to find."""
        if self.ctl.on_top():
            flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                     Qt.WindowType.Tool)
        else:
            flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        if flags == self.windowFlags():
            return
        pos = self.pos()
        self.setWindowFlags(flags)  # this hides the window, so show it again
        if show:
            self.move(pos)
            self.show()

    # --- docking ---
    def apply_dock(self):
        if self.ctl.docked():
            self.dock()
        else:
            self.undock()

    def retire(self):
        """Remove this banner for good (e.g. switching from Every screen to One screen):
        give back its reserved strip first, and make sure nothing can re-dock it."""
        self._closing = True
        self.release_dock()
        self.is_docked = False
        self.hide()
        self.deleteLater()

    def _redock(self):
        """Delayed re-dock (after a resolution or taskbar change) - only if still wanted."""
        if self.is_docked and not self._closing:
            self.dock()

    def dock(self):
        if self._closing:
            return
        screen = self.screen_ or self.screen() or QApplication.primaryScreen()
        if self._watched_screen is not screen:  # re-dock after a resolution change
            if self._watched_screen is not None:
                try:
                    self._watched_screen.geometryChanged.disconnect(self._screen_changed)
                except TypeError:
                    pass
            screen.geometryChanged.connect(self._screen_changed)
            self._watched_screen = screen
        self.sizegrip.hide()
        if not self.is_docked:  # square corners: sits flush against the screen edge
            self.setStyleSheet(self.styleSheet().replace("border-radius: 12px", "border-radius: 0px"))
        self.is_docked = True
        height = max(self.minimumSizeHint().height(), self.sizeHint().height())
        if sys.platform.startswith("win"):
            g = screen.geometry()
            self.setGeometry(g.x(), g.y(), g.width(), height)  # get onto the right monitor first
            height = self.height()  # Qt may enforce a taller minimum than requested
            hwnd = int(self.winId())
            if self._appbar and self._appbar_hwnd != hwnd:
                # Qt recreated the window (e.g. a window-mode change): drop the old registration.
                _win_appbar(1, self._appbar_hwnd)  # ABM_REMOVE
                self._appbar = False
            if not self._appbar:
                _win_appbar(0, hwnd)  # ABM_NEW
                self._appbar, self._appbar_hwnd = True, hwnd
            left, top, right, _ = _win_monitor_rect(hwnd)
            h = round(height * screen.devicePixelRatio())
            # Ask Windows where the bar may go (e.g. below a top taskbar), then claim it.
            left, top, right, _ = _win_appbar(2, hwnd, (left, top, right, top + h))
            left, top, right, bottom = _win_appbar(3, hwnd, (left, top, right, top + h))
            import ctypes
            ctypes.windll.user32.MoveWindow(hwnd, left, top, right - left, bottom - top, True)
            return
        # macOS / Linux: sit just below the menu bar or top panel.
        if self._dock_area is None:
            self._dock_area = screen.availableGeometry()
        area = self._dock_area
        self.setGeometry(area.x(), area.y(), area.width(), height)
        height = self.height()  # Qt may enforce a taller minimum than requested
        if QApplication.platformName() == "xcb":
            try:
                dpr = screen.devicePixelRatio()
                _x11_strut(int(self.winId()), (round((area.y() + height) * dpr),
                                               round(area.x() * dpr), round((area.right()) * dpr)))
            except Exception as e:
                print(f"Docking (X11) failed: {e!r}", file=sys.stderr)

    def _screen_changed(self, *_):
        if self.is_docked:
            self.release_dock()
            QTimer.singleShot(500, self._redock)

    def release_dock(self):
        """Give the reserved screen space back (on undock, quit, update)."""
        self._dock_area = None
        if self._appbar:
            try:
                _win_appbar(1, self._appbar_hwnd)  # ABM_REMOVE, for the window that registered
            except Exception:
                pass
            self._appbar, self._appbar_hwnd = False, None
        elif self.is_docked and QApplication.platformName() == "xcb":
            try:
                _x11_strut(int(self.winId()), None)
            except Exception:
                pass

    def undock(self):
        if not self.is_docked:
            return
        self.release_dock()
        self.is_docked = False
        self.fullscreen_app = False
        self.setStyleSheet(self.styleSheet().replace("border-radius: 0px", "border-radius: 12px"))
        self.sizegrip.show()
        self.restore_geometry()

    def nativeEvent(self, event_type, message):
        if self._appbar and sys.platform.startswith("win"):
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == _APPBAR_CALLBACK:
                self._appbar_notice(msg.wParam, msg.lParam)
        return False, 0

    def _appbar_notice(self, wparam, lparam):
        """Windows tells docked bars when the layout changes or a full-screen app opens."""
        if self._closing or not self._appbar:
            return
        if wparam == ABN_POSCHANGED:  # taskbar moved, another bar added/removed...
            QTimer.singleShot(0, self._redock)
        elif wparam == ABN_FULLSCREENAPP:
            self.fullscreen_app = bool(lparam)
            if self.fullscreen_app:
                self.lower()

    def closeEvent(self, event):
        self.release_dock()  # last chance: never leave a reserved strip behind
        super().closeEvent(event)

    def raise_without_focus(self):
        if not self.isVisible():
            return
        if sys.platform.startswith("win"):
            # Re-assert "topmost" without taking keyboard focus from the app you're using.
            import ctypes
            HWND_TOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = -1, 0x1, 0x2, 0x10
            ctypes.windll.user32.SetWindowPos(int(self.winId()), HWND_TOPMOST, 0, 0, 0, 0,
                                              SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        else:
            self.raise_()

    def update_display(self):
        c = self.ctl
        show_solar = _yes(c.config.get("solar"))
        if self.ticker.isVisibleTo(self) != show_solar:
            self.ticker.setVisible(show_solar)
            if not show_solar:  # shrink back to one line
                self.resize(self.width(), self.minimumSizeHint().height())
            if self.is_docked:
                QTimer.singleShot(0, self._redock)  # the bar's height changed
        if show_solar:
            self.ticker.set_speed(c.config.get("ticker_speed").lower())
            self.ticker.set_html(c.solar_html, self.ticker_font(c.font_pt("ticker")))
        utc_dt = QDateTime.currentDateTimeUtc()
        local_dt = QDateTime.currentDateTime().toTimeZone(c.tz)
        # Each part has its own font size in points. The main text of a part (callsign, the
        # times, the temperature) is exactly that size; its small labels scale along with it.
        def sizer(part, main):
            pt = c.font_pt(part)
            return lambda n: f"{max(5.0, pt * n / main):.1f}pt"
        call, clock, wx = sizer("callsign", 18), sizer("clock", 24), sizer("weather", 18)
        clock_lbl = f"{c.font_pt('clock_label')}pt"   # "UTC" / "New York"
        date_size = f"{c.font_pt('date')}pt"
        cond_size = f"{c.font_pt('conditions')}pt"
        wx_lbl = f"{c.font_pt('weather_label')}pt"    # "WX" / "24h"
        weather = ""
        if _yes(c.config.get("show_weather")):
            weather = (f"<span style='color: #67C23A; font-size: {wx_lbl};'>WX</span>"
                       f"<span style='color: white; font-size: {wx(18)};'> {c.weather_html(wx_lbl, cond_size)} </span>")
        extra = ""
        if c.tz_bad:
            extra += f"<span style='color:red; font-size:{clock_lbl};'> ! BAD TIMEZONE IN SETTINGS !</span>"
        if c.status_text:
            extra += f"<span style='color:#E6A23C; font-size:{clock_lbl};'> &nbsp;{c.status_text}</span>"
        self.label.setText(f"""
        <div style='white-space: nowrap; font-weight: bold;'>
            <span style='color: #E6A23C; font-size: {call(18)};'>{c.callsign}</span> &nbsp;&nbsp;
            <span style='color: #409EFF; font-size: {clock_lbl};'>UTC</span>
            <span style='color: #409EFF; font-size: {clock(24)};'> {utc_dt.toString("hh:mm:ss")} </span> &nbsp;&nbsp;
            <span style='color: #888; font-size: {date_size};'>{local_dt.toString("yyyy-MM-dd")}</span> &nbsp;&nbsp;
            <span style='color: #E6A23C; font-size: {clock_lbl};'>{c.tz_label}</span>
            <span style='color: #E6A23C; font-size: {clock(24)};'> {local_dt.toString("hh:mm:ss")} </span> &nbsp;&nbsp;
            {weather}
            {extra}
        </div>
        """)

    # --- remember where the window was ---
    def restore_geometry(self):
        parser = self.ctl.config.parser
        target = self.screen_ or QApplication.primaryScreen()
        if parser.has_section(self.key) and "x" in parser[self.key]:
            w = parser[self.key]
            self.resize(_int(w.get("width"), 0) or self.sizeHint().width(),
                        _int(w.get("height"), 0) or self.sizeHint().height())
            pos = QPoint(_int(w.get("x"), 0), _int(w.get("y"), 0))
            # Only restore the position if it is still on a connected screen (the right one).
            on = QApplication.screenAt(pos + QPoint(20, 20))
            if on is not None and (self.screen_ is None or on == self.screen_):
                self.move(pos)
                return
        area = target.availableGeometry()
        self.adjustSize()
        self.move(area.center().x() - self.width() // 2, area.top() + 20)

    # --- mouse ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.offset = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and not self.is_docked:
            self.move(self.mapToGlobal(event.position().toPoint() - self.offset))

    def mouseReleaseEvent(self, event):
        self.ctl.save_geometry(self)

    def mouseDoubleClickEvent(self, event):
        self.ctl.open_settings()

    def contextMenuEvent(self, event):
        self.ctl.menu_banner = self
        self.ctl.menu.exec(event.globalPos())


def draw_icon(size):
    """The app icon - an antenna tower sending radio waves - drawn in code so the same
    picture is used for the program file, desktop shortcut, tray and windows."""
    from PyQt6.QtCore import QPointF, QRectF
    from PyQt6.QtGui import QImage, QLinearGradient, QPainterPath, QPen
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    small = size <= 32  # fewer, thicker lines so it still reads at tray size

    # Rounded dark tile
    m = s * 0.04
    tile = QRectF(m, m, s - 2 * m, s - 2 * m)
    grad = QLinearGradient(0, 0, 0, s)
    grad.setColorAt(0, QColor("#22304a"))
    grad.setColorAt(1, QColor("#0a0d16"))
    p.setBrush(grad)
    p.setPen(QPen(QColor("#3a4a66"), max(1.0, s * 0.012)))
    p.drawRoundedRect(tile, s * 0.2, s * 0.2)

    def pen(color, width):
        pn = QPen(QColor(color), max(1.2, s * width))
        pn.setCapStyle(Qt.PenCapStyle.RoundCap)
        pn.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pn

    # Radio waves (blue arcs) on both sides of the antenna tip
    apex = QPointF(s * 0.5, s * 0.36)
    p.setBrush(Qt.BrushStyle.NoBrush)
    radii = (0.17, 0.29) if small else (0.13, 0.22, 0.31)
    for i, r in enumerate(radii):
        p.setPen(pen("#409EFF" if i < 2 else "#2f6fb3", 0.075 if small else 0.045))
        box = QRectF(apex.x() - s * r, apex.y() - s * r, 2 * s * r, 2 * s * r)
        p.drawArc(box, 150 * 16, 60 * 16)    # left
        p.drawArc(box, -30 * 16, 60 * 16)    # right

    # Lattice tower (amber)
    base_y, half = s * 0.86, s * 0.17
    left, right = QPointF(s * 0.5 - half, base_y), QPointF(s * 0.5 + half, base_y)
    tower = QPainterPath(left)
    tower.lineTo(apex)
    tower.lineTo(right)
    p.setPen(pen("#E6A23C", 0.07 if small else 0.05))
    p.drawPath(tower)
    if not small:
        p.setPen(pen("#E6A23C", 0.028))
        steps = 4
        pts = []
        for i in range(1, steps + 1):
            t = i / (steps + 1)
            y = apex.y() + (base_y - apex.y()) * t
            w = half * t
            pts.append((QPointF(s * 0.5 - w, y), QPointF(s * 0.5 + w, y)))
        for (l1, r1), (l2, r2) in zip(pts, pts[1:]):
            p.drawLine(l1, r2)
            p.drawLine(r1, l2)
        for l1, r1 in pts:
            p.drawLine(l1, r1)
        p.setPen(pen("#E6A23C", 0.05))
        p.drawLine(QPointF(left.x() - s * 0.06, base_y), QPointF(right.x() + s * 0.06, base_y))

    # Glowing tip
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(230, 162, 60, 90))
    p.drawEllipse(apex, s * 0.07, s * 0.07)
    p.setBrush(QColor("#ffd27a"))
    p.drawEllipse(apex, s * (0.05 if small else 0.035), s * (0.05 if small else 0.035))
    p.end()
    return img


def make_icon():
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(draw_icon(size)))
    return icon


def main():
    # Wayland doesn't let apps place their own windows, stay on top or dock, so on Linux
    # prefer X11 (XWayland, present on nearly every Wayland desktop) and fall back to Wayland.
    if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY") \
            and not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "xcb;wayland"
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    icon = make_icon()
    app.setWindowIcon(icon)

    # Only one copy at a time: if the banner is already running (e.g. it started with
    # the computer), ask it to show itself and quit.
    server_name = f"{APP_NAME}-{os.environ.get('USERNAME') or os.environ.get('USER') or 'user'}"
    probe = QLocalSocket()
    probe.connectToServer(server_name)
    if probe.waitForConnected(500):
        probe.write(b"show")
        probe.waitForBytesWritten(500)
        probe.disconnectFromServer()
        return
    QLocalServer.removeServer(server_name)  # clear a leftover from a crash
    server = QLocalServer()
    server.listen(server_name)

    config = Config()
    config.migrate()
    ctl = Controller(config, icon)
    ctl.instance_server = server
    server.newConnection.connect(lambda: (server.nextPendingConnection(), ctl.show_banners()))
    refresh_autostart()
    ensure_desktop_shortcut_once(config)  # existing installs get the shortcut once, too
    if not config.exists:
        QTimer.singleShot(300, lambda: ctl.open_settings(first_run=True))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
