@echo off
title NIFTY Trade Journal
cd /d "%~dp0"

echo.
echo ============================================
echo   NIFTY Trade Journal
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found!
    echo Install from: https://python.org/downloads
    echo Tick "Add Python to PATH" during install.
    pause
    exit
)

echo Checking dependencies...
pip install flask flask-cors -q

:: ── SET YOUR ANTHROPIC API KEY HERE ──
:: Get your free key from: https://console.anthropic.com
:: Paste it between the quotes below:
set ANTHROPIC_API_KEY=your-api-key-here

if "%ANTHROPIC_API_KEY%"=="your-api-key-here" (
    echo.
    echo NOTE: AI chart reading is disabled.
    echo To enable it, open start.bat and set your ANTHROPIC_API_KEY
    echo Get a free key at: https://console.anthropic.com
    echo.
)

echo  Starting server...
echo  Open browser at: http://localhost:5050
echo  Press Ctrl+C to stop
echo.

python server.py
pause
