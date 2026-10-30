@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] Checking Python 3.11+...
py -3 --version
if errorlevel 1 (
  echo Python 3 was not found. Install Python 3.11+ from https://www.python.org/downloads/windows/
  echo During installation, tick "Add Python to PATH".
  pause
  exit /b 1
)
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
  echo Python 3.11 or newer is required. Install it from https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
echo [2/3] Creating virtual environment...
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 (
    echo Could not create the virtual environment.
    pause
    exit /b 1
  )
)
echo [3/3] Installing packages...
"%~dp0.venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
  echo pip upgrade failed. Check network and try again.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Package installation failed. Check network and try again.
  pause
  exit /b 1
)
echo.
echo Setup complete.
echo Next: configure DEEPSEEK_API_KEY, then run daily_paper_agent.py --self-test.
echo After tests pass, run run_now.bat.
pause
