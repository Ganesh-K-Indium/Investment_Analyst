"""
Every MCP server the quant agents use, in one place: URL, port, enabled flag, auth.

Override per server with env vars (MCP_<KEY>_URL, MCP_<KEY>_ENABLED, MCP_<KEY>_BIND) or the shared MCP_HOST for all of them.
Nothing else in the app hardcodes a port: stock_agent.py, the health check, the agents and the start script all read this.
"""
import os
from typing import Any, Dict, List
from urllib.parse import urlparse

# key: (label, port, agent that needs it)
_DEFAULTS = {
    "stock_info": ("Stock Information", 8565, "stock_information"),
    "technical": ("Technical Analysis", 8566, "technical_analysis"),
    "research": ("Research", 8567, "research"),
    "options": ("Options Intelligence", 8568, "options_intelligence"),
    "postgres": ("Postgres", 8570, "postgres"),
}


def server(key: str) -> Dict[str, Any]:
    label, port, agent = _DEFAULTS[key]
    env = f"MCP_{key.upper()}"
    host = os.getenv("MCP_HOST", "127.0.0.1")
    url = os.getenv(f"{env}_URL") or f"http://{host}:{port}/mcp"
    return {"key": key, "label": label, "agent": agent, "url": url, "port": urlparse(url).port or port,
            "enabled": os.getenv(f"{env}_ENABLED", "1").lower() not in ("0", "false", "no"),
            # servers listen on loopback unless told otherwise: they have no login of their own
            "bind": os.getenv(f"{env}_BIND", "127.0.0.1")}


def all_servers() -> List[Dict[str, Any]]:
    return [server(k) for k in _DEFAULTS]


def url_for(key: str) -> str:
    return server(key)["url"]
