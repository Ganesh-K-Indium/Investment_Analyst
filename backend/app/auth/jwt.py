"""Signing and reading login tokens.

Two tokens: a short-lived access token (sent as a bearer header, kept only in the browser's memory) and a long-lived refresh
token (an httpOnly cookie scoped to /auth). Every refresh token carries a `jti` stored in `refresh_tokens`, so each one can be
used once, rotated, and revoked; see app/api/auth.py.

The signing key: JWT_SECRET_KEY in production (the server refuses to start without it). In development, a random key is
created once in data/.jwt_secret and reused, so restarting the API, or `--reload`, does not sign everyone out.
"""
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

from app.core.config import get_settings

logger = logging.getLogger("api")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = get_settings().access_token_expire_minutes
REFRESH_TOKEN_EXPIRE_DAYS = get_settings().refresh_token_expire_days


def _load_secret() -> str:
    cfg = get_settings()
    if cfg.jwt_secret_key:
        return cfg.jwt_secret_key
    if cfg.is_production:
        raise RuntimeError("JWT_SECRET_KEY must be set in the environment when APP_ENV=production.")
    from app.core.paths import DATA_DIR
    f = DATA_DIR / ".jwt_secret"
    try:
        if not f.exists():
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            f.write_text(secrets.token_hex(32))
            os.chmod(f, 0o600)
        key = f.read_text().strip()
        if key:
            return key
    except OSError as e:
        logger.warning("Could not keep a development JWT key in %s (%s); using one for this process only.", f, e)
    return secrets.token_hex(32)


SECRET_KEY = _load_secret()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _create_token(data: dict, expires_delta: timedelta) -> str:
    now = _now()
    payload = {**data, "iat": now, "exp": now + expires_delta}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(user_id: int, email: str, role: str, token_version: int) -> str:
    return _create_token(
        {"sub": str(user_id), "email": email, "role": role, "type": "access", "ver": token_version},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def new_jti() -> str:
    return uuid.uuid4().hex


def create_refresh_token(user_id: int, token_version: int, jti: str, family: str) -> str:
    """`jti` names this token in refresh_tokens; `family` is the sign-in it descends from (one per device/browser)."""
    return _create_token(
        {"sub": str(user_id), "type": "refresh", "ver": token_version, "jti": jti, "fam": family},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
