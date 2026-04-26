# FastAPI backend for the GDPHub web interface.
# Provides REST endpoints for configuration management, pipeline execution,
# document lifecycle operations, ROPA management, and the Janitor service.
# Serves the static frontend from the /web directory.

import os
import sys
import json
import asyncio
import re
from typing import Optional
from pathlib import Path
from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import ollama
import psutil

from database import get_session, create_db_and_tables
from models import Document, DocumentClassification, RopaRecord, DocumentRopaMapping
from sqlmodel import select, delete
from config_manager import get_config, set_config, Configuration
from deletion_service import execute_deletion_workflow

# --- APPLICATION INITIALIZATION ---
ACTIVE_PROCESS_PID = None
app = FastAPI(title="GDPHub Platform")

create_db_and_tables()

def _seed_defaults():
    """Seeds the Configuration and RopaRecord tables with sensible defaults
    when the database is freshly created (i.e. tables are empty).
    This ensures the application works out-of-the-box after a fresh clone."""
    with get_session() as session:
        # --- Seed Configuration defaults ---
        existing_configs = session.exec(select(Configuration)).first()
        if existing_configs is None:
            defaults = {
                "active_source": "local",
                "input_folder": "./data/input",
                "database_folder": "./data/output",
                "log_folder": "./logs",
                "log_level": "INFO",
                "0_extract_mail.py": {
                    "query": "",
                    "max_emails": 50,
                    "import_override_days": 0,
                    "delete_after_processing": False,
                    "import_override_ignore_processed": False
                },
                "extract_text.py": {
                    "tesseract_path": "",
                    "max_workers": 4
                },
                "classify_text.py": {
                    "ollama_url": "http://localhost:11434",
                    "ollama_model_default": "gemma3:4b",
                    "title_max_length": 500,
                    "text_max_length": 1500,
                    "timeout_seconds": 60,
                    "api_request_timeout": 45,
                    "ollama_options": {
                        "num_predict": 64,
                        "temperature": 0.2,
                        "num_ctx": 4096,
                        "top_p": 0.9,
                        "top_k": 40
                    }
                },
                "extract_ROPA.py": {
                    "ropa_folder": "./data/ROPA"
                }
            }
            for key, value in defaults.items():
                session.add(Configuration(key=key, value=json.dumps(value)))
            session.commit()
            print("[GDPHub] Default configuration seeded into database.")

        # --- Seed example ROPA records ---
        existing_ropa = session.exec(select(RopaRecord)).first()
        if existing_ropa is None:
            example_ropa = [
                RopaRecord(
                    id="0001",
                    activity="Employee Payroll Processing",
                    lawful_bases="Art. 6(1)(b) Contract, Art. 6(1)(c) Legal Obligation",
                    subject_categories="Employees, Contractors",
                    personal_data_categories="Name, Address, Tax ID, Bank Account, Salary",
                    recipients_categories="Payroll Provider, Tax Authorities",
                    international_transfers="None",
                    retention_periods="+3650 days"
                ),
                RopaRecord(
                    id="0002",
                    activity="Customer Relationship Management",
                    lawful_bases="Art. 6(1)(b) Contract, Art. 6(1)(f) Legitimate Interest",
                    subject_categories="Customers, Prospects",
                    personal_data_categories="Name, Email, Phone, Purchase History",
                    recipients_categories="CRM Platform, Marketing Team",
                    international_transfers="None",
                    retention_periods="+1825 days"
                ),
                RopaRecord(
                    id="0003",
                    activity="IT Security & Access Logging",
                    lawful_bases="Art. 6(1)(f) Legitimate Interest",
                    subject_categories="Employees, System Users",
                    personal_data_categories="Username, IP Address, Access Timestamps",
                    recipients_categories="IT Security Team",
                    international_transfers="None",
                    retention_periods="+365 days"
                ),
            ]
            session.add_all(example_ropa)
            session.commit()
            print("[GDPHub] Example ROPA records seeded into database.")

_seed_defaults()

# Enable CORS for local cross-origin connections during UI dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONFIG_FILE = SCRIPT_DIR / 'config.json'
WEBUI_DIR = PROJECT_ROOT / 'web'

# Auto-bootstrap directories for GitHub usability
for dir_path in ["data/input", "data/output", "data/ROPA", "logs", "src/auth"]:
    (PROJECT_ROOT / dir_path).mkdir(parents=True, exist_ok=True)

