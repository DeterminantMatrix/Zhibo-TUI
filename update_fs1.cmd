@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python was not found in PATH and .venv\Scripts\python.exe does not exist.
        echo Install Python or create the virtual environment, then run this shortcut again.
        pause
        exit /b 1
    )
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" main.py fs1-update
echo.
echo Press any key to close this window...
pause >nul
