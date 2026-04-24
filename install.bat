@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM  GDPHub - One-Click Installer (Windows)
REM  Installs Python dependencies, NLP models, and checks optional tools.
REM  Skips components that are already installed.
REM ============================================================================

echo.
echo ============================================================
echo   GDPHub Privacy Hub - Environment Setup
echo ============================================================
echo.

REM =========================================================================
REM  STEP 1 - Verify Python
REM =========================================================================
echo [1/5] Verifying Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Python is not installed or not found in PATH.
    echo         Please install Python 3.10+ from https://www.python.org/downloads/
    echo         and ensure "Add Python to PATH" is checked during installation.
    echo.
    pause
    exit /b 1
)
python --version
echo       Python found successfully.
echo.

REM =========================================================================
REM  STEP 2 - Install Python dependencies (skip already-satisfied)
REM =========================================================================
echo [2/5] Checking Python dependencies from requirements.txt...

REM Count how many packages actually need installing
set NEEDS_INSTALL=0
for /f "delims=" %%i in ('pip install --dry-run -r requirements.txt 2^>^&1 ^| findstr /i "Would install"') do (
    set NEEDS_INSTALL=1
)

if !NEEDS_INSTALL!==1 (
    echo       Some packages are missing or outdated. Installing now...
    echo.
    pip install -r requirements.txt
    if !errorlevel! neq 0 (
        echo.
        echo [ERROR] Failed to install one or more Python packages.
        echo         Please check the output above for details.
        echo.
        pause
        exit /b 1
    )
    echo.
    echo       All Python dependencies installed successfully.
) else (
    echo       All Python dependencies are already installed. Skipping.
)
echo.

REM =========================================================================
REM  STEP 3 - Download spaCy NLP models (skip if already present)
REM =========================================================================
echo [3/5] Checking spaCy NLP language models...
echo.

REM --- Italian model ---
python -c "import spacy; spacy.load('it_core_news_lg')" >nul 2>&1
set SPACY_IT=!errorlevel!
if !SPACY_IT! neq 0 (
    echo       Downloading Italian model [it_core_news_lg]...
    python -m spacy download it_core_news_lg
    if !errorlevel! neq 0 (
        echo [WARNING] Italian spaCy model download may have failed.
    )
) else (
    echo       Italian model [it_core_news_lg] is already installed. Skipping.
)
echo.

REM --- English model ---
python -c "import spacy; spacy.load('en_core_web_lg')" >nul 2>&1
set SPACY_EN=!errorlevel!
if !SPACY_EN! neq 0 (
    echo       Downloading English model [en_core_web_lg]...
    python -m spacy download en_core_web_lg
    if !errorlevel! neq 0 (
        echo [WARNING] English spaCy model download may have failed.
    )
) else (
    echo       English model [en_core_web_lg] is already installed. Skipping.
)
echo.

REM =========================================================================
REM  STEP 4 - Check Ollama (required for AI classification)
REM =========================================================================
echo [4/5] Checking Ollama [Local LLM Server]...

where ollama >nul 2>&1
if !errorlevel! equ 0 goto :ollama_found

echo.
echo       Ollama is NOT installed.
echo       Ollama is required for AI-powered document classification
echo       and ROPA mapping [pipeline steps 2 and 4].
echo.

REM Check if winget is available for automated install
where winget >nul 2>&1
if !errorlevel! neq 0 goto :ollama_no_winget

set /p INSTALL_OLLAMA="       Would you like to install Ollama now via winget? [Y/N]: "
if /i "!INSTALL_OLLAMA!" neq "Y" goto :ollama_skip

echo.
echo       Installing Ollama via winget...
winget install --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
if !errorlevel! neq 0 (
    echo       [WARNING] Winget installation may have failed.
    echo       You can install manually from https://ollama.com/download
)
echo.
echo       Ollama installed. You may need to restart this script
echo       or open a new terminal for the 'ollama' command to be available.
goto :ollama_model

:ollama_no_winget
echo       Winget is not available on this system.
set /p INSTALL_OLLAMA="       Would you like to open the Ollama download page? [Y/N]: "
if /i "!INSTALL_OLLAMA!"=="Y" (
    start https://ollama.com/download
    echo       Browser opened. Install Ollama, then run this script again.
) else (
    echo       Skipped. You can install Ollama later from https://ollama.com/
)
goto :ollama_done

:ollama_skip
echo       Skipped. You can install Ollama later from https://ollama.com/
goto :ollama_done

:ollama_found
echo       Ollama found successfully.
for /f "tokens=*" %%v in ('where ollama 2^>nul') do echo       Location: %%v
for /f "tokens=*" %%v in ('ollama --version 2^>^&1') do echo       %%v

:ollama_model
REM Check if the recommended model is already pulled
ollama list 2>nul | findstr /i "qwen3.5:9b" >nul 2>&1
if !errorlevel! equ 0 (
    echo.
    echo       Recommended model qwen3.5:9b is already installed. Skipping.
    goto :ollama_done
)
echo.
echo       Recommended AI model: qwen3.5:9b
echo       This model offers an excellent balance of speed and accuracy
echo       for document classification and ROPA mapping tasks.
echo.
set /p PULL_MODEL="       Would you like to pull qwen3.5:9b now? [Y/N]: "
if /i "!PULL_MODEL!"=="Y" (
    echo.
    echo       Pulling qwen3.5:9b ... this may take several minutes.
    ollama pull qwen3.5:9b
) else (
    echo       Skipped. You can pull a model later with: ollama pull qwen3.5:9b
)

:ollama_done
echo.

REM =========================================================================
REM  STEP 5 - Check Tesseract OCR (optional)
REM =========================================================================
echo [5/5] Checking Tesseract OCR [optional]...

REM Try PATH first, then scan common Windows install locations
set TESS_CMD=
where tesseract >nul 2>&1
if !errorlevel! equ 0 set TESS_CMD=tesseract
if defined TESS_CMD goto :tess_found

if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" set "TESS_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe"
if defined TESS_CMD goto :tess_found

if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" set "TESS_CMD=C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
if defined TESS_CMD goto :tess_found

if exist "%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe" set "TESS_CMD=%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"
if defined TESS_CMD goto :tess_found

if exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" set "TESS_CMD=%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"
if defined TESS_CMD goto :tess_found

REM --- Tesseract not found ---
echo.
echo       Tesseract OCR was not found on this system.
echo           Tesseract is OPTIONAL - it is only needed if you plan to
echo           process scanned images or image-based PDF documents.
echo           The email pipeline and text-based document processing
echo           work perfectly without it.
echo.
set /p INSTALL_TESS="       Would you like to open the Tesseract download page? [Y/N]: "
if /i "!INSTALL_TESS!"=="Y" (
    start https://github.com/UB-Mannheim/tesseract/wiki
    echo.
    echo       Browser opened. After installing, update the tesseract_path
    echo       in the GDPHub Configuration page.
) else (
    echo       Skipped. You can install Tesseract later if needed.
)
goto :tess_done

:tess_found
echo       Tesseract OCR found successfully.
echo       Location: !TESS_CMD!
for /f "tokens=*" %%v in ('"!TESS_CMD!" --version 2^>^&1') do (
    echo       %%v
    goto :tess_done
)

:tess_done
echo.

REM =========================================================================
REM  DONE
REM =========================================================================
echo ============================================================
echo   Setup Complete!
echo.
echo   To start GDPHub, double-click 'start.bat'.
echo   The web interface will open at http://localhost:8000
echo ============================================================
echo.
pause
