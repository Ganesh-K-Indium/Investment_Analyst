# Developer tool and launch scripts

## `dev.py`: one command set for Windows, macOS and Linux

Needs only Python (3.11–3.13 to create the virtualenv). On Windows write `py scripts\dev.py`.

| Command | Does |
|---|---|
| `python3 scripts/dev.py setup` | First-time setup: `venv/`, pinned dependencies, Chromium for SEC filings, `.env` from `.env.example`, the web UI's packages (`frontend/`, `npm ci`). Checks for Docker and Tesseract. |
| `python3 scripts/dev.py start` | Postgres, Redis and Qdrant in Docker (if not already running), the five MCP servers, both workers, the API and the web UI. The API logs to the terminal, the rest to `data/logs/dev/`. **Ctrl+C stops everything**, including child processes, within a few seconds; press it again to stop immediately. |
| `python3 scripts/dev.py status` | Which ports are serving, and the configuration check (`python -m app.core.config`). |
| `python3 scripts/dev.py stop` | Stop anything an earlier `start` left running (its terminal was closed, or it crashed). `start` also does this first. |
| `python3 scripts/dev.py services up` / `down` | Only Postgres, Redis and Qdrant. |
| `python3 scripts/dev.py mcp [--reload]` | Only the MCP servers, in the foreground. |
| `python3 scripts/dev.py install` | Install `backend/requirements.txt` (exact versions) into `venv/`. |
| `python3 scripts/dev.py lock [--upgrade] [--upgrade-package NAME]` | Re-pin dependencies after editing `backend/pyproject.toml`. |
| `python3 scripts/dev.py test [pytest args]` | Run the backend tests. |
| `python3 scripts/dev.py check` | Everything a change must pass: backend tests, then the web UI's lint, type check and production build (what CI runs). |

`start` options: `--reload` (restart the API, the workers and the MCP servers when their Python code changes; the web UI always hot-reloads), `--no-services`, `--no-mcp`, `--no-workers`, `--no-ui`, `--host 0.0.0.0` (reach the API from other machines).

| MCP server | Port | Folder |
|---|---|---|
| Stock information | 8565 | `backend/quant/yahoo_mcp` |
| Technical analysis | 8566 | `backend/quant/technical_mcp` |
| Research and ratings | 8567 | `backend/quant/research_mcp` |
| Options intelligence | 8568 | `backend/quant/options_mcp` |
| Postgres query | 8570 | `backend/quant/postgres_mcp` |

Addresses can be changed with `MCP_<NAME>_URL` / `_BIND` / `_ENABLED` or `MCP_HOST` (see `config.example.yaml`).

## Wrappers

| Script | Same as |
|---|---|
| `start_api.sh`, `start_api.bat` | `dev.py start` (`--dev` → `--reload`, `--api-only` → API alone) |
| `start_mcp_servers.bat` | `dev.py mcp` |
| `start_mcp_servers.sh start \| stop \| restart \| status \| logs` | Background MCP servers with PID files in `data/logs/mcp/` (macOS/Linux) |
