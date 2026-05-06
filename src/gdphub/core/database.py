# Database engine configuration and session management for GDPHub.
# Uses SQLite with WAL mode for concurrent read access and SQLModel/SQLAlchemy ORM.
# The database path is resolved from config.json at import time.

import json
import os
from pathlib import Path
from sqlalchemy import event
from sqlmodel import create_engine, Session, SQLModel
import gdphub.core.models as models  # noqa: F401  side-effect import — registers SQLModel tables on metadata

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
# Kept for backward-compat with ``seed_config.py`` (one-shot legacy import).
# The runtime no longer reads or writes this file.
CONFIG_FILE = PROJECT_ROOT / "src" / "config.json"

# Env var precedence (highest first):
#   1) ``GDPHUB_DB_FOLDER``                      — explicit override
#   2) ``./data/output`` (relative to project)   — built-in default
#
# Historically this path was read from ``config.json``. The dual-storage
# pattern caused chicken-and-egg bugs (DB path came from JSON, JSON path was
# rewritten from the DB after every config save). Switching to an env var
# eliminates the cycle without breaking existing installs (the default folder
# remains unchanged).
_DB_FOLDER_ENV = "GDPHUB_DB_FOLDER"


def _get_db_path() -> Path:
    """Resolve the SQLite file path. See module-level note for precedence."""
    folder = os.environ.get(_DB_FOLDER_ENV, "").strip()
    if folder:
        path = Path(folder)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
    else:
        # Legacy fallback: honor any ``config.json`` left over from older
        # installs so existing users don't lose access to their DB on upgrade.
        # New installs hit the project-relative default below.
        legacy = _read_legacy_config_json()
        if legacy:
            out = legacy.get("database_folder") or "./data/output"
            path = PROJECT_ROOT / out
        else:
            path = PROJECT_ROOT / "data" / "output"
    path.mkdir(parents=True, exist_ok=True)
    return path / "GDPHub.db"


def _read_legacy_config_json():
    """Best-effort read of a legacy ``config.json`` for backward compatibility.

    Returns ``None`` when the file does not exist or is malformed. This is
    the *only* runtime read of ``config.json`` left in the codebase; future
    installs should rely on the env var or the database-backed config.
    """
    try:
        if not CONFIG_FILE.exists():
            return None
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

DB_FILE = _get_db_path()

engine = create_engine(
    f"sqlite:///{DB_FILE.resolve()}",
    connect_args={"check_same_thread": False}
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Configures SQLite pragmas (WAL mode, foreign keys) on each new connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

def create_db_and_tables():
    """Creates all registered SQLModel tables if they don't already exist."""
    SQLModel.metadata.create_all(engine)

def get_session():
    """Returns a new SQLModel database session."""
    return Session(engine)

if __name__ == "__main__":
    create_db_and_tables()
    print(f"Database initialized at {DB_FILE}")
