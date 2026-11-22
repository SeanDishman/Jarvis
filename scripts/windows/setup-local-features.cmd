@echo off
cd /d "%~dp0..\.."
call scripts\python.cmd scripts\setup-local-features.py --install
pause
