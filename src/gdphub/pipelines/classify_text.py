# This Python script is designed to classify texts extracted from documents.
# It uses an Ollama language model to perform two types of classification:
# a generic one and a short description.
# The script reads data from a JSON file, processes each document,
# and saves the results enriched with classifications in a new JSON file.
# It features progress saving and resume capabilities to prevent data loss.

import sys
import json
import logging
import re
import argparse
from pathlib import Path
from tqdm import tqdm
import ollama
from gdphub.core.database import get_session, create_db_and_tables
from gdphub.core.models import Document, DocumentClassification
from sqlmodel import select
from gdphub.core.config_manager import get_config
from gdphub.utils.model import get_model_profile  # noqa: F401  retained for back-compat re-export

# --- CONSTANTS AND PATHS DEFINITION ---
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

# --- ARGUMENT PARSING ---
# Parsed at module load so config-time blocks (e.g. GPU profile override below)
# can react to CLI flags without raising NameError.
def parse_arguments():
    parser = argparse.ArgumentParser(description="Classify extracted text using Ollama")
    parser.add_argument("--model", type=str, help="Ollama model to use", default=None)
    parser.add_argument("--run-all", action="store_true", help="Process entire JSON without prompting")
    parser.add_argument("--file-id", type=str, help="Specific file_id to process", default=None)
    parser.add_argument("--no-think", action="store_true", help="Disable model thinking/chain-of-thought")
    parser.add_argument("--gpu-profile", type=str, choices=["8gb", "12gb", "24gb"],
                        help="GPU VRAM preset (overrides ollama_options)", default=None)
    args, _ = parser.parse_known_args()
    return args

CLI_ARGS = parse_arguments()

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

# Ensure num_ctx is always set (default 2048)
if 'num_ctx' not in OLLAMA_OPTIONS:
    OLLAMA_OPTIONS['num_ctx'] = 2048

# Apply GPU profile override from CLI
if CLI_ARGS.gpu_profile:
    _gpu_profiles = get_config('gpu_profiles', {})
    _profile = _gpu_profiles.get(CLI_ARGS.gpu_profile)
    if _profile:
        OLLAMA_OPTIONS = _profile.copy()
        logging.info(f"Applied GPU profile '{CLI_ARGS.gpu_profile}': {OLLAMA_OPTIONS}")

TITLE_MAX_LENGTH = classify_config.get('title_max_length', 500)
TEXT_MAX_LENGTH = classify_config.get('text_max_length', 1500)
OVERALL_OPERATION_TIMEOUT_SECONDS = classify_config.get('timeout_seconds', 60)
API_REQUEST_TIMEOUT_SECONDS = classify_config.get('api_request_timeout', 45)

# --- LOGGING SYSTEM CONFIGURATION ---
from gdphub.utils.logging import setup_logging
setup_logging("2_classify_text")

# --- OLLAMA CLIENT INITIALIZATION ---
# Bootstrap a temporary client purely so the interactive `select_ollama_model`
# below can list models. The full ChatService (with timeouts and model-profile
# adjustments) is constructed after model selection.
try:
    ollama_client = ollama.Client(host=OLLAMA_URL, timeout=API_REQUEST_TIMEOUT_SECONDS)
    logging.info(f"Ollama client initialized for URL: {OLLAMA_URL} with API timeout of {API_REQUEST_TIMEOUT_SECONDS}s.")
except Exception as e:
    logging.error(f"Cannot create Ollama client: {e}", exc_info=True)
    sys.exit(1)

# --- UTILITY FUNCTIONS ---
from gdphub.utils.text import clean_text  # noqa: F401  re-exported for module-internal calls

def get_ollama_models(client: ollama.Client) -> list[str]:
    """Retrieves the list of available Ollama models using the native client API."""
    try:
        response = client.list()
        models_list = response.get("models", [])
        available_models = [m.get("model") or m.get("name") for m in models_list]
        available_models = [m for m in available_models if m]
        logging.info(f"Available models from Ollama: {available_models}")
        return available_models
    except Exception as e:
        logging.error(f"Unexpected error retrieving Ollama models: {e}", exc_info=True)
        return []

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

# --- MODEL PROFILE: ADAPT OPTIONS AND TIMEOUTS TO THE SELECTED MODEL ---
# Centralized in services.ollama_client.ChatService — preserves the previous
# behavior (model-profile multipliers, GPU-profile override, think-tag
# stripping, threaded timeout) without inlining 80 LOC of scaffolding.
from gdphub.services.ollama_client import ChatService

_chat_service = ChatService.from_config(
    section='classify_text.py',
    cli_model=ACTIVE_OLLAMA_MODEL,
    cli_gpu_profile=CLI_ARGS.gpu_profile,
    cli_no_think=CLI_ARGS.no_think,
    default_model=OLLAMA_MODEL_DEFAULT,
    default_timeout=OVERALL_OPERATION_TIMEOUT_SECONDS,
    default_api_timeout=API_REQUEST_TIMEOUT_SECONDS,
)

