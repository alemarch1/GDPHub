import logging
from logging.handlers import RotatingFileHandler
import sys
from pathlib import Path
from config_manager import get_config

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

def setup_logging(script_name):
    """
    Centralized logging configuration for GDPHub.
    Allows 4 levels: DEBUG, INFO, WARNING, ERROR.
    Level is read from the global configuration.
    """
    run_level_str = get_config("log_level", "INFO").upper()
    
    levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR
    }
    
    log_level = levels.get(run_level_str, logging.INFO)

    log_folder_path = get_config("log_folder", "logs")
    log_folder = PROJECT_ROOT / log_folder_path
    log_folder.mkdir(parents=True, exist_ok=True)
    log_file = log_folder / f"{script_name}.log"
    
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
        
    root_logger.setLevel(log_level)

    # 5MB is ~50000 lines. 500KB is ~5000 lines.
    file_handler = RotatingFileHandler(
        filename=str(log_file), 
        maxBytes=500 * 1024, 
        backupCount=1, 
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(processName)s - %(message)s'))
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter('%(levelname)s - %(processName)s - %(message)s'))
    root_logger.addHandler(console_handler)

    return root_logger
