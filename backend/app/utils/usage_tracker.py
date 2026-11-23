"""
Lightweight token/timing tracker for OpenAI calls made anywhere in the
ingestion pipeline or the RAG/alpha generation graph.

Why a contextvar instead of threading a tracker object through every function
signature: the calls we need to measure are buried several layers deep (e.g.
ingestion/image_data_prep.py's vision call, rag/vectordb/client.py's
embeddings call, individual LangChain chains inside rag/graph/nodes.py) and
passing a tracker parameter through all of them would touch a large number of
call sites for no benefit. `track_usage(...)` sets the active tracker for the
duration of one ingestion run or one RAG query; contextvars are copied
per-asyncio-Task, so concurrent runs (e.g. multiple ALPHA tickers via
asyncio.gather) each get their own isolated tracker automatically.

Usage:
    with track_usage("ingest:AAPL_10K_2024") as tracker:
        ... existing ingestion code, anywhere calls record_usage(...) ...
    # summary already logged on exit; tracker.summary() also available

    record_usage("ingestion.image_vision", "gpt-4o", prompt_tokens=1200,
                  completion_tokens=650, duration_seconds=4.2, detail="page_12_fig3.png")
"""
import contextvars
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("usage_tracker")


@dataclass
class UsageEntry:
    stage: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    duration_seconds: float = 0.0
    detail: str = ""


@dataclass
class UsageTracker:
    run_label: str
    entries: list = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    def record(self, stage: str, model: str, prompt_tokens: int = 0,
               completion_tokens: int = 0, total_tokens: int = 0,
               duration_seconds: float = 0.0, detail: str = "") -> UsageEntry:
        entry = UsageEntry(
            stage=stage,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens or (prompt_tokens + completion_tokens),
            duration_seconds=duration_seconds,
            detail=detail,
        )
        self.entries.append(entry)
        logger.info(
            "[usage] %s | %-28s | model=%-16s | tokens=%-7d (in=%d out=%d) | %6.2fs%s",
            self.run_label, stage, model, entry.total_tokens,
            prompt_tokens, completion_tokens, duration_seconds,
            f" | {detail}" if detail else "",
        )
        return entry

    def summary(self) -> dict:
        elapsed = time.time() - self.start_time
        by_stage: dict = {}
        for e in self.entries:
            agg = by_stage.setdefault(e.stage, {"calls": 0, "tokens": 0, "seconds": 0.0})
            agg["calls"] += 1
            agg["tokens"] += e.total_tokens
            agg["seconds"] += e.duration_seconds
        return {
            "run_label": self.run_label,
            "elapsed_seconds": round(elapsed, 2),
            "total_tokens": sum(e.total_tokens for e in self.entries),
            "total_prompt_tokens": sum(e.prompt_tokens for e in self.entries),
            "total_completion_tokens": sum(e.completion_tokens for e in self.entries),
            "by_stage": by_stage,
        }

    def log_summary(self):
        s = self.summary()
        logger.info("=" * 70)
        logger.info("USAGE SUMMARY | %s", s["run_label"])
        logger.info("Total time: %.2fs", s["elapsed_seconds"])
        logger.info(
            "Total tokens: %d (prompt=%d, completion=%d)",
            s["total_tokens"], s["total_prompt_tokens"], s["total_completion_tokens"],
        )
        for stage, agg in s["by_stage"].items():
            logger.info(
                "  - %-28s calls=%-3d tokens=%-8d time=%6.2fs",
                stage, agg["calls"], agg["tokens"], agg["seconds"],
            )
        logger.info("=" * 70)


_current_tracker: contextvars.ContextVar[Optional[UsageTracker]] = contextvars.ContextVar(
    "_current_usage_tracker", default=None
)


def get_current_tracker() -> Optional[UsageTracker]:
    return _current_tracker.get()


def record_usage(stage: str, model: str, prompt_tokens: int = 0,
                  completion_tokens: int = 0, total_tokens: int = 0,
                  duration_seconds: float = 0.0, detail: str = "") -> Optional[UsageEntry]:
    """No-op if no tracker is active for the current run (e.g. an ad-hoc
    script calling ingestion/RAG code outside of track_usage())."""
    tracker = _current_tracker.get()
    if tracker is None:
        return None
    return tracker.record(stage, model, prompt_tokens, completion_tokens,
                           total_tokens, duration_seconds, detail)


class track_usage:
    """Context manager (sync and async) that makes a fresh UsageTracker the
    active one for `run_label` for the duration of the block, and logs a
    full summary on exit — success or failure."""

    def __init__(self, run_label: str):
        self.run_label = run_label
        self.tracker = UsageTracker(run_label=run_label)
        self._token = None

    def __enter__(self) -> UsageTracker:
        self._token = _current_tracker.set(self.tracker)
        return self.tracker

    def __exit__(self, exc_type, exc, tb):
        self.tracker.log_summary()
        _current_tracker.reset(self._token)
        return False

    async def __aenter__(self) -> UsageTracker:
        return self.__enter__()

    async def __aexit__(self, exc_type, exc, tb):
        return self.__exit__(exc_type, exc, tb)
