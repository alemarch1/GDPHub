# Database engine configuration and session management for GDPHub.
# Uses SQLite with WAL mode for concurrent read access and SQLModel/SQLAlchemy ORM.
# The database path is resolved from config.json at import time.

import json
import os
from pathlib import Path
from sqlalchemy import event, text
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
    
    with get_session() as session:
        # 1. Drop old single-action trigger
        session.execute(text("DROP TRIGGER IF EXISTS trg_calculate_lifecycle"))
        
        # 2. Create insert trigger (when no lifecycle record exists yet)
        session.execute(text("""
            CREATE TRIGGER IF NOT EXISTS trg_calculate_lifecycle_insert
            AFTER INSERT ON "document_ropa_mapping"
            FOR EACH ROW
            WHEN NOT EXISTS (SELECT 1 FROM document_lifecycle WHERE document_id = NEW.document_id)
            BEGIN
                INSERT INTO document_lifecycle (document_id, document_type, creation_date, scheduled_deletion_date, status)
                SELECT 
                    NEW.document_id,
                    (SELECT type FROM document WHERE id = NEW.document_id),
                    (SELECT creation_date FROM document WHERE id = NEW.document_id),
                    COALESCE(datetime((SELECT creation_date FROM document WHERE id = NEW.document_id), r.retention_periods), datetime('now', '+100 years')),
                    'PENDING'
                FROM "ropa_record" r
                WHERE r.id = NEW.ropa_id;
            END
        """))
        
        # 3. Create update trigger (when lifecycle record already exists, keeping the maximum retention date)
        session.execute(text("""
            CREATE TRIGGER IF NOT EXISTS trg_calculate_lifecycle_update
            AFTER INSERT ON "document_ropa_mapping"
            FOR EACH ROW
            WHEN EXISTS (SELECT 1 FROM document_lifecycle WHERE document_id = NEW.document_id)
            BEGIN
                UPDATE document_lifecycle
                SET scheduled_deletion_date = CASE 
                    WHEN scheduled_deletion_date < COALESCE(datetime(creation_date, (SELECT retention_periods FROM ropa_record WHERE id = NEW.ropa_id)), datetime('now', '+100 years'))
                    THEN COALESCE(datetime(creation_date, (SELECT retention_periods FROM ropa_record WHERE id = NEW.ropa_id)), datetime('now', '+100 years'))
                    ELSE scheduled_deletion_date
                END
                WHERE document_id = NEW.document_id;
            END
        """))
        
        # 4. Recalculate lifecycle on ROPA mapping UPDATE (e.g. manual corrections)
        session.execute(text("DROP TRIGGER IF EXISTS trg_recalc_lifecycle_on_update"))
        session.execute(text("""
            CREATE TRIGGER IF NOT EXISTS trg_recalc_lifecycle_on_update
            AFTER UPDATE OF ropa_id ON "document_ropa_mapping"
            FOR EACH ROW
            WHEN EXISTS (SELECT 1 FROM document_lifecycle WHERE document_id = NEW.document_id)
            BEGIN
                UPDATE document_lifecycle
                SET scheduled_deletion_date = COALESCE(
                    (SELECT MAX(COALESCE(
                        datetime(dl.creation_date, r.retention_periods),
                        datetime('now', '+100 years')
                    ))
                    FROM document_ropa_mapping m
                    JOIN ropa_record r ON r.id = m.ropa_id
                    JOIN document_lifecycle dl ON dl.document_id = m.document_id
                    WHERE m.document_id = NEW.document_id AND m.ropa_id IS NOT NULL),
                    datetime('now', '+100 years')
                )
                WHERE document_id = NEW.document_id;
            END
        """))

        # 5. Recalculate lifecycle on ROPA mapping DELETE (e.g. --force re-run)
        #    When all mappings are deleted, we keep the existing date unchanged
        #    rather than inflating to +100 years (the pipeline will re-insert
        #    new mappings and the INSERT trigger will recalculate properly).
        session.execute(text("DROP TRIGGER IF EXISTS trg_recalc_lifecycle_on_delete"))
        session.execute(text("""
            CREATE TRIGGER IF NOT EXISTS trg_recalc_lifecycle_on_delete
            AFTER DELETE ON "document_ropa_mapping"
            FOR EACH ROW
            WHEN EXISTS (SELECT 1 FROM document_lifecycle WHERE document_id = OLD.document_id)
              AND EXISTS (SELECT 1 FROM document_ropa_mapping
                          WHERE document_id = OLD.document_id AND ropa_id IS NOT NULL
                            AND id != OLD.id)
            BEGIN
                UPDATE document_lifecycle
                SET scheduled_deletion_date = (
                    SELECT MAX(COALESCE(
                        datetime(dl.creation_date, r.retention_periods),
                        datetime('now', '+100 years')
                    ))
                    FROM document_ropa_mapping m
                    JOIN ropa_record r ON r.id = m.ropa_id
                    JOIN document_lifecycle dl ON dl.document_id = m.document_id
                    WHERE m.document_id = OLD.document_id AND m.ropa_id IS NOT NULL
                      AND m.id != OLD.id
                )
                WHERE document_id = OLD.document_id;
            END
        """))

        # 6. Consolidate any existing duplicate lifecycle records
        duplicates = session.execute(text(
            "SELECT document_id FROM document_lifecycle GROUP BY document_id HAVING count(*) > 1"
        )).all()
        
        if duplicates:
            from gdphub.core.models import DocumentLifecycle
            from sqlmodel import select
            
            for row in duplicates:
                doc_id = row[0]
                records = session.exec(
                    select(DocumentLifecycle)
                    .where(DocumentLifecycle.document_id == doc_id)
                    .order_by(DocumentLifecycle.scheduled_deletion_date.desc())
                ).all()
                
                # Keep the first record (latest date), delete the rest
                for delete_rec in records[1:]:
                    session.delete(delete_rec)
                    
        session.commit()

