import os
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt

logger = logging.getLogger("api")

APP_ENV = os.getenv("APP_ENV", "development")

_env_secret = os.getenv("JWT_SECRET_KEY")
if _env_secret:
    SECRET_KEY = _env_secret
elif APP_ENV == "production":
    # Refuse to start with a guessable/shared secret in production — anyone
    # who knows the old hardcoded fallback could forge valid tokens.
    raise RuntimeError(
        "JWT_SECRET_KEY must be set in the environment when APP_ENV=production."
    )
else:
    # Dev/test convenience: a random secret generated once per process start
    # instead of a fixed string baked into source control. Every restart
    # invalidates existing tokens, which is fine for local development.
    SECRET_KEY = secrets.token_hex(32)
    logger.warning(
        "JWT_SECRET_KEY not set — using an ephemeral random secret for this "
        "process (APP_ENV=%s). Set JWT_SECRET_KEY explicitly for any shared "
        "or persistent environment.", APP_ENV,
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))


def _create_token(data: dict, expires_delta: timedelta) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + expires_delta
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(user_id: int, email: str, role: str, token_version: int) -> str:
    return _create_token(
        {"sub": str(user_id), "email": email, "role": role, "type": "access", "ver": token_version},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(user_id: int, token_version: int) -> str:
    return _create_token(
        {"sub": str(user_id), "type": "refresh", "ver": token_version},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
