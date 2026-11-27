#!/usr/bin/env bash
# =============================================================================
# Docker Entrypoint - Investment Analyst API
# =============================================================================
# Starts the 5 MCP servers in background, then the FastAPI app (which migrates the database on startup).
# =============================================================================

set -e

echo "[entrypoint] Starting MCP servers..."

# Start Stock Info MCP server (port 8565)
cd /app/quant/yahoo_mcp
python server.py &
echo "[entrypoint] Stock Info MCP server starting on port 8565 (PID: $!)"

# Start Technical Analysis MCP server (port 8566)
cd /app/quant/technical_mcp
python server_mcp.py &
echo "[entrypoint] Technical Analysis MCP server starting on port 8566 (PID: $!)"

# Start Research MCP server (port 8567)
cd /app/quant/research_mcp
python server_mcp.py &
echo "[entrypoint] Research MCP server starting on port 8567 (PID: $!)"

# Start Options Intelligence MCP server (port 8568)
cd /app/quant/options_mcp
python server_mcp.py &
echo "[entrypoint] Options Intelligence MCP server starting on port 8568 (PID: $!)"

# Start Postgres query MCP server (port 8570): read-only queries against databases users connect
cd /app/quant/postgres_mcp
python server_mcp.py &
echo "[entrypoint] Postgres MCP server starting on port 8570 (PID: $!)"

# Give MCP servers a moment to initialize before the API tries to connect
sleep 3

# Database migrations run inside the API (and each worker) at startup, one process at a time (app/database/connection.py).
cd /app
echo "[entrypoint] Starting Investment Analyst API on port 8000..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
