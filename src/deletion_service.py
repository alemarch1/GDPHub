import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import update, and_
from sqlmodel import Session, select
from models import Document, DocumentLifecycle, ProcessedEmail
from config_manager import get_config

# --- Google API Wrapper ---
class GoogleAPIJanitor:
    """
    Internal wrapper to handle Gmail trashing via the official API service.
    """
    def __init__(self, service):
        self.service = service

    def trash_message(self, msg_id: str):
        """Moves a specific message to the Gmail Trash (idempotent target)."""
        self.service.users().messages().trash(userId='me', id=msg_id).execute()


# --- Microsoft Graph API Wrapper ---
class OutlookAPIJanitor:
    """
    Internal wrapper to handle Outlook message deletion via Microsoft Graph API.
    Moves messages to the Deleted Items folder.
    """
    def __init__(self, client):
        self.client = client

    def trash_message(self, msg_id: str):
        """Moves a specific message to the Outlook Deleted Items folder."""
        asyncio.run(self._async_trash_message(msg_id))

    async def _async_trash_message(self, msg_id: str):
        """Async helper: moves message to deletedItems folder via Graph."""
        from msgraph.generated.users.item.messages.item.move.move_post_request_body import MovePostRequestBody
        body = MovePostRequestBody()
        body.destination_id = "deleteditems"
        await self.client.me.messages.by_message_id(msg_id).move.post(body)


def _get_janitor_for_source(source: str):
    """
    Returns the appropriate cloud API janitor based on the email source.
    Falls back to the active_source config if source is not specified.
    """
    if source == "outlook":
        try:
            from utils_outlook import get_outlook_service
            client = get_outlook_service()
            return OutlookAPIJanitor(client)
        except Exception as e:
            logging.error(f"Failed to initialize Outlook API for janitor: {e}")
            return None
    else:
        # Default: Gmail
        try:
            from utils_gmail import get_gmail_service
            service = get_gmail_service()
            return GoogleAPIJanitor(service)
        except Exception as e:
            logging.error(f"Failed to initialize Gmail API for janitor: {e}")
            return None


def execute_deletion_workflow(db_session: Session, specific_document_ids: list[str] = None, ignore_status: bool = False) -> dict:
    """
    Janitor service to securely delete emails across Cloud, Filesystem, and Database.
    
    Workflow:
    1. Identification: Finds records past scheduled date or specific manual IDs.
    2. Concurrency Control: Locks records by moving them to 'PROCESSING' state.
    3. Deletion Sequence: Cloud (Trash) -> Filesystem (Remove) -> Database (Delete Row).
    """
    results = {"success": 0, "failed": 0, "errors": []}
    
    # Determine which cloud janitors we may need
    # We lazily initialize them per-document based on the source field
    active_source = get_config("active_source", "gmail")
    janitor_cache = {}

    def get_janitor(source: str):
        if source not in janitor_cache:
            janitor_cache[source] = _get_janitor_for_source(source)
        return janitor_cache[source]

    # 1. SCOPE IDENTIFICATION
    if specific_document_ids is not None:
        # Manual override: Process specific IDs provided by UI
        if ignore_status:
            # Delete regardless of status (e.g. even if ON_HOLD)
            stmt = select(DocumentLifecycle).where(
                DocumentLifecycle.document_id.in_(specific_document_ids)
            )
        else:
            # Respect PENDING status
            stmt = select(DocumentLifecycle).where(
                and_(
                    DocumentLifecycle.document_id.in_(specific_document_ids),
                    DocumentLifecycle.status == "PENDING"
                )
            )
    else:
        # Automated batch: Records past their scheduled deletion date
        stmt = select(DocumentLifecycle).where(
            and_(
                DocumentLifecycle.status == "PENDING",
                DocumentLifecycle.scheduled_deletion_date <= datetime.now(timezone.utc)
            )
        )

    targets = db_session.exec(stmt).all()
    if not targets:
        return results

    # 2. CONCURRENCY CONTROL
    # Immediate update to prevent other workers from grabbing the same records
    target_ids = [t.document_id for t in targets]
    db_session.execute(
        update(DocumentLifecycle)
        .where(DocumentLifecycle.document_id.in_(target_ids))
        .values(status="PROCESSING")
    )
    db_session.commit()

    # 3. IDEMPOTENT DELETION SEQUENCE
    for lifecycle_rec in targets:
        # Fetch the master document record
        doc = db_session.exec(select(Document).where(Document.id == lifecycle_rec.document_id)).first()
        
        if not doc:
            lifecycle_rec.status = "FAILED"
            lifecycle_rec.notes = "Technical Error: Source 'document' record missing."
            db_session.add(lifecycle_rec)
            db_session.commit()
            results["failed"] += 1
            continue

        try:
            # STEP A: Cloud Deletion (Gmail Trash or Outlook Deleted Items)
            if doc.parent_id:
                # Determine source from ProcessedEmail record, fall back to active_source
                source = active_source
                processed_rec = db_session.exec(
                    select(ProcessedEmail).where(ProcessedEmail.id == doc.parent_id)
                ).first()
                if processed_rec and processed_rec.source:
                    source = processed_rec.source

                janitor = get_janitor(source)
                if janitor:
                    try:
                        janitor.trash_message(doc.parent_id)
                    except Exception as cloud_err:
                        # Idempotency: 404 means already deleted, which is a success state for us
                        err_msg = str(cloud_err).lower()
                        if "404" in err_msg or "not found" in err_msg:
                            logging.info(f"Cloud message {doc.parent_id} already deleted (404).")
                        else:
                            raise cloud_err
                else:
                    logging.warning(f"No cloud janitor available for source '{source}', skipping cloud deletion.")

            # STEP B: Filesystem Deletion
            if doc.file_path:
                try:
                    p = os.path.normpath(doc.file_path)
                    if os.path.exists(p):
                        os.remove(p)
                except FileNotFoundError:
                    logging.info(f"File {doc.file_path} already absent from filesystem.")
                except Exception as fs_err:
                    raise fs_err

            # STEP C: Database Atomic Transaction
            with db_session.begin_nested():
                # Mark lifecycle as completed
                lifecycle_rec.status = "DELETED"
                lifecycle_rec.actual_deletion_date = datetime.now(timezone.utc)
                db_session.add(lifecycle_rec)
                
                # Permanently remove the document from the master table
                db_session.delete(doc)
            
            db_session.commit()
            results["success"] += 1
            logging.info(f"Successfully deleted document {doc.id} (Type: {doc.type})")

        except Exception as err:
            db_session.rollback()
            err_str = str(err)
            logging.error(f"Janitor failed for Document {lifecycle_rec.document_id}: {err_str}")
            
            lifecycle_rec.status = "FAILED"
            lifecycle_rec.notes = f"Janitor Error: {err_str}"
            db_session.add(lifecycle_rec)
            db_session.commit()
            
            results["failed"] += 1
            results["errors"].append({"id": lifecycle_rec.document_id, "error": err_str})

    return results
