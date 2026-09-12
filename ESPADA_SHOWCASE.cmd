@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run_showcase.ps1"
if errorlevel 1 pause
endlocal
