"""Streamed chats handed to the interactive worker (app/jobs/chat_stream.py): the relay, its fallbacks and its clean-up.

Redis and the worker are replaced by an in-memory stand-in, so these tests need no services. The real round trip (separate
worker process, real Redis) was checked by hand when this path was built; see the module docstring."""
import asyncio
import json

import pytest
from arq.jobs import JobStatus

import app.jobs.chat_stream as cs
import app.jobs.queue as queue

EVENTS = [{"type": "step", "node": "retrieve"}, {"type": "token", "text": "Hel"}, {"type": "token", "text": "lo"},
          {"type": "done", "result": {"answer": "Hello"}}]


class FakePubSub:
    def __init__(self, broker):
        self.broker, self.q, self.channel = broker, asyncio.Queue(), None

    async def subscribe(self, channel):
        self.channel = channel
        self.broker.subscribers[channel] = self.q

    async def get_message(self, ignore_subscribe_messages=True, timeout=None):
        try:
            data = await asyncio.wait_for(self.q.get(), timeout)
        except asyncio.TimeoutError:
            return None
        return {"type": "message", "data": data.encode()}

    async def unsubscribe(self, channel):
        self.broker.subscribers.pop(channel, None)

    async def close(self):
        self.broker.closed += 1


class FakeJob:
    def __init__(self, job_id, status=JobStatus.in_progress):
        self.job_id, self._status = job_id, status

    async def status(self):
        return self._status


class FakePool:
    """Plays Redis + one worker: enqueueing "runs" the job by publishing `events` on the stream's channel."""

    def __init__(self, alive=True, events=EVENTS, finish_status=JobStatus.in_progress):
        self.alive, self.events, self.finish_status = alive, events, finish_status
        self.subscribers, self.closed, self.aborted, self.enqueued = {}, 0, [], []

    async def exists(self, key):
        return 1 if self.alive and key == "interactive:health-check" else 0

    def pubsub(self):
        return FakePubSub(self)

    async def enqueue_job(self, function, stream_id, kind, payload, user_id, _queue_name, _job_id):
        self.enqueued.append((function, kind, payload, user_id, _queue_name))
        channel = cs._channel(stream_id)
        for event in self.events:
            await self.subscribers[channel].put(json.dumps(event))
        return FakeJob(_job_id, self.finish_status)

    async def zadd(self, key, mapping):
        self.aborted.extend(mapping)


@pytest.fixture()
def pool(monkeypatch):
    def install(**kw):
        p = FakePool(**kw)
        monkeypatch.setattr(queue, "_pool", p)
        return p
    monkeypatch.setenv("CHAT_STREAM_MODE", "auto")
    return install


async def _frames(response):
    return [chunk async for chunk in response.body_iterator]


def test_the_browser_gets_the_same_frames_as_from_the_in_process_path(pool):
    p = pool()
    response = asyncio.run(cs.stream_via_worker(cs.KIND_ASK, {"query": "q"}, 7))
    frames = asyncio.run(_frames(response))
    assert frames == [f"data: {json.dumps(e, default=str)}\n\n" for e in EVENTS]
    assert p.enqueued == [("run_chat_stream", "ask", {"query": "q"}, "7", "interactive")]
    assert p.closed == 1 and not p.subscribers and not p.aborted       # subscription released; a finished run is not aborted


def test_no_running_worker_means_the_chat_runs_in_the_api_process(pool):
    p = pool(alive=False)
    assert asyncio.run(cs.stream_via_worker(cs.KIND_ASK, {}, 7)) is None
    assert p.enqueued == []


def test_inline_mode_never_uses_the_worker(pool, monkeypatch):
    p = pool()
    monkeypatch.setenv("CHAT_STREAM_MODE", "inline")
    assert asyncio.run(cs.stream_via_worker(cs.KIND_QUANT, {}, 7)) is None and p.enqueued == []


def test_worker_mode_skips_the_health_check(pool, monkeypatch):
    p = pool(alive=False)
    monkeypatch.setenv("CHAT_STREAM_MODE", "worker")
    assert asyncio.run(cs.stream_via_worker(cs.KIND_ASK, {}, 7)) is not None and p.enqueued


def test_a_worker_that_stops_without_answering_ends_the_stream_with_an_error(pool, monkeypatch):
    monkeypatch.setattr(cs, "_IDLE_SECONDS", 0.6)
    pool(events=[{"type": "step", "node": "retrieve"}], finish_status=JobStatus.not_found)

    async def run():
        return await _frames(await cs.stream_via_worker(cs.KIND_ASK, {}, 7))
    frames = asyncio.run(run())
    last = json.loads(frames[-1][len("data: "):])
    assert last["type"] == "error" and last["status"] == 500


def test_a_quiet_run_sends_keepalives_while_the_job_is_still_going(pool, monkeypatch):
    monkeypatch.setattr(cs, "_IDLE_SECONDS", 0.6)
    p = pool(events=[], finish_status=JobStatus.in_progress)

    async def run():
        it = (await cs.stream_via_worker(cs.KIND_ASK, {}, 7)).body_iterator
        first = await asyncio.wait_for(it.__anext__(), 5)
        await it.aclose()                                              # the browser goes away
        return first
    assert asyncio.run(run()) == ": keepalive\n\n"
    assert len(p.aborted) == 1                                         # leaving mid-answer stops the worker's run
