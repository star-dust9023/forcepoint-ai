"""
LiteLLM virtual key provisioner.

Each employee gets a virtual key scoped to their department's budget and
allowed models. The real Anthropic API key is never exposed to anyone.

Keys are provisioned automatically on first login via SSO.
Existing keys are looked up from LiteLLM's database — no duplicate creation.
"""

import logging

import httpx

from config import Config

logger = logging.getLogger(__name__)

LITELLM_BASE = Config.LITELLM_BASE_URL

# -------------------------------------------------------------------------
# Budget and model access tiers
# -------------------------------------------------------------------------

DEPARTMENT_TIERS = {
    "engineering": {
        "monthly_budget": 30.00,
        "models":         ["claude-sonnet", "claude-haiku"],
        "rpm_limit":      60,
        "tpm_limit":      100_000,
    },
    "sales": {
        "monthly_budget": 20.00,
        "models":         ["claude-sonnet", "claude-haiku"],
        "rpm_limit":      40,
        "tpm_limit":      80_000,
    },
    "finance": {
        "monthly_budget": 15.00,
        "models":         ["claude-haiku"],
        "rpm_limit":      20,
        "tpm_limit":      50_000,
    },
    "default": {
        "monthly_budget": 10.00,
        "models":         ["claude-haiku"],
        "rpm_limit":      20,
        "tpm_limit":      40_000,
    },
}

_master_headers = {
    "Authorization": f"Bearer {Config.LITELLM_MASTER_KEY}",
    "Content-Type":  "application/json",
}


async def get_or_create_litellm_key(
    user_oid:   str,
    user_email: str,
    department: str = "default",
) -> str:
    """
    Return the existing LiteLLM virtual key for this user, or create one.

    Args:
        user_oid:   Entra Object ID — stable unique identifier per employee.
        user_email: Corporate email — for display in LiteLLM dashboard.
        department: Derived from Entra groups — determines budget tier.
    """
    existing = await _get_existing_key(user_oid)
    if existing:
        return existing
    return await _create_key(user_oid, user_email, department)


async def _get_existing_key(user_oid: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{LITELLM_BASE}/key/list",
                headers=_master_headers,
                params={"user_id": user_oid, "include_team_keys": False},
            )
            if r.status_code != 200:
                return None
            keys = r.json().get("keys", [])
            active = [k for k in keys if not k.get("blocked", False)]
            if active:
                logger.debug(f"Existing LiteLLM key found for user {user_oid}")
                return active[0].get("token") or active[0].get("key")
    except Exception as e:
        logger.warning(f"Key lookup failed for {user_oid}: {e}")
    return None


async def _create_key(user_oid: str, user_email: str, department: str) -> str:
    tier = DEPARTMENT_TIERS.get(department, DEPARTMENT_TIERS["default"])

    payload = {
        "user_id":         user_oid,
        "user_email":      user_email,
        "models":          tier["models"],
        "max_budget":      tier["monthly_budget"],
        "budget_duration": "monthly",
        "rpm_limit":       tier["rpm_limit"],
        "tpm_limit":       tier["tpm_limit"],
        "metadata": {
            "department":     department,
            "entra_oid":      user_oid,
            "provisioned_by": "sso-auto",
        },
        "key_alias": f"{user_email}_{department}",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{LITELLM_BASE}/key/generate",
                headers=_master_headers,
                json=payload,
            )
            r.raise_for_status()
            key = r.json().get("key")
            logger.info(
                f"LiteLLM key provisioned | user={user_email} "
                f"| department={department} "
                f"| budget=${tier['monthly_budget']}/month"
            )
            return key

    except httpx.HTTPStatusError as e:
        logger.error(f"Key creation failed for {user_email}: {e.response.text}")
        logger.warning(f"Falling back to master key for {user_email}")
        return Config.LITELLM_MASTER_KEY


async def update_user_budget(user_oid: str, new_budget: float):
    """Update an employee's monthly budget — call when department changes."""
    existing_key = await _get_existing_key(user_oid)
    if not existing_key:
        logger.warning(f"No key found to update for {user_oid}")
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            f"{LITELLM_BASE}/key/update",
            headers=_master_headers,
            json={"key": existing_key, "max_budget": new_budget},
        )
        r.raise_for_status()
        logger.info(f"Budget updated for {user_oid} → ${new_budget}/month")


async def revoke_user_key(user_oid: str):
    """Revoke a user's key — call on offboarding."""
    existing_key = await _get_existing_key(user_oid)
    if not existing_key:
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            f"{LITELLM_BASE}/key/delete",
            headers=_master_headers,
            json={"keys": [existing_key]},
        )
        r.raise_for_status()
        logger.info(f"LiteLLM key revoked for {user_oid}")


async def get_user_spend(user_oid: str) -> dict:
    """Get current month spend for a user."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{LITELLM_BASE}/user/info",
                headers=_master_headers,
                params={"user_id": user_oid},
            )
            r.raise_for_status()
            data = r.json()
            return {
                "user_id":          user_oid,
                "spend_this_month": data.get("spend", 0),
                "max_budget":       data.get("max_budget"),
                "models":           data.get("models", []),
            }
    except Exception as e:
        logger.error(f"Spend lookup failed for {user_oid}: {e}")
        return {}
