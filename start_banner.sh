#!/usr/bin/env bash
# Installs (first time only) and starts the Ham Radio Banner on macOS / Linux.
# Run with:  ./start_banner.sh      (or double-click "Start Banner (Mac).command" on a Mac)
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    if ! command -v python3 >/dev/null 2>&1; then
        echo "Python 3 was not found. Install it from https://www.python.org/downloads/ and run this again."
        exit 1
    fi
    echo "First-time setup: installing what the banner needs. This takes a minute..."
    python3 -m venv .venv || {
        echo "Could not create a virtual environment. On Debian/Ubuntu run: sudo apt install python3-venv"
        rm -rf .venv; exit 1; }
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -r requirements.txt
fi

nohup .venv/bin/python banner.py >/dev/null 2>&1 &
echo "Ham Radio Banner started. Right-click it for Settings or Quit."
