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
from datetime import datetime, timezone
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
    QLabel, QLineEdit, QMenu, QMessageBox, QSizeGrip, QSpinBox, QSystemTrayIcon, QVBoxLayout,
    QWidget,
)

__version__ = "1.3.0"  # must match the version typed when publishing a release
APP_NAME = "HamRadioBanner"
RELEASES_API = os.environ.get("HAMRADIOBANNER_RELEASES_API",  # override is for testing only
                              "https://api.github.com/repos/KD2YFY/HamRadioBanner/releases/latest")
RELEASES_PAGE = "https://github.com/KD2YFY/HamRadioBanner/releases/latest"
DEFAULTS = {
    "callsign": "N0CALL",
    "timezone": "",           # blank = use the computer's timezone
    "weather_refresh": "30",  # minutes
    "units": "F",             # F or C
    "location": "",           # blank = detect from IP; or "City, State" or "lat, lon"
    "screens": "one",         # one = a single banner; all = a banner on every monitor
    "auto_update": "no",      # yes = install new versions without asking
    "solar": "no",            # yes = scrolling solar/propagation ticker under the clock
    "ticker_speed": "normal", # slow, normal or fast
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


def fetch_weather(location, units):
    lat, lon, name = lookup_location_cached(location)
    res = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m",
            "daily": "temperature_2m_max,temperature_2m_min",
            "temperature_unit": "celsius" if units == "C" else "fahrenheit",
            "timezone": "auto", "forecast_days": 1,
        },
        timeout=5,
    ).json()
    now = res["current"]["temperature_2m"]
    hi = res["daily"]["temperature_2m_max"][0]
    lo = res["daily"]["temperature_2m_min"][0]
    return (f"{name} {round(now)}° "
            f"<span style='color:#ff5f5f;'>H:{round(hi)}°</span> "
            f"<span style='color:#5fafff;'>L:{round(lo)}°</span>")




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
        if html == self._html:
            return
        self._html = html
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
        QLineEdit, QComboBox, QSpinBox {{
            background: #2a2a35; color: #ffffff; font-size: 13px;
            border: 1px solid #4a4a5a; border-radius: 5px; padding: 5px 8px; min-height: 18px;
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: #409EFF; }}
        QComboBox::drop-down {{ border: none; width: 24px; }}
        QComboBox::down-arrow {{ image: url("{arrow}"); width: 14px; height: 14px; }}
        QComboBox QAbstractItemView {{
            background: #2a2a35; color: #ffffff; selection-background-color: #409EFF;
            border: 1px solid #4a4a5a;
        }}
        QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
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
    """


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None, first_run=False):
        super().__init__(parent)
        self.setWindowTitle("Ham Radio Banner - Settings")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(settings_style())
        self.setMinimumWidth(440)
        self.config = config
        layout = QVBoxLayout(self)

        if first_run:
            intro = QLabel("Welcome! Enter your callsign to get started.\n"
                           "You can change these later by right-clicking the banner.")
            intro.setWordWrap(True)
            layout.addWidget(intro)

        form = QFormLayout()
        layout.addLayout(form)

        self.callsign = QLineEdit(config.get("callsign") if not first_run else "")
        self.callsign.setPlaceholderText("e.g. N0CALL")
        self.callsign.textEdited.connect(lambda t: self.callsign.setText(t.upper()))
        form.addRow("Callsign:", self.callsign)

        self.timezone = QComboBox()
        self.timezone.setEditable(True)
        zones = sorted(z.data().decode() for z in QTimeZone.availableTimeZoneIds())
        self.timezone.addItems(zones)
        self.timezone.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.timezone.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.timezone.setCurrentText(config.get("timezone") or QTimeZone.systemTimeZoneId().data().decode())
        form.addRow("Local timezone:", self.timezone)

        self.location = QLineEdit(config.get("location"))
        self.location.setPlaceholderText("Blank = detect automatically")
        self.location.setToolTip("Leave blank to guess from your internet connection,\n"
                                 "or enter a place like 'Albany, NY', a ZIP/postal code,\n"
                                 "or coordinates like '42.65, -73.75'.")
        form.addRow("Weather location:", self.location)

        self.units = QComboBox()
        self.units.addItems(["°F", "°C"])
        self.units.setCurrentIndex(1 if config.get("units").upper() == "C" else 0)
        form.addRow("Temperature:", self.units)

        self.refresh = QSpinBox()
        self.refresh.setRange(5, 240)
        self.refresh.setSuffix(" min")
        self.refresh.setValue(_int(config.get("weather_refresh"), 30))
        form.addRow("Weather refresh:", self.refresh)

        self.screens = QComboBox()
        self.screens.addItems(["One screen", "Every screen"])
        self.screens.setCurrentIndex(1 if config.get("screens").lower() == "all" else 0)
        self.screens.setToolTip("Every screen = a separate banner on each monitor.\n"
                                "Drag each one where you want it; positions are remembered.")
        form.addRow("Show on:", self.screens)

        self.auto_update = QCheckBox("Install updates automatically")
        self.auto_update.setChecked(_yes(config.get("auto_update")))
        if self_update_mode() is None:
            self.auto_update.setEnabled(False)
            self.auto_update.setToolTip("This copy can't update itself (installed with pipx or git).")
        form.addRow("Updates:", self.auto_update)

        self.solar = QCheckBox("Show solar && band conditions ticker")
        self.solar.setChecked(_yes(config.get("solar")))
        self.solar.setToolTip("A scrolling line with SFI, A/K index, X-ray, solar wind and\n"
                              "HF/VHF band conditions from N0NBH (hamqsl.com).")
        form.addRow("Propagation:", self.solar)

        self.ticker_speed = QComboBox()
        self.ticker_speed.addItems(["Slow", "Normal", "Fast"])
        self.ticker_speed.setCurrentText(config.get("ticker_speed").capitalize()
                                         if config.get("ticker_speed").lower() in Ticker.SPEEDS else "Normal")
        self.solar.toggled.connect(self.ticker_speed.setEnabled)
        self.ticker_speed.setEnabled(self.solar.isChecked())
        form.addRow("Ticker speed:", self.ticker_speed)

        self.on_top = QCheckBox("Always on top of other windows")
        self.on_top.setChecked(_yes(config.get("on_top")))
        self.on_top.setToolTip("When off, the banner acts like a normal window and gets a taskbar button.")
        form.addRow("Window:", self.on_top)

        self.autostart = QCheckBox("Start when the computer starts")
        self.autostart.setChecked(True if first_run else autostart_enabled())
        form.addRow("Startup:", self.autostart)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
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
        self.config.set("screens", "all" if self.screens.currentIndex() == 1 else "one")
        self.config.set("auto_update", "yes" if self.auto_update.isChecked() else "no")
        self.config.set("on_top", "yes" if self.on_top.isChecked() else "no")
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
    _weather_ready = pyqtSignal(str)
    _solar_ready = pyqtSignal(str)
    _update_found = pyqtSignal(dict)
    _update_ready = pyqtSignal(object)
    _update_failed = pyqtSignal(str)

    def __init__(self, config, icon):
        super().__init__()
        self.config = config
        self.icon = icon
        self.banners = {}
        self.weather_info = "Loading WX..."
        self.update_info = None
        self.status_text = ""
        self._fetching = False
        self._updating = False
        self.instance_server = None

        # Shared menu used by every banner and the tray icon.
        self.menu = QMenu()
        self.menu.addAction("Settings…", lambda: self.open_settings())
        self.menu.addAction("Refresh Weather", self.get_weather)
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
        self._update_ready.connect(self._on_update_ready)
        self._update_failed.connect(self._on_update_failed)

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.changed)
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
        if any(old.get(k) != self._applied[k] for k in ("location", "units")) or not old:
            self.get_weather()
        if _yes(self._applied["solar"]) and (not old or not _yes(old.get("solar"))
                                             or old.get("location") != self._applied["location"]):
            self.get_solar()
        if old.get("screens") != self._applied["screens"]:
            self.layout_banners()
        if old and old.get("on_top") != self._applied["on_top"]:
            for b in self.banners.values():
                b.apply_window_mode()
        self.changed.emit()

    def open_settings(self, first_run=False):
        # No parent window: otherwise the banner's dark style leaks into the dialog.
        if SettingsDialog(self.config, None, first_run=first_run).exec():
            self.apply_settings()

    # --- banners: one, or one per monitor ---
    def layout_banners(self):
        if self.config.get("screens").lower() == "all":
            wanted = {f"WINDOW {s.name()}": s for s in QApplication.screens()}
        else:
            wanted = {"WINDOW": None}
        for key in list(self.banners):
            if key not in wanted:
                self.banners.pop(key).deleteLater()
        for key, screen in wanted.items():
            if key not in self.banners:
                banner = Banner(self, key, screen)
                self.banners[key] = banner
                banner.show()

    def _forget_menu_banner(self):
        self.menu_banner = None

    def match_size(self):
        source = self.menu_banner
        if source is None:
            return
        for b in self.banners.values():
            if b is not source:
                b.resize(source.size())
                self.save_geometry(b)

    def on_top(self):
        return _yes(self.config.get("on_top"))

    def keep_on_top(self):
        # Don't cover the Settings window or a message box while one is open.
        if not self.on_top() or QApplication.activeModalWidget() or QApplication.activePopupWidget():
            return
        for b in self.banners.values():
            b.raise_without_focus()

    def show_banners(self, *_):
        for b in self.banners.values():
            b.show()
            b.raise_()

    def save_geometry(self, banner):
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
    def get_weather(self):
        if self._fetching:
            return
        self._fetching = True
        location, units = self.config.get("location"), self.config.get("units").upper()

        def work():
            try:
                text = fetch_weather(location, units)
            except LocationNotFound:
                text = ("<span style='color:#888;'>Location not found - "
                        "right-click &gt; Settings</span>")
            except requests.RequestException as e:
                print(f"Weather error: {e}", file=sys.stderr)
                text = "<span style='color:#888;'>WX Unavailable (no connection)</span>"
            except Exception as e:  # unexpected reply from the weather service
                print(f"Weather error: {e!r}", file=sys.stderr)
                text = "<span style='color:#888;'>WX Unavailable</span>"
            self._weather_ready.emit(text)

        threading.Thread(target=work, daemon=True).start()

    def _on_weather(self, text):
        self._fetching = False
        self.weather_info = text
        self.changed.emit()

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
    def check_update(self):
        def work():
            try:
                found = check_for_update()
            except Exception as e:  # offline, GitHub down, rate-limited... try again tomorrow
                print(f"Update check failed: {e}", file=sys.stderr)
                return
            if found:
                self._update_found.emit(found)

        threading.Thread(target=work, daemon=True).start()

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

    @staticmethod
    def ticker_font():
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFamilies(["Consolas", "Menlo", "DejaVu Sans Mono", "monospace"])
        font.setPixelSize(14)
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
        if show_solar:
            self.ticker.set_speed(c.config.get("ticker_speed").lower())
            self.ticker.set_html(c.solar_html, self.ticker_font())
        utc_dt = QDateTime.currentDateTimeUtc()
        local_dt = QDateTime.currentDateTime().toTimeZone(c.tz)
        extra = ""
        if c.tz_bad:
            extra += "<span style='color:red; font-size:10px;'> ! BAD TIMEZONE IN SETTINGS !</span>"
        if c.status_text:
            extra += f"<span style='color:#E6A23C; font-size:11px;'> &nbsp;{c.status_text}</span>"
        self.label.setText(f"""
        <div style='white-space: nowrap; font-weight: bold;'>
            <span style='color: #E6A23C; font-size: 18px;'>{c.callsign}</span> &nbsp;&nbsp;
            <span style='color: #409EFF; font-size: 11px;'>UTC</span>
            <span style='color: #409EFF; font-size: 24px;'> {utc_dt.toString("hh:mm:ss")} </span> &nbsp;&nbsp;
            <span style='color: #888; font-size: 14px;'>{local_dt.toString("yyyy-MM-dd")}</span> &nbsp;&nbsp;
            <span style='color: #E6A23C; font-size: 11px;'>{c.tz_label}</span>
            <span style='color: #E6A23C; font-size: 24px;'> {local_dt.toString("hh:mm:ss")} </span> &nbsp;&nbsp;
            <span style='color: #67C23A; font-size: 11px;'>WX</span>
            <span style='color: white; font-size: 18px;'> {c.weather_info} </span>
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
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(self.mapToGlobal(event.position().toPoint() - self.offset))

    def mouseReleaseEvent(self, event):
        self.ctl.save_geometry(self)

    def mouseDoubleClickEvent(self, event):
        self.ctl.open_settings()

    def contextMenuEvent(self, event):
        self.ctl.menu_banner = self
        self.ctl.menu.exec(event.globalPos())


def make_icon():
    """Draw a simple tray icon so no image file needs to ship with the program."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(10, 10, 15))
    p.setPen(QColor("#E6A23C"))
    p.drawRoundedRect(2, 2, 60, 60, 12, 12)
    p.setFont(QFont("Arial", 26, QFont.Weight.Bold))
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "HR")
    p.end()
    return QIcon(pix)


def main():
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
    ctl = Controller(config, icon)
    ctl.instance_server = server
    server.newConnection.connect(lambda: (server.nextPendingConnection(), ctl.show_banners()))
    refresh_autostart()
    if not config.exists:
        QTimer.singleShot(300, lambda: ctl.open_settings(first_run=True))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
