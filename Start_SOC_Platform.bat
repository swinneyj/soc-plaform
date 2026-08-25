@echo off
title Start SOC Platform
color 0A
cd /d "%~dp0"

powershell -ExecutionPolicy Bypass -File "%~dp0scripts\start_platform.ps1" -EnsureOllama -OpenBrowser -StartCommander
if errorlevel 1 (
    echo.
    echo [!] SOC Platform startup failed.
    pause
    exit /b 1
)