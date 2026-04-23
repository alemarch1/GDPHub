# This script matches extracted documents against ROPA processing activities
# using an Ollama language model. It analyzes the document's content and
# classification to identify which legal processing activities it relates to.

import os
import sys
import json
import logging
import re
import threading
import random
import argparse
import ollama
from pathlib import Path
from tqdm import tqdm
from config_manager import get_config

# --- CONFIGURATION AND PATHS ---
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

# --- ARGUMENT PARSING HELPER ---
def parse_arguments():
    """Parses command line arguments for ROPA identification."""
    parser = argparse.ArgumentParser(description="Match Extracted documents against ROPA activities using Ollama")
    parser.add_argument("--model", type=str, help="Ollama model to use", default=None)
    parser.add_argument("--no-think", action="store_true", help="Disable model thinking/chain-of-thought")
    args, _ = parser.parse_known_args()
    return args

CLI_ARGS = parse_arguments()

# Fetch configuration from the database
classify_cfg = get_config('classify_text.py', {})
ropa_cfg     = get_config('extract_ROPA.py', {})

LOG_FOLDER             = PROJECT_ROOT / get_config('log_folder', './logs')
OLLAMA_URL             = classify_cfg.get('ollama_url', 'http://localhost:11434')
OLLAMA_OPTIONS         = classify_cfg.get('ollama_options', {})

# Ensure num_ctx is always set (default 4096)
if 'num_ctx' not in OLLAMA_OPTIONS:
    OLLAMA_OPTIONS['num_ctx'] = 4096
OLLAMA_MODEL_DEFAULT   = classify_cfg.get('ollama_model_default', 'mistral:latest')
TIMEOUT_SECONDS        = classify_cfg.get('timeout_seconds', 30)
API_REQUEST_TIMEOUT    = classify_cfg.get('api_request_timeout', 25)

# --- LOGGING SYSTEM CONFIGURATION ---
from utils_logging import setup_logging
setup_logging("4_identify_ROPA")

# --- OLLAMA CLIENT INITIALIZATION ---
try:
    ollama_client = ollama.Client(host=OLLAMA_URL, timeout=API_REQUEST_TIMEOUT)
    logging.info(f"Ollama client initialized for URL: {OLLAMA_URL}")
except Exception as e:
    logging.error(f"Cannot create Ollama client: {e}", exc_info=True)
    sys.exit(1)

# --- UTILITY FUNCTIONS ---
def clean_text(text: str) -> str:
    """Removes non-ASCII characters and normalizes whitespace."""
    txt = re.sub(r'[^\x20-\x7E\n\t]', '', text)
    return re.sub(r'\s+', ' ', txt).strip()

def get_models(client: ollama.Client) -> list:
    """Retrieves the list of available Ollama models using the native client API."""
    try:
        response = client.list()
        models_list = response.get("models", [])
        available_models = [m.get("model") or m.get("name") for m in models_list]
        return [m for m in available_models if m]
    except Exception as e:
        logging.error(f"Error retrieving models from Ollama: {e}", exc_info=True)
        return []

def select_ollama_model(client: ollama.Client, force_model: str = None) -> str:
    """Allows the user to select an Ollama model or uses the forced/default one."""
    if force_model:
        logging.info(f"Model forcefully set via CLI: {force_model}")
        return force_model

    mods = get_models(client)
    if not mods:
        logging.warning(f"No models found, using default {OLLAMA_MODEL_DEFAULT}")
        return OLLAMA_MODEL_DEFAULT
    print("Available models:")
    for i, m in enumerate(mods, 1):
        print(f"{i}. {m}")
    try:
        choice = input("Choose a model (number) or press Enter to use default: ").strip()
        if not choice:
            return OLLAMA_MODEL_DEFAULT
        idx = int(choice) - 1
        return mods[idx]
    except Exception:
        logging.warning("Invalid choice, using default model")
        return OLLAMA_MODEL_DEFAULT

