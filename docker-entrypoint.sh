#!/usr/bin/env bash
# =============================================================================
# Docker Entrypoint - Investment Analyst API
# =============================================================================
# Starts the 4 MCP servers in background, then runs the main FastAPI app.
# =============================================================================

set -e

echo "[entrypoint] Starting MCP servers..."

# Start Stock Info MCP server (port 8565)
cd /app/quant/yahoo-finance-mcp
python server.py &
echo "[entrypoint] Stock Info MCP server starting on port 8565 (PID: $!)"

# Start Technical Analysis MCP server (port 8566)
cd /app/quant/Stock_Analysis
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

# Give MCP servers a moment to initialize before the API tries to connect
sleep 3

echo "[entrypoint] Running database migrations..."
cd /app
alembic upgrade head

echo "[entrypoint] Starting Investment Analyst API on port 8000..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
