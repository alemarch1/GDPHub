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
#  STEP 1 - Verify Python & Setup Virtual Environment
# =========================================================================
echo "[1/5] Verifying Python installation..."
if command -v python3 &> /dev/null; then
    BASE_PYTHON="python3"
elif command -v python &> /dev/null; then
    BASE_PYTHON="python"
else
    echo ""
    echo "[ERROR] Python is not installed or not found in PATH."
    echo "        Please install Python 3.10+ from https://www.python.org/downloads/"
    echo ""
    read -rp "Press Enter to exit..."
    exit 1
fi

$BASE_PYTHON --version
echo "       Python found successfully."
echo ""

echo "       Setting up Virtual Environment (venv)..."

create_venv() {
    if ! $BASE_PYTHON -m venv venv; then
        echo ""
        echo "[WARNING] Virtual environment creation failed."
        echo "          Attempting to automatically install the 'venv' package via apt..."
        
        if command -v apt-get &> /dev/null; then
            PY_VER=$($BASE_PYTHON -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}-venv")')
            echo "          Running: sudo apt-get update && sudo apt-get install -y $PY_VER"
            sudo apt-get update > /dev/null 2>&1 || true
            sudo apt-get install -y "$PY_VER"
            
            echo "          Retrying virtual environment creation..."
            rm -rf venv
            if ! $BASE_PYTHON -m venv venv; then
                echo "[ERROR] Still failed to create virtual environment."
                rm -rf venv
                read -rp "Press Enter to exit..."
                exit 1
            fi
        else
            echo "[ERROR] Could not detect apt-get. You must install the venv package manually."
            rm -rf venv
            read -rp "Press Enter to exit..."
            exit 1
        fi
    fi
}

if [ ! -d "venv" ]; then
    create_venv
    echo "       Virtual environment created."
else
    echo "       Virtual environment already exists."
    # Basic sanity check to ensure the venv isn't broken (needs both python and pip)
    VENV_OK=false
    if [ -f "venv/bin/python" ] && [ -f "venv/bin/pip" ]; then
        VENV_OK=true
    elif [ -f "venv/Scripts/python.exe" ] && [ -f "venv/Scripts/pip.exe" ]; then
        VENV_OK=true
    fi

    if [ "$VENV_OK" = false ]; then
        echo "[WARNING] Existing virtual environment is broken or missing pip."
        echo "          Removing it to try again..."
        rm -rf venv
        create_venv
        echo "       Virtual environment re-created successfully."
    fi
fi

# Determine venv python/pip path based on OS
if [ -f "venv/bin/python" ]; then
    PYTHON_CMD="$(pwd)/venv/bin/python"
    PIP_CMD="$(pwd)/venv/bin/pip"
elif [ -f "venv/Scripts/python.exe" ]; then
    PYTHON_CMD="$(pwd)/venv/Scripts/python.exe"
    PIP_CMD="$(pwd)/venv/Scripts/pip.exe"
else
    echo "[ERROR] Virtual environment python/pip not found."
    exit 1
fi

# Ensure basic build tools are up to date in venv
echo "       Updating pip and core packages..."
$PIP_CMD install -U pip setuptools wheel || echo "       [WARNING] Failed to update pip. Continuing..."

# =========================================================================
#  STEP 2 - Install SpaCy with GPU Support & Project Dependencies
# =========================================================================
echo "[2/5] Checking CUDA version and installing dependencies..."

CUDA_VERSION=""
if command -v nvcc &> /dev/null; then
    CUDA_VERSION=$(nvcc --version 2>/dev/null | grep -o "release [0-9]*\.[0-9]*" | cut -d' ' -f2)
elif command -v nvidia-smi &> /dev/null; then
    CUDA_VERSION=$(nvidia-smi 2>/dev/null | grep -o "CUDA Version: [0-9]*\.[0-9]*" | cut -d' ' -f3)
fi

SPACY_PKG="spacy"
if [ -n "$CUDA_VERSION" ]; then
    echo "       Detected CUDA Version: $CUDA_VERSION"
    MAJOR=$(echo "$CUDA_VERSION" | cut -d'.' -f1)
    MINOR=$(echo "$CUDA_VERSION" | cut -d'.' -f2)
    
    if [ "$MAJOR" -ge 12 ]; then
        SPACY_PKG="spacy[cuda12x]"
    elif [ "$MAJOR" -eq 11 ]; then
        SPACY_PKG="spacy[cuda11x]"
    elif [ "$MAJOR" -eq 10 ]; then
        SPACY_PKG="spacy[cuda10${MINOR}]"
    else
        SPACY_PKG="spacy[cuda]"
    fi
    echo "       Will install optimized SpaCy for GPU: $SPACY_PKG"
else
    echo "       No CUDA detected or macOS. Installing standard CPU SpaCy."
fi

echo "       Installing SpaCy..."
$PIP_CMD install -U "$SPACY_PKG"

echo ""
echo "       Installing other Python dependencies from requirements.txt..."
DRY_RUN_OUTPUT=$($PIP_CMD install --dry-run -r requirements.txt 2>&1 || true)

if echo "$DRY_RUN_OUTPUT" | grep -qi "Would install"; then
    echo "       Some packages are missing or outdated. Installing now..."
    echo ""
    $PIP_CMD install -r requirements.txt
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
