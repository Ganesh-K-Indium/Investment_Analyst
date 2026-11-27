"""HTTP middleware shared by the API."""
import logging
import time

logger = logging.getLogger("api")


class RequestLoggingMiddleware:
    """
    Log every request: method, path, status code, and elapsed time.

    Pure ASGI middleware, deliberately NOT a BaseHTTPMiddleware subclass —
    BaseHTTPMiddleware relays the response through an internal buffered
    stream, which silently stalls long-lived StreamingResponse endpoints
    (the analysis-task SSE streams) until the connection closes instead of
    flushing each event as it's published. A pure ASGI middleware passes
    `send` straight through, so streamed chunks reach the client immediately.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, send_wrapper)

        ms = (time.perf_counter() - start) * 1000
        logger.info("%-6s %-45s %s  %.0fms", scope.get("method", "?"), scope.get("path", "?"), status_code, ms)
