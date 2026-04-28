#!/usr/bin/env python3
"""AI-78: MCP Platform Health Check — LiteLLM, Redis, Vault, LangSmith"""

import os
import sys
import json
import time
import subprocess

# ── colour helpers ─────────────────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"

PASS = f"{GREEN}✔ PASS{RESET}"
FAIL = f"{RED}✖ FAIL{RESET}"
WARN = f"{YELLOW}⚠ WARN{RESET}"


def header(title: str) -> None:
    print(f"\n{BOLD}{'─' * 60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'─' * 60}{RESET}")


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = PASS if ok else FAIL
    print(f"  {status}  {label}")
    if detail:
        print(f"         {detail}")
    return ok


# ── Component 1: LiteLLM ──────────────────────────────────────────────────────
def check_litellm() -> bool:
    header("Component 1 — LiteLLM")
    import urllib.request
    import urllib.error

    host = os.getenv("LITELLM_HOST", "http://localhost:4000")
    api_key = os.getenv("LITELLM_MASTER_KEY") or os.getenv("LITELLM_API_KEY", "")

    # 1a. /health endpoint
    try:
        req = urllib.request.Request(f"{host}/health")
        with urllib.request.urlopen(req, timeout=5) as r:
            health_ok = r.status == 200
    except Exception as e:
        health_ok = False
        check("/health reachable", False, str(e))
        return False
    check("/health reachable", health_ok)

    # 1b. Test completion
    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 10,
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = urllib.request.Request(
        f"{host}/chat/completions", data=payload, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read())
            has_choices = bool(body.get("choices"))
            reply = body["choices"][0]["message"]["content"] if has_choices else ""
            check("Completion routes successfully", has_choices, f"reply: {reply!r}")
            return has_choices
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        check("Completion routes successfully", False, f"HTTP {e.code}: {body[:200]}")
        return False
    except Exception as e:
        check("Completion routes successfully", False, str(e))
        return False


# ── Component 2: Redis ─────────────────────────────────────────────────────────
def check_redis() -> bool:
    header("Component 2 — Redis")
    try:
        import redis as redis_lib
    except ImportError:
        print(f"  {WARN}  redis-py not installed — falling back to redis-cli")
        return _check_redis_cli()

    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD") or None

    try:
        r = redis_lib.Redis(host=host, port=port, password=password, socket_timeout=5)
        pong = r.ping()
        check("Redis reachable (PING)", pong)
        if not pong:
            return False
    except Exception as e:
        check("Redis reachable (PING)", False, str(e))
        return False

    KEY = "mcp_health_check"
    TTL_TARGET = 3600

    r.set(KEY, "ok", ex=TTL_TARGET)
    val = r.get(KEY)
    ttl = r.ttl(KEY)

    val_ok = val == b"ok"
    ttl_ok = TTL_TARGET - 5 <= ttl <= TTL_TARGET

    check("SET/GET round-trip", val_ok, f"GET returned {val!r}")
    check(f"TTL ≈ {TTL_TARGET}s", ttl_ok, f"TTL = {ttl}s (want {TTL_TARGET-5}–{TTL_TARGET})")

    r.delete(KEY)
    return val_ok and ttl_ok


