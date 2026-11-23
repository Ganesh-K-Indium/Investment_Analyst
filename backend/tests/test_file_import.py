"""Importing from a connected source goes through the same document pipeline as an upload (extract, hold, approve)."""
import asyncio
from types import SimpleNamespace as NS

from app.services import file_import
from app.services.file_import import FileImportService
from app.services.user_docs import manager


class FakeConnector:
    def __init__(self, tmp_path):
        self.tmp = tmp_path

    def download_file(self, path):
        if "missing" in path:
            raise FileNotFoundError(path)
        f = self.tmp / path.split("/")[-1]
        f.write_bytes(b"%PDF-1.4 fake " + path.encode())
        return str(f)


def test_import_creates_documents_reports_failures_and_cleans_up(monkeypatch, tmp_path):
    settings = {"documents": {"max_upload_mb": 1}}
    monkeypatch.setattr(file_import.IntegrationService, "get_integration", staticmethod(lambda db, i: _async(NS(vendor="s3", credentials={}, url=None))))
    monkeypatch.setattr(file_import.BaseConnector, "get_connector", staticmethod(lambda **k: FakeConnector(tmp_path)))
    created, started = [], []

    async def fake_create(db, user_id, filename, data, settings, **kw):
        if filename == "dup.pdf":
            raise manager.DuplicateDocument(NS(id="old", filename="dup.pdf"))
        doc = NS(id=f"id-{filename}")
        created.append((filename, kw["entity"]))
        return doc

    monkeypatch.setattr(file_import.manager, "create_document", fake_create)
    monkeypatch.setattr(file_import.manager, "start", lambda doc_id, *a, **k: started.append(doc_id))

    rows = asyncio.run(FileImportService.import_to_documents(None, "u1", 1, ["a/report.pdf", "a/missing.pdf", "a/dup.pdf"], settings, entity="aapl"))

    assert [r["filename"] for r in rows] == ["report.pdf", "missing.pdf", "dup.pdf"]
    assert rows[0]["ok"] and rows[0]["doc"].id == "id-report.pdf"
    assert not rows[1]["ok"] and "Could not download" in rows[1]["error"]
    assert not rows[2]["ok"] and rows[2]["duplicate_of"] == "old"
    assert created == [("report.pdf", "aapl")] and started == ["id-report.pdf"]
    assert list(tmp_path.iterdir()) == []          # the temporary downloads are removed


async def _async(v):
    return v
