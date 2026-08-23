@echo off
cd /d "%~dp0"
title MY Market Radar

echo.
echo ================================================================
echo   MY MARKET RADAR - Launching...
echo ================================================================
echo.

if exist "backend\venv\Scripts\python.exe" (
    echo [INFO] Using virtual environment Python
    backend\venv\Scripts\python.exe start.py
) else (
    echo [INFO] Using system Python
    python start.py
)

if errorlevel 1 (
    echo.
    echo [ERROR] Launch failed. Check logs for details.
    pause
)
