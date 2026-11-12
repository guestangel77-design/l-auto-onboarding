@echo off
cd /d %~dp0

python --version >nul 2>&1
if errorlevel 1 (
  echo [X] Python not found. Install Python 3.10+ and check "Add python.exe to PATH".
  pause
  exit /b
)

if not exist .env (
  copy .env.example .env >nul
  echo [!] .env file created. You can paste API keys into it later.
  echo     Without keys, Sample 1 and 2 still work in demo mode.
)

echo [1/2] Installing packages... first time takes 1-2 minutes.
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [X] Package install failed. Company network may be blocking pip.
  pause
  exit /b
)

echo [2/2] Starting... the browser will open automatically.
echo       To stop, press Ctrl + C in this window.
python -m streamlit run app.py
pause
