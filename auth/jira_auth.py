"""
Jira per-user OAuth 2.0 (3-legged).

Each employee authorises once via Atlassian OAuth. Jira enforces their
individual project permissions. Actions appear under their own name in
Jira audit logs.

Atlassian OAuth 2.0 (3LO) docs:
https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/
"""

import json
import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
import redis.asyncio as aioredis

from config import Config

logger = logging.getLogger(__name__)

JIRA_OAUTH_SCOPES = [
    "read:jira-user",
    "read:jira-work",
    "write:jira-work",
    "offline_access",
]

ATLASSIAN_AUTH_URL  = "https://auth.atlassian.com/authorize"
ATLASSIAN_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
ATLASSIAN_API_BASE  = "https://api.atlassian.com"

TOKEN_KEY = "jira_token:{user_oid}"
TOKEN_TTL = 3600 * 24  # 24 hours


def get_jira_auth_url(user_oid: str, state: str | None = None) -> str:
    """Build the Atlassian OAuth authorisation URL. Redirect the user's browser here."""
    params = {
        "audience":      "api.atlassian.com",
        "client_id":     Config.ATLASSIAN_OAUTH_CLIENT_ID,
        "scope":         " ".join(JIRA_OAUTH_SCOPES),
        "redirect_uri":  Config.ATLASSIAN_OAUTH_REDIRECT_URI,
        "state":         state or user_oid,
        "response_type": "code",
        "prompt":        "consent",
    }
    return f"{ATLASSIAN_AUTH_URL}?{urlencode(params)}"


async def handle_jira_oauth_callback(
    code: str,
    user_oid: str,
    redis_client: aioredis.Redis,
) -> dict:
    """Exchange the auth code for tokens and store in Redis. Called by /auth/jira/callback."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            ATLASSIAN_TOKEN_URL,
            json={
                "grant_type":    "authorization_code",
                "client_id":     Config.ATLASSIAN_OAUTH_CLIENT_ID,
                "client_secret": Config.ATLASSIAN_OAUTH_CLIENT_SECRET,
                "code":          code,
                "redirect_uri":  Config.ATLASSIAN_OAUTH_REDIRECT_URI,
            },
        )
        r.raise_for_status()
        tokens = r.json()

    cloud_id = await _fetch_cloud_id(tokens["access_token"])

    token_data = {
        "access_token":  tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "expires_at": (
            datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600))
        ).isoformat(),
        "cloud_id": cloud_id,
    }

    await redis_client.set(
        TOKEN_KEY.format(user_oid=user_oid),
        json.dumps(token_data),
        ex=TOKEN_TTL,
    )
    logger.info(f"Jira OAuth tokens stored for user {user_oid}")
    return token_data


async def _fetch_cloud_id(access_token: str) -> str:
    """Fetch the Forcepoint Atlassian cloud ID for this token."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ATLASSIAN_API_BASE}/oauth/token/accessible-resources",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        resources = r.json()
    if not resources:
        raise ValueError("No accessible Atlassian resources found for this token")
    return resources[0]["id"]


async def get_jira_token(
    user_oid: str,
    redis_client: aioredis.Redis,
) -> dict:
    """
    Get a valid Jira OAuth token for a user. Auto-refreshes if near expiry.
    Raises ValueError if the user has not completed OAuth.

    Returns dict with keys: access_token, cloud_id
    """
    raw = await redis_client.get(TOKEN_KEY.format(user_oid=user_oid))
    if not raw:
        raise ValueError(
            f"No Jira OAuth token for user {user_oid}. "
            "Direct the user to GET /auth/jira/start to authorise."
        )

    token_data = json.loads(raw)
    expires_at = datetime.fromisoformat(token_data["expires_at"])
    if datetime.utcnow() >= expires_at - timedelta(minutes=5):
        token_data = await _refresh_jira_token(user_oid, token_data, redis_client)

    return {"access_token": token_data["access_token"], "cloud_id": token_data["cloud_id"]}


async def _refresh_jira_token(
    user_oid: str,
    token_data: dict,
    redis_client: aioredis.Redis,
) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            ATLASSIAN_TOKEN_URL,
            json={
                "grant_type":    "refresh_token",
                "client_id":     Config.ATLASSIAN_OAUTH_CLIENT_ID,
                "client_secret": Config.ATLASSIAN_OAUTH_CLIENT_SECRET,
                "refresh_token": token_data["refresh_token"],
            },
        )
        r.raise_for_status()
        new_tokens = r.json()

    token_data.update({
        "access_token": new_tokens["access_token"],
        "expires_at": (
            datetime.utcnow() + timedelta(seconds=new_tokens.get("expires_in", 3600))
        ).isoformat(),
    })
    if "refresh_token" in new_tokens:
        token_data["refresh_token"] = new_tokens["refresh_token"]

    await redis_client.set(
        TOKEN_KEY.format(user_oid=user_oid),
        json.dumps(token_data),
        ex=TOKEN_TTL,
    )
    logger.info(f"Jira token refreshed for user {user_oid}")
    return token_data


def get_user_oauth_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