ACTIVE_OLLAMA_MODEL = select_ollama_model(ollama_client, CLI_ARGS.model)

# --- PROMPT CONSTRUCTION LOGIC ---
def build_prompt(document: dict, processing_activities: list, use_example: bool = True) -> str:
    """Constructs the LLM prompt to match a document against ROPA processing activities."""
    example = ""
    if use_example:
        example = (
            'Example:\n'
            'Document:\n'
            'Title: Client Newsletter\n'
            'Description: Sending promotional emails\n'
            'Text: ...we extracted client email addresses from the CRM and created a marketing campaign...\n'
            'Processing Activities:\n'
            '1 - Sending newsletter - Lawful Bases: a) Consent, f) Legitimate interests\n'
            '2 - Payroll management - Lawful Bases: b) Contract, c) Legal obligation\n'
            '3 - Human resources - Lawful Bases: b) Contract\n'
            'Answer: {"ropa_ids": [1, 3]}\n\n'
        )

    activity_list = "\n".join(
        f"{t.get('id', 'N/A')} - {t.get('Processing Activity', 'Unknown')} - Lawful Bases: {t.get('Lawful Bases', 'Unknown')}"
        for t in processing_activities
    )

    title = document.get('classification_generic', '')
    desc  = document.get('description_short', '')
    text  = clean_text(document.get('extracted_text_masked', ''))[:1000]

    prompt = (
        "You are a GDPR document matching AI. Your ONLY task is to match a document to the most relevant processing activities. "
        "Never generate conversational text.\n\n"
        f"{example}"
        f"Document to analyze:\n"
        f"Title: {title}\n"
        f"Description: {desc}\n"
        f"Text (extracted): {text}\n\n"
        f"Available Processing Activities:\n{activity_list}\n\n"
        'Return ONLY a JSON object in this exact format: {"ropa_ids": [<id1>, <id2>]}\n'
        'If you are not sure, return: {"ropa_ids": []}'
    )
    return prompt

# --- LLM QUERY ENGINE ---
def query_llm_for_document(client: ollama.Client, doc: dict, ropa_list: list) -> tuple:
    """Sends a document to the LLM and parses the matched ROPA IDs from the response."""
    file_id = doc.get('file_id', '')
    cls = doc.get('classification_generic', '')
    desc = doc.get('description_short', '')

    shuffled_ropa = ropa_list.copy()
    random.shuffle(shuffled_ropa)

    prompt = build_prompt(doc, shuffled_ropa, use_example=True)
    logging.info(f"Sending request for document {file_id}...")

    result = {'content': ''}
    def call_llm():
        try:
            chat_kwargs = dict(
                model=ACTIVE_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options=OLLAMA_OPTIONS,
                format="json"
            )
            if CLI_ARGS.no_think:
                try:
                    resp = client.chat(**chat_kwargs, think=False)
                except TypeError:
                    resp = client.chat(**chat_kwargs)
            else:
                resp = client.chat(**chat_kwargs)
            result['content'] = resp.get("message", {}).get("content", "").strip()
        except Exception as e:
            logging.error(f"Ollama error for {file_id}: {e}", exc_info=True)

    th = threading.Thread(target=call_llm)
    th.start()
    th.join(timeout=TIMEOUT_SECONDS)
    
    if th.is_alive():
        logging.error(f"Timeout for {file_id} after {TIMEOUT_SECONDS}s")
        return "Processing Activity not identified", cls, desc

    answer = result['content']
    
    if not answer:
        return "Processing Activity not identified", cls, desc

    # Parse JSON response
    try:
        parsed = json.loads(answer)
        raw_ids = parsed.get('ropa_ids', [])
        ids = [str(i) for i in raw_ids]
    except (json.JSONDecodeError, TypeError):
        logging.warning(f"Could not parse JSON for {file_id}, falling back to regex: {answer[:100]}")
        if "not identified" in answer.lower():
            return "Processing Activity not identified", cls, desc
        ids = re.findall(r"\b(\d+)\b", answer)

    def normalize_id(val):
        s = str(val).strip()
        return str(int(s)) if s.isdigit() else s

    matched_ids = []
    for raw_i in dict.fromkeys(ids):
        c_i = normalize_id(raw_i)
        for t in ropa_list:
            t_id = str(t.get('id', ''))
            if normalize_id(t_id) == c_i:
                matched_ids.append(t_id)
                break
                
    unique_ids = list(dict.fromkeys(matched_ids))
    
    if len(unique_ids) < 1:
        return "Processing Activity not identified", cls, desc
    
    return unique_ids[:2], cls, desc

