import os


class Config:
    # LiteLLM proxy
    LITELLM_BASE_URL   = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
    LITELLM_MASTER_KEY = os.environ["LITELLM_MASTER_KEY"]
    CLAUDE_MODEL       = os.environ.get("CLAUDE_MODEL", "claude-sonnet")

    # GitHub Skills Repo
    SKILLS_REPO_BASE_URL = os.environ.get(
        "SKILLS_REPO_BASE_URL",
        "https://github.cicd.cloud.fpdev.io/raw/BTS/claude-skills/main/docs",
    )
    SKILLS_GITHUB_TOKEN = os.environ.get("SKILLS_GITHUB_TOKEN")
    SKILLS = {
        "m365":       "m365-skill.md",
        "jira":       "jira-skill.md",
        "salesforce": "salesforce-skill.md",
    }

    # Redis
    REDIS_URL        = os.environ.get("REDIS_URL", "redis://localhost:6379")
    SKILLS_CACHE_TTL = int(os.environ.get("SKILLS_CACHE_TTL", 3600))

    # Azure / M365
    AZURE_TENANT_ID     = os.environ["AZURE_TENANT_ID"]
    AZURE_CLIENT_ID     = os.environ["AZURE_CLIENT_ID"]
    AZURE_CLIENT_SECRET = os.environ["AZURE_CLIENT_SECRET"]

    # Atlassian / Jira — per-user OAuth 3LO
    ATLASSIAN_OAUTH_CLIENT_ID     = os.environ["ATLASSIAN_OAUTH_CLIENT_ID"]
    ATLASSIAN_OAUTH_CLIENT_SECRET = os.environ["ATLASSIAN_OAUTH_CLIENT_SECRET"]
    ATLASSIAN_OAUTH_REDIRECT_URI  = os.environ.get(
        "ATLASSIAN_OAUTH_REDIRECT_URI",
        "https://your-agent.fpdev.io/auth/jira/callback",
    )
    JIRA_DEFAULT_PROJECT = os.environ.get("JIRA_DEFAULT_PROJECT", "AI")

    # Salesforce via CData Connect AI — department-mapped connections
    CDATA_BASE_URL           = os.environ["CDATA_BASE_URL"]
    CDATA_API_KEY            = os.environ["CDATA_API_KEY"]
    CDATA_CONNECTION_SALES   = os.environ.get("CDATA_CONNECTION_SALES",   "Salesforce_Sales")
    CDATA_CONNECTION_OPS     = os.environ.get("CDATA_CONNECTION_OPS",     "Salesforce_Ops")
    CDATA_CONNECTION_DEFAULT = os.environ.get("CDATA_CONNECTION_DEFAULT", "Salesforce")

    # Entra group → department mapping
    # Paste Object IDs from Azure AD → Groups
    ENTRA_GROUP_SALES   = os.environ.get("ENTRA_GROUP_SALES",   "")
    ENTRA_GROUP_ENG     = os.environ.get("ENTRA_GROUP_ENG",     "")
    ENTRA_GROUP_FINANCE = os.environ.get("ENTRA_GROUP_FINANCE", "")

    # LangSmith
    LANGSMITH_API_KEY = os.environ.get("LANGCHAIN_API_KEY")
    LANGSMITH_PROJECT = os.environ.get("LANGCHAIN_PROJECT", "forcepoint-enterprise-ai")
