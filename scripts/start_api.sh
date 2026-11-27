#!/usr/bin/env bash
# Kept for existing habits: everything now runs through the cross-platform tool, scripts/dev.py.
#   ./scripts/start_api.sh              ==  python scripts/dev.py start            (services, MCP servers, workers, API, UI)
#   ./scripts/start_api.sh --dev        ==  python scripts/dev.py start --reload
# Older flags still work: --with-mcp / --with-workers / --all are now the default; --api-only starts the API alone.
set -e
cd "$(dirname "$0")/.."
PY="$(command -v python3 || command -v python)"
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --dev) ARGS+=("--reload") ;;
        --with-mcp|--with-workers|--all) ;;                 # default now
        --api-only) ARGS+=("--no-mcp" "--no-workers" "--no-ui") ;;
        *) ARGS+=("$arg") ;;
    esac
done
exec "$PY" scripts/dev.py start "${ARGS[@]}"
