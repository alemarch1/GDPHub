"""Unified cleanup utility for GDPHub databases (SQLite + ChromaDB).

Interactive menu when run without arguments:
    python -m gdphub.utils.clean

Non-interactive via flags:
    python -m gdphub.utils.clean --sqlite
    python -m gdphub.utils.clean --vector-list
    python -m gdphub.utils.clean --vector-clear classifications
    python -m gdphub.utils.clean --vector-clear mappings
    python -m gdphub.utils.clean --vector-clear all
    python -m gdphub.utils.clean --vector-delete-id <id> --collection classifications
    python -m gdphub.utils.clean --all
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from gdphub.core.database import DB_FILE

CHROMA_DIR = DB_FILE.parent / "chromadb"

VECTOR_ALIASES = {
    "classifications": "manual_classifications",
    "mappings": "manual_ropa_mappings",
}

SQLITE_KEEP_TABLES = {"configuration", "ropa_record", "sqlite_sequence"}


# ── SQLite operations ────────────────────────────────────────────────

def sqlite_clean():
    if not DB_FILE.exists():
        print(f"  Database not found at: {DB_FILE}")
        return
    print(f"  Database: {DB_FILE}")
    conn = sqlite3.connect(str(DB_FILE))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]

    for table in tables:
        if table in SQLITE_KEEP_TABLES:
            print(f"    [SKIP]   {table}")
            continue
        try:
            cursor.execute(f'DELETE FROM "{table}"')
            print(f"    [ERASED] {table}")
        except sqlite3.OperationalError as e:
            print(f"    [ERROR]  {table}: {e}")

    try:
        cursor.execute("UPDATE sqlite_sequence SET seq = 0 WHERE name = 'document_lifecycle'")
        if cursor.rowcount > 0:
            print("    [RESET]  sqlite_sequence for document_lifecycle")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()
    print("  SQLite cleanup complete.")


# ── ChromaDB operations ──────────────────────────────────────────────

def _get_chroma_client():
    import chromadb
    if not CHROMA_DIR.exists():
        print(f"  ChromaDB directory not found at {CHROMA_DIR}")
        return None
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def _resolve(alias: str) -> str:
    return VECTOR_ALIASES.get(alias, alias)


def vector_list():
    client = _get_chroma_client()
    if client is None:
        return
    print(f"  ChromaDB: {CHROMA_DIR}")
    collections = client.list_collections()
    if not collections:
        print("  No collections found.")
        return
    for col in collections:
        name = col if isinstance(col, str) else col.name
        collection = client.get_collection(name)
        count = collection.count()
        print(f"\n  Collection: {name}  ({count} entries)")
        if count == 0:
            continue
        peek = collection.peek(limit=min(count, 5))
        for i, doc_id in enumerate(peek["ids"]):
            meta = peek["metadatas"][i] if peek["metadatas"] else {}
            preview = (peek["documents"][i][:80] + "...") if peek["documents"] and peek["documents"][i] else ""
            print(f"    [{doc_id}] {meta}")
            if preview:
                print(f"      text: {preview}")


def vector_clear(alias: str):
    client = _get_chroma_client()
    if client is None:
        return
    name = _resolve(alias)
    try:
        client.delete_collection(name)
        print(f"  Deleted collection '{name}'.")
    except Exception:
        print(f"  Collection '{name}' does not exist.")


def vector_clear_all():
    vector_clear("classifications")
    vector_clear("mappings")


def vector_delete_entry(alias: str, entry_id: str):
    client = _get_chroma_client()
    if client is None:
        return
    name = _resolve(alias)
    try:
        col = client.get_collection(name)
    except Exception:
        print(f"  Collection '{name}' does not exist.")
        return
    existing = col.get(ids=[entry_id])
    if not existing["ids"]:
        print(f"  No entry with id '{entry_id}' in '{name}'.")
        return
    col.delete(ids=[entry_id])
    print(f"  Deleted '{entry_id}' from '{name}'. Remaining: {col.count()}")


# ── Interactive menu ─────────────────────────────────────────────────

MENU = """
=========================================
   GDPHub Database Cleanup Utility
=========================================
  SQLite DB : {db}
  ChromaDB  : {chroma}
=========================================

  [1] Clean SQLite (erase documents, classifications, mappings, lifecycle)
  [2] Clean Vector DB — classifications
  [3] Clean Vector DB — ROPA mappings
  [4] Clean Vector DB — all collections
  [5] List Vector DB contents
  [6] Delete single Vector DB entry
  [7] Clean EVERYTHING (SQLite + Vector DB)
  [0] Exit
"""


def interactive():
    print(MENU.format(db=DB_FILE, chroma=CHROMA_DIR))
    choice = input("Choose an option: ").strip()

    if choice == "1":
        confirm = input("Erase all document data from SQLite? (y/N): ").strip()
        if confirm.lower() == "y":
            sqlite_clean()
        else:
            print("  Aborted.")

    elif choice == "2":
        vector_clear("classifications")

    elif choice == "3":
        vector_clear("mappings")

    elif choice == "4":
        vector_clear_all()

    elif choice == "5":
        vector_list()

    elif choice == "6":
        col = input("  Collection (classifications / mappings): ").strip()
        if col not in VECTOR_ALIASES:
            print("  Invalid collection.")
            return
        entry_id = input("  Entry ID to delete: ").strip()
        if entry_id:
            vector_delete_entry(col, entry_id)
        else:
            print("  No ID provided.")

    elif choice == "7":
        confirm = input("Erase ALL data (SQLite + Vector DB)? (y/N): ").strip()
        if confirm.lower() == "y":
            print("\n-- SQLite --")
            sqlite_clean()
            print("\n-- Vector DB --")
            vector_clear_all()
        else:
            print("  Aborted.")

    elif choice == "0":
        print("Bye.")

    else:
        print("Invalid choice.")


# ── CLI entry point ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Unified cleanup for GDPHub SQLite and ChromaDB databases",
    )
    parser.add_argument("--sqlite", action="store_true",
                        help="Clean SQLite (erase documents, classifications, lifecycle)")
    parser.add_argument("--vector-list", action="store_true",
                        help="List all ChromaDB collections and entries")
    parser.add_argument("--vector-clear", choices=["classifications", "mappings", "all"],
                        help="Delete a ChromaDB collection (or both)")
    parser.add_argument("--vector-delete-id", type=str,
                        help="Delete a single ChromaDB entry by ID")
    parser.add_argument("--collection", choices=["classifications", "mappings"],
                        help="Target collection for --vector-delete-id")
    parser.add_argument("--all", action="store_true",
                        help="Clean everything (SQLite + all ChromaDB collections)")
    args = parser.parse_args()

    has_flags = any([args.sqlite, args.vector_list, args.vector_clear,
                     args.vector_delete_id, args.all])

    if not has_flags:
        interactive()
        return

    if args.all:
        print("-- SQLite --")
        sqlite_clean()
        print("\n-- Vector DB --")
        vector_clear_all()
        return

    if args.sqlite:
        sqlite_clean()

    if args.vector_list:
        vector_list()

    if args.vector_clear:
        if args.vector_clear == "all":
            vector_clear_all()
        else:
            vector_clear(args.vector_clear)

    if args.vector_delete_id:
        if not args.collection:
            print("Error: --collection is required with --vector-delete-id")
            sys.exit(1)
        vector_delete_entry(args.collection, args.vector_delete_id)


if __name__ == "__main__":
    main()
