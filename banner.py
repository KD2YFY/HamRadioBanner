"""Ham Radio Banner - a small always-on-top HUD showing callsign, UTC/local time and weather.

Right-click the banner (or its tray icon) for Settings, Refresh Weather and Quit.
"""
import configparser
import os
import sys
import threading

import requests
from PyQt6.QtCore import QDateTime, QUrl, QFileSystemWatcher, QPoint, QStandardPaths, Qt, QTimer, QTimeZone, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QMessageBox, QSizeGrip, QSpinBox, QSystemTrayIcon, QVBoxLayout,
    QWidget,
)

__version__ = "1.1.2"  # must match the version typed when publishing a release
APP_NAME = "HamRadioBanner"
RELEASES_API = "https://api.github.com/repos/KD2YFY/HamRadioBanner/releases/latest"
RELEASES_PAGE = "https://github.com/KD2YFY/HamRadioBanner/releases/latest"
DEFAULTS = {
    "callsign": "N0CALL",
    "timezone": "",           # blank = use the computer's timezone
    "weather_refresh": "30",  # minutes
    "units": "F",             # F or C
    "location": "",           # blank = detect from IP; or "City, State" or "lat, lon"
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
    lat, lon, name = lookup_location(location)
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


# ---------------------------------------------------------------- update ---

def _version_tuple(v):
    """'v1.2.3' -> (1, 2, 3); anything unparseable -> ()"""
    try:
        return tuple(int(x) for x in v.strip().lstrip("vV").split("."))
    except ValueError:
        return ()


def check_for_update():
    """Return (version, url) of a newer GitHub release, or None."""
    res = requests.get(RELEASES_API, timeout=10,
                       headers={"Accept": "application/vnd.github+json"}).json()
    latest = res.get("tag_name", "")
    if _version_tuple(latest) > _version_tuple(__version__):
        return latest, res.get("html_url") or RELEASES_PAGE
    return None


# -------------------------------------------------------------- settings ---

class SettingsDialog(QDialog):
    def __init__(self, config, parent=None, first_run=False):
        super().__init__(parent)
        self.setWindowTitle("Ham Radio Banner - Settings")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
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
        self.config.set("callsign", self.callsign.text().strip().upper() or DEFAULTS["callsign"])
        self.config.set("timezone", tz)
        self.config.set("location", self.location.text().strip())
        self.config.set("units", "C" if self.units.currentIndex() == 1 else "F")
        self.config.set("weather_refresh", self.refresh.value())
        try:
            self.config.save()
        except OSError as e:
            QMessageBox.warning(self, "Could not save", f"Could not write {self.config.path}:\n{e}")
        super().accept()


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- banner ---

class MissionControlBanner(QWidget):
    weather_ready = pyqtSignal(str)
    update_found = pyqtSignal(str, str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.weather_info = "Loading WX..."
        self._fetching = False
        self.offset = QPoint()

        self.setWindowTitle("Ham Radio Banner")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setToolTip("Drag to move · drag corner to resize · right-click for options")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(30, 15, 30, 10)
        self.label = QLabel("Initializing HUD...")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        content_layout.addWidget(self.label)
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
        """)

        self.menu = QMenu()
        self.menu.addAction("Settings…", self.open_settings)
        self.menu.addAction("Refresh Weather", self.get_weather)
        self.update_url = RELEASES_PAGE
        self.update_action = QAction("Download update", self)
        self.update_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(self.update_url)))
        self.update_action.setVisible(False)
        self.menu.addAction(self.update_action)
        self.menu.addSeparator()
        about = self.menu.addAction(f"Version {__version__}")
        about.setEnabled(False)
        self.menu.addAction("Quit", QApplication.instance().quit)
        self.update_text = ""

        # Check GitHub for a newer release shortly after start, then once a day.
        self.update_found.connect(self._on_update_found)
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.check_update)
        self.update_timer.start(24 * 60 * 60 * 1000)
        QTimer.singleShot(5000, self.check_update)

        self.weather_ready.connect(self._on_weather)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_display)
        self.clock_timer.start(1000)
        self.weather_timer = QTimer(self)
        self.weather_timer.timeout.connect(self.get_weather)

        # Apply hand edits to config.ini right away (e.g. another operator's callsign).
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._on_config_file_changed)
        self.apply_settings()
        self.restore_geometry()

    # --- settings ---
    def _watch_config(self):
        if os.path.exists(self.config.path) and self.config.path not in self.watcher.files():
            self.watcher.addPath(self.config.path)

    def _on_config_file_changed(self, _path):
        # Some editors replace the file on save; wait a moment, then watch it again.
        QTimer.singleShot(200, self._reload_config)

    def _reload_config(self):
        self._watch_config()
        if self.config.reload() and self.config.settings() != self._applied:
            self.apply_settings()

    def apply_settings(self):
        self._applied = self.config.settings()
        self._watch_config()
        self.callsign = self.config.get("callsign")
        tz_name = self.config.get("timezone")
        self.tz = QTimeZone(tz_name.encode()) if tz_name else QTimeZone.systemTimeZone()
        self.tz_bad = not self.tz.isValid()
        if self.tz_bad:
            self.tz = QTimeZone.systemTimeZone()
        tz_id = self.tz.id().data().decode()
        self.tz_label = tz_id.split("/")[-1].replace("_", " ")
        self.weather_timer.start(max(5, _int(self.config.get("weather_refresh"), 30)) * 60000)
        self.get_weather()
        self.update_display()

    def open_settings(self, first_run=False):
        dlg = SettingsDialog(self.config, self, first_run=first_run)
        if dlg.exec():
            self.apply_settings()

    # --- weather (runs in background so the clock never freezes) ---
    def check_update(self):
        def work():
            try:
                found = check_for_update()
            except Exception as e:  # offline, GitHub down, rate-limited... try again tomorrow
                print(f"Update check failed: {e}", file=sys.stderr)
                return
            if found:
                self.update_found.emit(*found)

        threading.Thread(target=work, daemon=True).start()

    def _on_update_found(self, version, url):
        self.update_url = url
        self.update_action.setText(f"Download update ({version})…")
        self.update_action.setVisible(True)
        self.update_text = ("<span style='color:#E6A23C; font-size:11px;'> &nbsp;⬆ Update available "
                            "(right-click)</span>")
        self.update_display()

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
            self.weather_ready.emit(text)

        threading.Thread(target=work, daemon=True).start()

    def _on_weather(self, text):
        self._fetching = False
        self.weather_info = text
        self.update_display()

    def update_display(self):
        utc_dt = QDateTime.currentDateTimeUtc()
        local_dt = QDateTime.currentDateTime().toTimeZone(self.tz)
        warn = ""
        if self.tz_bad:
            warn = "<span style='color:red; font-size:10px;'> ! BAD TIMEZONE IN SETTINGS !</span>"
        self.label.setText(f"""
        <div style='white-space: nowrap; font-weight: bold;'>
            <span style='color: #E6A23C; font-size: 18px;'>{self.callsign}</span> &nbsp;&nbsp;
            <span style='color: #409EFF; font-size: 11px;'>UTC</span>
            <span style='color: #409EFF; font-size: 24px;'> {utc_dt.toString("hh:mm:ss")} </span> &nbsp;&nbsp;
            <span style='color: #888; font-size: 14px;'>{local_dt.toString("yyyy-MM-dd")}</span> &nbsp;&nbsp;
            <span style='color: #E6A23C; font-size: 11px;'>{self.tz_label}</span>
            <span style='color: #E6A23C; font-size: 24px;'> {local_dt.toString("hh:mm:ss")} </span> &nbsp;&nbsp;
            <span style='color: #67C23A; font-size: 11px;'>WX</span>
            <span style='color: white; font-size: 18px;'> {self.weather_info} </span>
            {warn}{self.update_text}
        </div>
        """)

    # --- remember where the window was ---
    def restore_geometry(self):
        w = self.config.parser["WINDOW"]
        if "x" in w:
            self.resize(_int(w.get("width"), 0) or self.sizeHint().width(),
                        _int(w.get("height"), 0) or self.sizeHint().height())
            pos = QPoint(_int(w.get("x"), 0), _int(w.get("y"), 0))
            # Only restore the position if it is still on a connected screen.
            if QApplication.screenAt(pos + QPoint(20, 20)) is not None:
                self.move(pos)
                return
        screen = QApplication.primaryScreen().availableGeometry()
        self.adjustSize()
        self.move(screen.center().x() - self.width() // 2, screen.top() + 20)

    def save_geometry(self):
        if not self.config.exists:
            return
        # Re-read first so we never overwrite edits someone made to config.ini by hand.
        if not self.config.reload():
            return
        w = self.config.parser["WINDOW"]
        w["x"], w["y"] = str(self.x()), str(self.y())
        w["width"], w["height"] = str(self.width()), str(self.height())
        try:
            self.config.save()
        except OSError:
            pass

    # --- mouse ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.offset = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(self.mapToGlobal(event.position().toPoint() - self.offset))

    def mouseReleaseEvent(self, event):
        self.save_geometry()

    def mouseDoubleClickEvent(self, event):
        self.open_settings()

    def contextMenuEvent(self, event):
        self.menu.exec(event.globalPos())


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

    config = Config()
    banner = MissionControlBanner(config)
    banner.show()

    # Tray icon: the banner has no taskbar button, so this is how to find/quit it.
    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(icon)
        tray.setToolTip("Ham Radio Banner")
        tray_menu = QMenu()
        show = QAction("Show Banner", tray_menu)
        show.triggered.connect(lambda: (banner.show(), banner.raise_()))
        tray_menu.addAction(show)
        tray_menu.addAction("Settings…", banner.open_settings)
        tray_menu.addAction("Refresh Weather", banner.get_weather)
        tray_menu.addAction(banner.update_action)
        tray_menu.addSeparator()
        tray_menu.addAction("Quit", app.quit)
        tray.setContextMenu(tray_menu)
        tray.show()

    if not config.exists:
        QTimer.singleShot(300, lambda: banner.open_settings(first_run=True))

    app.aboutToQuit.connect(banner.save_geometry)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
