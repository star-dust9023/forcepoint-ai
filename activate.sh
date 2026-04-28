#!/usr/bin/env bash
# Source this file to activate the AI-78 environment:
#   source activate.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VENV_DIR="$HOME/.venvs/mcp-platform"

export PATH="$HOME/.local/bin:$PATH"
source "$VENV_DIR/bin/activate"

if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
    echo "[AI-78] .env loaded"
else
    echo "[AI-78] WARNING: .env not found — copy .env.example to .env and fill in credentials"
fi

echo "[AI-78] venv active  | vault $(vault --version 2>/dev/null | head -1)"
echo "[AI-78] python: $(python3 --version)"
