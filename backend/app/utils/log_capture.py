import logging
import asyncio
from contextvars import ContextVar
from datetime import datetime

# A ContextVar that holds a tuple of (asyncio.AbstractEventLoop, asyncio.Queue)
# When set, any logs processed by SSELogHandler in this context (or child threads)
# will be safely pushed to the queue.
sse_log_context: ContextVar[tuple] = ContextVar('sse_log_context', default=None)

class SSELogHandler(logging.Handler):
    """
    A logging handler that captures logs in the current asynchronous context
    and forwards them to an asyncio.Queue for SSE streaming to the client.
    Thread-safe for background worker threads (like asyncio.to_thread).
    """
    def emit(self, record: logging.LogRecord):
        ctx = sse_log_context.get()
        if ctx is None:
            return
            
        loop, queue = ctx
        
        try:
            msg = self.format(record)
            timestamp = datetime.fromtimestamp(record.created).strftime('%H:%M:%S')
            level = record.levelname
            
            # Format nicely for the UI terminal
            formatted_msg = f"[{timestamp}] [{level}] {msg}"
            
            # Thread-safe push to the queue
            if loop.is_running() and not loop.is_closed():
                loop.call_soon_threadsafe(queue.put_nowait, formatted_msg)
        except Exception:
            self.handleError(record)
