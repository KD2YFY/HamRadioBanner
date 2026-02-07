import sys
import os
import requests
import configparser
from PyQt6.QtWidgets import QApplication, QLabel, QWidget, QHBoxLayout, QSizeGrip, QVBoxLayout
from PyQt6.QtCore import QTimer, Qt, QDateTime, QPoint, QTimeZone

class MissionControlBanner(QWidget):
    def __init__(self):
        super().__init__()

        # --- 1. LOAD CONFIG WITH ABSOLUTE PATH ---
        self.config = configparser.ConfigParser()
        base_path = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(base_path, 'config.ini')
        
        read_success = self.config.read(config_path)
        
        # Default fallback values if config is missing or broken
        self.callsign = self.config.get('SETTINGS', 'callsign', fallback='N0CALL')
        self.tz_name = self.config.get('SETTINGS', 'timezone', fallback='UTC')
        self.wx_interval = int(self.config.get('SETTINGS', 'weather_refresh', fallback='30')) * 60000
        
        self.status_msg = "" if read_success else "<span style='color:red; font-size:10px;'>! CONFIG MISSING !</span>"

        # --- 2. WINDOW CONFIGURATION ---
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Main vertical layout to house content + resize grip
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Content widget
        self.content_widget = QWidget()
        self.content_layout = QHBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(30, 15, 30, 10)
        
        self.label = QLabel("Initializing HUD...")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        
        self.content_layout.addWidget(self.label)
        self.main_layout.addWidget(self.content_widget)

        # Resize Grip (Bottom Right)
        self.sizegrip = QSizeGrip(self)
        self.main_layout.addWidget(self.sizegrip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        # --- 3. STYLING ---
        self.setStyleSheet("""
            QWidget {
                background-color: rgba(10, 10, 15, 230); 
                border: 1px solid #3a3a4a;
                border-radius: 12px;
                font-family: 'Consolas', monospace;
            }
            QLabel { border: none; background: transparent; color: white; }
        """)

        # --- 4. DATA & TIMERS ---
        self.offset = QPoint()
        self.weather_info = "Loading WX..."
        
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_display)
        self.clock_timer.start(1000)
        
        self.weather_timer = QTimer(self)
        self.weather_timer.timeout.connect(self.get_weather)
        self.weather_timer.start(self.wx_interval)
        
        self.get_weather()
        self.update_display()

    def get_weather(self):
        try:
            # Get Lat/Lon from IP
            loc = requests.get('http://ip-api.com/json/', timeout=5).json()
            lat, lon, city = loc['lat'], loc['lon'], loc['city']
            
            # Fetch High/Low in Fahrenheit
            w_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=temperature_2m_max,temperature_2m_min&temperature_unit=fahrenheit&timezone=auto&forecast_days=1"
            res = requests.get(w_url, timeout=5).json()
            
            h, l = res['daily']['temperature_2m_max'][0], res['daily']['temperature_2m_min'][0]
            self.weather_info = f"{city} <span style='color:#ff5f5f;'>H:{int(h)}°</span> <span style='color:#5fafff;'>L:{int(l)}°</span>"
        except:
            self.weather_info = "<span style='color:#888;'>WX Unavailable</span>"

    def update_display(self):
        # Time Calculations
        utc_dt = QDateTime.currentDateTimeUtc()
        target_tz = QTimeZone(self.tz_name.encode())
        local_dt = QDateTime.currentDateTime().toTimeZone(target_tz)
        
        # Display logic
        clean_tz_label = self.tz_name.split('/')[-1].replace('_', ' ')
        
        content = f"""
        <div style='white-space: nowrap; font-weight: bold;'>
            <span style='color: #E6A23C; font-size: 18px;'>{self.callsign}</span> &nbsp;&nbsp;
            <span style='color: #409EFF; font-size: 11px;'>UTC</span>
            <span style='color: #409EFF; font-size: 24px;'> {utc_dt.toString("hh:mm:ss")} </span> &nbsp;&nbsp;
            <span style='color: #888; font-size: 14px;'>{local_dt.toString("yyyy-MM-dd")}</span> &nbsp;&nbsp;
            <span style='color: #E6A23C; font-size: 11px;'>{clean_tz_label}</span>
            <span style='color: #E6A23C; font-size: 24px;'> {local_dt.toString("hh:mm:ss")} </span> &nbsp;&nbsp;
            <span style='color: #67C23A; font-size: 11px;'>WX</span>
            <span style='color: white; font-size: 18px;'> {self.weather_info} </span>
            {self.status_msg}
        </div>
        """
        self.label.setText(content)

    # --- MOUSE EVENTS FOR MOVING ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.offset = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(self.mapToGlobal(event.position().toPoint() - self.offset))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    banner = MissionControlBanner()
    banner.show()
    sys.exit(app.exec())