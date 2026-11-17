@echo off
setlocal
cd /d "%~dp0"
call "%~dp0setup-source.cmd"
if errorlevel 1 (
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -I -B -X utf8 "scripts\start_source.py"
if errorlevel 1 (
    pause
    exit /b 1
)
exit /b %ERRORLEVEL%
