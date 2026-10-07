@echo off
rem Double-click or run from any terminal. Runs start-app.ps1 even when PowerShell script execution is restricted.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-app.ps1" %*
