#!/usr/bin/env bash
# =============================================================================
# Docker Entrypoint - Analysis job worker
# =============================================================================
# Runs an Arq worker process (interactive or batch queue) instead of the API
# server. Shares the "api" container's network namespace (see
# docker-compose.yml network_mode: service:api) so its hardcoded
# http://localhost:856x/mcp calls reach the MCP servers the api container
# hosts, exactly as the API process's own agent invocations do — no code
# changes needed to the MCP client URLs scattered across quant/stock_agent.
# =============================================================================
set -e

WORKER_SETTINGS="${1:-app.worker.InteractiveWorkerSettings}"

echo "[worker-entrypoint] Starting Arq worker: $WORKER_SETTINGS"
cd /app
exec arq "$WORKER_SETTINGS"
