@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
    echo Run setup.cmd first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -B -m bunker.bot
if errorlevel 1 pause
