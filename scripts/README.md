# Launch scripts

Local development launchers. For Docker, use `docker compose` instead (see the main README).

## `start_api.sh`

Starts the API, and optionally the MCP servers and the background workers, from the repo's virtualenv.

```bash
./scripts/start_api.sh --all --dev    # MCP servers + workers + API with auto-reload: the usual local setup
./scripts/start_api.sh                # API only
./scripts/start_api.sh --help
```

| Option | Does |
|---|---|
| `--with-mcp` | Starts the five MCP servers first (via `start_mcp_servers.sh`). |
| `--with-workers` | Starts the interactive and batch workers next to the API; logs in `data/logs/workers/`; stopped with the API. |
| `--all` | Both of the above. |
| `--dev` | Auto-reload on code changes. |

Before starting it checks for the virtualenv (`venv/` at the repo root), `.env` (created from `.env.example` if missing), and installed dependencies.

## `start_mcp_servers.sh`

Manages the MCP servers the quant agents call.

```bash
./scripts/start_mcp_servers.sh start     # also: stop | restart | status | logs
```

| Server | Port | Folder |
|---|---|---|
| Stock information | 8565 | `backend/quant/yahoo_mcp` |
| Technical analysis | 8566 | `backend/quant/technical_mcp` |
| Research and ratings | 8567 | `backend/quant/research_mcp` |
| Options intelligence | 8568 | `backend/quant/options_mcp` |
| Postgres query | 8570 | `backend/quant/postgres_mcp` |

Logs and PID files go to `data/logs/mcp/`. Addresses can be changed with `MCP_<NAME>_URL` / `_BIND` / `_ENABLED` or `MCP_HOST` (see `config.example.yaml`).

## Windows

`start_api.bat` and `start_mcp_servers.bat` start the API and the MCP servers. The background workers (Arq) are best run under WSL or Docker on Windows.
