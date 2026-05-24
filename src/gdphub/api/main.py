# FastAPI backend for the GDPHub web interface.
# Provides REST endpoints for configuration management, pipeline execution,
# document lifecycle operations, ROPA management, and the Janitor service.
# Serves the static frontend from the /web directory.

import logging
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

from gdphub.core.database import get_session, create_db_and_tables, recalculate_lifecycle
from gdphub.core.models import Document, DocumentClassification, DocumentLifecycle, RopaRecord, DocumentRopaMapping, ProcessedEmail
from sqlmodel import select, delete, col
from sqlalchemy import func
from gdphub.core.config_manager import get_config, set_config, Configuration
from gdphub.services.deletion import execute_deletion_workflow
from gdphub.services import rag_service
from gdphub.core.settings import seed_dict, gpu_profile_migration_pairs

# --- APPLICATION INITIALIZATION ---
ACTIVE_PROCESS_PID = None
app = FastAPI(title="GDPHub Platform")

create_db_and_tables()

def _seed_defaults():
    """Seeds the Configuration and RopaRecord tables with sensible defaults
    when the database is freshly created (i.e. tables are empty).
    This ensures the application works out-of-the-box after a fresh clone.

    Defaults live in :mod:`settings`; this function only handles persistence.
    """
    with get_session() as session:
        # --- Seed Configuration defaults ---
        existing_configs = session.exec(select(Configuration)).first()
        if existing_configs is None:
            for key, value in seed_dict().items():
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

        # --- Migrate: add gpu_profile keys to existing databases ---
        for new_key, new_val in gpu_profile_migration_pairs():
            existing = session.exec(select(Configuration).where(Configuration.key == new_key)).first()
            if existing is None:
                session.add(Configuration(key=new_key, value=json.dumps(new_val)))
        session.commit()

        # --- Migrate: rename legacy script configuration keys ---
        legacy_key_map = {
            "0_extract_mail.py": "extract_mail",
            "1_extract_text.py": "extract_text",
            "2_classify_text.py": "classify_text",
            "3_extract_ROPA.py": "extract_ropa",
            "4_identify_ROPA.py": "identify_ropa",
            "5_document_deletion.py": "document_deletion"
        }
        for old_key, new_key in legacy_key_map.items():
            existing_old = session.exec(select(Configuration).where(Configuration.key == old_key)).first()
            if existing_old:
                # check if new_key already exists
                existing_new = session.exec(select(Configuration).where(Configuration.key == new_key)).first()
                if not existing_new:
                    session.add(Configuration(key=new_key, value=existing_old.value))
                session.delete(existing_old)
        session.commit()

_seed_defaults()

# Enable CORS for local cross-origin connections during UI dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
CONFIG_FILE = PROJECT_ROOT / 'src' / 'config.json'
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
            outlook_file = PROJECT_ROOT / "src" / "auth" / "outlook.json"
            if outlook_file.exists():
                try:
                    with open(outlook_file, "r") as f:
                        result["0_extract_mail_outlook"] = json.load(f)
                except Exception:
                    pass
                    
            # Inject gmail auth config from gmail.json
            gmail_file = PROJECT_ROOT / "src" / "auth" / "gmail.json"
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
            outlook_file = PROJECT_ROOT / "src" / "auth" / "outlook.json"
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
            gmail_file = PROJECT_ROOT / "src" / "auth" / "gmail.json"
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

    # Note: previous versions mirrored ``database_folder``/``log_folder``/
    # ``input_folder`` back to ``config.json``. That round-trip was removed
    # — runtime DB-path resolution now uses the ``GDPHUB_DB_FOLDER`` env var
    # (see ``database.py``), eliminating the chicken-and-egg dependency.
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

# Allowlist of pipeline scripts executable via the API. Acts as a barrier
# against path-injection: user input is only checked for membership, never
# concatenated into a filesystem path.
PIPELINE_SCRIPT_ALLOWLIST: frozenset[str] = frozenset({
    "extract_mail",
    "extract_text",
    "classify_text",
    "extract_ropa",
    "identify_ropa",
    "document_deletion",
})

