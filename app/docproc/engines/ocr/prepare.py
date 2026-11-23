"""
Download an engine's models from the Reading engines page, in the background.

Only the engines this app knows, only when they are already installed, and never with user-supplied names or commands: the work is
"read the built-in sample page", which makes the library fetch its own models into the app's models folder (local.models_dir).
Installing the Python packages themselves stays a deployment step (pip, or the Docker image): doing that from a web request would let
the page change the server's own software.
"""
import asyncio
import logging
import time
from typing import Any, Dict, Optional, Set

from app.docproc import config
from app.docproc.engines.ocr import local, sample

logger = logging.getLogger("app.docproc.engines.ocr.prepare")

_state: Dict[str, Dict[str, Any]] = {}
_tasks: Set["asyncio.Task[None]"] = set()


class Refused(Exception):
    """The request cannot be carried out. The message is safe to show; `status` is the HTTP status to use."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def current(engine_id: str) -> Optional[Dict[str, Any]]:
    return _state.get(engine_id)


def running() -> bool:
    return any(s["state"] == "running" for s in _state.values())


async def _work(eng: local.Engine) -> None:
    try:
        reading = await asyncio.wait_for(eng.read(sample.page()), 900)
        ok = sample.found(reading.text) >= 0.75
        _state[eng.id] = {"state": "done" if ok else "failed", "error": "" if ok else f"{eng.label} loaded but read the sample page poorly."}
    except Exception as e:
        logger.warning("Preparing %s failed: %s", eng.id, e)
        _state[eng.id] = {"state": "failed", "error": f"{eng.label} could not load its models. Check the server's internet access and disk space, then try again."}


def start(engine_id: str) -> Dict[str, Any]:
    from app.docproc.engines.providers import credentials
    eng = local.get(engine_id)
    if eng is None:
        raise Refused("No such engine.", 404)
    if config.local_only():
        raise Refused("Local-only mode is on, so this server does not download anything. Download the models elsewhere and copy them into the models folder.", 403)
    if credentials.locked():
        raise Refused("Settings are locked on this server. Ask whoever runs it to download the models.", 403)
    if not eng.available():
        raise Refused(f"{eng.label} is not installed on this server. {eng.install}", 409)
    if local.model_status(engine_id)["state"] in ("bundled", "none"):
        raise Refused(f"{eng.label} has no models to download.", 409)
    if running():
        raise Refused("A download is already running. Wait for it to finish.", 409)
    _state[engine_id] = {"state": "running", "error": "", "started": time.time()}
    task = asyncio.get_running_loop().create_task(_work(eng))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return _state[engine_id]
