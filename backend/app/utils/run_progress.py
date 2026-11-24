"""
Live, step-by-step progress for long background runs (ALPHA, Compare, Ask).

One run (one AnalysisTask) owns one `RunProgress`. Code anywhere beneath it — graph nodes, helpers, sync or async, even
code running on a worker thread — reports with the module-level helpers below (`start`, `finish`, `step`, `lane`). They find
the current run through a ContextVar, exactly like app/utils/usage_tracker.py does, and they are NO-OPS when no run is
active (the plain synchronous HTTP routes). So instrumenting a node never changes how it behaves and never needs a
parameter threaded through its callers.

Contextvars are copied per asyncio Task, so concurrent lanes (ALPHA runs its tickers through asyncio.gather) stay isolated.

What gets reported is a *snapshot*: lanes (one per ticker for ALPHA, a single "main" lane otherwise), each a list of steps
with a status and timestamps. Snapshots are throttled and handed to an async sink, which the job runner uses to write them
into AnalysisTask.result_metadata["progress"] — from where they reach the browser through the existing SSE/polling paths.

Two kinds of lane:
  * planned   – the steps are known up front (ALPHA: read/analyse each of the five dimensions, then compile). They start as
                "pending", so the UI can show the whole checklist and a real percentage.
  * unplanned – steps are appended as they happen (the graph's nodes for Compare/Ask, whose path depends on the question).

Usage (job runner):
    progress = RunProgress("alpha", sink)
    with run_progress.use(progress):
        result = await alpha_core(...)
    progress.finish_all(); await progress.aclose()
    snapshot = progress.snapshot()
"""
import asyncio
import contextvars
import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("app.utils.run_progress")

# (snapshot, one-line headline) -> persisted/published by the caller
Sink = Callable[[Dict[str, Any], str], Awaitable[None]]

PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"
DEFAULT_LANE = "main"

# Human labels for graph nodes (the unplanned lanes). Unknown nodes fall back to their name.
NODE_LABELS: Dict[str, str] = {
    "route_query": "Understanding the question",
    "clarify": "Checking the scope",
    "preprocess": "Planning the searches",
    "retrieve": "Searching the documents",
    "grade_documents": "Checking the evidence",
    "rewrite_query": "Searching again with a better query",
    "web_search": "Searching the web",
    "integrate_web_search": "Adding web results",
    "generate": "Writing the answer",
    "verify_answer": "Checking the figures against the sources",
    "generate_chart": "Drawing the chart",
    "show_result": "Finishing up",
    "custom_retrieve": "Gathering evidence for each section",
    "custom_generate": "Writing the report",
    "scenario_retrieve": "Gathering evidence",
    "scenario_notes": "Reading your notes",
    "scenario_generate": "Writing the scenarios",
    "macro_analyze": "Planning the macro lookup",
    "macro_calculate": "Calculating",
    "macro_format": "Writing the answer",
}

# The five ALPHA dimensions, in the order the graph works through them.
ALPHA_DIMENSIONS: List[Tuple[str, str]] = [
    ("alignment", "Alignment"),
    ("liquidity", "Liquidity"),
    ("performance", "Performance"),
    ("horizon", "Horizon"),
    ("action", "Action"),
]
_READ_LABELS = {
    "alignment": "Reading insider activity and governance",
    "liquidity": "Reading macro and liquidity data",
    "performance": "Reading filings and financials",
    "horizon": "Researching the competitive position",
    "action": "Pulling technical signals",
}


def alpha_plan() -> List[Tuple[str, str]]:
    """Every step of one ticker's ALPHA run, in execution order: read all five dimensions, analyse all five, compile."""
    plan = [(f"read.{key}", _READ_LABELS[key]) for key, _ in ALPHA_DIMENSIONS]
    plan += [(f"write.{key}", f"Analysing {name.lower()}") for key, name in ALPHA_DIMENSIONS]
    plan.append(("combine", "Compiling the report"))
    return plan


def _now_ms() -> int:
    return int(time.time() * 1000)


