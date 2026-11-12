@echo off
rem Double-click this file to install (first time only) and start the Ham Radio Banner.
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" goto run

echo First-time setup: installing what the banner needs. This takes a minute...
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY (python --version >nul 2>nul && set PY=python)
if not defined PY (
    echo.
    echo Python was not found. Please install it from https://www.python.org/downloads/
    echo During install, tick "Add python.exe to PATH". Then double-click this file again.
    start "" https://www.python.org/downloads/
    pause
    exit /b 1
)
%PY% -m venv .venv || goto fail
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto fail

:run
start "" ".venv\Scripts\pythonw.exe" banner.py
exit /b 0

:fail
echo.
echo Setup failed - see the messages above. Delete the .venv folder and try again.
pause
exit /b 1
