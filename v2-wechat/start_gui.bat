@echo off
python -c "import sys" >nul 2>&1 && (
    where pythonw >nul 2>&1 && (
        start "" pythonw "%~dp0wechat_grab_gui.py"
        exit /b 0
    )
    start "" python "%~dp0wechat_grab_gui.py"
    exit /b 0
)
py -3 -c "import sys" >nul 2>&1 && (
    where pyw >nul 2>&1 && (
        start "" pyw -3 "%~dp0wechat_grab_gui.py"
        exit /b 0
    )
    start "" py -3 "%~dp0wechat_grab_gui.py"
    exit /b 0
)
echo [ERROR] Python 3 not found. Please install Python 3.10+ and run: pip install -r "%~dp0requirements.txt"
pause
exit /b 1
