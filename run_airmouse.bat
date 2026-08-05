@echo off
cd /d "%~dp0"
rem pythonw, not python: no console window. Anything that goes wrong is
rem reported by a dialog and written to airmouse.log next to config.json.
start "" venv\Scripts\pythonw.exe airmouse.py %*
