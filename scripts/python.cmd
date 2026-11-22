@echo off
if exist "%~dp0..\.venv\Scripts\python.exe" (
  "%~dp0..\.venv\Scripts\python.exe" %*
) else (
  py -3.11 %*
)
exit /b %errorlevel%
