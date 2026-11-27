# Tests

```bash
cd backend
python ../scripts/dev.py test   # or plain `pytest` with the virtualenv active; about 2 minutes
pytest tests/test_config.py  # one file
pytest -k streaming          # by name
```

The suite needs no running Postgres, Redis or Qdrant. Where a test needs a database it uses a temporary SQLite file; where it needs Redis or a model it uses an in-memory stand-in. Six tests skip unless large sample filings are present in `backend/sample_data/`.

## Layout

| Path | Covers |
|---|---|
| `conftest.py` | Shared setup: every test runs against the code defaults (an empty `config.yaml`, no `SETTINGS__*` variables). |
| `test_config.py` | Deployment configuration: sources and precedence, validation, defaults for the Settings screen, `config.example.yaml` staying current. |
| `test_chat_stream.py` | Streamed chats relayed from a worker: frames, fallbacks, keepalives, abort on disconnect. |
| `test_settings_and_quant.py`, `test_api_settings_docs.py` | Product settings schema and API, quant runtime, settings-driven document behaviour. |
| `test_user_docs.py`, `test_file_import.py`, `test_documents_*.py`, `test_reading_engines_api.py` | Your documents: upload, reading, review and approval, import, the filing library. |
| `test_analyst_notes.py`, `test_handwriting_ocr.py` | Analyst notes and handwritten notes. |
| `test_providers.py`, `test_user_mcp.py`, `test_data_sources.py` | Model providers, users' MCP servers, market-data sources (URL safety, encryption, tool policy). |
| `test_rag_*.py`, `test_graph_extras.py`, `test_rerank.py`, `test_cited_quotes.py`, `test_verify_numbers.py`, `test_stream_graph.py`, `test_run_progress.py` | The RAG graph, retrieval, verification, streaming and progress. |
| `test_ingestion_chunking.py`, `test_vectordb_upsert.py` | Filing chunking and vector storage. |
| `docproc/` | The document-reading engine: OCR engines, digital pages, tables, figures, finance checks, the pipeline. |
| `rag_eval/golden.jsonl` | Question set for `scripts/rag_eval.py` (not collected by pytest). |

## Adding a test

1. Put it in the file for the area it covers, or a new `test_<area>.py`. Name the function after the behaviour, e.g. `test_a_closed_browser_stops_the_run`.
2. **No real services or model calls.** Use `tmp_path`, `monkeypatch`, and small fakes (see `FakePool` in `test_chat_stream.py`, or the fake readers in `docproc/`). A test must pass offline.
3. **Configuration:** change it with `monkeypatch.setenv("SETTING_NAME", ...)`; `get_settings()` picks the change up. For a config file, point `APP_CONFIG_FILE` at a file in `tmp_path` (see the `yaml_file` fixture).
4. **Product settings:** pass a layer to `app_settings.resolve(...)`, or run code inside `app_settings.use(settings)`.
5. Async code: call it with `asyncio.run(...)` inside a plain test function, as the rest of the suite does.
