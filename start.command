#!/usr/bin/env bash
# ============================================================================
#  GDPHub — One-Click Launcher (macOS / Linux) - 
#  Starts the FastAPI server and opens the WebUI in the default browser.
# ============================================================================

# Resolve the directory where this script lives (handles symlinks, Finder launch)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "============================================================"
echo "  GDPHub Privacy Hub — Starting Server..."
echo "============================================================"
echo ""

# --- Locate Virtual Environment or Global Python ---
if [ -f "venv/bin/python" ]; then
    PYTHON_CMD="$(pwd)/venv/bin/python"
elif [ -f "venv/Scripts/python.exe" ]; then
    PYTHON_CMD="$(pwd)/venv/Scripts/python.exe"
else
    echo "[WARNING] Virtual environment not found."
    echo "          Please run install.command first to set up the environment."
    echo "          Attempting to fall back to system Python..."
    if command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
    elif command -v python &> /dev/null; then
        PYTHON_CMD="python"
    else
        echo "[ERROR] Python is not installed or not found in PATH."
        read -rp "Press Enter to exit..."
        exit 1
    fi
fi

# --- Launch browser after a short delay ---
echo "Opening browser to http://localhost:8000 in 3 seconds..."
(
    sleep 3
    # macOS
    if command -v open &> /dev/null; then
        if [ -n "$SUDO_USER" ]; then
            sudo -u "$SUDO_USER" open "http://localhost:8000" > /dev/null 2>&1
        else
            open "http://localhost:8000" > /dev/null 2>&1
        fi
    # Linux / WSL
    elif command -v xdg-open &> /dev/null; then
        if [ -n "$SUDO_USER" ]; then
            # Run xdg-open as the original user so it can access the X11/Wayland session
            sudo -u "$SUDO_USER" xdg-open "http://localhost:8000" > /dev/null 2>&1
        else
            xdg-open "http://localhost:8000" > /dev/null 2>&1
        fi
    else
        echo "[INFO] Could not detect a browser opener. Please navigate to http://localhost:8000 manually."
    fi
) &

# --- Start the FastAPI server (blocking) ---
echo "Starting FastAPI server..."
echo "Press Ctrl+C to stop the server."
echo ""
$PYTHON_CMD src/api.py
