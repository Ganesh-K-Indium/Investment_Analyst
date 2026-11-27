#!/usr/bin/env python3
"""
Investment Analyst developer tool: one command set for Windows, macOS and Linux.

    python scripts/dev.py setup          first-time setup: virtualenv, pinned dependencies, browser, .env, web UI
    python scripts/dev.py start          services + MCP servers + workers + API (+ web UI), Ctrl+C stops everything
    python scripts/dev.py status         what is running, and the configuration check
    python scripts/dev.py mcp            only the MCP servers, in the foreground
    python scripts/dev.py services up    Postgres, Redis and Qdrant in Docker (also: down)
    python scripts/dev.py install        install backend/requirements.txt into the virtualenv (after a pull)
    python scripts/dev.py lock           re-resolve requirements.txt after editing backend/pyproject.toml
    python scripts/dev.py test           run the backend test suite

On Windows use `py scripts\\dev.py ...` if `python` is not on PATH. Needs Python 3.11-3.13 to create the virtualenv;
this script itself uses only the standard library.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
VENV = ROOT / "venv"
UI_DIR = Path(os.environ.get("UI_DIR") or ROOT.parent / "investment-analyst-ui")
LOGS = ROOT / "data" / "logs" / "dev"
IS_WINDOWS = os.name == "nt"
SUPPORTED = ((3, 11), (3, 13))

API_PORT = 8000
SERVICES = {"postgres": 5433, "redis": 6379, "qdrant": 6333}
MCP_SERVERS = [  # (name, folder, script, port)
    ("stock-info", "yahoo_mcp", "server.py", 8565),
    ("technical", "technical_mcp", "server_mcp.py", 8566),
    ("research", "research_mcp", "server_mcp.py", 8567),
    ("options", "options_mcp", "server_mcp.py", 8568),
    ("postgres-mcp", "postgres_mcp", "server_mcp.py", 8570),
]


# ── Output ───────────────────────────────────────────────────────────────────

def _supports_colour() -> bool:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if IS_WINDOWS:
        os.system("")  # enables ANSI colours in Windows 10+ consoles
    return True


_C = _supports_colour()


def say(msg: str, kind: str = "info") -> None:
    colour, mark = {"info": ("34", "•"), "ok": ("32", "✓"), "warn": ("33", "!"), "err": ("31", "✗"), "head": ("36", "─")}[kind]
    prefix = f"\033[{colour}m{mark}\033[0m" if _C else mark
    print(f"{prefix} {msg}", flush=True)


def die(msg: str) -> None:
    say(msg, "err")
    raise SystemExit(1)


# ── Environment helpers ──────────────────────────────────────────────────────

def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def require_venv() -> Path:
    py = venv_python()
    if not py.exists():
        die("No virtualenv yet. Run: python scripts/dev.py setup")
    return py


def run(cmd: list, cwd: Path | None = None, env: dict | None = None, check: bool = True) -> int:
    say(" ".join(str(c) for c in cmd), "info")
    code = subprocess.call([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=env)
    if check and code != 0:
        die(f"Command failed (exit {code}): {' '.join(str(c) for c in cmd)}")
    return code


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def wait_port(port: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if port_open(port):
            return True
        time.sleep(0.5)
    return False


def backend_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = str(venv_python().parent) + os.pathsep + env.get("PATH", "")      # `python` = the virtualenv's
    env["PYTHONPATH"] = str(BACKEND) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


# Code each kind of process imports, watched for --reload (paths relative to backend/).
BACKEND_CODE = ["app", "rag", "quant", "ingestion", "schemas"]


def reloading(command: str, watch: list) -> list:
    """Run `command` (e.g. "python -m app.worker batch") under watchfiles: restarted whenever a .py file in `watch`
    changes. The command names `python`, which is the virtualenv's because backend_env() puts it first on PATH."""
    return [venv_python(), "-m", "watchfiles", "--filter", "python", "--target-type", "command",
            "--sigint-timeout", "10", command, *[str(w) for w in watch]]


