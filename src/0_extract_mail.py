# This module connects securely to your email mailbox using either the
# Google Gmail API or the Microsoft Graph API (Outlook / Microsoft 365).
# Key functionalities:
# 1. Fetches emails continuously based on a configurable query.
# 2. Extracts both the text-based email body and any nested attachments.
# 3. Ensures files are uniquely named using message IDs and sub-ID sequences.
# 4. Logs processed message IDs into the database to prevent duplicate downloads.

import os
import sys
import json
import base64
import asyncio
import logging
import re
import email.utils
from pathlib import Path
from datetime import datetime, timedelta, timezone
from tqdm import tqdm
from database import get_session, create_db_and_tables
from models import ProcessedEmail
from sqlmodel import select

from config_manager import get_config

# --- CONFIGURATION AND PATHS ---
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


# --- GMAIL EXTRACTION ENGINE ---

def extract_gmail(mail_download_folder: Path, processed_emails: set,
                  mail_config: dict, query: str, max_emails: int,
                  ignore_processed: bool):
    """Extracts emails and attachments from a Gmail mailbox via Google API."""
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from utils_gmail import get_gmail_service

    try:
        service = get_gmail_service()
    except Exception as e:
        logging.error(f"Gmail authentication failed: {e}")
        sys.exit(1)

    logging.info(f"Querying Gmail with: '{query}'")

    try:
        results = service.users().messages().list(userId='me', q=query, maxResults=max_emails).execute()
        messages = results.get('messages', [])

        if not messages:
            logging.info('No messages found matching the query.')
            return

        logging.info(f"Found {len(messages)} potential messages.")

        if ignore_processed:
            logging.info("Override active: ignoring processed history. Fetching all matching messages again.")
            to_process = messages
        else:
            to_process = [m for m in messages if m['id'] not in processed_emails]

        if not to_process:
            logging.info("All found messages have already been processed.")
            return

        logging.info(f"Processing {len(to_process)} new messages...")

        for msg_info in tqdm(to_process, desc="Downloading Emails"):
            msg_id = msg_info['id']
            try:
                message = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
                payload = message.get('payload', {})

                # Extract Email Date for timestamp propagation
                headers = payload.get('headers', [])
                date_str = next((h['value'] for h in headers if h['name'].lower() == 'date'), None)
                email_timestamp = None
                if date_str:
                    try:
                        email_timestamp = email.utils.parsedate_to_datetime(date_str).timestamp()
                    except Exception as date_err:
                        logging.warning(f"Failed to parse email date '{date_str}': {date_err}")

                parts = [payload]
                if 'parts' in payload:
                    parts = payload['parts']

                plain_text_parts = []
                html_text_parts = []
                new_files_created = []
                attachment_counter = 1

                def process_parts(parts_list):
                    nonlocal attachment_counter
                    for part in parts_list:
                        mime_type = part.get('mimeType')
                        part_body = part.get('body', {})
                        part_data = part_body.get('data')

                        if part.get('parts'):
                            process_parts(part.get('parts'))

                        # Extract text
                        if not part.get('filename') and part_data:
                            if mime_type == 'text/plain':
                                text = base64.urlsafe_b64decode(part_data).decode('utf-8', errors='ignore')
                                if text: plain_text_parts.append(text)
                            elif mime_type == 'text/html':
                                text = base64.urlsafe_b64decode(part_data).decode('utf-8', errors='ignore')
                                if text: html_text_parts.append(text)

                        # Extract attachments
                        if part.get('filename'):
                            attachment_id = part_body.get('attachmentId')
                            if attachment_id:
                                try:
                                    att = service.users().messages().attachments().get(
                                        userId='me', messageId=msg_id, id=attachment_id).execute()
                                    file_data = base64.urlsafe_b64decode(att['data'])
                                    # Sanitize filename
                                    safe_name = "".join([c for c in part['filename'] if c.isalnum() or c in (' ', '.', '_', '-')]).strip()
                                    final_filename = f"email_{msg_id}_att_{attachment_counter}_{safe_name}"
                                    file_path = mail_download_folder / final_filename

                                    with open(file_path, 'wb') as f:
                                        f.write(file_data)

                                    if email_timestamp:
                                        os.utime(file_path, (email_timestamp, email_timestamp))

                                    new_files_created.append(final_filename)
                                    attachment_counter += 1
                                except Exception as att_err:
                                    logging.error(f"Error downloading attachment {part['filename']} in message {msg_id}: {att_err}")

                process_parts(parts)

                # Assemble Email Body
                body_text = ""
                if plain_text_parts:
                    body_text = "\n".join(plain_text_parts).strip()
                elif html_text_parts:
                    # Clean simple HTML
                    raw_html = "\n".join(html_text_parts)
                    raw_html = re.sub(r'<(script|style)\b[^>]*>.*?</\1\s*>', '', raw_html, flags=re.IGNORECASE | re.DOTALL)
                    body_text = re.sub(r'<[^>]+>', ' ', raw_html)
                    body_text = re.sub(r'\s+', ' ', body_text).strip()

                # Support single-part emails
                if not body_text and 'data' in payload.get('body', {}):
                    data = payload['body']['data']
                    body_text = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')

                if body_text.strip():
                    body_filename = f"email_{msg_id}_body.txt"
                    body_filepath = mail_download_folder / body_filename
                    with open(body_filepath, 'w', encoding='utf-8') as f:
                        f.write(body_text)

                    if email_timestamp:
                        os.utime(body_filepath, (email_timestamp, email_timestamp))

                    new_files_created.append(body_filename)

                # Save state to DB
                with get_session() as session:
                    session.add(ProcessedEmail(id=msg_id, source="gmail"))
                    session.commit()

                logging.info(f"Successfully processed email {msg_id}. Created {len(new_files_created)} files.")

            except Exception as msg_err:
                logging.error(f"Failed to process message {msg_id}: {msg_err}")

        logging.info(f"Gmail extraction complete. Payloads saved in: {mail_download_folder.resolve()}")

    except Exception as error:
        logging.error(f'An error occurred communicating with Gmail API: {error}')
        sys.exit(1)


