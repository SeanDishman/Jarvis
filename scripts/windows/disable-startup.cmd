@echo off
cd /d "%~dp0..\.."
call scripts\python.cmd scripts\desktop-control.py disable-startup
if errorlevel 1 pause
