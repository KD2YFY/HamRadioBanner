# Ham Radio Banner

A small always-on-top desktop banner for your shack. It shows your **callsign**, **UTC time**, **local time** and **live weather**. You can move and resize it.

```
W1AW   UTC 13:00:36   2026-11-12   New York 08:00:36   WX Albany 51° H:60° L:40°
```

---

## Install

Pick **one** of these options.

### Option A: Download the app (easiest, no Python needed)

1. Go to the [**Releases**](../../releases) page.
2. Download the zip for your computer: `HamRadioBanner-Windows.zip`, `HamRadioBanner-macOS.zip` or `HamRadioBanner-Linux.zip`.
3. Unzip it and double-click **HamRadioBanner**.

No downloads on the Releases page yet? Use Option B for now.

> **Windows:** if SmartScreen says "Windows protected your PC", click **More info → Run anyway**.
> **Mac:** the first time, right-click the app, choose **Open**, then click **Open** again.

### Linux: one-line install

Open a terminal and paste:

```bash
curl -fsSL https://raw.githubusercontent.com/KD2YFY/HamRadioBanner/main/install-linux.sh | bash
```

This downloads the latest version into `~/.local/share/HamRadioBanner` and installs the one small system library it needs (it may ask for your password). Then it starts the banner, which adds a desktop shortcut and an applications-menu entry. Run the same command again any time to reinstall; your settings are kept. It works on 64-bit PCs with Linux Mint 21 or newer, Ubuntu 22.04 or newer, Debian 12 or newer, current Fedora, and Arch. It adds **Ham Radio Banner** to your applications menu and desktop. The program itself lives in a hidden folder, which you can see with Ctrl+H in the file manager, but you shouldn't need to go there. On a Raspberry Pi, use Option C.

On Wayland desktops (the default on newer Ubuntu and Fedora), the banner runs through XWayland so that it can stay on top, remember its position and dock.

### Option B: Double-click launcher (needs Python 3.9+)

