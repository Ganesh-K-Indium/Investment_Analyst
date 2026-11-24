"""The job runners deliver live progress without ever letting it overtake the final status write."""
import asyncio
from types import SimpleNamespace

import pytest

import app.jobs.rag_jobs as jobs
from app.database.models import TaskStatus
from app.utils import run_progress


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def writes(monkeypatch):
    log = []

    async def update_status(db, task_id, status, progress_message=None, result_metadata=None, redis_client=None):
        await asyncio.sleep(0.01)   # a real DB write takes time: this is where a late progress write could land after the end
        log.append({"status": status, "message": progress_message, "meta": result_metadata})
        return SimpleNamespace(user_id="1")

    async def load_user(db, user_id):
        return object()

    monkeypatch.setattr(jobs, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(jobs.AnalysisTaskService, "update_status", staticmethod(update_status))
    monkeypatch.setattr(jobs, "_load_user", load_user)
    return log


def _payload():
    return {"user_id": "1", "thread_id": "t", "tickers": ["GOOGL"]}


def test_alpha_job_delivers_progress_then_completes_last(monkeypatch, writes):
    async def fake_alpha_core(payload, db, user, on_ticker_done=None):
        run_progress.register_lane("GOOGL", "GOOGL", run_progress.alpha_plan())
        with run_progress.lane("GOOGL"):
            for key, _ in run_progress.ALPHA_DIMENSIONS:
                run_progress.start(f"read.{key}"); await asyncio.sleep(0.02); run_progress.finish(f"read.{key}", detail="3 sources")
        return {"results": []}

    monkeypatch.setattr(jobs.rag_router_module, "alpha_core", fake_alpha_core)
    asyncio.run(jobs.run_rag_alpha({"redis": None}, "task-1", _payload()))

    assert writes[-1]["status"] == TaskStatus.COMPLETED                      # nothing is written after the end
    assert [w["status"] for w in writes].count(TaskStatus.COMPLETED) == 1
    final_progress = writes[-1]["meta"]["progress"]
    steps = final_progress["lanes"][0]["steps"]
    assert len(steps) == 11 and {s["status"] for s in steps} == {"done"}     # finish_all closes out the plan
    running = [w for w in writes[:-1] if w["status"] == TaskStatus.RUNNING and w["meta"] and "progress" in w["meta"]]
    assert running and all(w["message"] for w in running)                    # live updates carried a headline


def test_failed_job_keeps_its_step_record_and_fails_last(monkeypatch, writes):
    async def boom(payload, db, user, on_ticker_done=None):
        run_progress.register_lane("GOOGL", "GOOGL", run_progress.alpha_plan())
        with run_progress.lane("GOOGL"):
            run_progress.start("read.alignment")
            await asyncio.sleep(0.03)
        raise RuntimeError("provider timed out")

    monkeypatch.setattr(jobs.rag_router_module, "alpha_core", boom)
    asyncio.run(jobs.run_rag_alpha({"redis": None}, "task-2", _payload()))

    last = writes[-1]
    assert last["status"] == TaskStatus.FAILED and last["message"] == "provider timed out"
    steps = last["meta"]["progress"]["lanes"][0]["steps"]
    assert steps[0]["id"] == "read.alignment" and steps[0]["status"] == "failed"   # the step it died on
    assert all(s["status"] == "pending" for s in steps[1:])                         # the rest never ran


def test_compare_job_reports_graph_nodes(monkeypatch, writes):
    async def fake_compare_core(payload, db, user):
        run_progress.node_started("retrieve"); await asyncio.sleep(0.02); run_progress.node_finished("retrieve")
        run_progress.node_started("generate"); await asyncio.sleep(0.02); run_progress.node_finished("generate")
        return {"answer": "x"}

    monkeypatch.setattr(jobs.rag_router_module, "compare_core", fake_compare_core)
    asyncio.run(jobs.run_rag_compare({"redis": None}, "task-3", {"company1": "a", "company2": "b", "user_id": "1"}))

    final = writes[-1]
    assert final["status"] == TaskStatus.COMPLETED and final["meta"]["response"] == {"answer": "x"}
    steps = final["meta"]["progress"]["lanes"][0]["steps"]
    assert [(s["label"], s["status"]) for s in steps] == [("Searching the documents", "done"), ("Writing the answer", "done")]
