import asyncio
import threading

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.utils import run_progress
from app.utils.run_progress import RunProgress, alpha_plan
from rag.graph.benchmark import time_node
from rag.graph.state import GraphState


def _run(coro):
    return asyncio.run(coro)


class Sink:
    def __init__(self):
        self.calls = []

    async def __call__(self, snapshot, headline):
        self.calls.append((snapshot, headline))


def _steps(snapshot, lane="main"):
    return next(l for l in snapshot["lanes"] if l["id"] == lane)["steps"]


def test_helpers_are_no_ops_without_a_run():
    # the plain synchronous HTTP routes run with no RunProgress; instrumented code must not care
    run_progress.start("x")
    run_progress.finish("x")
    run_progress.node_started("retrieve")
    with run_progress.step("y"):
        pass
    with run_progress.lane("GOOGL"):
        pass
    assert run_progress.current() is None


def test_unplanned_steps_run_and_repeat():
    async def go():
        p = RunProgress("ask", Sink())
        p.start("node:retrieve", "Searching the documents")
        p.finish("node:retrieve")
        p.start("node:retrieve", "Searching the documents")   # corrective retry re-enters the node
        steps = _steps(p.snapshot())
        assert [s["id"] for s in steps] == ["node:retrieve", "node:retrieve#2"]
        assert [s["status"] for s in steps] == ["done", "running"]
        assert steps[0]["ended_at"] >= steps[0]["started_at"]
        await p.aclose()
    _run(go())


def test_start_is_idempotent_while_running():
    async def go():
        p = RunProgress("ask", Sink())
        p.start("a"); p.start("a")
        assert len(_steps(p.snapshot())) == 1
        await p.aclose()
    _run(go())


def test_planned_lane_starts_pending_and_headline_counts():
    async def go():
        p = RunProgress("alpha", Sink())
        p.add_lane("GOOGL", "GOOGL", alpha_plan())
        steps = _steps(p.snapshot(), "GOOGL")
        assert len(steps) == 11 and {s["status"] for s in steps} == {"pending"}
        p.start("read.alignment", lane="GOOGL")
        p.finish("read.alignment", lane="GOOGL", detail="12 sources")
        p.start("read.liquidity", lane="GOOGL")
        assert p.headline() == "Reading macro and liquidity data (1/11)"
        assert _steps(p.snapshot(), "GOOGL")[0]["detail"] == "12 sources"
        await p.aclose()
    _run(go())


def test_headline_names_the_ticker_when_several_lanes():
    async def go():
        p = RunProgress("alpha", Sink())
        p.add_lane("AAPL", "AAPL", alpha_plan()); p.add_lane("MSFT", "MSFT", alpha_plan())
        p.start("write.horizon", lane="MSFT")
        assert p.headline().startswith("MSFT · Analysing horizon")
        await p.aclose()
    _run(go())


def test_finish_all_on_success_and_on_failure():
    async def go():
        ok = RunProgress("alpha", Sink()); ok.add_lane("A", "A", alpha_plan()); ok.start("read.alignment", lane="A")
        ok.finish_all()
        assert {s["status"] for s in _steps(ok.snapshot(), "A")} == {"done"}
        bad = RunProgress("alpha", Sink()); bad.add_lane("A", "A", alpha_plan()); bad.start("read.alignment", lane="A")
        bad.finish_all(ok=False)
        statuses = [s["status"] for s in _steps(bad.snapshot(), "A")]
        assert statuses[0] == "failed" and set(statuses[1:]) == {"pending"}   # the one that was running failed; the rest never ran
        await ok.aclose(); await bad.aclose()
    _run(go())


def test_delivery_is_throttled_and_the_last_state_is_never_lost():
    async def go():
        sink = Sink()
        p = RunProgress("ask", sink, min_interval=0.05)
        for i in range(200):
            p.start(f"s{i}"); p.finish(f"s{i}")
        await asyncio.sleep(0.3)
        await p.aclose()
        assert 1 <= len(sink.calls) < 20          # 400 changes, a handful of writes
        assert len(_steps(sink.calls[-1][0])) == 200 and all(s["status"] == "done" for s in _steps(sink.calls[-1][0]))
    _run(go())


