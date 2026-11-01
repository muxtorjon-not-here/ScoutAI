@echo off
setlocal
cd /d "%~dp0"
title ScoutAI

rem ---- 1. Find Python ----
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    echo.
    echo Python is not installed.
    echo Install it from https://www.python.org/downloads/ and TICK "Add python.exe to PATH"
    echo on the first installer screen, then double-click run.bat again.
    echo.
    pause
    exit /b 1
)

rem ---- 2. Create the virtual environment and install packages ----
if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment, this happens only once...
    %PY% -m venv venv
    if errorlevel 1 (
        echo Could not create the virtual environment.
        pause
        exit /b 1
    )
)
call "venv\Scripts\activate.bat"
echo Checking packages...
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo Package installation failed. Check your internet connection and try again.
    pause
    exit /b 1
)

rem ---- 3. First-time setup (skipped once .env and the Telegram login exist) ----
if not exist ".env" goto :setup
if not exist "user_session.session" goto :setup
goto :launch

:setup
python setup.py
if errorlevel 1 (
    pause
    exit /b 1
)

:launch
rem Open the dashboard in the default browser a few seconds after the app starts
start "" /min powershell -WindowStyle Hidden -NoProfile -Command "Start-Sleep 8; Start-Process 'http://localhost:8000'"

python main.py
echo.
echo ScoutAI stopped. If this was unexpected, read the last lines above or open scout.log.
pause
