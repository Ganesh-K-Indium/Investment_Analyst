#!/usr/bin/env python3
"""
Investment Analyst developer tool: one command set for Windows, macOS and Linux.

    python scripts/dev.py setup          first-time setup: virtualenv, pinned dependencies, browser, .env, web UI
    python scripts/dev.py start          services + MCP servers + workers + API (+ web UI), Ctrl+C stops everything
    python scripts/dev.py status         what is running, and the configuration check
    python scripts/dev.py stop           stop anything an earlier start left running (e.g. after closing its terminal)
    python scripts/dev.py mcp            only the MCP servers, in the foreground
    python scripts/dev.py services up    Postgres, Redis and Qdrant in Docker (also: down)
    python scripts/dev.py install        install backend/requirements.txt into the virtualenv (after a pull)
    python scripts/dev.py lock           re-resolve requirements.txt after editing backend/pyproject.toml
    python scripts/dev.py ocr            OCR status; `ocr add tesseract|rapidocr|paddleocr|doctr|all` installs an engine
    python scripts/dev.py test           run the backend test suite
    python scripts/dev.py check          everything a change must pass: backend tests, web UI lint + type check + build

On Windows use `py scripts\\dev.py ...` if `python` is not on PATH. Needs Python 3.11-3.13 to create the virtualenv;
this script itself uses only the standard library.
"""
from __future__ import annotations

import argparse
import os
import platform
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
FRONTEND = ROOT / "frontend"          # the web UI (React + Vite)
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

# Optional OCR engines: id -> label. Each is the pyproject extra `ocr-<id>`, exported to backend/requirements-ocr/<id>.txt.
OCR_ENGINES = {"rapidocr": "RapidOCR", "paddleocr": "PaddleOCR", "doctr": "docTR"}
OCR_REQUIREMENTS = BACKEND / "requirements-ocr"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"     # matches [[tool.uv.index]] in pyproject.toml
TESSERACT_HINT = ("Windows: winget install UB-Mannheim.TesseractOCR · macOS: brew install tesseract · "
                  "Debian/Ubuntu: sudo apt install tesseract-ocr · Fedora: sudo dnf install tesseract")


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


def port_open(port: int) -> bool:
    """Something is listening on this machine's port, over IPv4 or IPv6 (Vite, for one, may listen on ::1 only)."""
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


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


def find_tesseract() -> str | None:
    """TESSERACT_CMD, else PATH, else (Windows) the installer's default folders. Same lookup as the app's tess.binary()."""
    found = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if found or not IS_WINDOWS:
        return found
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        exe = Path(base or "") / "Tesseract-OCR" / "tesseract.exe"
        if base and exe.is_file():
            return str(exe)
    return None


def ocr_engine_unsupported(engine: str) -> str | None:
    """Why this machine has no pre-built package for the engine, or None when it has one."""
    mac_intel = sys.platform == "darwin" and platform.machine() == "x86_64"
    linux_arm = sys.platform.startswith("linux") and platform.machine() in ("aarch64", "arm64")
    if engine == "paddleocr" and (mac_intel or linux_arm):
        return "PaddlePaddle publishes packages for Linux x86-64, Windows x64 and Apple-silicon Macs only"
    if engine == "doctr" and mac_intel:
        return "PyTorch no longer publishes packages for Intel Macs"
    return None