# --- OUTLOOK / MICROSOFT GRAPH EXTRACTION ENGINE ---

def extract_outlook(mail_download_folder: Path, processed_emails: set,
                    outlook_config: dict, max_emails: int,
                    ignore_processed: bool):
    """Extracts emails and attachments from an Outlook mailbox via Microsoft Graph API."""
    from utils_outlook import get_outlook_service

    try:
        client = get_outlook_service()
    except Exception as e:
        logging.error(f"Outlook authentication failed: {e}")
        sys.exit(1)

    query_filter = outlook_config.get("query_filter", "isRead eq false")
    override_days = outlook_config.get("import_override_days", 0)

    # Apply date override to the OData filter
    if override_days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=override_days)).strftime('%Y-%m-%dT%H:%M:%SZ')
        if query_filter:
            query_filter = f"({query_filter}) and receivedDateTime ge {cutoff}"
        else:
            query_filter = f"receivedDateTime ge {cutoff}"

    logging.info(f"Querying Outlook with filter: '{query_filter}'")

    try:
        messages = asyncio.run(_fetch_outlook_messages(client, query_filter, max_emails))
    except Exception as e:
        logging.error(f"Error fetching Outlook messages: {e}")
        sys.exit(1)

    if not messages:
        logging.info("No Outlook messages found matching the query.")
        return

    logging.info(f"Found {len(messages)} potential messages.")

    if ignore_processed:
        logging.info("Override active: ignoring processed history.")
        to_process = messages
    else:
        to_process = [m for m in messages if m.id not in processed_emails]

    if not to_process:
        logging.info("All found messages have already been processed.")
        return

    logging.info(f"Processing {len(to_process)} new messages...")

    error_count = 0
    success_count = 0

    for msg in tqdm(to_process, desc="Downloading Outlook Emails"):
        msg_id = msg.id
        try:
            new_files_created = []

            # Extract timestamp from receivedDateTime
            email_timestamp = None
            if msg.received_date_time:
                try:
                    email_timestamp = msg.received_date_time.timestamp()
                except Exception:
                    pass

            # Extract body
            body_text = ""
            if msg.body:
                raw_content = msg.body.content or ""
                content_type = msg.body.content_type  # text or html

                if content_type and str(content_type).lower() == "text":
                    body_text = raw_content.strip()
                else:
                    # Strip HTML tags
                    cleaned = re.sub(r'<(script|style)\b[^>]*>.*?</\1\s*>', '', raw_content, flags=re.IGNORECASE | re.DOTALL)
                    body_text = re.sub(r'<[^>]+>', ' ', cleaned)
                    body_text = re.sub(r'\s+', ' ', body_text).strip()

            if body_text.strip():
                body_filename = f"email_{msg_id}_body.txt"
                body_filepath = mail_download_folder / body_filename
                with open(body_filepath, 'w', encoding='utf-8') as f:
                    f.write(body_text)

                if email_timestamp:
                    os.utime(body_filepath, (email_timestamp, email_timestamp))

                new_files_created.append(body_filename)

            # Download attachments
            if msg.has_attachments:
                try:
                    attachments = asyncio.run(_fetch_outlook_attachments(client, msg_id))
                    attachment_counter = 1
                    for att in attachments:
                        if att.content_bytes:
                            safe_name = "".join([c for c in (att.name or "attachment") if c.isalnum() or c in (' ', '.', '_', '-')]).strip()
                            final_filename = f"email_{msg_id}_att_{attachment_counter}_{safe_name}"
                            file_path = mail_download_folder / final_filename

                            with open(file_path, 'wb') as f:
                                f.write(att.content_bytes)

                            if email_timestamp:
                                os.utime(file_path, (email_timestamp, email_timestamp))

                            new_files_created.append(final_filename)
                            attachment_counter += 1
                except Exception as att_err:
                    logging.error(f"Error downloading attachments for message {msg_id}: {att_err}")

            # Save state to DB
            with get_session() as session:
                session.add(ProcessedEmail(id=msg_id, source="outlook"))
                session.commit()

            success_count += 1
            logging.info(f"Successfully processed Outlook email {msg_id}. Created {len(new_files_created)} files.")

        except Exception as msg_err:
            error_count += 1
            logging.error(f"Failed to process Outlook message {msg_id}: {msg_err}")

    logging.info(f"Outlook extraction complete. {success_count} succeeded, {error_count} failed. Payloads saved in: {mail_download_folder.resolve()}")

    if error_count > 0 and success_count == 0:
        logging.error(f"All {error_count} messages failed to process.")
        sys.exit(1)


