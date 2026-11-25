@echo off
set "PY_CMD="
python -c "import sys" >nul 2>&1 && set "PY_CMD=python"
if not defined PY_CMD (
    py -3 -c "import sys" >nul 2>&1 && set "PY_CMD=py -3"
)
if not defined PY_CMD (
    echo [ERROR] Python 3 not found. Please install Python 3.10+ and run: pip install -r "%~dp0requirements.txt"
    pause
    exit /b 1
)
%PY_CMD% "%~dp0test_bg_click.py"
echo.
pause
