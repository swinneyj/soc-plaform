@echo off
title SOC Commander Orchestrator
color 0A
:: Force the working directory to the location of this batch file
cd /d "%~dp0"

echo ============================================
echo   Starting SOC Platform (Docker)
echo ============================================
echo.

docker compose up -d
if errorlevel 1 (
    echo.
    echo [!] Docker Compose failed to start. Is Docker Desktop running?
    pause
    exit /b 1
)

echo.
echo Waiting for API service to become healthy...
:waitloop
timeout /t 2 >nul
docker inspect --format="{{.State.Health.Status}}" soc-api-service 2>nul | findstr "healthy" >nul
if errorlevel 1 goto waitloop

echo.
echo [+] SOC Platform is up. Opening http://localhost:8000 ...
start http://localhost:8000

echo.
echo Starting SOC Commander...
python commander.py
pause
