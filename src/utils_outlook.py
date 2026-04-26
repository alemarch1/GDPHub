# Microsoft Graph API (Outlook / Microsoft 365) authentication and service
# initialization for GDPHub. Mirrors utils_gmail.py in purpose and structure.
#
# Authentication uses MSAL directly to allow storing the token cache and
# configuration inside a single json file (src/auth/outlook.json).

import logging
import json
import time
from pathlib import Path

from msgraph.graph_service_client import GraphServiceClient
from azure.core.credentials import TokenCredential, AccessToken
import msal

# Scopes required:
# - Mail.Read: list and read email messages
# - Mail.ReadWrite: needed for the Janitor to move messages to Deleted Items
SCOPES = [
    'https://graph.microsoft.com/Mail.Read',
    'https://graph.microsoft.com/Mail.ReadWrite'
]

SCRIPT_DIR = Path(__file__).resolve().parent

class MsalJsonTokenCredential(TokenCredential):
    """
    Custom TokenCredential that wraps MSAL and serializes both the
    configuration and the token cache into a single JSON file.
    """
    def __init__(self, auth_file: Path, scopes: list):
        self.auth_file = auth_file
        self.scopes = scopes
        
        if not auth_file.exists():
            logging.error("Outlook auth file is missing.")
            logging.error("Please configure the Outlook Client ID in the Configuration tab of the Web UI.")
            import sys
            sys.exit(1)
            
        with open(auth_file, "r") as f:
            data = json.load(f)
            
        self.client_id = data.get("client_id")
        self.tenant_id = data.get("tenant_id", "common")
        
        if not self.client_id:
            raise ValueError(
                f"Microsoft Graph client_id is not configured in {auth_file}."
            )
            
        self.cache = msal.SerializableTokenCache()
        if "token_cache" in data:
            self.cache.deserialize(data["token_cache"])
            
        self.app = msal.PublicClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            token_cache=self.cache
        )

    def _save_cache(self):
        if self.cache.has_state_changed:
            with open(self.auth_file, "r") as f:
                data = json.load(f)
            data["token_cache"] = self.cache.serialize()
            with open(self.auth_file, "w") as f:
                json.dump(data, f, indent=4)

    def get_token(self, *scopes, **kwargs) -> AccessToken:
        # msgraph might pass scopes to get_token, but MSAL needs our specific list
        target_scopes = list(scopes) if scopes else self.scopes
        
        accounts = self.app.get_accounts()
        result = None
        if accounts:
            result = self.app.acquire_token_silent(target_scopes, account=accounts[0])
            
        if not result:
            logging.info("Starting interactive Outlook authentication flow...")
            result = self.app.acquire_token_interactive(target_scopes)
            
        if "access_token" in result:
            self._save_cache()
            # MSAL returns expires_in, AccessToken expects expires_on timestamp
            expires_on = time.time() + int(result.get("expires_in", 3600))
            return AccessToken(result["access_token"], int(expires_on))
            
        error_msg = result.get("error_description", result.get("error", "Unknown error"))
        raise Exception(f"Failed to authenticate: {error_msg}")

def get_outlook_service() -> GraphServiceClient:
    """
    Authenticates and returns an authorized Microsoft Graph client.

    This function handles token caching, refreshing, and the initial
    interactive browser OAuth2 flow using a unified outlook.json file.
    """
    auth_file = SCRIPT_DIR / "auth" / "outlook.json"
    
    logging.info("Initializing Microsoft Graph client...")
    
    credential = MsalJsonTokenCredential(auth_file, SCOPES)
    client = GraphServiceClient(credential, scopes=SCOPES)
    
    logging.info("Microsoft Graph client initialized successfully.")
    return client
