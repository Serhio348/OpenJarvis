@echo off
title OpenJarvis
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-dev.ps1"
if errorlevel 1 (
  echo.
  echo OpenJarvis failed to start. See messages above.
  pause
)
