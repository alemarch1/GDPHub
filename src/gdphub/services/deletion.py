# Secure document deletion service for GDPHub.
# Orchestrates the three-phase deletion workflow: Cloud (Gmail/Outlook trash),
# Filesystem (local file removal), and Database (record cleanup).
# Supports both automated batch runs and manual per-document deletion.

import os
import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import update, and_
from sqlmodel import Session, select
from gdphub.core.models import Document, DocumentLifecycle, ProcessedEmail
from gdphub.core.config_manager import get_config

# --- GMAIL API WRAPPER ---
class GoogleAPIJanitor:
    """
    Internal wrapper to handle Gmail trashing via the official API service.
    """
    def __init__(self, service):
        self.service = service

    def trash_message(self, msg_id: str):
        """Moves a specific message to the Gmail Trash (idempotent target)."""
        self.service.users().messages().trash(userId='me', id=msg_id).execute()


# --- OUTLOOK / MICROSOFT GRAPH API WRAPPER ---
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


# --- JANITOR CLOUD SERVICE FACTORY ---
def _get_janitor_for_source(source: str):
    """
    Returns the appropriate cloud API janitor based on the email source.
    Falls back to the active_source config if source is not specified.
    """
    if source == "outlook":
        try:
            from gdphub.utils.outlook import get_outlook_service
            client = get_outlook_service()
            return OutlookAPIJanitor(client)
        except Exception as e:
            logging.error(f"Failed to initialize Outlook API for janitor: {e}")
            return None
    else:
        # Default: Gmail
        try:
            from gdphub.utils.gmail import get_gmail_service
            service = get_gmail_service()
            return GoogleAPIJanitor(service)
        except Exception as e:
            logging.error(f"Failed to initialize Gmail API for janitor: {e}")
            return None


# --- INTERNAL PHASE HELPERS ---

def _select_targets(
    db_session: Session,
    specific_document_ids: list[str] | None,
    ignore_status: bool,
) -> list[DocumentLifecycle]:
    """Phase 1 — selection.

    Returns the lifecycle rows eligible for deletion, replicating the original
    three-mode logic: explicit IDs (with optional status bypass) or the
    automated past-due batch.
    """
    if specific_document_ids is not None:
        if ignore_status:
            stmt = select(DocumentLifecycle).where(
                DocumentLifecycle.document_id.in_(specific_document_ids)  # type: ignore[attr-defined]
            )
        else:
            stmt = select(DocumentLifecycle).where(
                and_(
                    DocumentLifecycle.document_id.in_(specific_document_ids),  # type: ignore[attr-defined]
                    DocumentLifecycle.status == "PENDING",  # type: ignore[arg-type]
                )
            )
    else:
        stmt = select(DocumentLifecycle).where(
            and_(
                DocumentLifecycle.status == "PENDING",  # type: ignore[arg-type]
                DocumentLifecycle.scheduled_deletion_date <= datetime.now(timezone.utc),  # type: ignore[arg-type]
            )
        )
    return list(db_session.exec(stmt).all())


def _lock_targets(db_session: Session, targets: list[DocumentLifecycle]) -> None:
    """Phase 2 — concurrency control. Move all selected rows to PROCESSING."""
    target_ids = [t.document_id for t in targets]
    db_session.execute(
        update(DocumentLifecycle)
        .where(DocumentLifecycle.document_id.in_(target_ids))  # type: ignore[attr-defined]
        .values(status="PROCESSING")
    )
    db_session.commit()


def _resolve_source(db_session: Session, parent_id: str, default_source: str) -> str:
    """Look up the email-source for a given parent message id, with fallback."""
    processed_rec = db_session.exec(
        select(ProcessedEmail).where(ProcessedEmail.id == parent_id)
    ).first()
    if processed_rec and processed_rec.source:
        return processed_rec.source
    return default_source


