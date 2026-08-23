@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Stop MY Market Radar

echo.
echo ================================================================
echo   Stopping all MY Market Radar services...
echo ================================================================
echo.

set "stopped=0"

REM 1. Stop launcher window (start.py + uvicorn child process)
echo [INFO] Stopping launcher (start.py)...
taskkill /F /T /FI "WINDOWTITLE eq MY Market Radar*" >nul 2>&1
timeout /t 1 >nul

REM 2. Kill backend API on project port 8011
echo [INFO] Checking port 8011 (Backend)...
for /f "tokens=5" %%a in ('netstat -ano -p tcp 2^>nul ^| findstr ":8011.*LISTENING"') do (
    echo   - Found process on port 8011: PID %%a
    taskkill /F /T /PID %%a >nul 2>&1
    if !errorlevel! equ 0 (
        echo   -^> Killed PID %%a successfully
        set /a stopped+=1
    ) else (
        echo   -^> Failed to kill PID %%a
    )
)

REM 3. Stop only the dedicated Chrome/Chromium exposing the project's isolated CDP port.
REM Port 9231 is reserved for MY Market Radar, so this does not kill the user's normal browser.
echo [INFO] Checking port 9231 (Project Chrome CDP)...
for /f "tokens=5" %%a in ('netstat -ano -p tcp 2^>nul ^| findstr ":9231.*LISTENING"') do (
    echo   - Found project browser on port 9231: PID %%a
    taskkill /F /T /PID %%a >nul 2>&1
    if !errorlevel! equ 0 (
        echo   -^> Killed project browser PID %%a successfully
        set /a stopped+=1
    ) else (
        echo   -^> Failed to kill project browser PID %%a
    )
)

REM 4. Kill frontend dev server only if the developer started one separately.
echo [INFO] Checking port 3000 (Frontend dev server)...
for /f "tokens=5" %%a in ('netstat -ano -p tcp 2^>nul ^| findstr ":3000.*LISTENING"') do (
    echo   - Found process on port 3000: PID %%a
    taskkill /F /T /PID %%a >nul 2>&1
    if !errorlevel! equ 0 (
        echo   -^> Killed PID %%a successfully
        set /a stopped+=1
    )
)

echo.
if %stopped% gtr 0 (
    echo [INFO] Done. Stopped project processes: %stopped%
) else (
    echo [INFO] No running project services found on ports 8011/9231/3000.
)
echo [INFO] All MY Market Radar services stopped.
echo.
echo    Press any key to close this window...
pause >nul