# --- CONFIGURATION ENDPOINTS ---
@app.get("/api/config")
async def get_app_config():
    """Retrieves all configuration from the database."""
    try:
        with get_session() as session:
            configs = session.exec(select(Configuration)).all()
            result = {c.key: json.loads(c.value) for c in configs}
            
            # Inject outlook config from outlook.json
            outlook_file = SCRIPT_DIR / "auth" / "outlook.json"
            if outlook_file.exists():
                try:
                    with open(outlook_file, "r") as f:
                        result["0_extract_mail_outlook"] = json.load(f)
                except Exception:
                    pass
                    
            # Inject gmail auth config from gmail.json
            gmail_file = SCRIPT_DIR / "auth" / "gmail.json"
            if gmail_file.exists():
                try:
                    with open(gmail_file, "r") as f:
                        gmail_data = json.load(f)
                        installed = gmail_data.get("installed", {})
                        result["0_extract_mail_gmail_auth"] = {
                            "client_id": installed.get("client_id", ""),
                            "client_secret": installed.get("client_secret", "")
                        }
                except Exception:
                    pass
            return result
    except Exception as e:
        # Fallback to config.json if DB is not available
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

@app.post("/api/config")
async def save_app_config(request: Request):
    """Saves configuration to the database and syncs bootstrap paths to config.json."""
    data = await request.json()
    
    # Update Database
    for key, value in data.items():
        if key == "0_extract_mail_outlook":
            outlook_file = SCRIPT_DIR / "auth" / "outlook.json"
            outlook_file.parent.mkdir(parents=True, exist_ok=True)
            outlook_data = {}
            if outlook_file.exists():
                try:
                    with open(outlook_file, "r") as f:
                        outlook_data = json.load(f)
                except Exception:
                    pass
            outlook_data.update(value)
            with open(outlook_file, "w") as f:
                json.dump(outlook_data, f, indent=4)
        elif key == "0_extract_mail_gmail_auth":
            gmail_file = SCRIPT_DIR / "auth" / "gmail.json"
            gmail_file.parent.mkdir(parents=True, exist_ok=True)
            gmail_data = {}
            if gmail_file.exists():
                try:
                    with open(gmail_file, "r") as f:
                        gmail_data = json.load(f)
                except Exception:
                    pass
            
            # Preserve existing token data if it exists
            if "installed" not in gmail_data:
                gmail_data["installed"] = {}
                
            gmail_data["installed"]["client_id"] = value.get("client_id", "")
            gmail_data["installed"]["client_secret"] = value.get("client_secret", "")
            gmail_data["installed"]["project_id"] = "gdphub"
            gmail_data["installed"]["auth_uri"] = "https://accounts.google.com/o/oauth2/auth"
            gmail_data["installed"]["token_uri"] = "https://oauth2.googleapis.com/token"
            gmail_data["installed"]["auth_provider_x509_cert_url"] = "https://www.googleapis.com/oauth2/v1/certs"
            gmail_data["installed"]["redirect_uris"] = ["http://localhost"]
            
            with open(gmail_file, "w") as f:
                json.dump(gmail_data, f, indent=4)
        else:
            set_config(key, value)
        
    # Sync bootstrap paths back to config.json to maintain path resolution
    bootstrap_keys = ["database_folder", "log_folder", "input_folder"]
    bootstrap_data = {}
    
    # Load existing config.json to preserve other manual bootstrap entries
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                bootstrap_data = json.load(f)
        except Exception:
            pass
            
    updated_bootstrap = False
    for bk in bootstrap_keys:
        if bk in data:
            bootstrap_data[bk] = data[bk]
            updated_bootstrap = True
            
    if updated_bootstrap:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(bootstrap_data, f, indent=2, ensure_ascii=False)
            
    return {"status": "success"}

# --- OLLAMA MODEL ENDPOINTS ---
@app.get("/api/ollama/models")
async def get_ollama_models():
    """Retrieves the list of available Ollama models from the configured server."""
    try:
        classify_cfg = get_config("classify_text.py", {})
        url = classify_cfg.get("ollama_url", "http://localhost:11434")
        default = classify_cfg.get("ollama_model_default", "mistral:latest")
        
        client = ollama.Client(host=url)
        res = client.list()
        models = [m.get("model") or m.get("name") for m in res.get("models", [])]
        models = [m for m in models if m]
        
        if default not in models:
            models.append(default)
        return {"models": models, "default": default, "url": url}
    except Exception as e:
        return {"models": [], "error": "An internal error occurred retrieving models"}

