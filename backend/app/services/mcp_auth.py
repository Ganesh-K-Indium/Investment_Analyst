"""
Signed request context for MCP tools, and the SQL guard for the Postgres tool.

The model never chooses WHOSE database a tool touches. The API signs {user, integration, expiry} with a server secret and puts
the resulting token in the prompt; the tool verifies the signature and checks the integration belongs to that user. A model (or
a prompt injection) that invents another user's integration id has no valid token for it.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Tuple


class AuthError(ValueError):
    pass


def _secret() -> bytes:
    """Shared by the API and the MCP server processes. Set MCP_SHARED_SECRET (or JWT_SECRET_KEY) for any real deployment; for local
    development a random secret is created once in data/.mcp_secret, because the API's own JWT secret is random per process and
    the MCP servers are separate processes that must agree with it."""
    s = os.getenv("MCP_SHARED_SECRET") or os.getenv("JWT_SECRET_KEY")
    if s:
        return s.encode()
    if os.getenv("APP_ENV", "development") == "production":
        raise RuntimeError("MCP_SHARED_SECRET (or JWT_SECRET_KEY) must be set when APP_ENV=production.")
    from app.core.paths import DATA_DIR as root
    f = root / ".mcp_secret"
    try:
        if not f.exists():
            root.mkdir(parents=True, exist_ok=True)
            f.write_text(os.urandom(32).hex())
            f.chmod(0o600)
        return f.read_text().strip().encode()
    except OSError as e:
        raise RuntimeError(f"cannot read or create the development MCP secret: {e}")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_integration(user_id, integration_id: int, ttl: int = 900) -> str:
    body = _b64(json.dumps({"u": str(user_id), "i": int(integration_id), "e": int(time.time()) + ttl}, separators=(",", ":")).encode())
    return f"{body}.{_b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())}"


def verify_integration(token) -> Tuple[str, int]:
    """Return (user_id, integration_id) from a valid, unexpired token, else raise AuthError."""
    try:
        body, sig = str(token).split(".", 1)
        good = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            raise AuthError("bad signature")
        data = json.loads(_unb64(body))
        if data["e"] < time.time():
            raise AuthError("expired")
        return str(data["u"]), int(data["i"])
    except AuthError:
        raise
    except Exception as e:
        raise AuthError("malformed token") from e


# ---- SQL guard -------------------------------------------------------------------------------------------------------------

_FORBIDDEN = {"insert", "update", "delete", "drop", "alter", "create", "grant", "revoke", "truncate", "copy", "call", "do", "execute",
              "merge", "vacuum", "analyze", "lock", "listen", "notify", "set", "reset", "comment", "refresh", "reindex", "cluster",
              "pg_sleep", "pg_read_file", "pg_ls_dir", "lo_import", "lo_export", "dblink", "into"}


def _strip(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)           # string literals can contain anything
    return re.sub(r'"(?:[^"]|"")*"', '"x"', sql)          # quoted identifiers too


def check_select_only(sql: str) -> str:
    """Return the query to run (one statement, with a row limit) or raise AuthError. Defence in depth: the connection is also
    read-only with a statement timeout, so this is the readable error, not the only barrier."""
    clean = _strip(sql).strip().rstrip(";").strip()
    if not clean:
        raise AuthError("Empty query.")
    if ";" in clean:
        raise AuthError("Only one statement is allowed.")
    words = re.findall(r"[a-z_][a-z0-9_]*", clean.lower())
    if words[0] not in ("select", "with"):
        raise AuthError("Only SELECT or WITH queries are allowed.")
    bad = sorted(set(words) & _FORBIDDEN)
    if bad:
        raise AuthError(f"Not allowed in a read-only query: {', '.join(bad)}.")
    out = sql.strip().rstrip(";").strip()
    if "limit" not in words:
        out += " LIMIT 100"
    return out
