#!/usr/bin/env bash
# ============================================================================
#  GDPHub - One-Click Installer (macOS / Linux)
#  Installs Python dependencies, NLP models, and checks optional tools.
#  Skips components that are already installed.
# ============================================================================

set -e

# Resolve the directory where this script lives (handles symlinks, Finder launch)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "============================================================"
echo "  GDPHub Privacy Hub - Environment Setup"
echo "============================================================"
echo ""

# =========================================================================
#  STEP 1 - Verify Python
# =========================================================================
echo "[1/5] Verifying Python installation..."
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo ""
    echo "[ERROR] Python is not installed or not found in PATH."
    echo "        Please install Python 3.10+ from https://www.python.org/downloads/"
    echo ""
    read -rp "Press Enter to exit..."
    exit 1
fi

$PYTHON_CMD --version
echo "       Python found successfully."
echo ""

# Verify pip is available
if ! $PYTHON_CMD -m pip --version &> /dev/null; then
    echo "[ERROR] pip is not installed."
    echo "        Install it with: $PYTHON_CMD -m ensurepip --upgrade"
    echo ""
    read -rp "Press Enter to exit..."
    exit 1
fi

# =========================================================================
#  STEP 2 - Install Python dependencies (skip already-satisfied)
# =========================================================================
echo "[2/5] Checking Python dependencies from requirements.txt..."

DRY_RUN_OUTPUT=$($PYTHON_CMD -m pip install --dry-run -r requirements.txt 2>&1 || true)

if echo "$DRY_RUN_OUTPUT" | grep -qi "Would install"; then
    echo "       Some packages are missing or outdated. Installing now..."
    echo ""
    $PYTHON_CMD -m pip install -r requirements.txt
    echo ""
    echo "       All Python dependencies installed successfully."
else
    echo "       All Python dependencies are already installed. Skipping."
fi
echo ""

# =========================================================================
#  STEP 3 - Download spaCy NLP models (skip if already present)
# =========================================================================
echo "[3/5] Checking spaCy NLP language models..."
echo ""

# --- Italian model ---
if $PYTHON_CMD -c "import spacy; spacy.load('it_core_news_lg')" &> /dev/null; then
    echo "       Italian model (it_core_news_lg) is already installed. Skipping."
else
    echo "       Downloading Italian model (it_core_news_lg)..."
    $PYTHON_CMD -m spacy download it_core_news_lg || echo "[WARNING] Italian spaCy model download may have failed."
fi
echo ""

# --- English model ---
if $PYTHON_CMD -c "import spacy; spacy.load('en_core_web_lg')" &> /dev/null; then
    echo "       English model (en_core_web_lg) is already installed. Skipping."
else
    echo "       Downloading English model (en_core_web_lg)..."
    $PYTHON_CMD -m spacy download en_core_web_lg || echo "[WARNING] English spaCy model download may have failed."
fi
echo ""

# =========================================================================
#  STEP 4 - Check Ollama (required for AI classification)
# =========================================================================
echo "[4/5] Checking Ollama (Local LLM Server)..."

OLLAMA_INSTALLED=false
if command -v ollama &> /dev/null; then
    OLLAMA_INSTALLED=true
    echo "       Ollama found successfully."
    echo "       Location: $(command -v ollama)"
    ollama --version 2>/dev/null || true
else
    echo ""
    echo "       [!] Ollama is NOT installed."
    echo "           Ollama is required for AI-powered document classification"
    echo "           and ROPA mapping (pipeline steps 2 and 4)."
    echo ""
    read -rp "       Would you like to install Ollama now? (y/N): " INSTALL_OLLAMA
    case "$INSTALL_OLLAMA" in
        [Yy]*)
            echo ""
            echo "       Installing Ollama via official installer..."
            curl -fsSL https://ollama.com/install.sh | sh
            if command -v ollama &> /dev/null; then
                OLLAMA_INSTALLED=true
                echo "       Ollama installed successfully."
            else
                echo "       [WARNING] Installation may have failed."
                echo "       You can install manually from https://ollama.com/download"
            fi
            ;;
        *)
            echo "       Skipped. You can install Ollama later from https://ollama.com/"
            ;;
    esac
fi

# --- Offer to pull recommended model ---
if [ "$OLLAMA_INSTALLED" = true ]; then
    if ollama list 2>/dev/null | grep -qi "qwen3.5:9b"; then
        echo ""
        echo "       Recommended model qwen3.5:9b is already installed. Skipping."
    else
        echo ""
        echo "       Recommended AI model: qwen3.5:9b"
        echo "       This model offers an excellent balance of speed and accuracy"
        echo "       for document classification and ROPA mapping tasks."
        echo ""
        read -rp "       Would you like to pull qwen3.5:9b now? (y/N): " PULL_MODEL
        case "$PULL_MODEL" in
            [Yy]*)
                echo ""
                echo "       Pulling qwen3.5:9b ... this may take several minutes."
                ollama pull qwen3.5:9b
                ;;
            *)
                echo "       Skipped. You can pull a model later with: ollama pull qwen3.5:9b"
                ;;
        esac
    fi
fi
echo ""

# =========================================================================
#  STEP 5 - Check Tesseract OCR (optional)
# =========================================================================
echo "[5/5] Checking Tesseract OCR (optional)..."

if command -v tesseract &> /dev/null; then
    echo "       Tesseract OCR found successfully."
    echo "       Location: $(command -v tesseract)"
    tesseract --version 2>&1 | head -n 1
else
    echo ""
    echo "       [!] Tesseract OCR is NOT installed."
    echo "           Tesseract is OPTIONAL - it is only needed if you plan to"
    echo "           process scanned images or image-based PDF documents."
    echo "           The email pipeline and text-based document processing"
    echo "           work perfectly without it."
    echo ""
    read -rp "       Would you like to install Tesseract now? (y/N): " INSTALL_TESS
    case "$INSTALL_TESS" in
        [Yy]*)
            if command -v brew &> /dev/null; then
                echo "       Installing via Homebrew..."
                brew install tesseract
            elif command -v apt-get &> /dev/null; then
                echo "       Installing via apt..."
                sudo apt-get install -y tesseract-ocr
            else
                echo "       Could not detect a package manager."
                echo "       Please install Tesseract manually:"
                echo "         macOS:  brew install tesseract"
                echo "         Debian: sudo apt install tesseract-ocr"
            fi
            ;;
        *)
            echo "       Skipped. You can install Tesseract later if needed."
            ;;
    esac
fi
echo ""

# =========================================================================
#  DONE
# =========================================================================
echo "============================================================"
echo "  Setup Complete!"
echo ""
echo "  To start GDPHub, double-click 'start.command'."
echo "  The web interface will open at http://localhost:8000"
echo "============================================================"
echo ""
read -rp "Press Enter to exit..."
