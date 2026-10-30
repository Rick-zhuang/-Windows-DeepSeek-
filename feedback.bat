@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" give_feedback.py
) else (
  py -3 give_feedback.py
)
echo.
pause
