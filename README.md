# 🛡️ GDPHub

An automated Python pipeline that extracts text from documents and emails, anonymises PII offline using [Microsoft Presidio](https://microsoft.github.io/presidio/), classifies content with a local LLM via [Ollama](https://ollama.com/), and maps everything to your GDPR Record of Processing Activities (ROPA).

## Features

- **Multi-source ingestion** — local files, Gmail (OAuth 2), Microsoft 365 / Outlook (Graph API).
- **Text extraction** — PDF (with OCR fallback via Tesseract), DOCX, DOC, ODT, RTF, HTML, XML, JSON, XLS, CSV, TXT.
- **Privacy-first anonymisation** — PII detection and masking (names, emails, phones, IBANs, fiscal codes, license plates…) runs entirely offline via spaCy NER + Presidio.
- **LLM classification & ROPA matching** — Ollama models classify documents and cross-match them against your ROPA register, all locally.
- **Lifecycle management** — tracks retention periods and scheduled deletions (cloud, filesystem, DB).
- **Web UI + API** — FastAPI backend serving a Vue 3 single-page application at `http://localhost:8000`.

---

## Prerequisites

| Dependency | Notes |
|---|---|
| **Python 3.10+** | |
| **[Ollama](https://ollama.com/)** | Install and pull at least one model: `ollama pull gemma3:4b` |
| **Tesseract OCR** *(optional, for image PDFs)* | Windows: download the `.exe` installer and note its path. Linux: `sudo apt install tesseract-ocr`. Mac: `brew install tesseract`. |
| **spaCy language models** | Installed automatically with the commands below. |

### Optional (only if using email sources)

| Dependency | Notes |
|---|---|
| **Gmail** | Create a Google Cloud project, enable the Gmail API, create an OAuth Desktop Client ID, download the JSON and save it as `src/credentials.json`. |
| **Outlook / Microsoft 365** | Register an Azure AD app, note the Client ID, and configure it in the web UI settings. |

---

## How to Start

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Download spaCy language models

```bash
python -m spacy download it_core_news_lg
python -m spacy download en_core_web_lg
```

### 3. Create the bootstrap config

Copy the example and edit it if you want to change the default folder paths:

```bash
cp config.json.example src/config.json
```

The defaults (`./data/input`, `./data/output`, `./logs`) work out of the box.

### 4. Prepare your ROPA register

A sample file is provided: [`Dummy_Ropa.csv`](Dummy_Ropa.csv).  
Edit it to match your organisation's processing activities, or prepare your own `.csv` / `.xlsx` with these columns:

| Processing Activity | Lawful Bases | Data Subject Categories | Personal Data Categories | Recipients Categories | International Transfers | Retention Periods |
|---|---|---|---|---|---|---|

*Retention Periods* can be a plain number (interpreted as days) or a string like `+365 days`.

### 5. Start the web application

```bash
python src/api.py
```

Open **http://localhost:8000** in your browser.

### 6. Use the Pipeline (via Web UI)

1. Go to **⚙️ Configuration** and set paths and Ollama model. Click **Save**.  
2. Go to **🗺️ ROPA Mapping**, upload your ROPA CSV/Excel file.  
3. Go to **🚀 Pipeline Control** and run the steps in order:
   - **Step 1** — If using Gmail/Outlook, fetch emails. If using local files, point the input folder to your documents.
   - **Step 2** — Extract text & anonymise PII.
   - **Step 3** — Classify documents with AI.
   - **Step 4** — Match documents to ROPA activities.
4. View results in **📊 Dashboard** and manage retention in **🕰️ Lifecycle Manager**.

### Alternative: CLI mode

```bash
cd src
python 0_extract_mail.py          # optional: fetch emails
python 1_extract_text.py          # extract & anonymise
python 2_classify_text.py --model gemma3:4b --no-think --run-all
python 3_extract_ROPA.py          # import your ROPA register
python 4_identify_ROPA.py --model gemma3:4b --no-think
python 5_document_deletion.py     # run the Janitor (delete expired docs)
```

---

## Project Structure

```
src/
  0_extract_mail.py      # Email ingestion (Gmail / Outlook)
  1_extract_text.py      # Text extraction + PII anonymisation
  2_classify_text.py     # LLM document classification
  3_extract_ROPA.py      # ROPA register import
  4_identify_ROPA.py     # LLM ROPA matching
  5_document_deletion.py # Automated deletion (Janitor)
  api.py                 # FastAPI backend
  models.py              # SQLModel / DB schema
  database.py            # SQLite engine
  config_manager.py      # Config read/write (DB-backed)
  seed_config.py         # Migrate config.json → DB
web/
  index.html, app.js, style.css   # Vue 3 SPA frontend
  locales/en.json, it.json        # UI translations
```

---

## License

[Apache License 2.0](LICENSE)
