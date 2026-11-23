"""
Run an OCR engine in a child process of its own (see worker.py for why) and talk to it.

The process starts on the first page, loads its model there (which is when a first-use download happens), and stays for the next pages.
If it dies, times out or answers badly it is killed and the page fails with a clear message; the next page starts a fresh one.
"""
import atexit
import json
import logging
import os
import struct
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Dict, List, Optional

import app.docproc as _app
from app.docproc import config

logger = logging.getLogger("app.docproc.engines.ocr.isolated")

FIRST_TIMEOUT = 900.0     # the first page includes loading, and possibly downloading, the model
TIMEOUT = 180.0
_BACKEND_ROOT = Path(_app.__file__).resolve().parents[2]      # the project root (the folder that holds the `app` package), wherever this file sits
_io = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ocr-worker-io")


class WorkerError(RuntimeError):
    """The engine process failed. The message is safe to show."""


class Worker:
    def __init__(self, label: str, command: List[str], env: Optional[Dict[str, str]] = None, log: Optional[Path] = None,
                 first_timeout: float = FIRST_TIMEOUT, timeout: float = TIMEOUT):
        self.label, self.command, self.env, self.log = label, command, env, log
        self.first_timeout, self.timeout = first_timeout, timeout
        self._proc: Optional["subprocess.Popen[bytes]"] = None
        self._served = 0
        self._lock = threading.Lock()                     # one request at a time per engine

    # -- process --------------------------------------------------------------------------------------------------------
    def _start(self) -> "subprocess.Popen[bytes]":
        stderr: Any = subprocess.DEVNULL
        if self.log is not None:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            stderr = open(self.log, "ab")
        try:
            return subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, cwd=str(_BACKEND_ROOT), env=self.env)
        finally:
            if stderr is not subprocess.DEVNULL:
                stderr.close()                            # the child has its own copy

    def stop(self) -> None:
        proc, self._proc, self._served = self._proc, None, 0
        if proc is None:
            return
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        for stream in (proc.stdout,):
            try:
                stream.close()
            except OSError:
                pass

    def _read(self, n: int, timeout: float) -> bytes:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        future = _io.submit(proc.stdout.read, n)
        try:
            data = future.result(timeout)
        except FutureTimeout:
            self.stop()                                   # also unblocks the reader thread
            raise WorkerError(f"{self.label} did not answer within {int(timeout)} seconds.")
        if len(data) < n:
            code = proc.poll()
            self.stop()
            raise WorkerError(f"{self.label} stopped unexpectedly" + (f" (exit code {code})" if code is not None else "") + f". See {self.log or 'the server log'}.")
        return data

    # -- one page -------------------------------------------------------------------------------------------------------
    def call(self, jpeg: bytes) -> List[List[Any]]:
        """Send a page, return its words as [x0, y0, x1, y1, text, confidence] rows."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self.stop()
                self._proc = self._start()
            proc = self._proc
            try:
                assert proc.stdin is not None
                proc.stdin.write(struct.pack(">I", len(jpeg)) + jpeg)
                proc.stdin.flush()
            except (OSError, ValueError):
                self.stop()
                raise WorkerError(f"{self.label} stopped unexpectedly. See {self.log or 'the server log'}.")
            timeout = self.first_timeout if self._served == 0 else self.timeout
            (n,) = struct.unpack(">I", self._read(4, timeout))
            body = self._read(n, timeout)
            self._served += 1
        try:
            reply = json.loads(body)
        except ValueError:
            self.stop()
            raise WorkerError(f"{self.label} sent an answer that could not be read.")
        if not reply.get("ok"):
            raise WorkerError(f"{self.label} could not read the page: {reply.get('error', 'unknown error')}")
        return reply["words"]


_workers: Dict[str, Worker] = {}
_registry_lock = threading.Lock()


def worker_for(engine_id: str, label: str) -> Worker:
    with _registry_lock:
        if engine_id not in _workers:
            from app.docproc.engines.ocr import local
            env = {**os.environ, "DOCPROC_DATA_DIR": str(config.data_dir()), "DOCPROC_MODELS_DIR": str(local.models_dir()), "PYTHONUNBUFFERED": "1"}
            _workers[engine_id] = Worker(label, [sys.executable, "-m", "app.docproc.engines.worker", engine_id], env, config.data_dir() / "logs" / f"ocr-{engine_id}.log")
        return _workers[engine_id]


def call(engine_id: str, label: str, jpeg: bytes) -> List[List[Any]]:
    return worker_for(engine_id, label).call(jpeg)


def shutdown() -> None:
    with _registry_lock:
        workers = list(_workers.values())
        _workers.clear()
    for w in workers:
        try:
            w.stop()
        except Exception:
            logger.exception("Could not stop %s cleanly", w.label)


atexit.register(shutdown)
