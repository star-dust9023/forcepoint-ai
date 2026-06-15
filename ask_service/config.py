"""
Ask AI service configuration.

Kept separate from the top-level ``config.Config`` on purpose: that class
reads required Azure / Atlassian / CData env vars at import time and would
crash this contained service, which needs none of them. Ask AI only needs an
Anthropic key, the skills-repo location, and a service token shared with the
portal proxy.
"""

import os
from pathlib import Path


class AskConfig:
    # ── Anthropic (direct API — no LiteLLM in this contained service) ──────
    # Falls back to the shared ANTHROPIC_API_KEY if an Ask-specific one isn't set.
    ANTHROPIC_API_KEY = os.environ.get("ASK_ANTHROPIC_API_KEY") or os.environ.get(
        "ANTHROPIC_API_KEY", ""
    )
    ANTHROPIC_MODEL = os.environ.get("ASK_ANTHROPIC_MODEL", "claude-sonnet-4-6")
    MAX_TOKENS = int(os.environ.get("ASK_MAX_TOKENS", 1024))

    # ── Service auth (shared secret with the portal /api/ask proxy) ────────
    # The portal is the only caller. When unset, auth is disabled (dev only) and
    # the service logs a warning at startup.
    SERVICE_TOKEN = os.environ.get("ASK_SERVICE_TOKEN", "")

    # ── Retrieval tuning ───────────────────────────────────────────────────
    TOP_K = int(os.environ.get("ASK_TOP_K", 6))           # chunks fed to the model
    PER_SOURCE_K = int(os.environ.get("ASK_PER_SOURCE_K", 4))  # chunks per retriever

    # ── GitHub skills repo (reuses the same repo the agent loads from) ─────
    SKILLS_REPO_BASE_URL = os.environ.get(
        "SKILLS_REPO_BASE_URL",
        "https://github.cicd.cloud.fpdev.io/raw/BTS/claude-skills/main/docs",
    )
    SKILLS_GITHUB_TOKEN = os.environ.get("SKILLS_GITHUB_TOKEN")
    # name → filename in the repo. Mirrors config.Config.SKILLS.
    SKILLS = {
        "m365": "m365-skill.md",
        "jira": "jira-skill.md",
        "salesforce": "salesforce-skill.md",
    }
    SKILLS_CACHE_TTL = int(os.environ.get("SKILLS_CACHE_TTL", 3600))

    # ── Local portal knowledge files ───────────────────────────────────────
    KNOWLEDGE_DIR = Path(
        os.environ.get("ASK_KNOWLEDGE_DIR", str(Path(__file__).parent / "knowledge"))
    )

    # ── SharePoint / Confluence retrievers (stubs until access is granted) ─
    # Flip these on once Graph API / Confluence credentials are provisioned.
    # Until then the retrievers no-op cleanly.
    SHAREPOINT_ENABLED = os.environ.get("ASK_SHAREPOINT_ENABLED", "0") == "1"
    CONFLUENCE_ENABLED = os.environ.get("ASK_CONFLUENCE_ENABLED", "0") == "1"

    # ── CORS (the portal proxy is server-to-server, so usually empty) ──────
    ALLOWED_ORIGINS = [
        o.strip()
        for o in os.environ.get("ASK_ALLOWED_ORIGINS", "").split(",")
        if o.strip()
    ]
