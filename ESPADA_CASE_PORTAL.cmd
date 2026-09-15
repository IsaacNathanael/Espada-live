@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start_case_portal.ps1"
if errorlevel 1 (
  echo.
  echo ESPADA could not start. Read the error above, then press any key.
  pause >nul
)
