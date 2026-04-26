# This Python script is designed to classify texts extracted from documents.
# It uses an Ollama language model to perform two types of classification:
# a generic one and a short description.
# The script reads data from a JSON file, processes each document,
# and saves the results enriched with classifications in a new JSON file.
# It features progress saving and resume capabilities to prevent data loss.

import os
import sys
import time
import json
import logging
import threading
import re
from pathlib import Path
from tqdm import tqdm
import ollama 
from database import get_session, create_db_and_tables
from models import Document, DocumentClassification
from sqlmodel import select
from config_manager import get_config

# --- CONSTANTS AND PATHS DEFINITION ---
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

# --- INITIAL CONFIGURATION LOADING ---
_temp_logger = logging.getLogger("config_loader")
_temp_logger.addHandler(logging.StreamHandler(sys.stderr))
_temp_logger.setLevel(logging.INFO)

# Fetch configuration from the database
classify_config = get_config('classify_text.py', {})
if not classify_config:
    _temp_logger.warning("The 'classify_text.py' section is missing from the database. Using defaults.")

LOG_FOLDER_STR = get_config('log_folder', './logs')
LOG_FOLDER = PROJECT_ROOT / LOG_FOLDER_STR

OLLAMA_URL = classify_config.get('ollama_url', 'http://localhost:11434')
OLLAMA_OPTIONS = classify_config.get('ollama_options', {})
OLLAMA_MODEL_DEFAULT = classify_config.get('ollama_model_default', 'mistral:latest')

# Ensure num_ctx is always set (default 4096)
if 'num_ctx' not in OLLAMA_OPTIONS:
    OLLAMA_OPTIONS['num_ctx'] = 4096

TITLE_MAX_LENGTH = classify_config.get('title_max_length', 500)
TEXT_MAX_LENGTH = classify_config.get('text_max_length', 1500)
OVERALL_OPERATION_TIMEOUT_SECONDS = classify_config.get('timeout_seconds', 60)
API_REQUEST_TIMEOUT_SECONDS = classify_config.get('api_request_timeout', 45)

# --- LOGGING SYSTEM CONFIGURATION ---
from utils_logging import setup_logging
setup_logging("2_classify_text")

# --- OLLAMA CLIENT INITIALIZATION ---
try:
    ollama_client = ollama.Client(host=OLLAMA_URL, timeout=API_REQUEST_TIMEOUT_SECONDS)
    logging.info(f"Ollama client initialized for URL: {OLLAMA_URL} with API timeout of {API_REQUEST_TIMEOUT_SECONDS}s.")
except Exception as e:
    logging.error(f"Cannot create Ollama client: {e}", exc_info=True)
    sys.exit(1)

# --- UTILITY FUNCTIONS ---
def clean_text(text: str) -> str:
    """Cleans the text by removing non-ASCII characters and normalizing spaces."""
    if not isinstance(text, str):
        text = str(text)
    cleaned = re.sub(r'[^\x20-\x7E\n\t]', '', text)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip()

def get_ollama_models(client: ollama.Client) -> list[str]:
    """Retrieves the list of available Ollama models using the native client API."""
    try:
        # FIX 3: Using native client.list() instead of raw requests.
        response = client.list()
        models_list = response.get("models", [])
        available_models = [m.get("model") or m.get("name") for m in models_list]
        available_models = [m for m in available_models if m] # Remove any empty ones
        logging.info(f"Available models from Ollama: {available_models}")
        return available_models
    except Exception as e:
        logging.error(f"Unexpected error retrieving Ollama models: {e}", exc_info=True)
        return []

import argparse

# --- ARGUMENT PARSING HELPER ---
def parse_arguments():
    parser = argparse.ArgumentParser(description="Classify extracted text using Ollama")
    parser.add_argument("--model", type=str, help="Ollama model to use", default=None)
    parser.add_argument("--run-all", action="store_true", help="Process entire JSON without prompting")
    parser.add_argument("--file-id", type=str, help="Specific file_id to process", default=None)
    parser.add_argument("--no-think", action="store_true", help="Disable model thinking/chain-of-thought")
    args, _ = parser.parse_known_args()
    return args

CLI_ARGS = parse_arguments()

