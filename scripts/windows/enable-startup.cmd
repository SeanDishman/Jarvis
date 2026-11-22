@echo off
cd /d "%~dp0..\.."
call scripts\python.cmd scripts\desktop-control.py enable-startup
if errorlevel 1 pause
