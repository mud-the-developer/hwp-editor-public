@echo off
setlocal
title HWP Editor - Legacy Review
cd /d "%~dp0"
call "%~dp0setup-source.cmd"
if errorlevel 1 (
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -B -X utf8 "scripts\hwp_desktop.py" --legacy-desktop
set "REVIEW_EXIT=%ERRORLEVEL%"
if not "%REVIEW_EXIT%"=="0" pause
exit /b %REVIEW_EXIT%