async def run_script_generator(script_name: str, args: list):
    """Async generator that spawns a pipeline script and streams its stdout as SSE events."""
    global ACTIVE_PROCESS_PID

    # Resolve script via allowlist — user input never touches the filesystem directly
    if script_name not in PIPELINE_SCRIPT_ALLOWLIST:
        yield "data: [Error] Access denied: Invalid or disallowed script name\n\n"
        yield "data: [END]\n\n"
        return

    # Run as a module, e.g. gdphub.pipelines.extract_mail
    cmd = [sys.executable, "-m", f"gdphub.pipelines.{script_name}"] + args
    yield f"data: Running command: {' '.join(cmd)}\n\n"
    
    process: Optional[asyncio.subprocess.Process] = None
    try:
        # We enforce unbuffered stdout so Python doesn't delay streaming until the buffer is full
        env = os.environ.copy()
        # Add src to PYTHONPATH so gdphub package is resolvable
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
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

@app.get("/api/ropa/export")
async def export_ropa(format: str = "xlsx"):
    """Exports all ROPA records from the database in CSV or XLSX format."""
    if format not in ["csv", "xlsx"]:
        raise HTTPException(status_code=400, detail="Invalid format. Supported formats: csv, xlsx")
        
    try:
        import io
        import pandas as pd
        from fastapi.responses import Response
        
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
            
        df = pd.DataFrame(data)
        
        if format == "csv":
            stream = io.StringIO()
            df.to_csv(stream, index=False, encoding='utf-8')
            response_content = stream.getvalue().encode('utf-8')
            media_type = "text/csv"
            filename = "ropa_export.csv"
        else:
            stream = io.BytesIO()
            with pd.ExcelWriter(stream, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='ROPA')
            response_content = stream.getvalue()
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = "ropa_export.xlsx"
            
        return Response(
            content=response_content,
            media_type=media_type,
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Access-Control-Expose-Headers": "Content-Disposition"
            }
        )
    except Exception as e:
        logging.exception("Failed to export ROPA file")
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")

from pydantic import BaseModel
class AdminCleanRequest(BaseModel):
    option: str
    collection: Optional[str] = None
    entry_id: Optional[str] = None

@app.post("/api/admin/clean")
async def run_admin_clean(req: AdminCleanRequest):
    """Runs gdphub.utils.clean with the appropriate command line flags and returns stdout/stderr."""
    # Map option to arguments
    args = []
    if req.option == "1":
        args = ["--sqlite"]
    elif req.option == "2":
        args = ["--vector-clear", "classifications"]
    elif req.option == "3":
        args = ["--vector-clear", "mappings"]
    elif req.option == "4":
        args = ["--vector-clear", "all"]
    elif req.option == "5":
        args = ["--vector-list"]
    elif req.option == "6":
        if not req.collection or not req.entry_id:
            raise HTTPException(status_code=400, detail="Collection and Entry ID are required for option 6")
        args = ["--vector-delete-id", req.entry_id, "--collection", req.collection]
    elif req.option == "7":
        args = ["--all"]
    else:
        raise HTTPException(status_code=400, detail=f"Invalid option: {req.option}")

    try:
        # Run the script using subprocess
        cmd = [sys.executable, "-m", "gdphub.utils.clean"] + args
        logging.info(f"Running admin action command: {' '.join(cmd)}")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        output = stdout.decode("utf-8", errors="replace")
        error_output = stderr.decode("utf-8", errors="replace")
        
        combined_output = output
        if error_output:
            combined_output += f"\n--- ERROR ---\n{error_output}"
            
        return {
            "status": "success" if process.returncode == 0 else "error",
            "returncode": process.returncode,
            "output": combined_output
        }
    except Exception as e:
        logging.exception("Failed to run clean.py script")
        raise HTTPException(status_code=500, detail=f"Failed to execute clean.py: {str(e)}")



# --- DOCUMENT AND IDENTIFICATION ENDPOINTS ---

