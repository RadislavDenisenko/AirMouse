@echo off
cd /d "%~dp0"
set PY=..\venv\Scripts\python.exe
set FAILED=0
for %%T in (test_gestures.py test_smoothing.py test_pinch.py test_scroll_and_rightclick.py test_navigation_and_launcher.py test_config.py test_attention.py test_launcher_binding.py test_settings_ui.py test_settings_store.py test_magnet.py test_brake.py test_lens.py test_tutorial.py) do (
    echo ===== %%T =====
    %PY% %%T || set FAILED=1
    echo.
)
if %FAILED%==1 (echo SOME TESTS FAILED & exit /b 1) else (echo ALL TEST FILES PASSED)
pause
