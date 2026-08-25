@echo off
title SOC Platform - Git Sync
color 0A

:: Force the working directory to the location of this batch file
cd /d "%~dp0"

:: Launch the interactive PowerShell Git menu
powershell.exe -ExecutionPolicy Bypass -File ".\git-menu.ps1"

:: Keep the window open if it crashes so we can read the error!
pause