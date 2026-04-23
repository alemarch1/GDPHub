import os
import logging
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from config_manager import get_config

# REQUIRED SCOPES:
# We need gmail.readonly for extraction and gmail.modify for the Janitor (trashing messages).
# We also include the 'Full' scope (https://mail.google.com/) as a superset fallback.
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://mail.google.com/'
]

SCRIPT_DIR = Path(__file__).resolve().parent

def get_gmail_service():
    """
    Authenticates and returns an authorized Gmail API service.
    
    This function handles token loading, refreshing, and initial OAuth2 flow.
    It also verifies that the resulting credentials have 'gmail.modify' access,
    triggering an error if only 'readonly' is available.
    """
    mail_config = get_config("0_extract_mail.py", {})
    credentials_file = SCRIPT_DIR / mail_config.get("credentials_file", "credentials.json")
    token_file = SCRIPT_DIR / mail_config.get("token_file", "token.json")

    creds = None
    
    # 1. Attempt to load existing tokens
    if token_file.exists():
        try:
            # Note: Scopes in token.json might be different than the current SCOPES list
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except Exception as e:
            logging.error(f"Failed to load existing token.json: {e}")

    # 2. Re-authenticate if credentials are missing or invalid
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                 logging.warning(f"Refresh failed: {e}. Forcing interactive login.")
                 if not credentials_file.exists():
                     raise FileNotFoundError(f"Credentials file missing: {credentials_file}")
                 flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
                 creds = flow.run_local_server(port=0)
        else:
            if not credentials_file.exists():
                raise FileNotFoundError(f"Google API credentials file {credentials_file} is missing.")
            
            logging.info("Starting interactive Gmail authentication flow...")
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
            creds = flow.run_local_server(port=0)
            
        # Save the valid credentials for the next run
        try:
            with token_file.open('w') as token:
                token.write(creds.to_json())
            logging.info(f"Gmail tokens updated and saved to {token_file}")
        except Exception as e:
             logging.error(f"Error saving token.json: {e}")

    # 3. VERIFY SCOPES (Requested by User)
    # If the user previously authorized 'readonly' only, the token will lack 'modify' or 'full' access.
    authorized_scopes = creds.scopes if creds.scopes else []
    
    has_modify_access = any(s in authorized_scopes for s in [
        'https://www.googleapis.com/auth/gmail.modify',
        'https://mail.google.com/'
    ])

    if not has_modify_access:
        logging.critical("SECURITY ERROR: API access is limited to READ-ONLY.")
        logging.info("The Janitor requires 'gmail.modify' or 'mail.google.com' to move messages to the Trash.")
        
        # We raise a PermissionError to halt execution as requested.
        raise PermissionError(
            "Gmail API access does not allow message deletion (missing 'modify' or 'full' scope). "
            "To fix this, delete 'token.json' and re-run the script to perform a full re-authorization."
        )

    return build('gmail', 'v1', credentials=creds)
