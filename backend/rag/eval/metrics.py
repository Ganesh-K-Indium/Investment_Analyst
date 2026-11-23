"""Scoring for the RAG golden set. Pure functions: the runner (scripts/rag_eval.py) supplies the API responses."""
import statistics
from typing import Any, Dict, List, Optional

from rag.verify import numbers


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def fact_found(answer: str, fact: str) -> bool:
    """A fact is a phrase ("Services segment") or a figure ("$4.2 billion", "18.5%"); figures match by absolute value, so
    "4,200 million" satisfies "$4.2 billion"."""
    if _norm(fact) in _norm(answer):
        return True
    want = numbers.extract(fact)
    if len(want) == 1 and want[0]["digits"]:
        w = want[0]
        return any(abs(a["value"] - w["value"]) <= 0.005 * max(abs(w["value"]), 1e-9) and a["pct"] == w["pct"] for a in numbers.extract(answer))
    return False


def score_case(case: Dict[str, Any], resp: Dict[str, Any], latency: float) -> Dict[str, Any]:
    answer = resp.get("answer", "")
    docs = resp.get("documents") or []
    expected = case.get("expected") or {}
    facts: List[str] = expected.get("facts", [])
    found = [f for f in facts if fact_found(answer, f)]
    wrong = [f for f in expected.get("must_not_include", []) if _norm(f) in _norm(answer)]
    src_want = expected.get("sources", [])
    blob = " ".join(str((d.get("metadata") or d)) for d in docs).lower()
    src_hit = [s for s in src_want if s.lower() in blob]
    ver = (resp.get("verification") or {}).get("status")
    usage = resp.get("usage") or {}
    return {"id": case["id"], "question": case["question"], "scored": bool(facts or src_want),
            "fact_recall": (len(found) / len(facts)) if facts else None, "missing_facts": [f for f in facts if f not in found],
            "forbidden_found": wrong, "retrieval_hit": (len(src_hit) / len(src_want)) if src_want else None,
            "verification": ver, "unsupported_figures": len((resp.get("verification") or {}).get("issues", [])),
            "chunks": len(docs), "latency_s": round(latency, 2), "tokens": usage.get("total_tokens"),
            "cost_usd": (usage.get("cost") or {}).get("estimated_cost_usd"), "clarification": bool(resp.get("clarification_needed"))}


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    def mean(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(statistics.mean(vals), 3) if vals else None
    scored = [r for r in rows if r["scored"]]
    return {"cases": len(rows), "scored_cases": len(scored), "fact_recall": mean("fact_recall"), "retrieval_hit": mean("retrieval_hit"),
            "forbidden_hits": sum(1 for r in rows if r["forbidden_found"]),
            "verified_rate": round(sum(1 for r in rows if r["verification"] == "verified") / max(1, sum(1 for r in rows if r["verification"])), 3),
            "unsupported_figures": sum(r["unsupported_figures"] for r in rows), "mean_latency_s": mean("latency_s"),
            "total_tokens": sum(r["tokens"] or 0 for r in rows), "total_cost_usd": round(sum(r["cost_usd"] or 0 for r in rows), 4)}


def regressions(base: Dict[str, Any], new: Dict[str, Any], tolerance: float = 0.03) -> List[str]:
    """What got worse between two summaries (for CI): recall or retrieval dropping more than `tolerance`, forbidden facts appearing."""
    out = []
    for k in ("fact_recall", "retrieval_hit", "verified_rate"):
        if base.get(k) is not None and new.get(k) is not None and new[k] < base[k] - tolerance:
            out.append(f"{k} fell from {base[k]} to {new[k]}")
    if new.get("forbidden_hits", 0) > base.get("forbidden_hits", 0):
        out.append(f"forbidden facts appeared in {new['forbidden_hits']} answers (was {base.get('forbidden_hits', 0)})")
    return out
