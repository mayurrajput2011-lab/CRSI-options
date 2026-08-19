@echo off
title NIFTY Trade Journal + Local Coder
cd /d "%~dp0"

echo.
echo ============================================
echo   NIFTY Trade Journal  +  Local Coder
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

echo Checking dependencies (this can take a minute the first time)...
echo   - flask, flask-cors  ^(web server^)
echo   - pandas, numpy      ^(screener calculations^)
echo   - yfinance           ^(live market data for screener + paper trades^)
echo   - requests           ^(talks to your local Ollama models^)
echo.
python -m pip install --upgrade pip -q
python -m pip install flask flask-cors pandas numpy yfinance requests -q
if errorlevel 1 (
    echo.
    echo WARNING: Some packages may have failed to install.
    echo The Screener and Paper Trades pages need pandas, numpy, and yfinance
    echo to work - if you see errors there, re-run this file, or manually run:
    echo   python -m pip install flask flask-cors pandas numpy yfinance requests
    echo.
    pause
)

:: ── SET YOUR ANTHROPIC API KEY HERE (only for the journal's chart-reading) ──
:: The Local Coder does NOT need this - it runs fully offline via Ollama.
:: Get a key from: https://console.anthropic.com  (paste between the quotes)
set ANTHROPIC_API_KEY=your-api-key-here

if "%ANTHROPIC_API_KEY%"=="your-api-key-here" (
    echo.
    echo NOTE: Cloud AI chart reading is disabled.
    echo To enable it, open start.bat and set your ANTHROPIC_API_KEY
    echo Get a key at: https://console.anthropic.com
    echo.
)

:: ============================================================
::   LOCAL CODER  (Ollama)  -- optional, 100%% offline
:: ============================================================
echo.
echo ============================================
echo   Local Coder (Ollama)
echo ============================================
set CODER=0

where ollama >nul 2>&1
if errorlevel 1 goto no_ollama

echo Ollama detected. Checking for a local coding model...
ollama list 2>nul | findstr /i "coder codestral deepseek" >nul 2>&1
if errorlevel 1 goto no_model
echo Local coding model found - good to go.
set CODER=1
goto coder_done

:no_model
echo No local coding model found yet.
echo Recommended: qwen2.5-coder:7b  (about 5 GB, best all-round local coder)
set /p PULL="Download it now? [y/N]: "
if /i "%PULL%"=="y" goto pull_model
echo Skipped. You can pull one anytime with:
echo   ollama pull qwen2.5-coder:7b
echo (The Coding page will still open - just pick a model once you have one.)
set CODER=1
goto coder_done

:pull_model
echo Pulling qwen2.5-coder:7b ... this can take several minutes. Please wait.
ollama pull qwen2.5-coder:7b
set CODER=1
goto coder_done

:no_ollama
echo Ollama not found - the Coding page will be disabled.
echo Install it from https://ollama.com/download then re-run this file.

:coder_done

:: Start the Local Coder in its own window (it must run alongside the journal)
if "%CODER%"=="1" (
    if exist coding_server.py (
        echo.
        echo  Starting Local Coder on http://localhost:5070 ...
        start "Local Coder" /D "%~dp0" cmd /k python coding_server.py
    ) else (
        echo.
        echo  NOTE: coding_server.py not found next to this file - Coding page skipped.
        echo  Put coding_server.py and coding.html in this folder to enable it.
        set CODER=0
    )
)

echo.
echo  Starting journal server...
echo  Journal      : http://localhost:5050
if "%CODER%"=="1" echo  Local Coder  : http://localhost:5070  (its own window)
echo  Press Ctrl+C here to stop the journal. Close the Local Coder window to stop it.
echo.

:: Give the servers a moment to boot, then auto-open the browser tab(s)
if "%CODER%"=="1" (
    start "" /min cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:5050 & start http://localhost:5070"
) else (
    start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:5050"
)

python server.py

echo.
echo Journal server stopped.
pause
