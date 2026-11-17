@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
python -m venv --without-pip .venv
if errorlevel 1 goto error
python -m pip --python ".venv\Scripts\python.exe" install -r requirements.txt
if errorlevel 1 goto error
if not exist ".env" copy ".env.example" ".env" >nul
echo Ready. Fill DISCORD_TOKEN and DISCORD_GUILD_ID in .env, then run start.cmd.
pause
exit /b 0
:error
echo Setup failed. See the error above. Python 3.11 or newer and pip are required.
pause
exit /b 1