# --- ASYNC HELPERS FOR MICROSOFT GRAPH ---
async def _fetch_outlook_messages(client, query_filter: str, max_emails: int):
    """Async helper: fetches messages from the user's Outlook inbox."""
    from msgraph.generated.users.item.messages.messages_request_builder import MessagesRequestBuilder
    from kiota_abstractions.base_request_configuration import RequestConfiguration

    query_params = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
        filter=query_filter if query_filter else None,
        top=max_emails,
        select=['id', 'subject', 'body', 'receivedDateTime', 'hasAttachments', 'from'],
        orderby=['receivedDateTime desc']
    )
    config = RequestConfiguration(query_parameters=query_params)
    result = await client.me.messages.get(request_configuration=config)
    return result.value if result and result.value else []


async def _fetch_outlook_attachments(client, message_id: str):
    """Async helper: fetches all file attachments for a given message."""
    result = await client.me.messages.by_message_id(message_id).attachments.get()
    if result and result.value:
        # Filter to only file attachments (not item attachments, reference attachments, etc.)
        return [a for a in result.value if hasattr(a, 'content_bytes') and a.content_bytes]
    return []


# --- MAIN EXECUTION LOGIC ---
def main():
    active_source = get_config("active_source", "gmail")

    # Paths are centrally managed in DB
    input_folder_path = get_config("input_folder", "./data/input")
    mail_download_folder = PROJECT_ROOT / input_folder_path

    # Setup directories
    mail_download_folder.mkdir(parents=True, exist_ok=True)

    from utils_logging import setup_logging
    setup_logging("0_extract_mail")
    logging.info(f"Extraction destination directory configured as: {mail_download_folder.resolve()}")
    logging.info(f"Active source: {active_source}")

    # Load processed emails state
    create_db_and_tables()
    processed_emails = set()
    try:
        with get_session() as session:
            processed_emails = set(session.exec(select(ProcessedEmail.id)).all())
            logging.info(f"Loaded {len(processed_emails)} processed email IDs from database.")
    except Exception as e:
        logging.warning(f"Could not load processed emails state: {e}")

    if active_source == "outlook":
        # ── Outlook / Microsoft Graph path ──
        outlook_auth_file = SCRIPT_DIR / "auth" / "outlook.json"
        outlook_config = {}
        if outlook_auth_file.exists():
            with open(outlook_auth_file, "r") as f:
                outlook_config = json.load(f)
        
        if not outlook_config:
            logging.error(f"Missing outlook configuration. Please check {outlook_auth_file}.")
            sys.exit(1)

        max_emails = outlook_config.get("max_emails", 50)
        ignore_processed = outlook_config.get("import_override_ignore_processed", False)

        extract_outlook(
            mail_download_folder, processed_emails,
            outlook_config, max_emails, ignore_processed
        )

    else:
        # ── Gmail path (default) ──
        mail_config = get_config("0_extract_mail.py", {})
        if not mail_config:
            logging.error("Missing '0_extract_mail.py' section in the configuration database.")
            sys.exit(1)

        query = mail_config.get("query", "is:unread")
        max_emails = mail_config.get("max_emails", 50)
        ignore_processed = mail_config.get("import_override_ignore_processed", False)
        override_days = mail_config.get("import_override_days", 0)

        if override_days > 0:
            query = f"{query} newer_than:{override_days}d"

        extract_gmail(
            mail_download_folder, processed_emails,
            mail_config, query, max_emails, ignore_processed
        )


if __name__ == "__main__":
    main()
