@echo off
cd /d "%~dp0"
call scripts\python.cmd scripts\start-jarvis.py --resume %*
exit /b %errorlevel%
