@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" exit /b 1
if not exist "work\toy-review-security\proposal-layout.json" exit /b 2
".venv\Scripts\python.exe" scripts\hwp_review.py check-plan work\toy-review-security work\toy-review-security\proposal-layout.json
if errorlevel 1 exit /b 2
echo TOY ONLY - NOT FOR DELIVERY
echo Fresh human approval required: original row minima and generated preview policy.
echo http://127.0.0.1:8767/?proposal=layout^&panel=summary
start "" "http://127.0.0.1:8767/?proposal=layout&panel=summary"
echo If a new server is needed, keep this console open. Existing servers are NOT killed.
".venv\Scripts\python.exe" scripts\hwp_review.py serve work\toy-review-security --port 8767
pause
