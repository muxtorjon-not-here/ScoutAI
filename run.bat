@echo off
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3 -m venv venv
    if errorlevel 1 python -m venv venv
)

call "venv\Scripts\activate.bat"
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

rem Open the dashboard in the default browser a few seconds after the app starts
start "" /min powershell -WindowStyle Hidden -NoProfile -Command "Start-Sleep 8; Start-Process 'http://localhost:8000'"

python main.py
pause