def test_aclose_waits_for_delivery_in_flight_and_stops_later_ones():
    async def go():
        gate = asyncio.Event(); log = []

        async def slow_sink(snap, head):
            log.append("start"); await gate.wait(); log.append("end")

        p = RunProgress("ask", slow_sink, min_interval=0.0)
        p.start("a")
        await asyncio.sleep(0.05)               # delivery begins and blocks on the gate
        closer = asyncio.create_task(p.aclose())
        await asyncio.sleep(0.05)
        assert not closer.done()                # aclose is waiting for the write in flight
        gate.set(); await closer
        assert log == ["start", "end"]
        p.start("b"); await asyncio.sleep(0.05)
        assert log == ["start", "end"]          # nothing delivered after close
    _run(go())


def test_a_failing_sink_never_breaks_the_run():
    async def go():
        async def boom(snap, head):
            raise RuntimeError("db down")
        p = RunProgress("ask", boom, min_interval=0.01)
        p.start("a"); p.finish("a")
        await asyncio.sleep(0.1)
        await p.aclose()
        assert _steps(p.snapshot())[0]["status"] == "done"
    _run(go())


def test_reports_from_worker_threads_are_collected():
    async def go():
        sink = Sink()
        p = RunProgress("alpha", sink, min_interval=0.02)

        def work(n):
            with run_progress.use(p), run_progress.lane("T"):
                for i in range(25):
                    run_progress.start(f"t{n}.{i}"); run_progress.finish(f"t{n}.{i}")

        threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
        [t.start() for t in threads]
        await asyncio.get_running_loop().run_in_executor(None, lambda: [t.join() for t in threads])
        await asyncio.sleep(0.2)
        await p.aclose()
        assert len(_steps(p.snapshot(), "T")) == 100
    _run(go())


def test_concurrent_lanes_stay_isolated():
    async def go():
        p = RunProgress("alpha", Sink())

        async def one(ticker):
            with run_progress.lane(ticker, ticker, alpha_plan()):
                run_progress.start("read.alignment")
                await asyncio.sleep(0.01)
                run_progress.finish("read.alignment", detail=ticker)

        with run_progress.use(p):
            await asyncio.gather(*(one(t) for t in ("AAPL", "MSFT", "NVDA")))
        for t in ("AAPL", "MSFT", "NVDA"):
            first = _steps(p.snapshot(), t)[0]
            assert first["status"] == "done" and first["detail"] == t
        await p.aclose()
    _run(go())


def test_graph_nodes_report_through_time_node_including_sync_nodes():
    """The key assumption: a contextvar set around agent.ainvoke reaches nodes — sync ones run on a worker thread."""
    async def go():
        g = StateGraph(GraphState)
        g.add_node("retrieve", time_node("retrieve")(lambda s: {"Intermediate_message": "r"}))          # sync node
        async def agenerate(s):
            return {"messages": AIMessage(content="final")}
        g.add_node("generate", time_node("generate")(agenerate))                                         # async node
        g.add_edge(START, "retrieve"); g.add_edge("retrieve", "generate"); g.add_edge("generate", END)
        app = g.compile(checkpointer=MemorySaver())
        p = RunProgress("ask", Sink(), min_interval=0.01)
        with run_progress.use(p):
            await app.ainvoke({"messages": [HumanMessage(content="hi")]}, {"configurable": {"thread_id": "t"}})
        await p.aclose()
        steps = _steps(p.snapshot())
        assert [(s["id"], s["status"]) for s in steps] == [("node:retrieve", "done"), ("node:generate", "done")]
        assert steps[0]["label"] == "Searching the documents"
    _run(go())


def test_node_hook_is_skipped_inside_a_planned_lane():
    async def go():
        p = RunProgress("alpha", Sink())
        with run_progress.use(p), run_progress.lane("GOOGL", "GOOGL", alpha_plan()):
            run_progress.node_started("alpha_retrieve"); run_progress.node_finished("alpha_retrieve")
        assert all(not s["id"].startswith("node:") for s in _steps(p.snapshot(), "GOOGL"))
        await p.aclose()
    _run(go())
