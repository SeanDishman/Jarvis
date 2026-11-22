@echo off
cd /d "%~dp0..\.."
if exist ".venv\Scripts\pythonw.exe" (
  start "Jarvis Brain" ".venv\Scripts\pythonw.exe" scripts\open-jarvis-brain.py
) else (
  start "Jarvis Brain" pyw -3.11 scripts\open-jarvis-brain.py
)