# --- MAIN DOCUMENT IDENTIFICATION PROCESS ---
def process_identification():
    """Loads documents and ROPA records, runs LLM matching, and saves results to the database."""
    from database import get_session, create_db_and_tables
    from models import Document, RopaRecord, DocumentRopaMapping
    from sqlmodel import select
    create_db_and_tables()
    try:
        with get_session() as session:
            # Load ROPA
            ropa_records = session.exec(select(RopaRecord)).all()
            if not ropa_records:
                logging.error("No ROPA records found in database. Please extract ROPA first.")
                return
                
            ropa_data = []
            for r in ropa_records:
                ropa_data.append({
                    "id": r.id,
                    "Processing Activity": r.activity,
                    "Lawful Bases": r.lawful_bases,
                    "Data Subject Categories": r.subject_categories,
                    "Personal Data Categories": r.personal_data_categories,
                    "Recipients Categories": r.recipients_categories,
                    "International Transfers": r.international_transfers,
                    "Retention Periods": r.retention_periods
                })

            # Load Documents that do not have a ROPA mapping for this AI model yet
            docs = session.exec(select(Document)).all()
            if not docs:
                print("No documents found in the database to identify. Please run text extraction first.")
                return
                
            docs_to_process = []
            for doc in docs:
                has_mapped = any(m.model_used == ACTIVE_OLLAMA_MODEL for m in doc.ropa_mappings)
                if not has_mapped:
                    class_obj = next((c for c in reversed(doc.classifications) if c.model_used == ACTIVE_OLLAMA_MODEL), None)
                    if not class_obj and doc.classifications:
                        class_obj = doc.classifications[-1]
                        
                    if class_obj:
                        doc_dict = {
                            "file_id": doc.id,
                            "type": doc.type,
                            "parent_id": doc.parent_id,
                            "classification_generic": class_obj.classification_generic,
                            "description_short": class_obj.description_short,
                            "extracted_text_masked": doc.extracted_text_masked
                        }
                        docs_to_process.append((doc, doc_dict))

            if not docs_to_process:
                print("All documents have already been identified with this model.")
                return

            for doc_obj, doc_dict in tqdm(docs_to_process, desc="Identifying ROPA"):
                res, cls, desc = query_llm_for_document(ollama_client, doc_dict, ropa_data)
                
                # Check for string return (meaning "Processing Activity not identified")
                if isinstance(res, str):
                    new_mapping = DocumentRopaMapping(
                        document_id=doc_obj.id,
                        ropa_id=None,
                        model_used=ACTIVE_OLLAMA_MODEL,
                        raw_fallback_text=res
                    )
                    session.add(new_mapping)
                else:
                    for rid in res:
                        new_mapping = DocumentRopaMapping(
                            document_id=doc_obj.id,
                            ropa_id=rid,
                            model_used=ACTIVE_OLLAMA_MODEL,
                            raw_fallback_text=None
                        )
                        session.add(new_mapping)
                
                session.commit()
            print("Mapping saved to database.")
    except Exception as e:
        logging.error(f"Error during identification process: {e}", exc_info=True)
                    
    logging.info(f"Processing complete.")
    print(f"Processing complete!")

# --- SCRIPT EXECUTION ENTRY POINT ---
if __name__ == "__main__":
    process_identification()