# --- UTILITY ENDPOINTS ---
def _ask_folder_logic(initial_dir):
    """Opens a native tkinter folder selection dialog (runs in a thread)."""
    import tkinter as tk
    from tkinter import filedialog
    try:
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes('-topmost', 1)
        selected_path = filedialog.askdirectory(initialdir=initial_dir, title="Select Directory")
        root.destroy()
        return selected_path
    except Exception as e:
        print(f"Tkinter dialog error: {e}")
        return None

@app.get("/api/utils/browse-folder")
async def browse_local_folder(current_path: Optional[str] = None):
    """Opens a native OS folder dialog on the server and returns the selected path."""
    print(f"[GDPHub API] Request: browse-folder (current: {current_path})")
    
    initial_dir = None
    if current_path and isinstance(current_path, str):
        # Basic validation: avoid directory traversal patterns
        # We skip os.path.exists() to avoid CodeQL filesystem sinks; tkinter handles invalid paths natively.
        if ".." not in current_path:
            initial_dir = current_path
    # Run tkinter dialog in a separate thread to avoid freezing the FastAPI event loop
    try:
        selected_path = await asyncio.to_thread(_ask_folder_logic, initial_dir)
        print(f"[GDPHub API] Selection: {selected_path}")
        return {"path": selected_path if selected_path else None}
    except Exception as e:
        print(f"[GDPHub API] Error opening folder dialog: {e}")
        raise HTTPException(status_code=500, detail="An internal server error occurred")

# --- PIPELINE EXECUTION ENGINE ---
async def run_script_generator(script_name: str, args: list):
    """Async generator that spawns a pipeline script and streams its stdout as SSE events."""
    global ACTIVE_PROCESS_PID
    
    # Validate script_name as a strict plain filename and enforce SCRIPT_DIR containment
    script_leaf = Path(script_name).name if script_name else ""
    if (
        not script_name
        or script_leaf != script_name
        or not script_name.endswith(".py")
        or not all(ch.isalnum() or ch in ("_", "-", ".") for ch in script_name)
    ):
        yield "data: [Error] Access denied: Invalid script name\n\n"
        yield "data: [END]\n\n"
        return
        
    base_dir = SCRIPT_DIR.resolve()
    script_path = (base_dir / script_name).resolve()
    try:
        script_path.relative_to(base_dir)
    except ValueError:
        yield "data: [Error] Access denied: Invalid script path\n\n"
        yield "data: [END]\n\n"
        return
    if not script_path.exists():
        yield f"data: [Error] Cannot find script {script_path}\n\n"
        yield "data: [END]\n\n"
        return
    
    cmd = [sys.executable, str(script_path)] + args
    yield f"data: Running command: {' '.join(cmd)}\n\n"
    
    process: Optional[asyncio.subprocess.Process] = None
    try:
        # We enforce unbuffered stdout so Python doesn't delay streaming until the buffer is full
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(SCRIPT_DIR),
            env=env
        )
        
        ACTIVE_PROCESS_PID = process.pid
        
        assert process.stdout is not None

        import re
        buf = b""
        while True:
            chunk = await process.stdout.read(64)
            if not chunk:
                if buf:
                    text = buf.decode('utf-8', errors='replace').strip()
                    if text:
                        yield f"data: {text}\n\n"
                break
            
            buf += chunk
            while b'\n' in buf or b'\r' in buf:
                r_idx = buf.find(b'\r')
                n_idx = buf.find(b'\n')
                
                if r_idx != -1 and n_idx != -1:
                    idx = min(r_idx, n_idx)
                else:
                    idx = r_idx if r_idx != -1 else n_idx
                
                line = buf[:idx]
                buf = buf[idx+1:]
                
                text = line.decode('utf-8', errors='replace').strip()
                if not text:
                    continue
                
                # Look for tqdm pattern like `  10%|` or `100%|`
                match = re.search(r'(\d+)%\|', text)
                if match:
                    percent = match.group(1)
                    yield f"data: __PROGRESS__:{percent}::{text}\n\n"
                else:
                    yield f"data: {text}\n\n"
        
        await process.wait()
        if process.returncode == 0:
            yield f"data: \n\ndata: [SUCCESS] {script_name} completed successfully!\n\n"
        else:
            yield f"data: \n\ndata: [ERROR] {script_name} exited with status {process.returncode}\n\n"
    except asyncio.CancelledError:
        if process is not None and process.returncode is None:
            try:
                process.terminate()
                # Await termination to ensure pipes are closed
                await process.wait()
            except Exception:
                pass
        raise
    except Exception as e:
        print(f"[GDPHub API] Exception running script: {e}")
        yield "data: Exception running script: An internal error occurred while executing the pipeline.\n\n"
    finally:
        ACTIVE_PROCESS_PID = None
        yield "data: [END]\n\n"

