# Gmail authentication and service initialization for GDPHub.
# Handles OAuth2 token loading, refreshing, and the interactive consent flow.
# Verifies that the resulting credentials include 'modify' scope for the Janitor.

import os
import logging
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from config_manager import get_config

# Scopes required:
# - gmail.readonly: list and read messages for extraction
# - gmail.modify: move messages to Trash for the Janitor
# - mail.google.com: superset fallback for full access
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
    auth_file = SCRIPT_DIR / "auth" / "gmail.json"
    
    creds = None
    
    # Attempt to load existing tokens
    if auth_file.exists():
        try:
            # The file acts as BOTH the credentials and the token file.
            # Credentials.from_authorized_user_file checks for token fields.
            creds = Credentials.from_authorized_user_file(str(auth_file), SCOPES)
        except Exception as e:
            # It might not have token fields yet, which is fine
            pass

    # Re-authenticate if credentials are missing or invalid
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                 logging.warning(f"Refresh failed: {e}. Forcing interactive login.")
                 if not auth_file.exists():
                     logging.error("Google API auth file is missing. Please configure it in the Web UI.")
                     import sys
                     sys.exit(1)
                 flow = InstalledAppFlow.from_client_secrets_file(str(auth_file), SCOPES)
                 creds = flow.run_local_server(port=0)
        else:
            if not auth_file.exists():
                logging.error("Google API auth file is missing.")
                logging.error("Please configure the Gmail Client ID and Secret in the Configuration tab of the Web UI.")
                import sys
                sys.exit(1)
            
            logging.info("Starting interactive Gmail authentication flow...")
            flow = InstalledAppFlow.from_client_secrets_file(str(auth_file), SCOPES)
            creds = flow.run_local_server(port=0)
            
        # Save the valid credentials for the next run
        # We must merge to preserve the "installed" client secrets section
        try:
            import json
            with open(auth_file, "r") as f:
                data = json.load(f)
                
            token_data = json.loads(creds.to_json())
            data.update(token_data)
            
            with open(auth_file, "w") as f:
                json.dump(data, f, indent=4)
                
            logging.info(f"Gmail tokens updated and saved to {auth_file}")
        except Exception as e:
             logging.error(f"Error saving to {auth_file}: {e}")

    # Verify scopes include modify/full access (required by the Janitor)
    authorized_scopes = creds.scopes if creds.scopes else []
    
    has_modify_access = any(s in authorized_scopes for s in [
        'https://www.googleapis.com/auth/gmail.modify',
        'https://mail.google.com/'
    ])

    if not has_modify_access:
        logging.critical("SECURITY ERROR: API access is limited to READ-ONLY.")
        logging.info("The Janitor requires 'gmail.modify' or 'mail.google.com' to move messages to the Trash.")
        
        # Halt execution: the Janitor cannot function without modify access
        raise PermissionError(
            "Gmail API access does not allow message deletion (missing 'modify' or 'full' scope). "
            "To fix this, delete 'token.json' and re-run the script to perform a full re-authorization."
        )

    return build('gmail', 'v1', credentials=creds)
