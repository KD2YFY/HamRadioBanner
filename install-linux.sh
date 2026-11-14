#!/usr/bin/env bash
# Ham Radio Banner - one-step Linux installer.
#
#   curl -fsSL https://raw.githubusercontent.com/KD2YFY/HamRadioBanner/main/install-linux.sh | bash
#
# Downloads the latest release into ~/.local/share/HamRadioBanner, installs the one system
# library Qt needs that some distributions leave out, and starts the banner. On first run the
# banner asks for your callsign and adds a desktop shortcut and an applications-menu entry.
# Run it again any time to reinstall; your settings (config.ini) are kept.
set -euo pipefail

APP_DIR="$HOME/.local/share/HamRadioBanner"
ZIP_URL="${HRB_ZIP_URL:-https://github.com/KD2YFY/HamRadioBanner/releases/latest/download/HamRadioBanner-Linux.zip}"

say() { printf '\033[1;33m%s\033[0m\n' "$*"; }

if [ "$(uname -m)" != "x86_64" ]; then
    say "The ready-made Linux app is built for 64-bit PCs (x86_64); this machine is $(uname -m)."
    say "On a Raspberry Pi or other ARM computer, use the Python version instead:"
    echo "  pipx install git+https://github.com/KD2YFY/HamRadioBanner.git && hamradiobanner"
    exit 1
fi

# Qt needs libxcb-cursor, which Ubuntu/Debian and Fedora don't always install.
need_pkg=""
if command -v dpkg >/dev/null 2>&1; then
    dpkg -s libxcb-cursor0 >/dev/null 2>&1 || need_pkg="apt-get install -y libxcb-cursor0"
elif command -v rpm >/dev/null 2>&1; then
    rpm -q xcb-util-cursor >/dev/null 2>&1 || need_pkg="dnf install -y xcb-util-cursor"
elif command -v pacman >/dev/null 2>&1; then
    pacman -Q xcb-util-cursor >/dev/null 2>&1 || need_pkg="pacman -S --noconfirm xcb-util-cursor"
fi
if [ -n "$need_pkg" ]; then
    say "Installing a small library the banner needs (you may be asked for your password)..."
    sudo sh -c "$need_pkg" || say "Could not install it automatically. If the banner won't start, run: sudo $need_pkg"
fi

say "Downloading Ham Radio Banner..."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
if [ -f "$ZIP_URL" ]; then cp "$ZIP_URL" "$tmp/app.zip"; else curl -fL --progress-bar "$ZIP_URL" -o "$tmp/app.zip"; fi

say "Installing to $APP_DIR ..."
mkdir -p "$APP_DIR"
if pkill -f "$APP_DIR/HamRadioBanner" 2>/dev/null; then sleep 2; fi  # stop a running copy first
if command -v unzip >/dev/null 2>&1; then
    unzip -qo "$tmp/app.zip" -d "$tmp/new"
else
    python3 -m zipfile -e "$tmp/app.zip" "$tmp/new"
fi
# Copy the program in, keeping config.ini (your settings) if it is already there.
cp -Rf "$tmp/new/HamRadioBanner/." "$APP_DIR/"
cp -f "$tmp/new/README.md" "$tmp/new/config.example.ini" "$APP_DIR/" 2>/dev/null || true
chmod +x "$APP_DIR/HamRadioBanner"

say "Starting Ham Radio Banner..."
nohup "$APP_DIR/HamRadioBanner" >/dev/null 2>&1 &
say "Done! Look for the banner at the top of your screen and its antenna icon in the tray."
say "It adds a desktop shortcut and a menu entry; right-click the banner for Settings."
