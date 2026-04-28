"""
On-Behalf-Of (OBO) flow — Microsoft identity platform.

The agent API receives the user's Entra SSO token (proving who they are).
OBO exchanges that token for a Graph API token that carries the user's identity.
When the MCP server calls Graph API with this token, Microsoft enforces
that user's exact permissions — they can only see their own email, calendar,
files, and Teams channels.

No extra access control layer needed. Microsoft does it.
"""

import logging
from functools import lru_cache

import msal

from config import Config

logger = logging.getLogger(__name__)

# Scopes must match what was granted in the Azure App Registration
GRAPH_DELEGATED_SCOPES = [
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Calendars.Read",
    "https://graph.microsoft.com/Files.Read",
    "https://graph.microsoft.com/Chat.Read",
    "https://graph.microsoft.com/ChannelMessage.Read.All",
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Team.ReadBasic.All",
]


@lru_cache(maxsize=1)
def _get_msal_app() -> msal.ConfidentialClientApplication:
    """One MSAL instance reused across requests — handles token caching internally."""
    return msal.ConfidentialClientApplication(
        client_id=Config.AZURE_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{Config.AZURE_TENANT_ID}",
        client_credential=Config.AZURE_CLIENT_SECRET,
    )


def exchange_token_for_graph(user_entra_token: str) -> str:
    """
    Exchange the user's Entra SSO token for a Graph API token via OBO.

    Returns:
        A Graph API access token scoped to that user's delegated permissions.

    Raises:
        Exception if OBO exchange fails (expired token, missing scope, etc.)
    """
    result = _get_msal_app().acquire_token_on_behalf_of(
        user_assertion=user_entra_token,
        scopes=GRAPH_DELEGATED_SCOPES,
    )

    if "access_token" in result:
        logger.debug("OBO exchange successful")
        return result["access_token"]

    error             = result.get("error", "unknown_error")
    error_description = result.get("error_description", "No description")
    correlation_id    = result.get("correlation_id", "")

    logger.error(
        f"OBO exchange failed | error={error} "
        f"| description={error_description} "
        f"| correlation_id={correlation_id}"
    )
    raise Exception(f"OBO token exchange failed: {error} — {error_description}")


def get_app_token() -> str:
    """
    Application-level token — NOT tied to any user.
    Use ONLY for dev/testing or service-to-service calls.
    Never use this for end-user M365 queries — it bypasses RBAC.
    """
    result = _get_msal_app().acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" in result:
        return result["access_token"]
    raise Exception(
        f"App token acquisition failed: {result.get('error_description')}"
    )
