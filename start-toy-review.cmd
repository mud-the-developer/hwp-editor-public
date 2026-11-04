@echo off
setlocal
title TOY HWP Review - http://127.0.0.1:8766
cd /d "%~dp0"
set "TOY_SOURCE=work\toy-source\TOY-ONLY-template.hwp"
set "TOY_WORKSPACE=work\toy-review-security"
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Project Python environment is missing.
    pause
    exit /b 1
)
if not exist "%TOY_SOURCE%" (
    echo ERROR: The synthetic toy source does not exist.
    pause
    exit /b 1
)
echo TOY ONLY - NOT FOR DELIVERY
echo Toy review: http://127.0.0.1:8766
echo The real review on port 8765 is not changed.
echo Keep this console open while using the toy editor.
echo The explicitly authorized Automation security module is loaded on each HWP instance.
echo If the approved DLL is missing, the local pinned ZIP installs it in user AppData only.
echo Unrecognized DLLs, altered ZIPs or module registration failures STOP preparation.
echo Document edit approval remains a separate manual UI action.
echo.
if not exist "%TOY_WORKSPACE%\manifest.json" (
    if exist "%TOY_WORKSPACE%" (
        echo STOP: The previous preparation is incomplete. No automatic retry or overwrite.
        echo Inspect this workspace and resolve the native error before starting again.
        pause
        exit /b 2
    )
    echo Preparing a NEW toy baseline. The editor starts only after strict roundtrip checks pass.
    ".venv\Scripts\python.exe" "scripts\hwp_review.py" prepare "%TOY_SOURCE%" "%TOY_WORKSPACE%"
    if errorlevel 1 (
        echo STOP: Toy baseline preparation failed. Editing remains blocked.
        pause
        exit /b 2
    )
)
".venv\Scripts\python.exe" -c "import json,pathlib; p=pathlib.Path(r'%TOY_WORKSPACE%')/'manifest.json'; m=json.loads(p.read_text(encoding='utf-8')); assert pathlib.Path(m['source']).resolve()==pathlib.Path(r'%TOY_SOURCE%').resolve(), 'This workspace is not the toy source'"
if errorlevel 1 (
    echo STOP: Workspace source does not match the toy document.
    pause
    exit /b 2
)
".venv\Scripts\python.exe" "scripts\hwp_review.py" serve "%TOY_WORKSPACE%" --port 8766 --open
set "TOY_EXIT=%ERRORLEVEL%"
echo.
echo Toy server stopped. Exit code: %TOY_EXIT%
pause
exit /b %TOY_EXIT%