def pip_has(py: Path, dist: str) -> bool:
    return subprocess.call([str(py), "-m", "pip", "show", "-q", dist], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


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

    if find_tesseract():
        say(f"Tesseract OCR found ({find_tesseract()})", "ok")
    else:
        say("Tesseract OCR not found: scans will not be read. Install it with  python scripts/dev.py ocr add tesseract  "
            f"or by hand ({TESSERACT_HINT})", "warn")
    for engine in args.ocr or []:
        ocr_add(engine)
    say("Docker found" if docker_compose() else "Docker not found: install Docker Desktop to run Postgres, Redis and Qdrant", "ok" if docker_compose() else "warn")

    if not args.no_ui:
        if FRONTEND.exists() and npm():
            say(f"Installing the web UI in {FRONTEND}")
            run([npm(), "ci" if (FRONTEND / "package-lock.json").exists() else "install"], cwd=FRONTEND)
            ui_env, ui_example = FRONTEND / ".env", FRONTEND / ".env.example"
            if not ui_env.exists() and ui_example.exists():
                shutil.copy(ui_example, ui_env)
        elif not FRONTEND.exists():
            say(f"Web UI not found at {FRONTEND}", "warn")
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

    # Each optional OCR engine: only the pins it adds on top of requirements.txt (one lock, so shared packages match).
    def pins(*extras: str) -> list:
        out = subprocess.run([str(py), "-m", "uv", "export", "--frozen", "--format", "requirements-txt", "--no-hashes",
                              "--no-annotate", "--no-emit-project", "--quiet", "--extra", "dev",
                              *[a for e in extras for a in ("--extra", e)]],
                             cwd=str(BACKEND), capture_output=True, text=True, check=True).stdout
        return [line for line in out.splitlines() if line and not line.startswith(("#", " ", "-"))]
    base = set(pins())
    OCR_REQUIREMENTS.mkdir(exist_ok=True)
    for engine, label in OCR_ENGINES.items():
        added = [line for line in pins(f"ocr-{engine}") if line not in base]
        lines = [f"# GENERATED from the `ocr-{engine}` extra in pyproject.toml by `python scripts/dev.py lock`. Do not edit.",
                 f"# Optional OCR engine {label}. Install: python scripts/dev.py ocr add {engine}", "-r ../requirements.txt"]
        if any(line.startswith(("torch==", "torchvision==")) for line in added):
            lines.append(f"--extra-index-url {TORCH_CPU_INDEX}")          # the CPU-only PyTorch build on Linux
        (OCR_REQUIREMENTS / f"{engine}.txt").write_text("\n".join(lines + added) + "\n")
    say("Updated backend/uv.lock, backend/requirements.txt and backend/requirements-ocr/. "
        "Next: python scripts/dev.py install, then commit them together.", "ok")


def ocr_add(engine: str) -> None:
    """Install one OCR engine: Tesseract through the system's package manager, the others from their locked
    requirements file, then download their models and check them on a sample page."""
    if engine == "tesseract":
        if find_tesseract():
            say(f"Tesseract already installed ({find_tesseract()})", "ok")
        elif IS_WINDOWS and shutil.which("winget"):
            run(["winget", "install", "--id", "UB-Mannheim.TesseractOCR", "-e", "--accept-package-agreements",
                 "--accept-source-agreements"], check=False)
            say("Tesseract installed: open a new terminal so it is found (TESSERACT_CMD is not needed)", "ok" if find_tesseract() else "warn")
        elif sys.platform == "darwin" and shutil.which("brew"):
            run(["brew", "install", "tesseract"])
        else:
            say(f"Install Tesseract with your system's package manager ({TESSERACT_HINT}), then re-run  python scripts/dev.py ocr", "warn")
        return

    py = require_venv()
    reason = ocr_engine_unsupported(engine)
    if reason:
        die(f"{OCR_ENGINES[engine]} cannot be installed on this machine: {reason}. Use RapidOCR, or the Docker image on Linux x86-64.")
    say(f"Installing {OCR_ENGINES[engine]} (exact versions from backend/requirements-ocr/{engine}.txt)", "head")
    run([py, "-m", "pip", "install", "-r", OCR_REQUIREMENTS / f"{engine}.txt"])
    # PaddleOCR uses opencv-contrib-python, the others opencv-python; both install the same `cv2` module. The contrib
    # build is a superset, so when Paddle shares the environment with another engine, its copy must be the one on disk.
    if engine != "paddleocr" and pip_has(py, "paddleocr"):
        contrib = next(line for line in (OCR_REQUIREMENTS / "paddleocr.txt").read_text().splitlines()
                       if line.startswith("opencv-contrib-python=="))
        run([py, "-m", "pip", "install", "--force-reinstall", "--no-deps", contrib.split(";")[0].strip()])
    say(f"Downloading {OCR_ENGINES[engine]}'s models (first time only) and checking it on a sample page", "head")
    run([py, "-m", "app.docproc.engines.warmup", engine], cwd=BACKEND, env=backend_env(), check=False)
    say(f"{OCR_ENGINES[engine]} ready. Restart the API, then choose it in Settings → Your documents → Reading engines.", "ok")


def cmd_ocr(args) -> None:
    if args.action == "add":
        if not args.engines:
            die(f"Name an engine: python scripts/dev.py ocr add rapidocr   (tesseract, {', '.join(OCR_ENGINES)} or all)")
        wanted = ["tesseract", *OCR_ENGINES] if "all" in args.engines else args.engines
        for engine in sorted(dict.fromkeys(wanted), key=lambda e: e == "paddleocr"):     # Paddle last (see ocr_add)
            if "all" in args.engines and ocr_engine_unsupported(engine):
                say(f"Skipping {OCR_ENGINES[engine]}: {ocr_engine_unsupported(engine)}", "warn")
                continue
            ocr_add(engine)
        return
    exe = find_tesseract()
    say(f"Tesseract  {'installed: ' + exe if exe else 'not installed. python scripts/dev.py ocr add tesseract'}", "ok" if exe else "warn")
    if exe:
        out = subprocess.run([exe, "--list-langs"], capture_output=True, text=True).stdout.splitlines()
        langs = [line.strip() for line in out[1:] if line.strip() and line.strip() != "osd"]
        say(f"Tesseract languages: {', '.join(langs) or 'none'} (TESSERACT_LANG picks them, e.g. eng+deu)", "info")
    py = require_venv()
    subprocess.call([str(py), "-m", "app.docproc.engines.warmup", "--status"], cwd=str(BACKEND), env=backend_env())
    say("Add an engine: python scripts/dev.py ocr add rapidocr   (also paddleocr, doctr, all)", "info")


def cmd_services(args) -> None:
    compose = docker_compose()
    if not compose:
        die("Docker is not available. Install Docker Desktop, or point DATABASE_URL / REDIS_URL / QDRANT_URL at your own services.")
    if args.action == "up":
        run(compose + ["up", "-d", "postgres", "redis", "qdrant"], cwd=ROOT)
        unreachable = [name for name, port in SERVICES.items() if not wait_port(port, 30)]
        if unreachable:
            # A container can be running without its port published on this machine (e.g. it started while another
            # project's container held the port). Recreating it publishes the port again; data lives in volumes.
            say(f"{', '.join(unreachable)} running but not reachable on this machine: recreating", "warn")
            run(compose + ["up", "-d", "--force-recreate", *unreachable], cwd=ROOT)
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


def cmd_backfill_insights(args) -> None:
    """Fill the dashboard fields (title, tickers, stance per ticker, cost) of runs made before the dashboard revamp.
    Reads what each run already stored; no model calls. Safe to run again."""
    py = require_venv()
    code = ("import asyncio\n"
            "from app.database.connection import SessionLocal, init_db\n"
            "from app.services.analysis_tasks import AnalysisTaskService\n"
            "from app.services import usage_ledger\n"
            "async def main():\n"
            "    async with SessionLocal() as db:\n"
            "        print(f'{await AnalysisTaskService.backfill(db)} runs updated')\n"
            "        print(f'{await usage_ledger.reprice(db)} usage records re-priced')\n"
            "init_db()\n"
            "asyncio.run(main())\n")
    run([py, "-c", code], cwd=BACKEND, env=backend_env())


def cmd_alpha_eval(args) -> None:
    """Live ALPHA evaluation (spends model budget): completeness, fact checks, revenue vs filings, stance stability."""
    py = require_venv()
    rest = args.rest[1:] if args.rest[:1] == ["--"] else args.rest     # `dev.py alpha-eval -- --tickers NVDA`
    run([py, "scripts/alpha_eval.py", *rest], cwd=BACKEND, env=backend_env())


def cmd_check(args) -> None:
    """Everything a change should pass before it is committed: backend tests, then the web UI's lint, type check and
    production build. Stops at the first failure."""
    py = require_venv()
    say("Backend: tests", "head")
    run([py, "-m", "pytest", "-q"], cwd=BACKEND, env=backend_env())
    if FRONTEND.exists():
        if not npm():
            die("Node.js is not installed: it is needed to check the web UI (Node 20.19+, 22 LTS recommended).")
        if not (FRONTEND / "node_modules").exists():
            run([npm(), "ci"], cwd=FRONTEND)
        say("Web UI: lint", "head")
        run([npm(), "run", "lint"], cwd=FRONTEND)
        say("Web UI: type check and production build", "head")
        run([npm(), "run", "build"], cwd=FRONTEND)
    say("All checks passed", "ok")


def cmd_test(args) -> None:
    py = require_venv()
    raise SystemExit(subprocess.call([str(py), "-m", "pytest", *args.pytest_args], cwd=str(BACKEND), env=backend_env()))


def spawn_mcp(sup, py, name: str, folder: str, script: str, reload: bool) -> None:
    cwd = BACKEND / "quant" / folder
    cmd = reloading(f"python {script}", [cwd, BACKEND / "app"]) if reload else [py, script]
    sup.spawn(f"mcp-{name}", cmd, cwd=cwd)


# Text each process's command line contains, checked before cleanup_stale() stops a recorded PID. The API's arguments are
# relative and the venv's python can show as the system interpreter, so it is recognised by its app path instead.
MARKERS = {"api": "app.main:app", "ui": "run dev"}
REGISTRY = LOGS / "processes.json"     # what the current/last `start` launched, so a crashed run can be cleaned up


def _command_of(pid: int) -> str:
    """The command line of a running process ('' if it is gone)."""
    try:
        if IS_WINDOWS:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                                 capture_output=True, text=True, timeout=10).stdout
        else:
            out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
        return out.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def cleanup_stale() -> int:
    """Stop whatever a previous `start`/`mcp` left running (it crashed, or its terminal was closed). Only processes whose
    command line still points into this project are touched, so a reused PID is never killed by mistake."""
    if not REGISTRY.exists():
        return 0
    import json
    try:
        entries = json.loads(REGISTRY.read_text())
    except ValueError:
        entries = []
    stopped = 0
    for entry in entries:
        cmd = _command_of(entry["pid"])
        if not cmd or entry.get("marker", str(ROOT)) not in cmd:
            continue
        say(f"Stopping {entry['name']} left over from an earlier run (pid {entry['pid']})", "warn")
        try:
            if IS_WINDOWS:
                subprocess.call(["taskkill", "/PID", str(entry["pid"]), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.killpg(entry["pid"], signal.SIGTERM)
                for _ in range(30):
                    if not _command_of(entry["pid"]):
                        break
                    time.sleep(0.2)
                else:
                    os.killpg(entry["pid"], signal.SIGKILL)
            stopped += 1
        except (ProcessLookupError, PermissionError, OSError):
            pass
    REGISTRY.unlink(missing_ok=True)
    return stopped


class Supervisor:
    """Starts child processes, sends their output to log files (the API's to this console), stops them all on exit.

    Ctrl+C: the first press stops everything gracefully (a few seconds while the API and workers finish cleanly); a
    second press during that stops them immediately. Never leaves processes behind, and records what it started in
    REGISTRY so even a crashed run is cleaned up by the next `start` or by `dev.py stop`."""

    GRACE_SECONDS = 8

    def __init__(self):
        self.procs: list[tuple[str, subprocess.Popen, object]] = []
        self.interrupted = False
        self.force = False
        LOGS.mkdir(parents=True, exist_ok=True)

    def install_signal_handlers(self) -> None:
        def handler(*_):
            if self.interrupted:          # pressed again while stopping: stop now, never crash
                self.force = True
                say("Stopping immediately", "warn")
                return
            self.interrupted = True
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, handler)      # explicit, so Ctrl+C works even where SIGINT was inherited as ignored
        signal.signal(signal.SIGTERM, handler)

    def _record(self) -> None:
        import json
        # marker: text the process's command line must contain for cleanup_stale() to touch it (guards against reused PIDs)
        REGISTRY.write_text(json.dumps([{"name": n, "pid": p.pid, "marker": MARKERS.get(n, str(ROOT))}
                                        for n, p, _ in self.procs if p.poll() is None]))

    def spawn(self, name: str, cmd: list, cwd: Path, env: dict | None = None, console: bool = False) -> subprocess.Popen:
        log = None if console else open(LOGS / f"{name}.log", "a", buffering=1, encoding="utf-8")
        # Each child gets its own process group, so stopping it also stops what it spawned (npm -> vite,
        # uvicorn --reload -> server, watchfiles -> worker). Ctrl+C reaches only this script, which stops every group.
        extra = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WINDOWS else {"start_new_session": True}
        proc = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd), env=env or backend_env(),
                                stdout=log, stderr=subprocess.STDOUT if log else None, **extra)
        self.procs.append((name, proc, log))
        self._record()
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
        self.interrupted = True                     # any further Ctrl+C now means "force", not "crash"
        alive = [n for n, p, _ in self.procs if p.poll() is None]
        if alive:
            say(f"Stopping {len(alive)} processes… (press Ctrl+C again to stop them immediately)", "info")
        for _, proc, _ in reversed(self.procs):
            self._signal_tree(proc, force=False)
        deadline = time.monotonic() + self.GRACE_SECONDS
        while time.monotonic() < deadline and not self.force and any(p.poll() is None for _, p, _ in self.procs):
            time.sleep(0.2)
        for _, proc, log in self.procs:
            self._signal_tree(proc, force=True)     # whatever is still alive in the group, including grandchildren
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            if log:
                log.close()
        REGISTRY.unlink(missing_ok=True)
        say("All processes stopped", "ok")