class RunProgress:
    """Thread-safe progress state for one run, with throttled delivery to an async sink."""

    def __init__(self, kind: str, sink: Sink, loop: Optional[asyncio.AbstractEventLoop] = None, min_interval: float = 0.6):
        self.kind = kind
        self._sink = sink
        self._loop = loop or asyncio.get_running_loop()
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._lanes: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []
        self._started_at = _now_ms()
        # delivery state (touched on the loop thread, except _version)
        self._version = 0
        self._flushed_version = 0
        self._last_flush = 0.0
        self._timer: Optional[asyncio.TimerHandle] = None
        self._flushing = False
        self._closed = False
        self._tasks: set = set()

    # ── lanes & steps ──────────────────────────────────────────────────────────────────────────────────────────────

    def add_lane(self, lane_id: str, label: Optional[str] = None, plan: Optional[Sequence[Tuple[str, str]]] = None) -> None:
        """Register a lane (idempotent). With a plan, its steps exist from the start as 'pending'."""
        with self._lock:
            lane = self._lanes.get(lane_id)
            if lane is None:
                lane = {"id": lane_id, "label": label or lane_id, "planned": bool(plan), "steps": []}
                self._lanes[lane_id] = lane
                self._order.append(lane_id)
                for step_id, step_label in (plan or ()):
                    lane["steps"].append(self._new_step(step_id, step_label, PENDING))
        self._touch()

    def lane_is_planned(self, lane_id: Optional[str]) -> bool:
        with self._lock:
            lane = self._lanes.get(lane_id or DEFAULT_LANE)
            return bool(lane and lane["planned"])

    def start(self, step_id: str, label: Optional[str] = None, lane: Optional[str] = None, detail: Optional[str] = None) -> None:
        with self._lock:
            steps = self._lane_locked(lane)["steps"]
            rec = self._latest(steps, step_id)
            if rec is not None and rec["status"] == RUNNING:
                return  # already running: idempotent
            if rec is not None and rec["status"] == PENDING:
                rec["status"] = RUNNING
                rec["started_at"] = _now_ms()
            else:
                # first time, or re-entered after finishing (e.g. retrieve again after a corrective rewrite): a new record
                repeats = sum(1 for s in steps if self._base(s["id"]) == step_id)
                rec = self._new_step(step_id if repeats == 0 else f"{step_id}#{repeats + 1}", label or step_id, RUNNING)
                rec["started_at"] = _now_ms()
                steps.append(rec)
            if label:
                rec["label"] = label
            if detail:
                rec["detail"] = detail
        self._touch()

    def finish(self, step_id: str, lane: Optional[str] = None, detail: Optional[str] = None, ok: bool = True) -> None:
        with self._lock:
            steps = self._lane_locked(lane)["steps"]
            rec = self._latest(steps, step_id)
            if rec is None:
                rec = self._new_step(step_id, step_id, RUNNING)
                rec["started_at"] = _now_ms()
                steps.append(rec)
            rec["status"] = DONE if ok else FAILED
            rec["ended_at"] = _now_ms()
            if detail:
                rec["detail"] = detail
        self._touch()

    def finish_all(self, ok: bool = True) -> None:
        """Close out the run: running steps end (failed if the run failed); on success, steps that never ran count as done."""
        now = _now_ms()
        with self._lock:
            for lane in self._lanes.values():
                for rec in lane["steps"]:
                    if rec["status"] == RUNNING:
                        rec["status"] = DONE if ok else FAILED
                        rec["ended_at"] = now
                    elif rec["status"] == PENDING and ok:
                        rec["status"] = DONE
                        rec["started_at"] = rec["ended_at"] = now
        self._touch()

    # ── reading ────────────────────────────────────────────────────────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def headline(self) -> str:
        with self._lock:
            return self._headline_locked()

    def _snapshot_locked(self) -> Dict[str, Any]:
        return {
            "v": 1,
            "kind": self.kind,
            "started_at": self._started_at,
            "updated_at": _now_ms(),
            "lanes": [
                {"id": lane["id"], "label": lane["label"], "planned": lane["planned"], "steps": [dict(s) for s in lane["steps"]]}
                for lane in (self._lanes[i] for i in self._order)
            ],
        }

    def _headline_locked(self) -> str:
        """One line for places that only show text (the run list, notifications): what is happening now, with a count."""
        lanes = [self._lanes[i] for i in self._order]
        if not lanes:
            return "Starting…"
        total = sum(len(l["steps"]) for l in lanes if l["planned"])
        done = sum(1 for l in lanes if l["planned"] for s in l["steps"] if s["status"] in (DONE, FAILED))
        newest = None  # most recently started running step across lanes
        for lane in lanes:
            for s in lane["steps"]:
                if s["status"] == RUNNING and (newest is None or s["started_at"] >= newest[1]["started_at"]):
                    newest = (lane, s)
        if newest is None:
            return f"{done}/{total} steps done" if total else "Working…"
        lane, step = newest
        prefix = f"{lane['label']} · " if len(lanes) > 1 else ""
        count = f" ({done}/{total})" if total else ""
        return f"{prefix}{step['label']}{count}"

    # ── delivery (throttled, loop thread) ──────────────────────────────────────────────────────────────────────────

    def _touch(self) -> None:
        with self._lock:
            self._version += 1
        if self._closed:
            return
        try:
            self._loop.call_soon_threadsafe(self._arm)
        except RuntimeError:  # the loop is gone (shutdown): nothing left to deliver to
            pass

    def _arm(self) -> None:
        if self._closed or self._timer is not None or self._flushing:
            return
        delay = max(0.0, self._min_interval - (time.monotonic() - self._last_flush))
        self._timer = self._loop.call_later(delay, self._fire)

    def _fire(self) -> None:
        self._timer = None
        if self._closed:
            return
        task = self._loop.create_task(self._flush())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _flush(self) -> None:
        if self._flushing:
            return
        self._flushing = True
        try:
            while not self._closed:
                with self._lock:
                    version, snap, head = self._version, self._snapshot_locked(), self._headline_locked()
                if version == self._flushed_version:
                    break
                try:
                    await self._sink(snap, head)
                except Exception:  # noqa: BLE001 — progress is best-effort; never fail the run over it
                    logger.exception("Could not deliver run progress")
                self._flushed_version = version
                self._last_flush = time.monotonic()
                await asyncio.sleep(self._min_interval)
        finally:
            self._flushing = False
        if not self._closed and self._version != self._flushed_version:
            self._arm()  # something changed while we were finishing

    async def aclose(self) -> None:
        """Stop delivering and wait for any delivery in flight, so the caller's final status write can't be overtaken by a
        late 'running' progress write."""
        self._closed = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ── internals ──────────────────────────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _base(step_id: str) -> str:
        return step_id.split("#", 1)[0]

    @staticmethod
    def _new_step(step_id: str, label: str, status: str) -> Dict[str, Any]:
        return {"id": step_id, "label": label, "status": status, "started_at": None, "ended_at": None, "detail": None}

    def _latest(self, steps: List[Dict[str, Any]], step_id: str) -> Optional[Dict[str, Any]]:
        for rec in reversed(steps):
            if self._base(rec["id"]) == step_id:
                return rec
        return None

    def _lane_locked(self, lane_id: Optional[str]) -> Dict[str, Any]:
        lane_id = lane_id or DEFAULT_LANE
        lane = self._lanes.get(lane_id)
        if lane is None:
            lane = {"id": lane_id, "label": lane_id, "planned": False, "steps": []}
            self._lanes[lane_id] = lane
            self._order.append(lane_id)
        return lane


