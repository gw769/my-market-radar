@echo off
setlocal
cd /d "%~dp0"
title Stop MY Market Radar

echo.
echo ================================================================
echo   Stopping MY Market Radar local services...
echo ================================================================
echo.

REM 1. Stop only the exact start_local.bat launcher title.
REM Do not use a wildcard here: a normal Chrome window can be titled "MY Market Radar - Google Chrome".
echo [INFO] Stopping launcher window if it is running...
taskkill /F /T /FI "WINDOWTITLE eq MY Market Radar" >nul 2>&1
timeout /t 1 /nobreak >nul

REM 2. Only stop port 8011 when the owning command line can be identified as this app.
REM Docker Desktop or another unrelated service can also own a TCP listener; never kill it by port alone.
echo [INFO] Checking port 8011 (local backend)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$connections=@(Get-NetTCPConnection -State Listen -LocalPort 8011 -ErrorAction SilentlyContinue);" ^
  "if(-not $connections){ Write-Host '  - No listener on 8011.'; exit 0 };" ^
  "foreach($c in $connections){" ^
  "  $ownerPid=[int]$c.OwningProcess;" ^
  "  $proc=Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $ownerPid) -ErrorAction SilentlyContinue;" ^
  "  $cmd=[string]$proc.CommandLine;" ^
  "  if($cmd -match '(?i)(uvicorn.+app\.main:app|start\.py|my-market-radar)'){" ^
  "    Write-Host ('  - Stopping confirmed project backend PID ' + $ownerPid);" ^
  "    & taskkill /F /T /PID $ownerPid *> $null;" ^
  "  } else {" ^
  "    Write-Host ('  - WARNING: PID ' + $ownerPid + ' owns 8011 but is not identifiable as MY Market Radar. Leaving it untouched.');" ^
  "    if($cmd){ Write-Host ('    Command: ' + $cmd) };" ^
  "    Write-Host '    If this is the Docker deployment, use stop_docker.bat.';" ^
  "  }" ^
  "}"

REM 3. Port 9231 is reserved by this project for the dedicated Chrome/Chromium CDP instance.
echo [INFO] Checking port 9231 (project Chrome CDP)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$connections=@(Get-NetTCPConnection -State Listen -LocalPort 9231 -ErrorAction SilentlyContinue);" ^
  "if(-not $connections){ Write-Host '  - No project Chrome listener on 9231.'; exit 0 };" ^
  "foreach($c in $connections){ $ownerPid=[int]$c.OwningProcess; Write-Host ('  - Stopping project Chrome PID ' + $ownerPid); & taskkill /F /T /PID $ownerPid *> $null }"

REM 4. Port 3000 is commonly used by unrelated frontend projects. Never kill it automatically.
echo [INFO] Checking port 3000 (optional frontend dev server)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$connections=@(Get-NetTCPConnection -State Listen -LocalPort 3000 -ErrorAction SilentlyContinue);" ^
  "if($connections){ foreach($c in $connections){ Write-Host ('  - Port 3000 is in use by PID ' + $c.OwningProcess + '; leaving it untouched.') } } else { Write-Host '  - No listener on 3000.' }"

echo.
echo [INFO] Local stop finished.
echo [INFO] Docker deployments are stopped separately with stop_docker.bat.
echo.
echo Press any key to close this window...
pause >nul
