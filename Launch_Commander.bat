@echo off
title SOC Commander Orchestrator
color 0A
:: Force the working directory to the location of this batch file
cd /d "%~dp0"

powershell -ExecutionPolicy Bypass -File "%~dp0scripts\start_platform.ps1" -EnsureOllama -OpenBrowser
if errorlevel 1 (
    echo.
    echo [!] SOC Platform startup failed.
    pause
    exit /b 1
)
