@echo off
cd /d "%~dp0"
call scripts\python.cmd scripts\desktop-control.py enable-startup
if errorlevel 1 (
  echo Startup could not be enabled. Run this file from your normal Windows desktop.
)
call scripts\python.cmd scripts\start-jarvis.py --desktop
if errorlevel 1 (
  pause
  exit /b 1
)