def select_ollama_model(client: ollama.Client, default_model: str, force_model: str | None = None) -> str:
    """Allows the user to select an Ollama model or uses the forced/default one."""
    if force_model:
        logging.info(f"Model forcefully set via CLI: {force_model}")
        return force_model

    available_models = get_ollama_models(client)

    if not available_models:
        logging.warning(f"No Ollama model found or error retrieving them. Default model will be used: {default_model}")
        return default_model

    print("\nAvailable Ollama models:")
    for i, model_name in enumerate(available_models, 1):
        print(f"{i}. {model_name}")

    while True:
        try:
            choice_str = input(f"Choose the model (number) or press Enter to use [{default_model}]: ").strip()
            if not choice_str:
                logging.info(f"No selection, using default model: {default_model}")
                return default_model
            
            choice_idx = int(choice_str) - 1
            if 0 <= choice_idx < len(available_models):
                selected_model = available_models[choice_idx]
                logging.info(f"Model selected by user: {selected_model}")
                return selected_model
            else:
                print("Invalid selection. Try again.")
        except ValueError:
            print("Invalid input. Enter a number. Try again.")
        except Exception as e:
            logging.error(f"Error during model selection: {e}. Using default model: {default_model}", exc_info=True)
            return default_model

ACTIVE_OLLAMA_MODEL = select_ollama_model(ollama_client, OLLAMA_MODEL_DEFAULT, CLI_ARGS.model)

# --- CORE LOGIC FOR OLLAMA REQUESTS WITH TIMEOUT ---
def _execute_ollama_request_with_timeout(
    client: ollama.Client,
    prompt_content: str,
    model_name: str,
    ollama_api_options: dict,
    operation_timeout: int,
    error_message_default: str,
    log_context_description: str,
    disable_thinking: bool = False
) -> tuple[str, float]:
    """Executes a chat request to Ollama in a separate thread with timeout management."""
    logging.info(f"Sending '{log_context_description}' request to Ollama (model: {model_name}).")
    start_time = time.time()
    
    result_holder: list[str | None] = [None]
    exception_holder: list[Exception | None] = [None]

    def ollama_worker():
        try:
            # We enforce 'no-think' via prompt instructions to ensure cross-model compatibility
            processed_prompt = prompt_content
            if disable_thinking:
                processed_prompt = "DO NOT use <think> tags. Answer directly.\n\n" + prompt_content

            _messages: list[dict[str, str]] = [{"role": "user", "content": processed_prompt}]
            
            # Try with think=False first; if the library doesn't support it, retry without
            response = None
            if disable_thinking:
                try:
                    response = client.chat(model=model_name, messages=_messages, options=ollama_api_options, think=False)
                except TypeError:
                    # Library version doesn't support 'think' parameter — fall back
                    logging.info(f"'think' parameter not supported by ollama library, using prompt-only approach.")
                    response = client.chat(model=model_name, messages=_messages, options=ollama_api_options)
            else:
                response = client.chat(model=model_name, messages=_messages, options=ollama_api_options)

            raw_content = response.get("message", {}).get("content", "").strip()
            
            # 1. Standard extraction: remove everything inside <think> tags
            clean_content = re.sub(r'<think>.*?</think>', '', raw_content, flags=re.DOTALL).strip()
            
            # 2. Fallback: if clean_content is empty but raw exists, the model wrote only inside tags
            if not clean_content and raw_content:
                if "</think>" in raw_content:
                    clean_content = raw_content.split("</think>")[-1].strip()
                
                # 3. Last Resort: extract the last line of the thinking process as the answer
                if not clean_content:
                    think_matches = re.findall(r'<think>(.*?)(?:</think>|$)', raw_content, flags=re.DOTALL)
                    if think_matches:
                        last_thought_block = think_matches[-1].strip().split('\n')
                        clean_content = last_thought_block[-1].strip()
                    else:
                        clean_content = raw_content

            result_holder[0] = clean_content if clean_content else error_message_default
        except Exception as e: 
            logging.error(f"Error during request for '{log_context_description}': {e}", exc_info=False)
            exception_holder[0] = e
            result_holder[0] = f"{error_message_default} (Error)"
            
    request_thread = threading.Thread(target=ollama_worker)
    request_thread.start()
    request_thread.join(timeout=operation_timeout)

    elapsed_time = time.time() - start_time

    if request_thread.is_alive():
        logging.error(f"General timeout for '{log_context_description}' after {operation_timeout}s.")
        return (f"Timeout: {error_message_default}", elapsed_time)
        
    return (result_holder[0] or error_message_default, elapsed_time)