# Re-export the post-profile values for any downstream caller that imported
# them (preserved for zero-regression with external CLI invocations / tests).
MODEL_PROFILE = get_model_profile(ACTIVE_OLLAMA_MODEL)
OLLAMA_OPTIONS = _chat_service.options
OVERALL_OPERATION_TIMEOUT_SECONDS = _chat_service.operation_timeout
API_REQUEST_TIMEOUT_SECONDS = _chat_service.api_timeout
ollama_client = _chat_service.client
AUTO_DISABLE_THINKING = _chat_service.disable_thinking

# --- CORE LOGIC FOR OLLAMA REQUESTS WITH TIMEOUT ---
# Thin shim that preserves the historical signature while delegating the
# threading + think-tag handling to ``services.ollama_client.ChatService``.
def _execute_ollama_request_with_timeout(
    client: ollama.Client,
    prompt_content: str,
    model_name: str,
    ollama_api_options: dict,
    operation_timeout: int,
    error_message_default: str,
    log_context_description: str,
    disable_thinking: bool = False,
    response_format: str | None = None,
) -> tuple[str, float]:
    """Execute a chat request via the centralized ChatService.

    The ``client``/``model_name``/``disable_thinking`` arguments are preserved
    for back-compat. ``ChatService`` handles model targeting and ``think=False``
    fallback internally; the per-call ``ollama_api_options`` override is
    routed through :meth:`ChatService.chat_options`.
    """
    return _chat_service.chat_options(
        prompt_content,
        options=ollama_api_options,
        error_default=error_message_default,
        log_context=log_context_description,
        operation_timeout=operation_timeout,
        response_format=response_format,
    )

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
        disable_thinking=AUTO_DISABLE_THINKING,
    )

    # Final cleanup of common LLM artifacts (quotes, dots, etc)
    final_value = raw_result.strip().replace('"', '').replace("'", "")
    final_value = re.sub(r'^[^\w]+|[^\w]+$', '', final_value)
    
    return (final_value if final_value else error_default, elapsed)

# --- COMBINED CLASSIFICATION (SINGLE OLLAMA CALL FOR BOTH FIELDS) ---
def classify_document_combined(
    title: str,
    text_content: str,
    timeout_sec: int = OVERALL_OPERATION_TIMEOUT_SECONDS,
) -> tuple[str, float, str, float]:
    """Returns (generic_type, elapsed_s, short_description, elapsed_s) in one Ollama call.

    Merges the two separate classification prompts into a single JSON request,
    halving the number of model round-trips per document.
    """
    if not text_content.strip() and not title.strip():
        return ("Text not available", 0.0, "Text not available", 0.0)

    cleaned_title = clean_text(title)[:TITLE_MAX_LENGTH]
    cleaned_text = clean_text(text_content)[:TEXT_MAX_LENGTH]

    prompt = (
        "Analyze this document. Respond ONLY with a valid JSON object — no explanation, no markdown.\n\n"
        f"TITLE: {cleaned_title}\n"
        f"TEXT: {cleaned_text}\n\n"
        'Return exactly: {"type": "<1 to 3 words classifying the document>", "description": "<max 10 words describing it>"}'
    )

    raw_result, elapsed = _execute_ollama_request_with_timeout(
        client=ollama_client,
        prompt_content=prompt,
        model_name=ACTIVE_OLLAMA_MODEL,
        ollama_api_options=OLLAMA_OPTIONS,
        operation_timeout=timeout_sec,
        error_message_default='{"type": "Classification error", "description": "Classification error"}',
        log_context_description="combined classification",
        disable_thinking=AUTO_DISABLE_THINKING,
        response_format="json",
    )

    generic = "Classification error"
    description = "Classification error"
    try:
        parsed = json.loads(raw_result)
        generic = str(parsed.get("type", generic)).strip()
        description = str(parsed.get("description", description)).strip()
    except (json.JSONDecodeError, TypeError):
        logging.warning(f"Could not parse combined classification JSON: {raw_result[:120]}")
        # Best-effort: treat the raw text as the generic type
        generic = raw_result.strip().split('\n')[0][:80] or generic
        description = generic

    def _clean(val: str) -> str:
        val = val.replace('"', '').replace("'", "")
        return re.sub(r'^[^\w]+|[^\w]+$', '', val)

    generic = _clean(generic) or "Classification error"
    description = _clean(description) or "Classification error"
    # elapsed is shared — store it against the first field, zero for the second
    return (generic, elapsed, description, 0.0)


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
                        generic_class, generic_time, short_desc, short_time = classify_document_combined(
                            current_file_name, extracted_text, OVERALL_OPERATION_TIMEOUT_SECONDS
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
