# HamRadioBanner
A Simple Ham Radio Banner using Python - Vibe Coded with Gemini. Callsign/UTC/Local/WX information. Resizeable and moveable

=====================================================
          MISSION CONTROL HUD
=====================================================

A transparent, movable, and resizable desktop banner 
displaying UTC time, Local time, and live weather.

-----------------------------------------------------
1. PREREQUISITES
-----------------------------------------------------
You must have Python 3.9 or newer installed on your 
system. You can download it from python.org.

-----------------------------------------------------
2. INSTALLATION
-----------------------------------------------------
Open your terminal (Command Prompt or PowerShell) and 
run the following command to install the required 
libraries:

python -m pip install PyQt6 requests

-----------------------------------------------------
3. CONFIGURATION (config.ini)
-----------------------------------------------------
Before running the script, ensure there is a file 
named 'config.ini' in the same folder. 

Open 'config.ini' in a text editor (like Notepad) 
to customize:
- callsign: Your Amateur Radio callsign.
- timezone: Your local timezone (e.g., America/New_York).
- weather_refresh: How often to update weather (in mins).

-----------------------------------------------------
4. HOW TO RUN
-----------------------------------------------------
Double-click 'banner.py' or run via terminal:

python banner.py

-----------------------------------------------------
5. CONTROLS
-----------------------------------------------------
- MOVE: Click and hold anywhere on the dark 
        background and drag.
- RESIZE: Click and drag the bottom-right corner.
- CLOSE: Close the terminal window running the 
         script.

=====================================================
