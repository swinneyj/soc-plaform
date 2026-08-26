@echo off
title Restart SOC Platform
color 0A
cd /d "%~dp0"

powershell -ExecutionPolicy Bypass -File "%~dp0scripts\restart_platform.ps1" -EnsureOllama -OpenBrowser
if errorlevel 1 (
    echo.
    echo [!] SOC Platform restart failed.
    pause
    exit /b 1
)

echo.
echo [*] SOC Platform successfully restarted.
pause
