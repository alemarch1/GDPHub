import os
import sys
import json
import asyncio
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

ACTIVE_PROCESS_PID = None
app = FastAPI(title="GDPHub Platform")
create_db_and_tables()

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

@app.get("/api/config")
async def get_app_config():
    """Retrieves all configuration from the database."""
    try:
        with get_session() as session:
            configs = session.exec(select(Configuration)).all()
            return {c.key: json.loads(c.value) for c in configs}
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

@app.get("/api/ollama/models")
async def get_ollama_models():
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
        return {"models": [], "error": str(e)}

def _ask_folder_logic(initial_dir):
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
async def browse_local_folder(current_path: str = None):
    """Opens a native OS folder dialog on the server and returns the selected path."""
    print(f"[GDPHub API] Request: browse-folder (current: {current_path})")
    
    initial_dir = current_path if current_path and os.path.exists(current_path) else None
    # Run tkinter dialog in a separate thread to avoid freezing the FastAPI event loop
    try:
        selected_path = await asyncio.to_thread(_ask_folder_logic, initial_dir)
        print(f"[GDPHub API] Selection: {selected_path}")
        return {"path": selected_path if selected_path else None}
    except Exception as e:
        print(f"[GDPHub API] Error opening folder dialog: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def run_script_generator(script_name: str, args: list):
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        yield f"data: [Error] Cannot find script {script_path}\n\n"
        yield "data: [END]\n\n"
        return
    
    cmd = [sys.executable, str(script_path)] + args
    yield f"data: Running command: {' '.join(cmd)}\n\n"
    
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
        
        global ACTIVE_PROCESS_PID
        ACTIVE_PROCESS_PID = process.pid
        

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
    except Exception as e:
        yield f"data: Exception running script: {str(e)}\n\n"
    finally:
        ACTIVE_PROCESS_PID = None
        yield "data: [END]\n\n"

@app.post("/api/control/{action}")
async def process_control(action: str):
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
            raise HTTPException(status_code=500, detail=str(e))
    return {"status": "no process"}

@app.post("/api/run/{script_name}")
async def run_pipeline_post(script_name: str, request: Request):
    data = await request.json()
    args = data.get("args", [])
    return StreamingResponse(run_script_generator(script_name, args), media_type="text/event-stream")

@app.post("/api/upload_ropa")
async def upload_ropa_file(file: UploadFile = File(...)):
    """Uploads a ROPA file (CSV/Excel) to the ROPA folder."""
    try:
        ropa_dir = PROJECT_ROOT / "data" / "ROPA"
        ropa_dir.mkdir(parents=True, exist_ok=True)
        file_path = ropa_dir / file.filename
        with open(file_path, "wb") as f:
            f.write(await file.read())
        return {"status": "success", "file_path": str(file_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/ropa")
async def get_ropa():
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
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ropa")
async def save_ropa(request: Request):
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
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/documents")
async def get_docs():
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

@app.get("/api/lifecycle")
async def get_lifecycle():
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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/janitor/run")
async def run_janitor_batch():
    """Triggers the Janitor to process all documents past their scheduled deletion date."""
    try:
        with get_session() as session:
            results = execute_deletion_workflow(db_session=session, specific_document_ids=None)
            return {"status": "success", "summary": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

# Mount Frontend App statically at the end so it doesn't mask /api routes
if WEBUI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEBUI_DIR), html=True), name="webui")

if __name__ == "__main__":
    import uvicorn
    print("Starting GDPHub API on http://0.0.0.0:8000 ...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
