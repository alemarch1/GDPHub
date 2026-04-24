#!/usr/bin/env bash
# ============================================================================
#  GDPHub — One-Click Launcher (macOS / Linux)
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

# --- Verify Python is available ---
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo "[ERROR] Python is not installed or not found in PATH."
    echo "        Please run install.command first."
    read -rp "Press Enter to exit..."
    exit 1
fi

# --- Launch browser after a short delay ---
echo "Opening browser to http://localhost:8000 in 3 seconds..."
(
    sleep 3
    # macOS
    if command -v open &> /dev/null; then
        open "http://localhost:8000"
    # Linux / WSL
    elif command -v xdg-open &> /dev/null; then
        xdg-open "http://localhost:8000"
    else
        echo "[INFO] Could not detect a browser opener. Please navigate to http://localhost:8000 manually."
    fi
) &

# --- Start the FastAPI server (blocking) ---
echo "Starting FastAPI server..."
echo "Press Ctrl+C to stop the server."
echo ""
$PYTHON_CMD src/api.py
