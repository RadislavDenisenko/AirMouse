@echo off
cd /d "%~dp0"
venv\Scripts\python.exe airmouse.py %*
if errorlevel 1 pause