@app.post("/api/control/{action}")
async def process_control(action: str):
    """Pauses, resumes, or stops the currently running pipeline subprocess."""
    if action not in ["pause", "resume", "stop"]:
        raise HTTPException(status_code=400, detail="Invalid action")
    
    global ACTIVE_PROCESS_PID
    if ACTIVE_PROCESS_PID:
        try:
            p = psutil.Process(ACTIVE_PROCESS_PID)
            if action == "pause":
                p.suspend()
                return {"status": "paused"}
            elif action == "resume":
                p.resume()
                return {"status": "resumed"}
            elif action == "stop":
                p.kill()
                return {"status": "stopped"}
        except Exception as e:
            raise HTTPException(status_code=500, detail="An internal server error occurred")
    return {"status": "no process"}

@app.post("/api/run/{script_name}")
async def run_pipeline_post(script_name: str, request: Request):
    """Launches a pipeline script by name and returns a streaming SSE response."""
    data = await request.json()
    args = data.get("args", [])
    return StreamingResponse(run_script_generator(script_name, args), media_type="text/event-stream")

# --- ROPA MANAGEMENT ENDPOINTS ---
@app.post("/api/upload_ropa")
async def upload_ropa_file(file: UploadFile = File(...)):
    """Uploads a ROPA file (CSV/Excel) to the ROPA folder."""
    try:
        ropa_dir = PROJECT_ROOT / "data" / "ROPA"
        ropa_dir.mkdir(parents=True, exist_ok=True)
        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided.")
        file_path = ropa_dir / file.filename
        with open(file_path, "wb") as f:
            f.write(await file.read())
        return {"status": "success", "file_path": str(file_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail="An internal server error occurred")

@app.get("/api/ropa")
async def get_ropa():
    """Returns all ROPA records from the database."""
    try:
        with get_session() as session:
            records = session.exec(select(RopaRecord)).all()
            data = []
            for r in records:
                data.append({
                    "id": r.id,
                    "Processing Activity": r.activity,
                    "Lawful Bases": r.lawful_bases,
                    "Data Subject Categories": r.subject_categories,
                    "Personal Data Categories": r.personal_data_categories,
                    "Recipients Categories": r.recipients_categories,
                    "International Transfers": r.international_transfers,
                    "Retention Periods": r.retention_periods
                })
            return {"data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail="An internal server error occurred")

@app.post("/api/ropa")
async def save_ropa(request: Request):
    """Replaces all ROPA records in the database with the provided data."""
    try:
        data = await request.json()
        ropa_list = data.get("data", [])
        with get_session() as session:
            session.exec(delete(RopaRecord))
            
            records = []
            for row in ropa_list:
                records.append(RopaRecord(
                    id=row.get('id', ''),
                    activity=row.get('Processing Activity', ''),
                    lawful_bases=row.get('Lawful Bases', '') or row.get('Processing Purpose', ''),
                    subject_categories=row.get('Data Subject Categories', ''),
                    personal_data_categories=row.get('Personal Data Categories', ''),
                    recipients_categories=row.get('Recipients Categories', ''),
                    international_transfers=row.get('International Transfers', ''),
                    retention_periods=row.get('Retention Periods', '')
                ))
            session.add_all(records)
            session.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="An internal server error occurred")

# --- DOCUMENT AND IDENTIFICATION ENDPOINTS ---
@app.get("/api/documents")
async def get_docs():
    """Returns all extracted documents with their latest classification."""
    try:
        with get_session() as session:
            docs = session.exec(select(Document)).all()
            data = []
            for d in docs:
                item = {
                    "file_id": d.id,
                    "type": d.type,
                    "parent_id": d.parent_id,
                    "file_path": d.file_path,
                    "file_name": d.file_name,
                    "extracted_text_masked": d.extracted_text_masked,
                    "md5_hash": d.md5_hash,
                    "names_or_surnames_masked": d.names_or_surnames_masked
                }
                if d.classifications:
                    last_c = d.classifications[-1]
                    item.update({
                        "classification_generic": last_c.classification_generic,
                        "description_short": last_c.description_short,
                        "ollama_model_used": last_c.model_used,
                        "ollama_time_generic_s": last_c.time_generic_s,
                        "ollama_time_short_s": last_c.time_short_s
                    })
                data.append(item)
            return {"data": data}
    except Exception as e:
         return {"data": []}

@app.get("/api/identified")
async def get_identified():
    """Returns all document-to-ROPA mappings with associated metadata."""
    try:
        with get_session() as session:
            mappings = session.exec(select(DocumentRopaMapping)).all()
            records = []
            for m in mappings:
                doc = m.document
                ropa_act = "Processing Activity not identified"
                if m.ropa_id:
                    ropa = session.get(RopaRecord, m.ropa_id)
                    if ropa:
                        ropa_act = ropa.activity
                elif m.raw_fallback_text:
                    ropa_act = m.raw_fallback_text

                last_cls = ""
                last_desc = ""
                if doc.classifications:
                    c = doc.classifications[-1]
                    last_cls = c.classification_generic
                    last_desc = c.description_short
                
                records.append({
                    "ROPA_ID": m.ropa_id,
                    "Processing_Activity": ropa_act,
                    "File_ID": doc.id,
                    "type": doc.type,
                    "parent_id": doc.parent_id,
                    "classification": last_cls,
                    "description": last_desc
                })
            return {"data": records}
    except Exception as e:
        return {"data": []}

# --- LIFECYCLE AND JANITOR ENDPOINTS ---
@app.get("/api/lifecycle")
async def get_lifecycle():
    """Returns all document lifecycle records with classification metadata."""
    try:
        query = """
        SELECT 
            dl.id as lifecycle_id,
            dl.document_id,
            COALESCE(dl.document_type, d.type) as document_type,
            dl.creation_date,
            dl.scheduled_deletion_date,
            dl.actual_deletion_date,
            dl.status,
            dl.notes,
            dc.classification_generic as classification
        FROM document_lifecycle dl
        LEFT JOIN document_classification dc ON dl.document_id = dc.document_id
        LEFT JOIN document d ON dl.document_id = d.id
        ORDER BY dl.scheduled_deletion_date ASC
        """
        import sqlite3
        import pandas as pd
        
        output_dir = PROJECT_ROOT / get_config("database_folder", "./data/output")
        db_path = output_dir / "GDPHub.db"
        
        if not db_path.exists():
            return {"data": []}
            
        conn = sqlite3.connect(str(db_path))
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        # Convert datetime objects to string
        df['scheduled_deletion_date'] = df['scheduled_deletion_date'].astype(str)
        df['creation_date'] = df['creation_date'].astype(str)
        df['actual_deletion_date'] = df['actual_deletion_date'].astype(str)
        return {"data": df.to_dict(orient="records")}
    except Exception as e:
        return {"data": []}

@app.post("/api/lifecycle/{lifecycle_id}")
async def update_lifecycle(lifecycle_id: int, request: Request):
    """Updates the status and notes of a specific lifecycle record."""
    try:
        data = await request.json()
        new_status = data.get("status")
        new_notes = data.get("notes")
        
        import sqlite3
        db_path = PROJECT_ROOT / get_config("database_folder", "./data/output") / "GDPHub.db"
        
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            "UPDATE document_lifecycle SET status = ?, notes = ? WHERE id = ?",
            (new_status, new_notes, lifecycle_id)
        )
        conn.commit()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="An internal server error occurred")


@app.post("/api/janitor/run")
async def run_janitor_batch():
    """Triggers the Janitor to process all documents past their scheduled deletion date."""
    try:
        with get_session() as session:
            results = execute_deletion_workflow(db_session=session, specific_document_ids=None)
            return {"status": "success", "summary": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail="An internal server error occurred")

@app.post("/api/janitor/delete-manual")
async def run_janitor_manual(request: Request):
    """Triggers immediate deletion for specific document IDs."""
    try:
        data = await request.json()
        doc_ids = data.get("document_ids", [])
        force = data.get("force", False)
        
        if not doc_ids:
             raise HTTPException(status_code=400, detail="No document IDs provided")
             
        with get_session() as session:
            results = execute_deletion_workflow(
                db_session=session, 
                specific_document_ids=doc_ids,
                ignore_status=force
            )
            return {"status": "success", "summary": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- STATIC FRONTEND MOUNT ---
# Must be last so it doesn't mask /api routes
if WEBUI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEBUI_DIR), html=True), name="webui")

# --- MAIN SCRIPT EXECUTION BLOCK ---
if __name__ == "__main__":
    import uvicorn
    print("Starting GDPHub API server on 0.0.0.0:8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
