@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Project Python environment is missing.
    echo Follow README setup first. No dependency will be installed automatically.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -X utf8 "scripts\hwp_desktop.py"
exit /b %ERRORLEVEL%
