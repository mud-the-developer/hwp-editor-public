@echo off
setlocal
cd /d "%~dp0"
call "%~dp0setup-source.cmd"
if errorlevel 1 (
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -X utf8 "scripts\hwp_desktop.py"
exit /b %ERRORLEVEL%
