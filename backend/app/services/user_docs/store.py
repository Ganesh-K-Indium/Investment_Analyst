"""
Qdrant side of user documents: their own collection (`user_documents`), outside the `ticker_*` namespace, so SEC filing retrieval and
the filings registry never see them. Every chunk carries `metadata.owner_id` and `metadata.visibility`; every read goes through
`read_filter`, which is the only way chunks are fetched.

    private    only the owner can retrieve it
    workspace  everyone on this deployment can (the owner chose to share it)
"""
import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional

from langchain_core.documents import Document
from qdrant_client import models
from qdrant_client.http.models import PayloadSchemaType

logger = logging.getLogger("app.user_docs.store")

COLLECTION = "user_documents"
SOURCE_TYPE = "user_document"
PRIVATE, WORKSPACE = "private", "workspace"
_INDEXED = {"owner_id": PayloadSchemaType.KEYWORD, "visibility": PayloadSchemaType.KEYWORD, "doc_id": PayloadSchemaType.KEYWORD,
            "doc_type": PayloadSchemaType.KEYWORD, "entity": PayloadSchemaType.KEYWORD, "page_num": PayloadSchemaType.INTEGER,
            "review_state": PayloadSchemaType.KEYWORD}
_db = None
_ready = False


def _eq(key: str, value: Any) -> models.FieldCondition:
    return models.FieldCondition(key=f"metadata.{key}", match=models.MatchValue(value=value))


def read_filter(user_id: Any, entities: Optional[Iterable[str]] = None, doc_ids: Optional[Iterable[str]] = None,
                doc_types: Optional[Iterable[str]] = None) -> models.Filter:
    """What `user_id` may read: their own chunks and chunks shared with the workspace; optionally narrowed to entities, documents or types."""
    must: List[Any] = [models.Filter(should=[_eq("owner_id", str(user_id)), _eq("visibility", WORKSPACE)])]
    for key, vals in (("entity", entities), ("doc_id", doc_ids), ("doc_type", doc_types)):
        vals = sorted({str(v) for v in (vals or []) if v})
        if vals:
            must.append(models.FieldCondition(key=f"metadata.{key}", match=models.MatchAny(any=vals)))
    return models.Filter(must=must)


def _get_db(create: bool = False):
    global _db
    if _db is None or create:
        from rag.vectordb.client import load_vector_database
        _db = load_vector_database(collection_name=COLLECTION, create_if_missing=create)
    return _db


async def _ensure() -> Any:
    global _ready
    db = _get_db()
    if _ready:
        return db

    def setup():
        db.ensure_collection_exists()
        for field, schema in _INDEXED.items():
            try:
                db.qdrant_client.create_payload_index(collection_name=COLLECTION, field_name=f"metadata.{field}", field_schema=schema)
            except Exception as e:  # already there is fine
                logger.debug("payload index %s: %s", field, e)
    await asyncio.to_thread(setup)
    _ready = True
    return db


def embed_text(chunk: Dict[str, Any]) -> str:
    from ingestion.chunking import embedding_input
    return embedding_input(chunk["text"], chunk["metadata"].get("context_header"))


async def index_chunks(chunks: List[Dict[str, Any]]) -> int:
    if not chunks:
        return 0
    db = await _ensure()
    return await db.upsert_texts([{**c, "embed": embed_text(c)} for c in chunks], label="user document chunk(s)", batch_size=64, wait=True)


async def _delete(flt: models.Filter) -> None:
    try:
        await _get_db().async_qdrant_client.delete(collection_name=COLLECTION, points_selector=models.FilterSelector(filter=flt), wait=True)
    except Exception as e:
        if "not found" in str(e).lower() or "doesn't exist" in str(e).lower():
            return
        raise


async def delete_document(doc_id: str) -> None:
    await _delete(models.Filter(must=[_eq("doc_id", doc_id)]))


async def delete_page(doc_id: str, page: int) -> None:
    await _delete(models.Filter(must=[_eq("doc_id", doc_id), _eq("page_num", page)]))


async def set_metadata(doc_id: str, payload: Dict[str, Any]) -> None:
    """Change fields on the stored chunks (visibility, type, entity) without re-embedding."""
    await _get_db().async_qdrant_client.set_payload(collection_name=COLLECTION, payload=payload, key="metadata",
                                                    points=models.FilterSelector(filter=models.Filter(must=[_eq("doc_id", doc_id)])), wait=True)


def to_document(payload: Dict[str, Any]) -> Document:
    """A retrieved chunk as a Document. The header tells the model whose document this is and how far to trust it."""
    meta = dict(payload.get("metadata") or {})
    bits = [f"{meta.get('filename', 'uploaded document')}", meta.get("doc_type_label") or meta.get("doc_type"), f"page {meta.get('page_num')}" if meta.get("page_num") else None,
            f"amounts in {meta['units']}" if meta.get("units") else None, f"currency {meta['currency']}" if meta.get("currency") else None]
    low = " Low confidence: this text has not been confirmed by a person; say so if you rely on it." if meta.get("review_state") == "flagged" else ""
    header = (f"[USER DOCUMENT — {', '.join(str(b) for b in bits if b)}. This is a file the user provided, not a company filing: "
              f"cite it by name and page.{low}]")
    meta.update({"source_file": meta.get("filename", "uploaded document"), "source_type": SOURCE_TYPE, "filing_type": meta.get("doc_type_label") or meta.get("doc_type") or "User document",
                 "company": meta.get("entity"), "content_type": "text"})
    return Document(page_content=f"{header}\n{payload.get('page_content', '')}", metadata=meta)


async def has_documents(user_id: Any, entities: Optional[Iterable[str]] = None) -> bool:
    try:
        res = await _get_db().async_qdrant_client.count(collection_name=COLLECTION, count_filter=read_filter(user_id, entities), exact=False)
        return res.count > 0
    except Exception as e:
        logger.debug("user documents count skipped: %s", e)
        return False


async def search(user_id: Any, query: str, limit: int = 6, entities: Optional[Iterable[str]] = None, doc_ids: Optional[Iterable[str]] = None,
                 doc_types: Optional[Iterable[str]] = None) -> List[Document]:
    """Best chunks for `query` among what `user_id` may read. [] on any failure: filing retrieval never depends on this."""
    if not user_id or not await has_documents(user_id, entities):
        return []
    try:
        pts = await _get_db().hybrid_search(query=query, content_type="text", limit=limit, dense_limit=40, sparse_limit=40,
                                            extra_conditions=[read_filter(user_id, entities, doc_ids, doc_types)])
        return [to_document(p.payload) for p in pts if getattr(p, "payload", None)]
    except Exception as e:
        logger.warning("User document search failed, continuing without it: %s", e)
        return []
