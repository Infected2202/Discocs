@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV_DIR=.venv"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo [run] Creating virtual environment in %VENV_DIR%...
    py -3.11 -m venv "%VENV_DIR%" 2>nul
    if errorlevel 1 py -3 -m venv "%VENV_DIR%" 2>nul
    if errorlevel 1 python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [run] ERROR: failed to create venv. Install Python 3.11+.
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"

"%PYTHON%" -c "import telegram, httpx, pydantic_settings, aiosqlite" >nul 2>&1
if errorlevel 1 (
    echo [run] Installing dependencies...
    "%PYTHON%" -m pip install --upgrade pip
    "%PYTHON%" -m pip install -r requirements.txt
    "%PYTHON%" -m pip install -e .
    if errorlevel 1 (
        echo [run] ERROR: dependency install failed.
        exit /b 1
    )
)

echo [run] Checking for other bot instances...
call "%~dp0stop.bat" >nul 2>&1
timeout /t 2 /nobreak >nul

echo [run] Starting Discocs Bot...
"%PYTHON%" -m bot.main
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
