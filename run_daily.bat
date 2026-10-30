@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" daily_paper_agent.py >> "%~dp0\scheduler.log" 2>&1
) else (
  py -3 daily_paper_agent.py >> "%~dp0\scheduler.log" 2>&1
)
