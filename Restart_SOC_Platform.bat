@echo off
title Restart SOC Platform
color 0E
cd /d "%~dp0"

echo ============================================
echo   Restarting SOC Platform
echo ============================================
echo.

echo [*] Stopping existing SOC Platform containers...
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\stop_platform.ps1"
set "STOP_EXITCODE=%ERRORLEVEL%"

if not "%STOP_EXITCODE%"=="0" (
	echo [!] Warning: stop_platform.ps1 exited with code %STOP_EXITCODE%.
	echo     Containers may not have been stopped cleanly. Continuing anyway...
	echo.
)

echo [*] Starting SOC Platform...
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\start_platform.ps1" -EnsureOllama -OpenBrowser
if errorlevel 1 (
	echo.
	echo [!] SOC Platform restart failed.
	pause
	exit /b 1
)

echo.
echo [+] SOC Platform restart complete.
pause

