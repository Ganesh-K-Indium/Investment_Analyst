"""
In-memory sliding-window rate limiter for auth endpoints (login/signup).

In-memory is correct here because the API runs as a single uvicorn process
(no --workers flag in docker-entrypoint.sh) — if that ever changes to a
multi-process/multi-instance deployment, this needs to move to a shared
store (Redis) instead, since each process would otherwise track its own
independent counters.
"""
import time
from collections import defaultdict
from fastapi import HTTPException, Request, status

_WINDOW_SECONDS = 60
_MAX_ATTEMPTS = 10

# key -> list of attempt timestamps within the current window
_attempts: dict[str, list[float]] = defaultdict(list)


def _client_key(request: Request, discriminator: str) -> str:
    client_ip = request.client.host if request.client else "unknown"
    return f"{client_ip}:{discriminator}"


def enforce_rate_limit(request: Request, discriminator: str) -> None:
    """Raise 429 if this IP+identifier pair has exceeded the attempt budget."""
    key = _client_key(request, discriminator)
    now = time.monotonic()
    window_start = now - _WINDOW_SECONDS

    attempts = [t for t in _attempts[key] if t > window_start]
    if len(attempts) >= _MAX_ATTEMPTS:
        _attempts[key] = attempts
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait a minute and try again.",
        )

    attempts.append(now)
    _attempts[key] = attempts
