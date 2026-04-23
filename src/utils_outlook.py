# Microsoft Graph API (Outlook / Microsoft 365) authentication and service
# initialization for GDPHub. Mirrors utils_gmail.py in purpose and structure.
#
# Authentication uses InteractiveBrowserCredential, which opens a browser
# window for the user to sign in with their Microsoft account.
# Tokens are cached locally so subsequent runs don't require re-authentication.

import logging
from pathlib import Path

from azure.identity import InteractiveBrowserCredential, TokenCachePersistenceOptions
from msgraph import GraphServiceClient

from config_manager import get_config

# Scopes required:
# - Mail.Read: list and read email messages
# - Mail.ReadWrite: needed for the Janitor to move messages to Deleted Items
SCOPES = [
    'https://graph.microsoft.com/Mail.Read',
    'https://graph.microsoft.com/Mail.ReadWrite'
]

SCRIPT_DIR = Path(__file__).resolve().parent


def get_outlook_service() -> GraphServiceClient:
    """
    Authenticates and returns an authorized Microsoft Graph client.

    This function handles token caching, refreshing, and the initial
    interactive browser OAuth2 flow. On first run it opens a browser
    for login; subsequent runs reuse the cached token silently.
    """
    outlook_config = get_config("0_extract_mail_outlook", {})
    client_id = outlook_config.get("client_id", "")
    tenant_id = outlook_config.get("tenant_id", "common")

    if not client_id:
        raise ValueError(
            "Microsoft Graph client_id is not configured. "
            "Please set it in Configuration → Outlook Extraction Engine."
        )

    logging.info(f"Initializing Microsoft Graph client (tenant: {tenant_id})...")

    credential = InteractiveBrowserCredential(
        client_id=client_id,
        tenant_id=tenant_id,
        timeout=60, # Prevent indefinite hang if auth fails or user abandons
        cache_persistence_options=TokenCachePersistenceOptions(
            name="gdphub_outlook_cache_v3",
            allow_unencrypted_storage=True  # Falls back to plaintext if OS keyring unavailable
        )
    )

    client = GraphServiceClient(credential, scopes=SCOPES)
    logging.info("Microsoft Graph client initialized successfully.")
    return client