def recalculate_lifecycle(session, document_id: str):
    """Recalculates the scheduled_deletion_date for a document based on all its ROPA mappings.

    Uses the MAX retention period across all models' mappings (all-models-combined).
    This is a Python safety net alongside the SQLite triggers.
    """
    from gdphub.core.models import DocumentLifecycle, DocumentRopaMapping, RopaRecord, Document
    from sqlmodel import select
    from datetime import datetime as dt

    lifecycle = session.exec(
        select(DocumentLifecycle).where(DocumentLifecycle.document_id == document_id)
    ).first()
    if not lifecycle:
        return

    mappings = session.exec(
        select(DocumentRopaMapping).where(
            DocumentRopaMapping.document_id == document_id,
            DocumentRopaMapping.ropa_id.isnot(None),  # type: ignore[union-attr]
        )
    ).all()

    if not mappings:
        # No valid mappings — set far-future fallback
        lifecycle.scheduled_deletion_date = dt(2126, 1, 1)
        session.add(lifecycle)
        return

    max_date = None
    for m in mappings:
        ropa = session.get(RopaRecord, m.ropa_id)
        if ropa and lifecycle.creation_date:
            try:
                # Use SQLite datetime arithmetic via raw query
                result = session.execute(
                    text("SELECT datetime(:creation, :retention)"),
                    {"creation": str(lifecycle.creation_date), "retention": ropa.retention_periods},
                ).scalar()
                if result:
                    candidate = dt.fromisoformat(result)
                    if max_date is None or candidate > max_date:
                        max_date = candidate
            except Exception:
                pass

    if max_date:
        lifecycle.scheduled_deletion_date = max_date
    else:
        lifecycle.scheduled_deletion_date = dt(2126, 1, 1)
    session.add(lifecycle)


def get_session():
    """Returns a new SQLModel database session."""
    return Session(engine)

if __name__ == "__main__":
    create_db_and_tables()
    print(f"Database initialized at {DB_FILE}")
