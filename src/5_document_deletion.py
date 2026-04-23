# This script acts as the automated 'Janitor' for the GDPHub pipeline.
# It identifies documents that have reached their scheduled deletion date
# and securely removes them from the Cloud (Gmail), Filesystem, and Database.

import sys
import logging
import argparse
from pathlib import Path
from database import get_session, create_db_and_tables
from deletion_service import execute_deletion_workflow
from utils_logging import setup_logging

# --- MAIN EXECUTION LOGIC ---
def main():
    """Main entry point for the GDPHub Janitor service."""
    # --- ARGUMENT PARSING ---
    parser = argparse.ArgumentParser(description="GDPHub Janitor - Secure Document Deletion Service")
    parser.add_argument(
        "--ids", 
        nargs="+", 
        help="Specific Document IDs (UUIDs) to delete manually. If omitted, the script deletes all expired documents."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force deletion even if the status is not PENDING (only for manual runs)."
    )
    args = parser.parse_args()

    # --- LOGGING SYSTEM CONFIGURATION ---
    setup_logging("5_document_deletion")
    
    if args.ids:
        logging.info(f"Starting manual Janitor run for {len(args.ids)} specific document(s)...")
    else:
        logging.info("Starting automated Janitor run (cleaning all expired documents)...")
    
    # Ensure tables exist
    try:
        create_db_and_tables()
    except Exception as db_init_err:
        logging.critical(f"Database initialization failed: {db_init_err}")
        sys.exit(1)

    # --- WORKFLOW EXECUTION ---
    try:
        with get_session() as session:
            # Pass args.ids (will be None if not provided, triggering batch mode)
            summary = execute_deletion_workflow(
                db_session=session, 
                specific_document_ids=args.ids,
                ignore_status=args.force
            )
            
            # --- RESULT REPORTING ---
            success_count = summary.get("success", 0)
            failed_count = summary.get("failed", 0)
            
            if success_count == 0 and failed_count == 0:
                logging.info("Janitor finished: No documents were processed.")
            else:
                logging.info(f"Janitor finished. Success: {success_count}, Failed: {failed_count}.")
                
            if failed_count > 0:
                for err in summary.get("errors", []):
                    logging.warning(f"  - Deletion Failed for Doc ID: {err['id']} | Error: {err['error']}")

    except PermissionError as perm_err:
        # This specifically catches the 'ReadOnly' scope error from utils_gmail
        logging.critical(f"FATAL PERMISSION ERROR: {perm_err}")
        print(f"\n[!] {perm_err}")
    except Exception as e:
        logging.error(f"Janitor process crashed due to an unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
