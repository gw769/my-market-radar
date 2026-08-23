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

REM 1. 先停止启动器窗口（start.py + 其子进程 uvicorn），防止后端退出后自动重启
echo [INFO] Stopping launcher (start.py)...
taskkill /F /T /FI "WINDOWTITLE eq MY Market Radar*" >nul 2>&1
timeout /t 1 >nul

REM 2. Kill port 8011 (Backend API)
echo [INFO] Checking port 8011 (Backend)...
for /f "tokens=5" %%a in ('netstat -ano -p tcp 2^>nul | findstr ":8011.*LISTENING"') do (
    echo   - Found process on port 8011: PID %%a
    taskkill /F /T /PID %%a >nul 2>&1
    if !errorlevel! equ 0 (
        echo   -^> Killed PID %%a successfully
        set /a stopped+=1
    ) else (
        echo   -^> Failed to kill PID %%a
    )
)

REM 3. Kill port 3000 (Frontend dev server - if running)
echo [INFO] Checking port 3000 (Frontend)...
for /f "tokens=5" %%a in ('netstat -ano -p tcp 2^>nul | findstr ":3000.*LISTENING"') do (
    echo   - Found process on port 3000: PID %%a
    taskkill /F /T /PID %%a >nul 2>&1
    if !errorlevel! equ 0 (
        echo   -^> Killed PID %%a successfully
        set /a stopped+=1
    )
)

echo.
if %stopped% gtr 0 (
    echo [INFO] Done. Stopped services: %stopped%
) else (
    echo [INFO] No running services found on ports 8011/3000.
)
echo [INFO] All services stopped.
echo.
echo    Press any key to close this window...
pause >nul
