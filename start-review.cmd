@echo off
setlocal
title HWP Page Review - http://127.0.0.1:8765
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Project Python environment is missing.
    pause
    exit /b 1
)
echo HWP Page Review: http://127.0.0.1:8765
echo Keep this window open while using the review page.
echo Press Ctrl+C or close this window to stop the server.
echo.
".venv\Scripts\python.exe" "scripts\hwp_review.py" serve "work\review" --port 8765 --open
set "REVIEW_EXIT=%ERRORLEVEL%"
echo.
echo Server stopped. Exit code: %REVIEW_EXIT%
pause
exit /b %REVIEW_EXIT%