# ── context helpers (no-ops when no run is active) ───────────────────────────────────────────────────────────────────

_current: "contextvars.ContextVar[Optional[RunProgress]]" = contextvars.ContextVar("run_progress", default=None)
_lane: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("run_progress_lane", default=None)


def current() -> Optional[RunProgress]:
    return _current.get()


@contextmanager
def use(progress: RunProgress):
    """Make `progress` the active run for everything executed inside this block (including tasks it spawns)."""
    token = _current.set(progress)
    try:
        yield progress
    finally:
        _current.reset(token)


def register_lane(lane_id: str, label: Optional[str] = None, plan: Optional[Sequence[Tuple[str, str]]] = None) -> None:
    p = _current.get()
    if p is not None:
        p.add_lane(lane_id, label, plan)


@contextmanager
def lane(lane_id: str, label: Optional[str] = None, plan: Optional[Sequence[Tuple[str, str]]] = None):
    """Steps reported inside this block belong to `lane_id` (created on first use)."""
    p = _current.get()
    if p is None:
        yield
        return
    p.add_lane(lane_id, label, plan)
    token = _lane.set(lane_id)
    try:
        yield
    finally:
        _lane.reset(token)


def start(step_id: str, label: Optional[str] = None, detail: Optional[str] = None) -> None:
    p = _current.get()
    if p is not None:
        p.start(step_id, label, _lane.get(), detail)


def finish(step_id: str, detail: Optional[str] = None, ok: bool = True) -> None:
    p = _current.get()
    if p is not None:
        p.finish(step_id, _lane.get(), detail, ok)


@contextmanager
def step(step_id: str, label: Optional[str] = None):
    """start() on entry; finish() on exit, marked failed if the block raises."""
    start(step_id, label)
    try:
        yield
    except BaseException:
        finish(step_id, ok=False)
        raise
    else:
        finish(step_id)


def node_started(name: str) -> None:
    """Hook for graph nodes (rag/graph/benchmark.time_node). Skipped in planned lanes: ALPHA reports its own finer steps."""
    p = _current.get()
    if p is not None and not p.lane_is_planned(_lane.get()):
        p.start(f"node:{name}", NODE_LABELS.get(name, name), _lane.get())


def node_finished(name: str, ok: bool = True) -> None:
    p = _current.get()
    if p is not None and not p.lane_is_planned(_lane.get()):
        p.finish(f"node:{name}", _lane.get(), None, ok)