# --- FUNCTION TO CLASSIFY DOCUMENT TEXT ---
def classify_document_text(
    title: str,
    text_content: str,
    classification_type: str, 
    timeout_sec: int = OVERALL_OPERATION_TIMEOUT_SECONDS
) -> tuple[str, float]:
    """Classifies a document's text using Ollama based on the specified type."""
    if not text_content.strip() and not title.strip():
        return ("Text not available", 0.0)

    cleaned_title = clean_text(title)[:TITLE_MAX_LENGTH]
    cleaned_text = clean_text(text_content)[:TEXT_MAX_LENGTH]
    
    error_default = "Classification error"
    log_context = f"classification '{classification_type}'"

    if classification_type == "generic_type":
        prompt = (
            f"Classify this document. Reply with 1 to 3 words only.\n\n"
            f"TITLE: {cleaned_title}\n"
            f"TEXT: {cleaned_text}\n\n"
            f"Document Type:"
        )
    elif classification_type == "short_description":
        prompt = (
            f"Describe this document in max 10 words.\n\n"
            f"TITLE: {cleaned_title}\n"
            f"TEXT: {cleaned_text}\n\n"
            f"Description:"
        )
    else:
        return (f"Type '{classification_type}' not supported", 0.0)

    raw_result, elapsed = _execute_ollama_request_with_timeout(
        client=ollama_client,
        prompt_content=prompt,
        model_name=ACTIVE_OLLAMA_MODEL,
        ollama_api_options=OLLAMA_OPTIONS,
        operation_timeout=timeout_sec,
        error_message_default=error_default,
        log_context_description=log_context,
        disable_thinking=CLI_ARGS.no_think
    )

    # Final cleanup of common LLM artifacts (quotes, dots, etc)
    final_value = raw_result.strip().replace('"', '').replace("'", "")
    final_value = re.sub(r'^[^\w]+|[^\w]+$', '', final_value)
    
    return (final_value if final_value else error_default, elapsed)

# --- MAIN DOCUMENT CLASSIFICATION PROCESS ---
def process_document_classifications(target_file_id: str | None = None) -> None:
    """Processes documents from DB, classifies them, and saves the results iteratively in DB."""
    create_db_and_tables()
    try:
        with get_session() as session:
            docs_to_process = []
            if target_file_id:
                doc = session.get(Document, target_file_id)
                if not doc:
                    print(f"WARNING: No document found with file_id: '{target_file_id}'.") 
                    return
                docs_to_process = [doc]
            else:
                docs = session.exec(select(Document)).all()
                if not docs:
                    print("No documents found in the database to classify. Please run text extraction first.")
                    return
                    
                for doc in docs:
                    # check if already classified conceptually by checking if it has a classification with this model
                    has_current = any(c.model_used == ACTIVE_OLLAMA_MODEL for c in doc.classifications)
                    if not has_current:
                        docs_to_process.append(doc)
                    else:
                        logging.info(f"FileID {doc.id} already classified with {ACTIVE_OLLAMA_MODEL}. Skipping.")

            if not docs_to_process:
                print("All documents have already been classified with this model.")
                return

            for doc in tqdm(docs_to_process, desc="Classifying"):
                current_file_id = doc.id
                current_file_name = doc.file_name

                logging.info(f"Starting classification for file_id: {current_file_id}")
                try:
                    extracted_text = doc.extracted_text_masked or ""
                    if not extracted_text.strip():
                        new_class = DocumentClassification(
                            document_id=current_file_id,
                            model_used=ACTIVE_OLLAMA_MODEL,
                            classification_generic="Empty text",
                            description_short="Empty text",
                            time_generic_s=0.0,
                            time_short_s=0.0
                        )
                    else:
                        generic_class, generic_time = classify_document_text(
                            current_file_name, extracted_text, "generic_type", OVERALL_OPERATION_TIMEOUT_SECONDS
                        )
                        short_desc, short_time = classify_document_text(
                            current_file_name, extracted_text, "short_description", OVERALL_OPERATION_TIMEOUT_SECONDS
                        )
                        new_class = DocumentClassification(
                            document_id=current_file_id,
                            model_used=ACTIVE_OLLAMA_MODEL,
                            classification_generic=generic_class,
                            description_short=short_desc,
                            time_generic_s=round(generic_time, 2),
                            time_short_s=round(short_time, 2)
                        )
                    
                    session.add(new_class)
                    session.commit()
                except Exception as e: 
                    logging.error(f"Error during classification of file_id {current_file_id}: {e}")
            
        print("Processing completed. Results saved to database.")
    except Exception as e:
        logging.error(f"Database error: {e}", exc_info=True)

# --- MAIN SCRIPT EXECUTION BLOCK ---
if __name__ == "__main__":
    logging.info(f"Starting classification script. Active Ollama model: {ACTIVE_OLLAMA_MODEL}")

    if CLI_ARGS.run_all:
        process_document_classifications()
    elif CLI_ARGS.file_id:
        process_document_classifications(CLI_ARGS.file_id)
    else:
        user_choice = ""
        while user_choice not in ["A", "B"]:
            user_choice = input("Do you want to process (A) Entire collection or (B) Only a specific file_id? [A/B]: ").strip().upper()

        if user_choice == "B":
            target_id = input("Enter the file_id to process: ").strip()
            if not target_id:
                print("ERROR: No file_id entered. Aborting.")
                sys.exit(1)
            process_document_classifications(target_id)
        else: 
            process_document_classifications()