1. Install Python from [python.org](https://www.python.org/downloads/). On Windows, tick **"Add python.exe to PATH"** during install.
2. Download this project with the green **Code → Download ZIP** button and unzip it.
3. Double-click the launcher for your system:
   - **Windows:** `Start Banner (Windows).bat`
   - **Mac:** `Start Banner (Mac).command`
   - **Linux:** `./start_banner.sh`

The first launch takes about a minute because it installs what the banner needs. Later launches are instant.

### Option C: For Python users

```bash
pipx install git+https://github.com/KD2YFY/HamRadioBanner.git
```

```bash
hamradiobanner
```

---

## First run

On first run, a small window asks for your **callsign**. Your timezone is detected automatically, and so is your weather location. Click **Save** and you're done. A **Ham Radio Banner** shortcut with the antenna icon is added to your desktop. From then on, the banner starts by itself whenever you turn on the computer.

## Using it

| To…                       | Do this                                                  |
|---------------------------|----------------------------------------------------------|
| Move it                   | Click and drag anywhere on the banner                    |
| Resize it                 | Drag the bottom-right corner                             |
| Match sizes (multi-screen)| Resize one banner, then **Right-click → Make all banners this size** |
| Change settings           | **Right-click → Settings…** (or double-click the banner) |
| Update the weather now    | **Right-click → Refresh Weather**                        |
| Check for a new version   | **Right-click → Check for Updates…** (or the button in Settings) |
| Get the desktop shortcut back | **Settings → Create desktop shortcut**               |
| Quit                      | **Right-click → Quit**, or use the tray icon             |
| Find a hidden banner      | Click the tray icon (orange antenna tower), or start the banner again; it comes to the front |

The banner remembers its position and size between runs. A tray icon (an orange antenna tower) also appears in your system tray or menu bar, so you can find the banner or quit it. On Windows, the icon may be hidden under the **^** arrow by the clock; drag it onto the taskbar to keep it in view.

## Settings

All settings can be changed from **Right-click → Settings…**, organized in tabs: **General** (callsign, timezone, screens, on top, dock, startup), **Weather**, **Station data**, **Propagation**, **Text sizes** and **Updates**.

- **Callsign:** your amateur radio callsign.
- **Local timezone:** start typing to search, e.g. `New_York` or `London`.
- **Weather location:** leave blank to detect it automatically. If a place name isn't found, Settings offers the closest match (e.g. *Did you mean Gansevoort, NY?*); a ZIP code always works. You can also enter a town and state such as `Warrensburg NY` or `Albany, NY`, a ZIP code such as `12885`, a place such as `London, United Kingdom`, or coordinates such as `42.65, -73.75`. Include the state, since many towns share a name.
- **Temperature:** °F or °C.
- **Weather refresh:** how often to update, in minutes.
- **Weather:** untick **Show weather** to remove the weather from the banner completely. Nothing is downloaded for it while it's off.
- **Conditions:** show the current conditions after the temperature. These are *Sunny/Clear*, *Mostly sunny*, *Partly cloudy*, *Cloudy*, *Fog*, *Drizzle*, *Rain*, *Showers*, *Freezing rain*, *Snow* and *Thunderstorm*, plus *Windy (gusts 35 mph)* when it's breezy. They come from the weather model, which works worldwide. When an NWS station supplies the current temperature, its own observed description is used instead (e.g. *Light snow*). Off by default.
- **High/low:** show or hide today's forecast high and low.
- **24-hour:** show the highest and lowest temperatures over the 24 hours ending at a set time. The default is 6:30 AM, like a daily weather observation, and you can change it. It rolls over to the new day at that time.
- **Station data:** where the 24-hour high/low comes from. The banner shows the source: `24h KGFL→6:30a`, `24h home→6:30a`, or `24h model→6:30a` when it had to fall back to the model.
  - **NWS weather station (US):** real thermometer readings from an official National Weather Service station, usually the nearest airport. Leave **Station ID** blank to use the nearest one, or type an ID such as `KGFL` or `KALB`. No account needed. Outside the US, it falls back to the model.
  - **Weather model:** Open-Meteo's 15-minute model data for your location, anywhere in the world.
  - **CWOP / APRS weather near you (aprs.fi):** backyard stations from the Citizen Weather Observer Program; many of them also feed Weather Underground. You don't need a station of your own.
    1. Sign up free at [aprs.fi](https://aprs.fi/) and copy your API key from *Account settings*.
    2. Find a station near you on the aprs.fi map (weather stations have a WX icon) and enter its callsign or CWOP ID, e.g. `KD2XXX-13` or `EW1234`.

    aprs.fi only gives the *latest* reading, so the banner records the station every 10 minutes and shows the 24-hour high/low once it has a full day of readings. Until then it shows *collecting (6h of 24)*. If the computer is off for more than 2 hours, it collects again rather than show a high/low that may have missed the overnight low.
  - **Synoptic Data, stations near you:** combines 100,000+ stations (CWOP backyard stations, schools, DOT road sensors…) with full history, so the 24-hour high/low works right away. [Request a free open-access token](https://synopticdata.com/open-access) and paste it in. Leave **Station ID** blank for the nearest station within 15 miles, or enter one.
  - **My station:** your own home weather station. It supplies both the current temperature and the 24-hour high/low. Click **Test station** to check that it connects.
    - **Weather Underground:** your station ID (like `KNYWARRE12`) and the API key from *wunderground.com → My Profile → API Keys*, which is free for station owners. Davis, Acurite and other stations that upload to Weather Underground work this way.
    - **Ambient Weather:** an API key and an Application key from *ambientweather.net → Account → API Keys*. A MAC address is needed only if you have more than one station.
    - **Ecowitt:** your station's MAC address, plus the API key and Application key from *ecowitt.net → User Profile → Private Center*.

    **Also use the station for the current temperature** (on by default) takes the current temperature from the station too: the latest NWS reading (ignored if over 2 hours old), or your own station's live reading. Untick it to use the station only for the 24-hour high/low.

    Your keys are saved in `config.ini` on your own computer and are only sent to your station's service.
- **Text sizes:** font sizes in points, like in a document. Any size from 6 to 72 pt works, and the banner resizes to fit. The defaults are:
  - **Callsign:** 14 pt
  - **Clock times:** 18 pt
  - **Date:** 10 pt
  - **Clock labels** ("UTC", "New York"): 8 pt
  - **Temperatures:** 14 pt; this covers the town name and the readings
  - **WX labels** ("WX", "24h"): 8 pt
  - **Conditions** ("Cloudy", "Light snow"): 11 pt
  - **Ticker:** 11 pt
- **Show on:** *One screen*, or *Every screen* for a separate banner on each monitor. Drag each banner where you want it on its monitor; every position is remembered, and banners appear or disappear as you plug in or unplug monitors.
- **Install updates automatically:** install new versions without asking. See [Updating](#updating).
- **Show solar & band conditions ticker:** off by default. When ticked, a second line scrolls under the clock. It shows SFI, sunspots, A and K index, X-ray, solar wind, Bz, geomagnetic field and noise. It also shows HF band conditions for day and night, and a VHF outlook: Aurora Likely or Watch, Possible 6m F2, E-skip Season, or Flat/Tropo. The VHF outlook is based on NOAA's aurora model, the 30-minute Bz and the Kp forecast, plus HamQSL's sporadic-E flags. Colors run green (good), yellow (fair) and red (poor). The data refreshes hourly.
- **Ticker speed:** Slow, Normal or Fast.
- **Always on top of other windows:** on by default. The banner stays above your other programs and has no taskbar button; use the tray icon to find it. Turn it off and the banner acts like a normal window with its own taskbar button.
- **Dock to top of screen:** off by default. When ticked, the banner becomes a full-width bar across the top of the screen, like a taskbar. Other windows, even maximized ones, fit underneath it instead of covering it. Full-screen games and videos can still use the whole screen. Untick it and the banner goes back to where you had it. With *Show on: Every screen*, each monitor gets its own docked bar. Docking works on Windows and on most Linux desktops (X11). On a Mac it pins the banner under the menu bar, but macOS doesn't let apps reserve screen space, so other windows can still go over it.
- **Start when the computer starts:** the banner opens by itself when you log in. This is on by default for new installs. Untick it to stop.

Settings are saved to `config.ini` next to the program. You can also edit that file in a text editor; changes (such as a different operator's callsign) show up within a second, no restart needed. If you installed with pipx or use the Mac app, `config.ini` is kept in your user settings folder instead. See `config.example.ini` for the format.

## Updating

The banner checks for a new version once a day. To check right away, use **Right-click → Check for Updates…** or **Check for updates now** in Settings. When one is out, **⬆ Update available** appears on the banner.

- **One click:** right-click → **Install update now**. The banner downloads the new version, swaps it in and restarts itself in a few seconds. Your settings are kept.
- **Fully automatic:** turn on **Settings → Install updates automatically**, and new versions install by themselves with no click needed.

If the banner can't update itself, the menu shows **Download update…** instead and opens the Releases page. Download the new zip and unzip it over your old folder. This happens when the program is in a protected folder, or on a Mac when the app is still in Downloads (move it to Applications). If you installed with pipx, run `pipx upgrade hamradiobanner`.

> Versions before 1.2.0 can't install updates themselves. Download 1.2.0 by hand once, and later versions will update themselves.

## Credits

Weather from [Open-Meteo](https://open-meteo.com/). Automatic location from [ip-api.com](https://ip-api.com/).
Solar and band conditions from N0NBH ([hamqsl.com](https://www.hamqsl.com/solar.html)), and aurora and Kp data from [NOAA SWPC](https://www.swpc.noaa.gov/), both served through [KD2YFY.net/solar](https://kd2yfy.net/solar).

---

### For the maintainer: publishing a release

First change `__version__` near the top of `banner.py` to the new number (e.g. `"1.2.0"`) and commit it. The build stops with an error if that number doesn't match the version you release.

To publish a test build that nobody is offered automatically, use a version with a dash, such as `v1.4.0-beta.1`. It becomes a GitHub pre-release that only people who download it by hand will get.

The **Build apps** GitHub Action builds the Windows, macOS and Linux downloads and puts them on the Releases page. To publish a new version: go to the **Actions** tab, pick **Build apps**, click **Run workflow**, enter a version such as `v1.2.0`, and click the green button. Pushing a version tag does the same:

```bash
git tag v1.1.0
```

```bash
git push origin v1.1.0
```
