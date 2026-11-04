@echo off
setlocal
"%~dp0HWPReview.exe" --setup-requirements
exit /b %errorlevel%
