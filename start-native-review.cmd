@echo off
setlocal
title TOY Native Contract Review - http://127.0.0.1:8767
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Project Python environment is missing.
    pause
    exit /b 1
)
if not exist "work\toy-review-security\proposal-native.json" (
    echo STOP: No separate native proposal exists. Nothing is prepared or applied automatically.
    pause
    exit /b 2
)
".venv\Scripts\python.exe" -c "import sys,pathlib; sys.path.insert(0,'scripts'); import hwp_review as r; w=pathlib.Path('work/toy-review-security'); m=r.verify_baseline(w); assert pathlib.Path(m['source']).resolve()==pathlib.Path('work/toy-source/TOY-ONLY-template.hwp').resolve(); r.validate_plan(r.read_json(w/'proposal-native.json'),m,w/'baseline.hwpx')"
if errorlevel 1 (
    echo STOP: Native proposal or synthetic baseline validation failed.
    pause
    exit /b 2
)
echo TOY ONLY - NOT FOR DELIVERY
echo Review: http://127.0.0.1:8767/?proposal=native
echo In the UI choose the native paragraph contract, review every orange region and policy.
echo This server NEVER applies edits. Approval is a separate manual UI action.
echo Existing servers on ports 8765/8766 are not stopped or changed.
echo Keep this console open. If port 8767 is already used, open the address above.
".venv\Scripts\python.exe" "scripts\hwp_review.py" serve "work\toy-review-security" --port 8767 --open
set "NATIVE_EXIT=%ERRORLEVEL%"
pause
exit /b %NATIVE_EXIT%
