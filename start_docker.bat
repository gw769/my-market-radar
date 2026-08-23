@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ================================================================
echo   MY MARKET RADAR - Docker Launcher
echo ================================================================
echo.

if not exist ".env" (
    echo [ERROR] .env file not found!
    echo.
    echo   Step 1: open a cmd window here and run:
    echo          copy .env.docker.simple .env
    echo   Step 2: edit .env and set SECRET_KEY and BOOTSTRAP_ADMIN_PASSWORD
    echo.
    pause
    exit /b 1
)
echo [OK] .env found.

set "bootstrap_password="
set "bootstrap_email=admin@market.my"
set "secret_key="
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="BOOTSTRAP_ADMIN_PASSWORD" set "bootstrap_password=%%B"
    if /I "%%A"=="BOOTSTRAP_ADMIN_EMAIL" set "bootstrap_email=%%B"
    if /I "%%A"=="SECRET_KEY" set "secret_key=%%B"
)
if not defined bootstrap_password (
    echo [ERROR] BOOTSTRAP_ADMIN_PASSWORD is empty in .env.
    echo   Set a private password for the first administrator account.
    pause
    exit /b 1
)
if not defined secret_key (
    echo [ERROR] SECRET_KEY is empty in .env.
    pause
    exit /b 1
)
if /I "!secret_key!"=="please-change-me-to-a-random-32-char-string" (
    echo [ERROR] SECRET_KEY still uses the template value.
    echo   Replace it with a random private string before starting Docker.
    pause
    exit /b 1
)

echo [OK] Bootstrap credentials and secret are configured.

where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] docker command not found.
    echo   Install Docker Desktop first:
    echo   https://www.docker.com/products/docker-desktop/
    pause
    exit /b 1
)
echo [OK] docker command found.

docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker engine is NOT running.
    echo   Open Docker Desktop and wait until it shows "Engine running".
    pause
    exit /b 1
)
echo [OK] Docker engine is running.

echo [INFO] Building and starting container...
echo.
docker compose -f docker-compose.simple.yml up -d --build
if errorlevel 1 (
    echo.
    echo [ERROR] Container failed to start. Scroll up to see the error.
    pause
    exit /b 1
)

echo [INFO] Waiting for backend to start...
set /a tries=0
:waitloop
timeout /t 5 /nobreak >nul
set /a tries+=1
powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://localhost:8011/health' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    if !tries! geq 60 (
        echo [WARN] Backend not ready after 5 min. Please inspect: docker logs my-market-radar
        goto opendone
    )
    echo [INFO] Backend starting, retrying (!tries!)...
    goto waitloop
)
echo [OK] Backend is ready.

:opendone
powershell -NoProfile -Command "$paths=@((Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),(Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),(Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')); $chrome=$paths | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1; if($chrome){ Start-Process -FilePath $chrome -ArgumentList 'http://localhost:8011'; exit 0 }; exit 1" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Google Chrome not found. Falling back to the Windows default browser.
    start "" http://localhost:8011
) else (
    echo [OK] Opened MY Market Radar in Google Chrome.
)

echo.
echo ================================================================
echo   SERVICE IS RUNNING
 echo   Open:  http://localhost:8011
 echo   Login: !bootstrap_email! / ^(password from .env^)
 echo   Stop:  double-click stop_docker.bat
 echo   Network: localhost only by default
 echo ================================================================
echo.
pause
