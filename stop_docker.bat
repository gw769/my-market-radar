@echo off
setlocal
cd /d "%~dp0"

echo ================================================================
echo   MY MARKET RADAR - Docker Stop
echo ================================================================
echo.

docker compose -f docker-compose.simple.yml down
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to stop. Check if Docker Desktop is running.
    pause
    exit /b 1
)

echo.
echo [OK] Service stopped. Data is kept in the Docker volume.
echo   To start again, double-click start_docker.bat
echo.
pause
