"""
Bring files from a connected source (SharePoint, S3, Azure Blob, Drive, SFTP...) into My documents.

Each file is downloaded and handed to the same pipeline as a direct upload: it is read by the extraction engine, held for the user's
review, and only indexed once they approve it. Nothing is written to the search index from here.
"""
import asyncio
import logging
import os
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.connectors.base import BaseConnector
from app.services.integration import IntegrationService
from app.services.user_docs import manager, normalize

logger = logging.getLogger(__name__)

MAX_FILES_PER_IMPORT = 20      # same cap as a direct upload
_DOWNLOADS_AT_ONCE = 3         # connector I/O is slow; a few in parallel is enough


class FileImportService:
    @staticmethod
    async def import_to_documents(db: AsyncSession, user_id: Any, integration_id: int, file_paths: List[str], settings: Dict[str, Dict[str, Any]],
                                  *, known_entities: Optional[List[str]] = None, doc_type: str = "auto", entity: Optional[str] = None,
                                  period: Optional[str] = None, tags: Optional[List[str]] = None, visibility: Optional[str] = None,
                                  reading: Optional[str] = None) -> List[Dict[str, Any]]:
        """Download each file and create a document for it. Returns one result per file, in the order given."""
        integration = await IntegrationService.get_integration(db, integration_id)
        if not integration:
            raise ValueError(f"Integration {integration_id} not found")
        connector = BaseConnector.get_connector(vendor=integration.vendor, credentials=integration.credentials, url=integration.url)
        gate = asyncio.Semaphore(_DOWNLOADS_AT_ONCE)
        max_bytes = settings["documents"]["max_upload_mb"] * 1024 * 1024

        async def fetch(path: str) -> Dict[str, Any]:
            name = PurePosixPath(path.replace("\\", "/")).name or "file"
            local = None
            try:
                async with gate:
                    local = await asyncio.to_thread(connector.download_file, path)   # connector I/O is synchronous
                if os.path.getsize(local) > max_bytes:
                    return {"filename": name, "ok": False, "error": f"The file is larger than {settings['documents']['max_upload_mb']} MB (your limit in Settings > Your documents)."}
                with open(local, "rb") as fh:
                    return {"filename": name, "data": fh.read()}
            except Exception as e:
                logger.warning("Import of %s failed: %s", path, e)
                return {"filename": name, "ok": False, "error": f"Could not download this file: {e}"}
            finally:
                if local and os.path.exists(local):
                    try:
                        os.remove(local)
                    except OSError:
                        pass

        fetched = await asyncio.gather(*(fetch(p) for p in file_paths))
        results: List[Dict[str, Any]] = []
        for item in fetched:
            if "data" not in item:
                results.append(item)
                continue
            try:
                doc = await manager.create_document(db, user_id, item["filename"], item["data"], settings, doc_type=doc_type, entity=entity, period=period,
                                                    tags=tags, visibility=visibility, reading=reading)
            except manager.DuplicateDocument as e:
                results.append({"filename": item["filename"], "ok": False, "duplicate_of": e.existing.id, "error": str(e)})
                continue
            except normalize.UnsupportedFile as e:
                results.append({"filename": item["filename"], "ok": False, "error": str(e)})
                continue
            manager.start(doc.id, str(user_id), settings, known_entities or [])
            results.append({"filename": item["filename"], "ok": True, "doc": doc})
        return results
