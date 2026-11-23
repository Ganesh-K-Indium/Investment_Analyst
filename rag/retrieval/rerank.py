"""
Second-stage re-ranking of retrieved chunks with a cross-encoder.

Hybrid search (dense + BM25, fused by RRF) finds candidates cheaply; a cross-encoder reads the question and each chunk TOGETHER
and scores how well the chunk answers it, which is far more precise. Providers:

    local    fastembed's ONNX cross-encoder on this machine (no new dependency, nothing leaves the server; the model downloads on first use)
    cohere   Cohere Rerank over HTTPS (COHERE_API_KEY). Sends the chunk text to Cohere: off unless the user chooses it
    off      keep the retrieval order

Any failure (model not downloadable, network down) keeps the retrieval order and says so in the log: a missing re-ranker must never
break an answer.
"""
import logging
import os
import threading
from typing import Any, List, Optional, Sequence, Tuple

logger = logging.getLogger("rag.retrieval.rerank")

LOCAL_MODEL = os.getenv("RERANK_LOCAL_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
COHERE_MODEL = os.getenv("RERANK_COHERE_MODEL", "rerank-english-v3.0")
MAX_CHARS = 2000                                    # a cross-encoder reads a few hundred tokens; the rest of a chunk adds cost, not signal

_local = None
_local_failed = False
_lock = threading.Lock()


def _local_encoder():
    global _local, _local_failed
    if _local is not None or _local_failed:
        return _local
    with _lock:
        if _local is None and not _local_failed:
            try:
                from fastembed.rerank.cross_encoder import TextCrossEncoder
                _local = TextCrossEncoder(model_name=LOCAL_MODEL)
                logger.info("Local re-ranker ready: %s", LOCAL_MODEL)
            except Exception as e:
                _local_failed = True
                logger.warning("Local re-ranker unavailable (%s); keeping retrieval order", e)
    return _local


def _scores_local(query: str, texts: Sequence[str]) -> Optional[List[float]]:
    enc = _local_encoder()
    if enc is None:
        return None
    return [float(s) for s in enc.rerank(query, [t[:MAX_CHARS] for t in texts])]


def _scores_cohere(query: str, texts: Sequence[str]) -> Optional[List[float]]:
    key = os.getenv("COHERE_API_KEY")
    if not key:
        logger.warning("COHERE_API_KEY not set; keeping retrieval order")
        return None
    import httpx
    r = httpx.post("https://api.cohere.com/v2/rerank", timeout=20,
                   headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                   json={"model": COHERE_MODEL, "query": query, "documents": [t[:MAX_CHARS] for t in texts], "top_n": len(texts)})
    r.raise_for_status()
    scores = [0.0] * len(texts)
    for item in r.json().get("results", []):
        scores[item["index"]] = float(item["relevance_score"])
    return scores


def order_by_scores(scores: Sequence[float]) -> List[int]:
    """Indexes best-first; ties keep the retrieval order (stable)."""
    return sorted(range(len(scores)), key=lambda i: -scores[i])


def rerank(query: str, docs: List[Any], provider: str = "local", top_n: Optional[int] = None) -> Tuple[List[Any], bool]:
    """Return (docs re-ordered best-first, optionally cut to top_n; whether re-ranking actually ran). Documents need `.page_content`."""
    if provider == "off" or len(docs) < 2:
        return docs, False
    texts = [getattr(d, "page_content", str(d)) for d in docs]
    try:
        scores = _scores_cohere(query, texts) if provider == "cohere" else _scores_local(query, texts)
    except Exception as e:
        logger.warning("Re-ranking failed (%s); keeping retrieval order", e)
        return docs, False
    if scores is None:
        return docs, False
    ordered = [docs[i] for i in order_by_scores(scores)]
    return (ordered[:top_n] if top_n else ordered), True