def _delete_cloud(janitor, parent_id: str) -> None:
    """Phase A — cloud trash. Idempotent on 404/not-found."""
    if janitor is None:
        logging.warning(f"No cloud janitor available, skipping cloud deletion for {parent_id}.")
        return
    try:
        janitor.trash_message(parent_id)
    except Exception as cloud_err:
        err_msg = str(cloud_err).lower()
        if "404" in err_msg or "not found" in err_msg:
            logging.info(f"Cloud message {parent_id} already deleted (404).")
            return
        raise


def _delete_filesystem(file_path: str) -> None:
    """Phase B — filesystem removal. Tolerates missing files."""
    if not file_path:
        return
    try:
        p = os.path.normpath(file_path)
        if os.path.exists(p):
            os.remove(p)
    except FileNotFoundError:
        logging.info(f"File {file_path} already absent from filesystem.")


def _delete_database(db_session: Session, doc: Document, lifecycle_rec: DocumentLifecycle) -> None:
    """Phase C — database atomic update. Marks lifecycle DELETED and drops document row."""
    with db_session.begin_nested():
        lifecycle_rec.status = "DELETED"
        lifecycle_rec.actual_deletion_date = datetime.now(timezone.utc)
        db_session.add(lifecycle_rec)
        db_session.delete(doc)


def _record_failure(
    db_session: Session,
    lifecycle_rec: DocumentLifecycle,
    note: str,
    results: dict,
    error_str: str | None = None,
) -> None:
    """Mark a lifecycle row FAILED with a note; update result counters."""
    lifecycle_rec.status = "FAILED"
    lifecycle_rec.notes = note
    db_session.add(lifecycle_rec)
    db_session.commit()
    results["failed"] += 1
    if error_str is not None:
        results["errors"].append({"id": lifecycle_rec.document_id, "error": error_str})


# --- MAIN DELETION WORKFLOW ---

def execute_deletion_workflow(
    db_session: Session,
    specific_document_ids: list[str] | None = None,
    ignore_status: bool = False,
) -> dict:
    """Janitor service — Cloud → Filesystem → Database, three-phase deletion.

    Public contract preserved exactly:
      * Args: ``db_session``, ``specific_document_ids`` (list or None),
        ``ignore_status`` (bool — only meaningful when IDs are supplied).
      * Returns: ``{"success": int, "failed": int, "errors": [{"id", "error"}]}``.
      * Status transitions: ``PENDING → PROCESSING → DELETED | FAILED``.
      * Idempotent on cloud 404, filesystem-missing, and missing source rows.

    Internal structure now decomposed into ``_select_targets``, ``_lock_targets``,
    per-phase helpers, and ``_record_failure`` for testability.
    """
    results: dict = {"success": 0, "failed": 0, "errors": []}

    targets = _select_targets(db_session, specific_document_ids, ignore_status)
    if not targets:
        return results

    _lock_targets(db_session, targets)

    active_source = get_config("active_source", "gmail")
    janitor_cache: dict[str, object] = {}

    def get_janitor(source: str):
        if source not in janitor_cache:
            janitor_cache[source] = _get_janitor_for_source(source)
        return janitor_cache[source]

    for lifecycle_rec in targets:
        doc = db_session.exec(
            select(Document).where(Document.id == lifecycle_rec.document_id)
        ).first()
        if not doc:
            _record_failure(
                db_session,
                lifecycle_rec,
                "Technical Error: Source 'document' record missing.",
                results,
            )
            continue

        try:
            if doc.parent_id:
                source = _resolve_source(db_session, doc.parent_id, active_source)
                _delete_cloud(get_janitor(source), doc.parent_id)

            _delete_filesystem(doc.file_path)
            _delete_database(db_session, doc, lifecycle_rec)
            db_session.commit()

            results["success"] += 1
            logging.info(f"Successfully deleted document {doc.id} (Type: {doc.type})")

        except Exception as err:
            db_session.rollback()
            err_str = str(err)
            logging.error(f"Janitor failed for Document {lifecycle_rec.document_id}: {err_str}")
            _record_failure(
                db_session,
                lifecycle_rec,
                f"Janitor Error: {err_str}",
                results,
                error_str=err_str,
            )

    return results
