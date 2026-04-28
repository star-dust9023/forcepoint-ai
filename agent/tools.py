"""
Tool definitions passed to Claude.
Auth context (user_token, user_oid, department) is injected automatically by
the agent — Claude does not supply these values.
"""

M365_TOOLS = [
    {
        "name": "m365_get_emails",
        "description": (
            "Search the signed-in user's emails. "
            "Returns preview only — never full body unless explicitly asked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":      {"type": "string"},
                "days_back":  {"type": "integer", "default": 7},
                "top":        {"type": "integer", "default": 10, "maximum": 20},
                "folder":     {"type": "string", "default": "inbox",
                               "enum": ["inbox", "sentitems", "drafts", "archive"]},
                "user_token": {"type": "string",
                               "description": "Graph API token — injected automatically"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "m365_get_calendar",
        "description": "Get the signed-in user's calendar events between two dates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date":   {"type": "string", "description": "YYYY-MM-DD"},
                "user_token": {"type": "string"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "m365_search_files",
        "description": "Search OneDrive and SharePoint files the user has access to.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":      {"type": "string"},
                "top":        {"type": "integer", "default": 10, "maximum": 20},
                "user_token": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "m365_get_teams_messages",
        "description": (
            "Get messages from a specific Teams channel. "
            "Use m365_list_teams first if team_id or channel_id is unknown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "team_id":    {"type": "string"},
                "channel_id": {"type": "string"},
                "top":        {"type": "integer", "default": 20, "maximum": 50},
                "user_token": {"type": "string"},
            },
            "required": ["team_id", "channel_id"],
        },
    },
    {
        "name": "m365_list_teams",
        "description": "List all Teams the signed-in user is a member of.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_token": {"type": "string"},
            },
        },
    },
    {
        "name": "m365_get_profile",
        "description": "Get the signed-in user's M365 profile (name, email, title, department).",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_token": {"type": "string"},
            },
        },
    },
]

JIRA_TOOLS = [
    {
        "name": "jira_search",
        "description": (
            "Search Jira using JQL. Returns lean issue list. "
            "Use cached JQL patterns from the Jira skill."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "jql":         {"type": "string"},
                "max_results": {"type": "integer", "default": 20, "maximum": 50},
                "user_oid":    {"type": "string",
                                "description": "Entra OID — injected automatically"},
            },
            "required": ["jql"],
        },
    },
    {
        "name": "jira_get_issue",
        "description": "Get a specific Jira issue by key, e.g. AI-86.",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_key":        {"type": "string"},
                "include_comments": {"type": "boolean", "default": False},
                "user_oid":         {"type": "string"},
            },
            "required": ["issue_key"],
        },
    },
    {
        "name": "jira_create_issue",
        "description": "Create a Jira story, task, or sub-task. Link to epic via parent_key.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary":     {"type": "string"},
                "description": {"type": "string"},
                "issue_type":  {"type": "string", "default": "Story",
                                "enum": ["Story", "Task", "Sub-task", "Epic", "Bug"]},
                "project_key": {"type": "string", "default": "AI"},
                "parent_key":  {"type": "string"},
                "priority":    {"type": "string", "default": "Medium",
                                "enum": ["Highest", "High", "Medium", "Low"]},
                "assignee_id": {"type": "string"},
                "user_oid":    {"type": "string"},
            },
            "required": ["summary"],
        },
    },
    {
        "name": "jira_add_comment",
        "description": "Add a comment to a Jira issue.",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_key": {"type": "string"},
                "comment":   {"type": "string"},
                "user_oid":  {"type": "string"},
            },
            "required": ["issue_key", "comment"],
        },
    },
    {
        "name": "jira_update_status",
        "description": "Move a Jira issue to a new workflow status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_key":       {"type": "string"},
                "transition_name": {
                    "type": "string",
                    "enum": [
                        "Backlog",
                        "Discovery / Scope",
                        "Selected for Development",
                        "In Progress",
                        "Done",
                    ],
                },
                "user_oid": {"type": "string"},
            },
            "required": ["issue_key", "transition_name"],
        },
    },
    {
        "name": "jira_get_sprint",
        "description": "Get all issues in a named sprint.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sprint_name": {"type": "string", "description": "e.g. 'EAI 2026 Sprint 08'"},
                "project_key": {"type": "string", "default": "AI"},
                "max_results": {"type": "integer", "default": 50},
                "user_oid":    {"type": "string"},
            },
            "required": ["sprint_name"],
        },
    },
    {
        "name": "jira_get_epic_children",
        "description": "Get all stories and tasks under a specific epic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "epic_key":    {"type": "string"},
                "max_results": {"type": "integer", "default": 50},
                "user_oid":    {"type": "string"},
            },
            "required": ["epic_key"],
        },
    },
]

