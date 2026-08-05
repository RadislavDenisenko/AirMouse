@echo off
cd /d "%~dp0"
rem Replay the gesture walkthrough on demand, without waiting for a launch.
rem It owns the webcam while it runs, so close AirMouse first. Finishing or
rem skipping marks it done again, exactly as it does on a first run.
start "" venv\Scripts\pythonw.exe airmouse.py --tutorial %*
