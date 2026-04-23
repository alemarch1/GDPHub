# Database engine configuration and session management for GDPHub.
# Uses SQLite with WAL mode for concurrent read access and SQLModel/SQLAlchemy ORM.
# The database path is resolved from config.json at import time.

import json
from pathlib import Path
from sqlalchemy import event
from sqlmodel import create_engine, Session, SQLModel
import models # ensure tables are registered

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONFIG_FILE = SCRIPT_DIR / "config.json"

def _get_db_path():
    """Resolves the database file path from config.json, with a fallback default."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            c = json.load(f)
            # Use database_folder as the DB root to match the typical 
            # output location for users
            out = c.get("database_folder", "./data/output")
            path = PROJECT_ROOT / out
            path.mkdir(parents=True, exist_ok=True)
            return path / "GDPHub.db"
    except Exception:
        path = PROJECT_ROOT / "data" / "output"
        path.mkdir(parents=True, exist_ok=True)
        return path / "GDPHub.db"

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
