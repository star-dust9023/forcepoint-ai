"""
Forcepoint Ask AI — a contained Retrieval-Augmented Generation (RAG) service.

This package is deliberately SEPARATE from the agentic connector server in
``agent/main.py``:

  - It does NOT touch the MCP connectors (m365/jira/salesforce) or LiteLLM.
  - It is text-in / text-out only: a question goes in, a grounded answer with
    citations comes out.
  - It calls the Anthropic API directly with a single org key (containment
    inside Forcepoint's boundary — no external Anthropic chat UI).
  - It grounds every answer in internal sources: the GitHub skills repo, the
    portal's own knowledge files, and (once provisioned) SharePoint/Confluence.

Run it standalone, on its own port, alongside the agentic app:

    uvicorn ask_service.main:app --port 8100
"""