SALESFORCE_TOOLS = [
    {
        "name": "sf_pipeline",
        "description": (
            "Get open pipeline opportunities. Filter by stage, theatre, owner, quarter. "
            "Skip CData discovery — use curated field set from SF skill."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "stage":          {"type": "string"},
                "theatre":        {"type": "string", "enum": ["AMER", "EMEA", "APAC", "LATAM"]},
                "owner_name":     {"type": "string"},
                "fiscal_year":    {"type": "integer"},
                "fiscal_quarter": {"type": "integer", "minimum": 1, "maximum": 4},
                "limit":          {"type": "integer", "default": 50, "maximum": 100},
                "department":     {"type": "string",
                                   "description": "User's department — injected automatically"},
            },
        },
    },
    {
        "name": "sf_closed_won",
        "description": (
            "Get closed-won opportunities. Group by quarter, theatre, stage, or owner. "
            "Use ACV_Reporting__c for executive numbers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fiscal_year":    {"type": "integer"},
                "fiscal_quarter": {"type": "integer"},
                "theatre":        {"type": "string"},
                "group_by":       {"type": "string",
                                   "enum": ["quarter", "theatre", "stage", "owner"],
                                   "default": "quarter"},
                "limit":          {"type": "integer", "default": 50},
                "department":     {"type": "string"},
            },
        },
    },
    {
        "name": "sf_closed_lost",
        "description": "Get closed-lost opportunities with loss reason breakdown.",
        "input_schema": {
            "type": "object",
            "properties": {
                "since_date": {"type": "string", "description": "YYYY-MM-DD, default 90 days ago"},
                "theatre":    {"type": "string"},
                "limit":      {"type": "integer", "default": 50},
                "department": {"type": "string"},
            },
        },
    },
    {
        "name": "sf_account_health",
        "description": "Get customer account health scores, ARR, and CSM sentiment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "theatre":    {"type": "string"},
                "min_arr":    {"type": "number", "default": 0},
                "health":     {"type": "string", "description": "e.g. 'Red', 'Yellow', 'Green'"},
                "limit":      {"type": "integer", "default": 50},
                "department": {"type": "string"},
            },
        },
    },
    {
        "name": "sf_renewal_pipeline",
        "description": "Get open renewal opportunities sorted by close date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "theatre":    {"type": "string"},
                "sentiment":  {"type": "string", "description": "e.g. 'At Risk', 'Positive'"},
                "limit":      {"type": "integer", "default": 50},
                "department": {"type": "string"},
            },
        },
    },
    {
        "name": "sf_acv_by_product",
        "description": "Get ACV breakdown by product line for closed-won deals.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fiscal_year":    {"type": "integer"},
                "fiscal_quarter": {"type": "integer"},
                "limit":          {"type": "integer", "default": 30},
                "department":     {"type": "string"},
            },
        },
    },
    {
        "name": "sf_account_opportunities",
        "description": "Get all opportunities for a named account.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_name":   {"type": "string"},
                "include_closed": {"type": "boolean", "default": False},
                "limit":          {"type": "integer", "default": 20},
                "department":     {"type": "string"},
            },
            "required": ["account_name"],
        },
    },
    {
        "name": "sf_raw_query",
        "description": (
            "Run a custom SQL query against Salesforce via CData. "
            "CData SQL dialect: [] brackets, no DATEADD(), LIMIT clause. "
            "Table format: [ConnectionName].[ConnectionName].[TableName]. "
            "Only use when no other sf_ tool covers the need."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql":        {"type": "string"},
                "limit":      {"type": "integer", "default": 20, "maximum": 100},
                "department": {"type": "string"},
            },
            "required": ["sql"],
        },
    },
]

# To add a new connector: create its list above, append here.
ALL_TOOLS = M365_TOOLS + JIRA_TOOLS + SALESFORCE_TOOLS
