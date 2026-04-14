@echo off
setlocal

:: ── osu!Taiko Replay Analyzer launcher ─────────────────────────────────────
:: Double-click this file to run the app.
:: On first launch it creates a local virtual environment and installs pygame.
:: Subsequent launches skip straight to running.
:: ────────────────────────────────────────────────────────────────────────────

set VENV=.venv
set PYTHON=%VENV%\Scripts\python.exe
set PIP=%VENV%\Scripts\pip.exe

:: Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Please install Python 3.11 or newer from https://www.python.org/downloads/
    echo Make sure to tick "Add Python to PATH" during installation.
    pause
    exit /b 1
)

:: Create venv on first run
if not exist "%PYTHON%" (
    echo First-time setup: creating virtual environment...
    python -m venv %VENV%
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Installing pygame...
    "%PIP%" install --quiet pygame
    if errorlevel 1 (
        echo ERROR: Failed to install pygame.
        pause
        exit /b 1
    )
    echo Setup complete.
    echo.
)

:: Run the app, passing any command-line arguments through
"%PYTHON%" main.py %*
