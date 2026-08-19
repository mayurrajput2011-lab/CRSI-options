@echo off
echo ============================================
echo  Installing NIFTY Trade Journal dependencies
echo ============================================
echo.

cd /d "%~dp0"

python -m pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo.
    echo First attempt failed, retrying with --break-system-packages ...
    python -m pip install -r requirements.txt --break-system-packages
)

echo.
echo ============================================
echo  Done. You can close this window and run:
echo    python server.py
echo ============================================
pause
