"""Every signed-in API request is metered: whatever model or embedding calls it makes, on any provider, are stored against the
user when the request ends (app/services/usage_ledger.metered). Flows that store their own usage mark it stored, so nothing is
counted twice. A plain ASGI middleware, so it also covers streamed (SSE) responses until their last byte."""
from app.auth.jwt import decode_token


def _user_id(scope) -> str | None:
    for name, value in scope.get("headers") or []:
        if name == b"authorization":
            raw = value.decode("latin-1")
            if raw.lower().startswith("bearer "):
                payload = decode_token(raw[7:].strip())
                if payload and payload.get("type") == "access" and payload.get("sub"):
                    return str(payload["sub"])
    return None


class UsageMeterMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        user_id = _user_id(scope)
        if not user_id:
            return await self.app(scope, receive, send)
        from app.services import usage_ledger
        async with usage_ledger.metered(user_id, f"{scope.get('method', '')} {scope.get('path', '')}"):
            await self.app(scope, receive, send)
