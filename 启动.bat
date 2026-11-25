@echo off
cd /d "%~dp0"
set NODE_PATH=%~dp0..\app\node_modules
echo ============================================
echo    Seat Assistant - starting...
echo ============================================
echo.
if exist "%~dp0stop-old.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-old.ps1" >nul 2>&1
)
node server.js
echo.
echo    Service stopped. Press any key to close this window.
pause >nul