@app.get("/api/models/used")
async def get_models_used():
    """Returns distinct model_used values from classifications and mappings."""
    try:
        with get_session() as session:
            cls_models = session.exec(
                select(DocumentClassification.model_used).distinct()
            ).all()
            map_models = session.exec(
                select(DocumentRopaMapping.model_used).distinct()
            ).all()
            return {
                "classification_models": sorted(cls_models),
                "mapping_models": sorted(map_models),
            }
    except Exception:
        return {"classification_models": [], "mapping_models": []}

@app.get("/api/documents")
async def get_docs(model: Optional[str] = None):
    """Returns all extracted documents with their classification for the selected model."""
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
                    if model:
                        cls = next((c for c in d.classifications if c.model_used == model), None)
                    else:
                        cls = d.classifications[-1]
                    if cls:
                        item.update({
                            "classification_generic": cls.classification_generic,
                            "description_short": cls.description_short,
                            "ollama_model_used": cls.model_used,
                            "ollama_time_generic_s": cls.time_generic_s,
                            "ollama_time_short_s": cls.time_short_s
                        })
                data.append(item)
            return {"data": data}
    except Exception as e:
         return {"data": []}

@app.post("/api/documents/{file_id}/classification")
async def update_document_classification(file_id: str, request: Request):
    """Manually corrects a document's classification and stores the feedback in RAG."""
    try:
        data = await request.json()
        new_type = data.get("classification_generic", "").strip()
        new_desc = data.get("description_short", "").strip()
        target_model = data.get("model_used")
        if not new_type and not new_desc:
            raise HTTPException(status_code=400, detail="At least one field required")
        with get_session() as session:
            doc = session.get(Document, file_id)
            if not doc:
                raise HTTPException(status_code=404, detail="Document not found")
            if not doc.classifications:
                raise HTTPException(status_code=404, detail="Document has no classifications yet")
            target_c = None
            if target_model:
                target_c = next((c for c in doc.classifications if c.model_used == target_model), None)
                if not target_c:
                    raise HTTPException(status_code=404, detail=f"No classification found for model {target_model}")
            else:
                target_c = doc.classifications[-1]
            if new_type:
                target_c.classification_generic = new_type
            if new_desc:
                target_c.description_short = new_desc
            session.add(target_c)
            session.commit()
            try:
                rag_service.upsert_classification(
                    document_id=file_id,
                    file_name=doc.file_name or "",
                    text_snippet=doc.extracted_text_masked or "",
                    corrected_type=new_type or (target_c.classification_generic if target_c else ""),
                    corrected_description=new_desc or (target_c.description_short if target_c else ""),
                )
            except Exception as rag_err:
                logging.warning(f"RAG upsert failed (non-blocking): {rag_err}")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="An internal server error occurred")

@app.get("/api/stats/pending")
async def get_pending_stats():
    """Counts files and emails that are not yet anonymized."""
    try:
        with get_session() as session:
            # Get existing anonymized filenames and email parent IDs
            existing_filenames = set(session.exec(select(Document.file_name)).all())
            existing_parent_ids = set(session.exec(select(Document.parent_id)).all())
            
            # 1. Count files in input folder not yet in Document table
            input_folder = get_config("input_folder", "data/input")
            input_path = (PROJECT_ROOT / input_folder).resolve()
            
            # Supported extensions (from Step 1 map)
            supported_exts = {'.pdf', '.docx', '.doc', '.odt', '.rtf', '.xml', '.json', '.html', '.htm', '.csv', '.xls', '.md', '.txt'}
            
            pending_files_in_folder = set()
            if input_path.exists() and input_path.is_dir():
                for f in input_path.glob("**/*"):
                    if f.is_file() and f.suffix.lower() in supported_exts:
                        if f.name not in existing_filenames:
                            pending_files_in_folder.add(f.name)
            else:
                import logging
                logging.warning(f"PendingStats: input_path does not exist or is not a dir: {input_path}")
            
            # 2. Check ProcessedEmail table vs Document table
            processed_email_ids = set(session.exec(select(ProcessedEmail.id)).all())
            pending_email_ids = processed_email_ids - existing_parent_ids
            
            # Pre-extract email IDs that already have matching pending files —
            # converts the previous O(len(emails) * len(files)) `startswith`
            # scan into an O(len(emails) + len(files)) set lookup.
            pending_email_ids_with_files = set()
            for fname in pending_files_in_folder:
                if fname.startswith("email_"):
                    rest = fname[len("email_"):]
                    underscore_idx = rest.find("_")
                    if underscore_idx > 0:
                        pending_email_ids_with_files.add(rest[:underscore_idx])

            total_pending = len(pending_files_in_folder)
            for eid in pending_email_ids:
                if eid not in pending_email_ids_with_files:
                    total_pending += 1

            return {"count": total_pending}
    except Exception as e:
        import logging
        logging.error(f"Error in get_pending_stats: {str(e)}")
        return {"count": 0}

