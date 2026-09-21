@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "scripts\pyqt_app.py"
) else (
    python "scripts\pyqt_app.py"
)

if errorlevel 1 (
    echo.
    echo A program hibaval allt le. Nyomj meg egy billentyut a bezarashoz.
    pause >nul
)
