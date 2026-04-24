@echo off
REM ============================================================================
REM  GDPHub - One-Click Launcher (Windows)
REM  Starts the FastAPI server and opens the WebUI in the default browser.
REM ============================================================================

echo.
echo ============================================================
echo   GDPHub Privacy Hub - Starting Server...
echo ============================================================
echo.

REM --- Verify Python is available ---
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not found in PATH.
    echo         Please run install.bat first.
    pause
    exit /b 1
)

REM --- Launch the default browser after a short delay ---
echo Opening browser to http://localhost:8000 in 3 seconds...
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8000"

REM --- Start the FastAPI server (blocking) ---
echo Starting FastAPI server...
echo Press Ctrl+C to stop the server.
echo.
python src/api.py
