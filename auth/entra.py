"""
Entra ID (Azure AD) token verification.
Every request to the agent API must carry a valid Entra Bearer token.
This middleware validates the token signature, audience, and issuer.
No database lookup needed — Microsoft's JWKS endpoint is the authority.
"""

import logging
from functools import lru_cache

import httpx
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config import Config

logger   = logging.getLogger(__name__)
security = HTTPBearer()

JWKS_URL = (
    f"https://login.microsoftonline.com"
    f"/{Config.AZURE_TENANT_ID}/discovery/v2.0/keys"
)


@lru_cache(maxsize=1)
def _get_jwks_sync() -> dict:
    """
    Fetch Microsoft's public signing keys.
    Cached in memory — keys rotate infrequently (days/weeks).
    Cache invalidated on process restart; force refresh by clearing cache.
    """
    response = httpx.get(JWKS_URL, timeout=10.0)
    response.raise_for_status()
    return response.json()


def _get_public_key(kid: str):
    """Find the RSA public key matching the token's kid header."""
    jwks = _get_jwks_sync()
    key_data = next(
        (k for k in jwks.get("keys", []) if k.get("kid") == kid), None
    )
    if not key_data:
        # Key not found — JWKS may have rotated, clear cache and retry once
        _get_jwks_sync.cache_clear()
        jwks = _get_jwks_sync()
        key_data = next(
            (k for k in jwks.get("keys", []) if k.get("kid") == kid), None
        )
    if not key_data:
        raise HTTPException(status_code=401, detail="Signing key not found")
    return jwt.algorithms.RSAAlgorithm.from_jwk(key_data)


async def verify_entra_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    FastAPI dependency. Validates Entra ID Bearer token.
    Returns decoded token payload — contains oid, upn, name, groups, roles.

    Usage:
        @app.post("/chat")
        async def chat(user: dict = Depends(verify_entra_token)):
            user_oid = user["oid"]
    """
    token = credentials.credentials

    try:
        header = jwt.get_unverified_header(token)
    except jwt.DecodeError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token header: {e}")

    public_key = _get_public_key(header.get("kid", ""))

    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=Config.AZURE_CLIENT_ID,
            issuer=[
                # V2 issuer — most apps
                f"https://login.microsoftonline.com/{Config.AZURE_TENANT_ID}/v2.0",
                # V1 issuer — some enterprise configurations
                f"https://sts.windows.net/{Config.AZURE_TENANT_ID}/",
            ],
            options={
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
            },
        )
        logger.debug(
            f"Token verified | user={payload.get('upn') or payload.get('preferred_username')} "
            f"| oid={payload.get('oid')}"
        )
        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidAudienceError:
        raise HTTPException(status_code=401, detail="Invalid audience")
    except jwt.InvalidIssuerError:
        raise HTTPException(status_code=401, detail="Invalid issuer")
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Token invalid: {e}")