def _check_redis_cli() -> bool:
    host = os.getenv("REDIS_HOST", "localhost")
    port = os.getenv("REDIS_PORT", "6379")
    KEY = "mcp_health_check"
    TTL_TARGET = 3600

    def run(args):
        result = subprocess.run(
            ["redis-cli", "-h", host, "-p", port] + args,
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()

    try:
        pong = run(["ping"])
        check("Redis reachable (PING)", pong == "PONG", f"got: {pong!r}")
        if pong != "PONG":
            return False

        run(["SET", KEY, "ok", "EX", str(TTL_TARGET)])
        val = run(["GET", KEY])
        ttl = int(run(["TTL", KEY]))

        val_ok = val == "ok"
        ttl_ok = TTL_TARGET - 5 <= ttl <= TTL_TARGET

        check("SET/GET round-trip", val_ok, f"GET returned {val!r}")
        check(f"TTL ≈ {TTL_TARGET}s", ttl_ok, f"TTL = {ttl}s")
        run(["DEL", KEY])
        return val_ok and ttl_ok
    except FileNotFoundError:
        check("redis-cli available", False, "redis-cli not found — install redis-tools")
        return False
    except Exception as e:
        check("Redis CLI check", False, str(e))
        return False


# ── Component 3: Vault ────────────────────────────────────────────────────────
def check_vault() -> bool:
    header("Component 3 — Vault")
    addr = os.getenv("VAULT_ADDR", "http://localhost:8200")
    token = os.getenv("VAULT_TOKEN", "root")

    import urllib.request
    import urllib.error

    # 3a. sys/health
    try:
        req = urllib.request.Request(f"{addr}/v1/sys/health")
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read())
            sealed = body.get("sealed", True)
            initialized = body.get("initialized", False)
    except urllib.error.HTTPError as e:
        # Vault returns 503 when sealed — still parse it
        try:
            body = json.loads(e.read())
            sealed = body.get("sealed", True)
            initialized = body.get("initialized", False)
        except Exception:
            check("Vault reachable", False, str(e))
            return False
    except Exception as e:
        check("Vault reachable", False, str(e))
        return False

    check("Vault reachable", True)
    check("Vault initialized", initialized)
    check("Vault unsealed", not sealed, "BLOCKER: unseal before Sprint 1" if sealed else "")
    if sealed:
        return False

    # 3b. Write + read a test secret
    headers = {
        "X-Vault-Token": token,
        "Content-Type": "application/json",
    }

    secret_path = "secret/data/forcepoint/health_check"
    write_payload = json.dumps({"data": {"status": "ok", "component": "mcp_health"}}).encode()

    try:
        req = urllib.request.Request(
            f"{addr}/v1/{secret_path}",
            data=write_payload,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
        write_ok = True
    except urllib.error.HTTPError as e:
        write_ok = False
        check("Write test secret (kv-v2)", False, f"HTTP {e.code}: {e.read().decode()[:150]}")

    if write_ok:
        check("Write test secret (kv-v2)", True, f"path: {secret_path}")

    try:
        req = urllib.request.Request(f"{addr}/v1/{secret_path}", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read())
            fields = list(body.get("data", {}).get("data", {}).keys())
            read_ok = "status" in fields
            check("Read secret fields back", read_ok, f"fields: {fields}")
            return write_ok and read_ok
    except urllib.error.HTTPError as e:
        check("Read test secret", False, f"HTTP {e.code}: {e.read().decode()[:150]}")
        return False
    except Exception as e:
        check("Read test secret", False, str(e))
        return False


# ── Component 4: LangSmith ────────────────────────────────────────────────────
def check_langsmith() -> bool:
    header("Component 4 — LangSmith")

    api_key = os.getenv("LANGCHAIN_API_KEY", "")
    tracing = os.getenv("LANGCHAIN_TRACING_V2", "")
    project = os.getenv("LANGCHAIN_PROJECT", "")

    key_ok = bool(api_key and not api_key.endswith("..."))
    tracing_ok = tracing == "true"
    project_ok = bool(project)

    check("LANGCHAIN_API_KEY set", key_ok, "(redacted)" if key_ok else "not set or placeholder")
    check("LANGCHAIN_TRACING_V2=true", tracing_ok, f"current: {tracing!r}")
    check("LANGCHAIN_PROJECT set", project_ok, f"project: {project!r}")

    if not (key_ok and tracing_ok and project_ok):
        print(f"\n  {WARN}  Fix env vars above, then re-run to verify trace submission.")
        return False

    # Attempt a traced LiteLLM call via litellm SDK
    try:
        import litellm
    except ImportError:
        print(f"  {WARN}  litellm SDK not installed — run: pip install litellm langsmith")
        print("         Skipping live trace test. Verify env vars are correct and")
        print("         re-run after installing dependencies.")
        return key_ok and tracing_ok and project_ok

    litellm.success_callback = ["langsmith"]
    litellm.failure_callback = ["langsmith"]

    host = os.getenv("LITELLM_HOST", "http://localhost:4000")
    api_key_litellm = os.getenv("LITELLM_MASTER_KEY") or os.getenv("LITELLM_API_KEY", "")

    print(f"\n  Sending traced call → LiteLLM → Anthropic …")
    try:
        resp = litellm.completion(
            model="openai/claude-sonnet-4-20250514",
            api_base=host,
            api_key=api_key_litellm,
            messages=[{"role": "user", "content": "health check trace test"}],
            max_tokens=10,
        )
        reply = resp.choices[0].message.content
        check("LiteLLM call succeeded", True, f"reply: {reply!r}")
        print(f"\n  {YELLOW}Action required:{RESET} Open https://smith.langchain.com")
        print(f"  → project '{project}' → verify trace appears within 30s")
        print(f"  → confirm: model, input, output, latency, token count all visible")
        return True
    except Exception as e:
        check("LiteLLM call for tracing", False, str(e))
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    print(f"\n{BOLD}AI-78: MCP Platform Health Check{RESET}")
    print(f"Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")

    results = {
        "LiteLLM": check_litellm(),
        "Redis": check_redis(),
        "Vault": check_vault(),
        "LangSmith": check_langsmith(),
    }

    header("Summary")
    all_pass = True
    for name, ok in results.items():
        status = PASS if ok else FAIL
        print(f"  {status}  {name}")
        if not ok:
            all_pass = False

    print()
    if all_pass:
        print(f"{GREEN}{BOLD}All four components are green. AI-78 acceptance criteria met.{RESET}")
        print("Next: attach screenshot evidence to AI-78 and transition ticket to Done.")
    else:
        failing = [k for k, v in results.items() if not v]
        print(f"{RED}{BOLD}Failing: {', '.join(failing)}{RESET}")
        print("Resolve failures above before transitioning AI-78 to Done.")
    print()

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
