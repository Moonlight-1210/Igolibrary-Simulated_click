@echo off
set "PY_CMD="
python -c "import sys" >nul 2>&1 && set "PY_CMD=python"
if not defined PY_CMD (
    py -3 -c "import sys" >nul 2>&1 && set "PY_CMD=py -3"
)
if not defined PY_CMD (
    echo [ERROR] Python 3 not found. Please install Python 3.10+ from https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [INFO] Installing Pillow and numpy using %PY_CMD% ...
%PY_CMD% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [INFO] Retrying with Tsinghua PyPI mirror...
    %PY_CMD% -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r "%~dp0requirements.txt"
)
echo.
pause