def docker_compose() -> list | None:
    if shutil.which("docker") and subprocess.call(["docker", "compose", "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return None


def npm() -> str | None:
    return shutil.which("npm.cmd" if IS_WINDOWS else "npm") or shutil.which("npm")


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_setup(args) -> None:
    say("Setting up Investment Analyst", "head")
    if not (SUPPORTED[0] <= sys.version_info[:2] <= SUPPORTED[1]):
        die(f"Python {sys.version.split()[0]} found; use Python 3.11, 3.12 or 3.13 to run setup "
            "(e.g. `py -3.12 scripts\\dev.py setup` on Windows, `python3.12 scripts/dev.py setup` elsewhere).")

    if not venv_python().exists():
        say(f"Creating virtualenv in {VENV}")
        run([sys.executable, "-m", "venv", VENV])
    cmd_install(args)

    say("Installing the Chromium build Playwright uses to render SEC filings")
    run([venv_python(), "-m", "playwright", "install", "chromium"], check=False)

    env_file, example = ROOT / ".env", ROOT / ".env.example"
    if not env_file.exists():
        shutil.copy(example, env_file)
        say("Created .env from .env.example: fill in OPENAI_API_KEY and the other keys", "warn")
    else:
        say(".env already exists (left unchanged)", "ok")

    if shutil.which("tesseract") or os.environ.get("TESSERACT_CMD"):
        say("Tesseract OCR found", "ok")
    else:
        say("Tesseract OCR not found: scans will not be read. Install it "
            "(Windows: UB-Mannheim installer, then set TESSERACT_CMD in .env · macOS: brew install tesseract · "
            "Linux: apt install tesseract-ocr)", "warn")
    say("Docker found" if docker_compose() else "Docker not found: install Docker Desktop to run Postgres, Redis and Qdrant", "ok" if docker_compose() else "warn")

    if not args.no_ui:
        if UI_DIR.exists() and npm():
            say(f"Installing the web UI in {UI_DIR}")
            run([npm(), "ci" if (UI_DIR / "package-lock.json").exists() else "install"], cwd=UI_DIR)
            ui_env, ui_example = UI_DIR / ".env", UI_DIR / ".env.example"
            if not ui_env.exists() and ui_example.exists():
                shutil.copy(ui_example, ui_env)
        elif not UI_DIR.exists():
            say(f"Web UI not found at {UI_DIR}: clone investment-analyst-ui next to this repo (or set UI_DIR)", "warn")
        else:
            say("Node.js not found: install Node 20.19+ (22 LTS) for the web UI", "warn")

    say("Setup complete. Next: fill in .env, then run  python scripts/dev.py start", "ok")


def cmd_install(args=None) -> None:
    py = require_venv()
    run([py, "-m", "pip", "install", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "-r", BACKEND / "requirements.txt"])
    say("Dependencies installed (exact versions from backend/requirements.txt)", "ok")


def cmd_lock(args) -> None:
    """pyproject.toml (what you edit) -> uv.lock -> requirements.txt (exact pins for every OS). Unchanged packages keep
    their versions; --upgrade moves everything to the newest allowed, --upgrade-package NAME just that one."""
    py = require_venv()
    if subprocess.call([str(py), "-m", "uv", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
        say("Installing uv (the resolver used to build the lock) into the virtualenv")
        run([py, "-m", "pip", "install", "uv"])
    os.environ.setdefault("UV_CACHE_DIR", str(ROOT / ".cache" / "uv"))     # project-local: works without a writable home cache
    lock = [py, "-m", "uv", "lock"]
    if args.upgrade:
        lock.append("--upgrade")
    for name in args.upgrade_package or []:
        lock += ["--upgrade-package", name]
    run(lock, cwd=BACKEND)
    run([py, "-m", "uv", "export", "--frozen", "--format", "requirements-txt", "--no-hashes", "--extra", "dev",
         "--no-emit-project", "--quiet", "-o", "requirements.txt"], cwd=BACKEND)
    text = (BACKEND / "requirements.txt").read_text()
    header = ("# GENERATED from pyproject.toml by `python scripts/dev.py lock`. Do not edit: add or change libraries in\n"
              "# pyproject.toml, then re-run the lock. Exact versions for Python 3.11-3.13 on Linux, macOS and Windows.\n")
    body = "\n".join(line for line in text.splitlines() if not line.startswith(("# This file was autogenerated", "#    uv export")))
    (BACKEND / "requirements.txt").write_text(header + body.lstrip("\n") + "\n")
    say("Updated backend/uv.lock and backend/requirements.txt. Next: python scripts/dev.py install, then commit both.", "ok")


def cmd_services(args) -> None:
    compose = docker_compose()
    if not compose:
        die("Docker is not available. Install Docker Desktop, or point DATABASE_URL / REDIS_URL / QDRANT_URL at your own services.")
    if args.action == "up":
        run(compose + ["up", "-d", "postgres", "redis", "qdrant"], cwd=ROOT)
        for name, port in SERVICES.items():
            say(f"{name} on :{port}", "ok" if wait_port(port, 30) else "warn")
    else:
        run(compose + ["stop", "postgres", "redis", "qdrant"], cwd=ROOT)


def cmd_status(args) -> None:
    rows = [("API", API_PORT)] + [(f"MCP {n}", p) for n, _, _, p in MCP_SERVERS] + [(n, p) for n, p in SERVICES.items()]
    for name, port in rows:
        say(f"{name:<18} :{port}  {'running' if port_open(port) else 'not running'}", "ok" if port_open(port) else "warn")
    if venv_python().exists():
        subprocess.call([str(venv_python()), "-m", "app.core.config"], cwd=str(BACKEND), env=backend_env())


def cmd_test(args) -> None:
    py = require_venv()
    raise SystemExit(subprocess.call([str(py), "-m", "pytest", *args.pytest_args], cwd=str(BACKEND), env=backend_env()))


def spawn_mcp(sup, py, name: str, folder: str, script: str, reload: bool) -> None:
    cwd = BACKEND / "quant" / folder
    cmd = reloading(f"python {script}", [cwd, BACKEND / "app"]) if reload else [py, script]
    sup.spawn(f"mcp-{name}", cmd, cwd=cwd)


class Supervisor:
    """Starts child processes, sends their output to log files (the API's to this console), stops them all on exit."""

    def __init__(self):
        self.procs: list[tuple[str, subprocess.Popen, object]] = []
        LOGS.mkdir(parents=True, exist_ok=True)

    def spawn(self, name: str, cmd: list, cwd: Path, env: dict | None = None, console: bool = False) -> subprocess.Popen:
        log = None if console else open(LOGS / f"{name}.log", "a", buffering=1, encoding="utf-8")
        # Each child gets its own process group, so stopping it also stops what it spawned (npm -> vite,
        # uvicorn --reload -> server). Ctrl+C reaches only this script, which then stops every group.
        extra = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WINDOWS else {"start_new_session": True}
        proc = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd), env=env or backend_env(),
                                stdout=log, stderr=subprocess.STDOUT if log else None, **extra)
        self.procs.append((name, proc, log))
        return proc

    @staticmethod
    def _signal_tree(proc: subprocess.Popen, force: bool) -> None:
        try:
            if IS_WINDOWS:
                subprocess.call(["taskkill", "/PID", str(proc.pid), "/T"] + (["/F"] if force else []),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def stop(self) -> None:
        for _, proc, _ in reversed(self.procs):
            self._signal_tree(proc, force=False)
        deadline = time.monotonic() + 10
        for _, proc, log in self.procs:
            try:
                proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
            self._signal_tree(proc, force=True)      # anything still alive in the group, including grandchildren
            if log:
                log.close()
        say("All processes stopped", "ok")


def cmd_start(args) -> None:
    py = require_venv()
    if not (ROOT / ".env").exists():
        die("No .env yet. Run: python scripts/dev.py setup  (then fill in the keys)")
    if port_open(API_PORT):
        die(f"Something is already listening on :{API_PORT}. Stop it, or run `python scripts/dev.py status`.")

    missing = [n for n, p in SERVICES.items() if not port_open(p)]
    if missing and not args.no_services:
        if docker_compose():
            say(f"Starting {', '.join(missing)} in Docker")
            cmd_services(argparse.Namespace(action="up"))
        else:
            say(f"{', '.join(missing)} not running and Docker not found: the API will fail to reach them", "warn")

    sup = Supervisor()

    def shutdown(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)     # explicit, so Ctrl+C works even where SIGINT was inherited as ignored
    try:
        if not args.no_mcp:
            for name, folder, script, port in MCP_SERVERS:
                if port_open(port):
                    say(f"MCP {name} already running on :{port}", "ok")
                    continue
                spawn_mcp(sup, py, name, folder, script, args.reload)
            for name, _, _, port in MCP_SERVERS:
                say(f"MCP {name} :{port}", "ok" if wait_port(port, 40) else "warn")

        if not args.no_workers:
            for kind in ("interactive", "batch"):
                cmd = (reloading(f"python -m app.worker {kind}", [BACKEND / d for d in BACKEND_CODE]) if args.reload
                       else [py, "-m", "app.worker", kind])
                sup.spawn(f"worker-{kind}", cmd, cwd=BACKEND)
            say("Workers started (interactive, batch)" + (", restarting on code changes" if args.reload else ""), "ok")

        ui = None
        if not args.no_ui and UI_DIR.exists() and npm() and (UI_DIR / "node_modules").exists():
            ui = sup.spawn("ui", [npm(), "run", "dev"], cwd=UI_DIR, env=dict(os.environ))
            say("Web UI starting on http://localhost:5173", "ok")
        elif not args.no_ui:
            say("Web UI not started (run setup, or clone investment-analyst-ui next to this repo)", "warn")

        say(f"Logs of the other processes: {LOGS}", "info")
        say(f"API on http://localhost:{API_PORT}  (docs /docs) · Ctrl+C stops everything", "head")
        api = [py, "-m", "uvicorn", "app.main:app", "--host", args.host, "--port", str(API_PORT)]
        if args.reload:
            api += ["--reload"] + [a for d in BACKEND_CODE for a in ("--reload-dir", d)]
        proc = sup.spawn("api", api, cwd=BACKEND, console=True)
        while proc.poll() is None:
            time.sleep(1)
            for name, child, _ in sup.procs:
                if child is not proc and child is not ui and child.poll() is not None and not getattr(child, "_reported", False):
                    child._reported = True
                    say(f"{name} exited (code {child.returncode}); see {LOGS / (name + '.log')}", "warn")
    except KeyboardInterrupt:
        print()
    finally:
        sup.stop()


def cmd_mcp(args) -> None:
    """Only the MCP servers, in the foreground (for running the API elsewhere, e.g. in a debugger)."""
    py = require_venv()
    sup = Supervisor()

    def shutdown(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        for name, folder, script, port in MCP_SERVERS:
            if port_open(port):
                say(f"MCP {name} already running on :{port}", "ok")
                continue
            spawn_mcp(sup, py, name, folder, script, args.reload)
        for name, _, _, port in MCP_SERVERS:
            say(f"MCP {name} :{port}", "ok" if wait_port(port, 40) else "warn")
        say(f"Logs: {LOGS} · Ctrl+C stops the servers", "head")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print()
    finally:
        sup.stop()


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(prog="dev.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="first-time setup")
    p.add_argument("--no-ui", action="store_true", help="skip the web UI")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("start", help="run everything for development")
    p.add_argument("--reload", action="store_true", help="restart the API, workers and MCP servers when their code changes")
    p.add_argument("--no-services", action="store_true", help="do not start Postgres/Redis/Qdrant in Docker")
    p.add_argument("--no-mcp", action="store_true", help="do not start the MCP servers")
    p.add_argument("--no-workers", action="store_true", help="do not start the background workers")
    p.add_argument("--no-ui", action="store_true", help="do not start the web UI")
    p.add_argument("--host", default="127.0.0.1", help="API bind address (0.0.0.0 to reach it from other machines)")
    p.set_defaults(func=cmd_start)

    sub.add_parser("status", help="what is running, and the configuration check").set_defaults(func=cmd_status)
    p = sub.add_parser("mcp", help="run only the five MCP servers (Ctrl+C stops them)")
    p.add_argument("--reload", action="store_true", help="restart a server when its code changes")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("services", help="Postgres, Redis and Qdrant in Docker")
    p.add_argument("action", choices=["up", "down"])
    p.set_defaults(func=cmd_services)

    sub.add_parser("install", help="install pinned dependencies into the virtualenv").set_defaults(func=cmd_install)

    p = sub.add_parser("lock", help="re-resolve requirements.txt from pyproject.toml")
    p.add_argument("--upgrade", action="store_true", help="move every package to the newest allowed version")
    p.add_argument("--upgrade-package", action="append", metavar="NAME", help="upgrade just this package (repeatable)")
    p.set_defaults(func=cmd_lock)

    p = sub.add_parser("test", help="run the backend tests (extra arguments go to pytest)")
    p.add_argument("pytest_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_test)

    args, extra = parser.parse_known_args()
    if args.command == "test":
        args.pytest_args = extra + list(args.pytest_args or [])      # e.g. dev.py test -k config -x
    elif extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    args.func(args)


if __name__ == "__main__":
    main()