def cmd_stop(args) -> None:
    n = cleanup_stale()
    say(f"Stopped {n} leftover process group(s)" if n else "Nothing left running from an earlier start", "ok")


def cmd_start(args) -> None:
    py = require_venv()
    if not (ROOT / ".env").exists():
        die("No .env yet. Run: python scripts/dev.py setup  (then fill in the keys)")
    if cleanup_stale():
        time.sleep(1)
    if port_open(API_PORT):
        die(f"Something else is already listening on :{API_PORT}. Stop it (`python scripts/dev.py status` shows what is running).")

    missing = [n for n, p in SERVICES.items() if not port_open(p)]
    if missing and not args.no_services:
        if docker_compose():
            say(f"Starting {', '.join(missing)} in Docker")
            cmd_services(argparse.Namespace(action="up"))
        else:
            say(f"{', '.join(missing)} not running and Docker not found: the API will fail to reach them", "warn")

    sup = Supervisor()
    sup.install_signal_handlers()
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
        if not args.no_ui and port_open(5173):
            say("Web UI already running on http://localhost:5173 (left as is)", "ok")
        elif not args.no_ui and FRONTEND.exists() and npm() and (FRONTEND / "node_modules").exists():
            ui = sup.spawn("ui", [npm(), "run", "dev"], cwd=FRONTEND, env=dict(os.environ))
            say("Web UI starting on http://localhost:5173", "ok")
        elif not args.no_ui:
            say("Web UI not started: run `python scripts/dev.py setup` to install its packages", "warn")

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
    cleanup_stale()
    sup = Supervisor()
    sup.install_signal_handlers()
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
    p.add_argument("--ocr", action="append", choices=["tesseract", *OCR_ENGINES], metavar="ENGINE",
                   help="also install this OCR engine (repeatable): tesseract, " + ", ".join(OCR_ENGINES))
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
    sub.add_parser("stop", help="stop anything an earlier start left running").set_defaults(func=cmd_stop)
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

    p = sub.add_parser("ocr", help="OCR engines: status, or `add ENGINE...` to install one")
    p.add_argument("action", nargs="?", choices=["status", "add"], default="status")
    p.add_argument("engines", nargs="*", choices=["tesseract", *OCR_ENGINES, "all"], metavar="ENGINE",
                   help=f"tesseract, {', '.join(OCR_ENGINES)} or all")
    p.set_defaults(func=cmd_ocr)

    sub.add_parser("check", help="backend tests + web UI lint, type check and build").set_defaults(func=cmd_check)
    sub.add_parser("backfill-insights", help="fill dashboard fields of older runs and re-price stored usage; no model calls").set_defaults(func=cmd_backfill_insights)
    p = sub.add_parser("alpha-eval", help="live ALPHA evaluation on real tickers (spends model budget); extra arguments go to scripts/alpha_eval.py")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_alpha_eval)

    p = sub.add_parser("test", help="run the backend tests (extra arguments go to pytest)")
    p.add_argument("pytest_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_test)

    args, extra = parser.parse_known_args()
    if args.command == "test":
        args.pytest_args = extra + list(args.pytest_args or [])      # e.g. dev.py test -k config -x
    elif args.command == "alpha-eval":
        args.rest = extra + list(args.rest or [])                    # e.g. dev.py alpha-eval --tickers NVDA --runs 3
    elif extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    args.func(args)


if __name__ == "__main__":
    main()