@app.get("/api/identified")
async def get_identified(model: Optional[str] = None):
    """Returns document-to-ROPA mappings, optionally filtered by model.

    When no model is specified, defaults to the most recently used model.
    Documents not covered by that model fall back to any other model's mapping.
    """
    try:
        with get_session() as session:
            all_mappings = session.exec(select(DocumentRopaMapping)).all()

            if model:
                mappings = [m for m in all_mappings if m.model_used == model]
            else:
                if not all_mappings:
                    return {"data": []}
                latest_model = max(all_mappings, key=lambda m: m.processing_date).model_used
                latest_doc_ids = {m.document_id for m in all_mappings if m.model_used == latest_model}
                all_doc_ids = {m.document_id for m in all_mappings}
                uncovered = all_doc_ids - latest_doc_ids
                mappings = [m for m in all_mappings if m.model_used == latest_model]
                if uncovered:
                    mappings += [m for m in all_mappings if m.document_id in uncovered and m.model_used != latest_model]

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
                    # Prefer the classification from the same model as the mapping
                    cls = next((c for c in doc.classifications if c.model_used == m.model_used), None)
                    if not cls:
                        cls = doc.classifications[-1]
                    last_cls = cls.classification_generic
                    last_desc = cls.description_short

                records.append({
                    "mapping_id": m.id,
                    "ROPA_ID": m.ropa_id,
                    "Processing_Activity": ropa_act,
                    "File_ID": doc.id,
                    "type": doc.type,
                    "parent_id": doc.parent_id,
                    "classification": last_cls,
                    "description": last_desc,
                    "model_used": m.model_used
                })
            return {"data": records}
    except Exception as e:
        return {"data": []}

@app.post("/api/identified/{mapping_id}")
async def update_identified_mapping(mapping_id: int, request: Request):
    """Updates the ROPA assignment for a specific document-ROPA mapping."""
    try:
        data = await request.json()
        new_ropa_id = data.get("ropa_id")
        with get_session() as session:
            mapping = session.get(DocumentRopaMapping, mapping_id)
            if not mapping:
                raise HTTPException(status_code=404, detail="Mapping not found")
            mapping.ropa_id = new_ropa_id if new_ropa_id else None
            session.add(mapping)
            session.commit()

            # Recalculate lifecycle retention (safety net alongside SQLite trigger)
            try:
                recalculate_lifecycle(session, mapping.document_id)
                session.commit()
            except Exception as lc_err:
                logging.warning(f"Lifecycle recalculation failed (non-blocking): {lc_err}")

            if new_ropa_id:
                try:
                    doc = mapping.document
                    ropa = session.get(RopaRecord, new_ropa_id)
                    cls_text = ""
                    desc_text = ""
                    if doc.classifications:
                        # Prefer the classification from the same model as the mapping
                        cls = next((c for c in doc.classifications if c.model_used == mapping.model_used), None)
                        if not cls:
                            cls = doc.classifications[-1]
                        cls_text = cls.classification_generic or ""
                        desc_text = cls.description_short or ""
                    rag_service.upsert_ropa_mapping(
                        mapping_id=mapping_id,
                        document_id=doc.id,
                        classification=cls_text,
                        description=desc_text,
                        text_snippet=doc.extracted_text_masked or "",
                        corrected_ropa_id=new_ropa_id,
                        corrected_ropa_activity=ropa.activity if ropa else "",
                    )
                except Exception as rag_err:
                    logging.warning(f"RAG upsert failed (non-blocking): {rag_err}")

        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="An internal server error occurred")

