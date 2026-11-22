@echo off
cd /d "%~dp0"
set NODE_PATH=%~dp0..\app\node_modules
echo ============================================
echo    Seat Assistant - starting...
echo ============================================
echo.
node server.js
echo.
echo    Service stopped. Press any key to close this window.
pause >nul
