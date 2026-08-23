@echo off
cd /d "%~dp0"
title MY Market Radar

echo.
echo ================================================================
echo   MY MARKET RADAR - Launching...
echo ================================================================
echo.

if exist "backend\.venv\Scripts\python.exe" (
    echo [INFO] Using backend\.venv Python
    backend\.venv\Scripts\python.exe start.py
) else if exist "backend\venv\Scripts\python.exe" (
    echo [INFO] Using backend\venv Python
    backend\venv\Scripts\python.exe start.py
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python was not found and no backend virtual environment exists.
        echo         Create one with: python -m venv backend\.venv
        pause
        exit /b 1
    )
    echo [INFO] No backend virtual environment found; using system Python to launch start.py
    python start.py
)

if errorlevel 1 (
    echo.
    echo [ERROR] Launch failed. Check logs for details.
    pause
)