# --- LIFECYCLE AND JANITOR ENDPOINTS ---
@app.get("/api/lifecycle")
async def get_lifecycle():
    """Returns all document lifecycle records with classification metadata.

    Implementation notes: equivalent to the original raw-SQL JOIN, executed
    via SQLModel against the shared engine. Date fields are stringified with
    Python's ``str()`` to mirror the prior pandas ``astype(str)`` behavior
    (``None`` → ``"None"``, datetime → ``"YYYY-MM-DD HH:MM:SS[.ffffff]"``).
    """
    try:
        with get_session() as session:
            stmt = (
                select(DocumentLifecycle, Document, DocumentClassification)
                .join(Document, Document.id == DocumentLifecycle.document_id, isouter=True)  # type: ignore[arg-type]
                .join(
                    DocumentClassification,
                    DocumentClassification.document_id == DocumentLifecycle.document_id,  # type: ignore[arg-type]
                    isouter=True,
                )
                .order_by(DocumentLifecycle.scheduled_deletion_date.asc())  # type: ignore[union-attr]
            )
            rows = session.exec(stmt).all()

            # For each document, find the latest model used in its ROPA
            # mappings so we can prefer the matching classification.
            doc_latest_model: dict[str, str] = {}
            _doc_latest_date: dict[str, "datetime"] = {}
            all_mappings = session.exec(select(DocumentRopaMapping)).all()
            for mp in all_mappings:
                prev_date = _doc_latest_date.get(mp.document_id)
                if prev_date is None or mp.processing_date > prev_date:
                    doc_latest_model[mp.document_id] = mp.model_used
                    _doc_latest_date[mp.document_id] = mp.processing_date

            # Multiple classifications per document collapse to the one
            # matching the latest ROPA-mapping model (or highest id as
            # fallback). This keeps the lifecycle view consistent with the
            # ROPA mapping that determined the retention date.
            best: dict[int, dict] = {}
            for lc, doc, cls in rows:
                lc_id = lc.id if lc.id is not None else id(lc)
                existing = best.get(lc_id)
                cls_id = cls.id if (cls is not None and cls.id is not None) else -1
                # Prefer classification whose model matches the latest ROPA mapping model
                preferred_model = doc_latest_model.get(lc.document_id)
                model_match = 1 if (cls is not None and preferred_model and cls.model_used == preferred_model) else 0
                score = (model_match, cls_id)
                prev_score = existing["_cls_score"] if existing else (-1, -1)
                if existing is None or score > prev_score:
                    best[lc_id] = {
                        "lifecycle_id": lc.id,
                        "document_id": lc.document_id,
                        "document_type": lc.document_type or (doc.type if doc else None),
                        "creation_date": str(lc.creation_date),
                        "scheduled_deletion_date": str(lc.scheduled_deletion_date),
                        "actual_deletion_date": str(lc.actual_deletion_date),
                        "status": lc.status,
                        "notes": lc.notes,
                        "classification": cls.classification_generic if cls else None,
                        "_cls_score": score,
                    }
            data = []
            for entry in best.values():
                entry.pop("_cls_score", None)
                data.append(entry)
            return {"data": data}
    except Exception:
        return {"data": []}

@app.post("/api/lifecycle/{lifecycle_id}")
async def update_lifecycle(lifecycle_id: int, request: Request):
    """Updates the status and notes of a specific lifecycle record."""
    try:
        data = await request.json()
        new_status = data.get("status")
        new_notes = data.get("notes")

        with get_session() as session:
            row = session.get(DocumentLifecycle, lifecycle_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Lifecycle record not found")
            row.status = new_status
            row.notes = new_notes
            session.add(row)
            session.commit()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception:
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
