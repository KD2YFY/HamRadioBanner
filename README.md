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

All settings can be changed from **Right-click → Settings…**:

- **Callsign:** your amateur radio callsign.
- **Local timezone:** start typing to search, e.g. `New_York` or `London`.
- **Weather location:** leave blank to detect it automatically. You can also enter a town and state such as `Warrensburg NY` or `Albany, NY`, a ZIP code such as `12885`, a place such as `London, United Kingdom`, or coordinates such as `42.65, -73.75`. Include the state, since many towns share a name.
- **Temperature:** °F or °C.
- **Weather refresh:** how often to update, in minutes.
- **High/low:** show or hide today's forecast high and low.
- **24-hour:** show the highest and lowest temperatures over the 24 hours ending at a set time. The default is 6:30 AM, like a daily weather observation, and you can change it. It's shown as `24h→6:30a H:52° L:29°` and rolls over to the new day at that time. The readings come from Open-Meteo's 15-minute model data for your location, not from a weather station.
- **Text sizes:** separate sizes for the **callsign**, the **clocks** (UTC, local time and date), the **weather** and the **ticker**, from 50% to 250%. The banner resizes to fit.
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
