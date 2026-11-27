"""What a background run was and what came out of it, read from the request and the stored response. No model calls.

ALPHA runs return a structured result per ticker (rag/alpha/framework.py signal_record: stance from the pillar scores, each
pillar's score and verdict, fact-check findings); that is used when present. Reports from before it existed are read from their
own markers: one "## <letter> — <Pillar>"
section per pillar, every fact-bearing sentence wrapped as [[+|...]] / [[-|...]] / [[~|...]], and a closing summary whose
final sentence names the stance. A pillar's direction is the balance of its tagged facts; the stance is the last stance
phrase in the summary. Anything that cannot be read is left empty (None), never guessed."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

PILLARS = ("alignment", "liquidity", "performance", "horizon", "action")
STANCES = {"Bullish": 2, "Cautiously Bullish": 1, "Neutral": 0, "Cautiously Bearish": -1, "Bearish": -2}

_TAG = re.compile(r"\[\[([+\-~])\|(.+?)\]\]", re.S)
_STANCE = re.compile(r"\b(cautiously\s+bullish|cautiously\s+bearish|bullish|bearish|neutral)\b", re.I)
_HEADING = re.compile(r"^##\s+(.+)$", re.M)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[\"(])")
_RISK = re.compile(r"\b(risk|concern|headwind|pressure|decline|weak)", re.I)
_MD = re.compile(r"[*_`#>]+")


def strip_tags(text: str) -> str:
    """Report text with the sentiment markers removed (the sentence stays)."""
    return _TAG.sub(lambda m: m.group(2), text or "")


def _plain(text: str) -> str:
    return " ".join(_MD.sub("", strip_tags(text)).split())


def first_sentence(text: Optional[str], limit: int = 280) -> Optional[str]:
    """The first real sentence of an answer: headings, tables and list markers skipped."""
    if not text:
        return None
    for line in str(text).splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "|", "---", "```", "![", "<")):
            continue
        plain = _plain(line).lstrip("-•0123456789. ").strip()
        if len(plain) < 12 or plain.endswith(":"):     # a label introducing a list or table, not an answer
            continue
        sentence = _SENTENCE.split(plain, maxsplit=1)[0]
        return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"
    return None


def sections(report: str) -> Dict[str, str]:
    """Split an ALPHA report into {pillar|summary: text} by its "## " headings."""
    out: Dict[str, str] = {}
    marks = list(_HEADING.finditer(report or ""))
    for i, m in enumerate(marks):
        head = m.group(1).lower()
        body = report[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(report)]
        body = body.split("\n---", 1)[0] if "summary" in head else body.rstrip("-\n ")
        key = next((p for p in PILLARS if p in head), "summary" if "summary" in head else None)
        if key and key not in out:
            out[key] = body.strip()
    return out


def facts(text: str) -> Dict[str, List[str]]:
    """The tagged sentences of a text by sign: {"pos": [...], "neg": [...], "neutral": [...]}."""
    out: Dict[str, List[str]] = {"pos": [], "neg": [], "neutral": []}
    for sign, sentence in _TAG.findall(text or ""):
        out[{"+": "pos", "-": "neg", "~": "neutral"}[sign]].append(" ".join(sentence.split()))
    return out


def direction(text: str) -> Optional[str]:
    """positive / mixed / negative from the balance of a section's tagged facts; None when it states no tagged fact."""
    f = facts(text)
    pos, neg = len(f["pos"]), len(f["neg"])
    if pos + neg == 0:
        return "mixed" if f["neutral"] else None
    balance = (pos - neg) / (pos + neg)
    return "positive" if balance >= 0.34 else "negative" if balance <= -0.34 else "mixed"


def stance_of(summary: str) -> Optional[str]:
    found = _STANCE.findall(summary or "")
    if not found:
        return None
    words = " ".join(found[-1].split()).lower()
    return next(s for s in STANCES if s.lower() == words)


def key_risk(summary: str) -> Optional[str]:
    for sentence in _SENTENCE.split(_plain(summary)):
        if _RISK.search(sentence) and not _STANCE.search(sentence):
            return sentence[:400]
    return None


def parse_alpha_report(report: str) -> Dict[str, Any]:
    """{stance, stance_score, pillars, fact_counts, key_risk, summary} for one ticker's report. Missing parts are None."""
    parts = sections(report)
    summary = parts.get("summary", "")
    stance = stance_of(summary)
    all_facts = facts(report)
    stance_sentence = None
    for sentence in _SENTENCE.split(_plain(summary)):
        if _STANCE.search(sentence):
            stance_sentence = sentence
    return {
        "stance": stance,
        "stance_score": STANCES.get(stance) if stance else None,
        "pillars": {p: direction(parts[p]) if p in parts else None for p in PILLARS},
        "fact_counts": {k: len(v) for k, v in all_facts.items()},
        "key_risk": key_risk(summary) if summary else None,
        "summary": (stance_sentence or first_sentence(summary))[:400] if summary else None,
    }


def diff_reports(old: str, new: str) -> Dict[str, Any]:
    """What changed between two ALPHA reports of one ticker: pillar directions, stance, and facts that are new on each side."""
    a, b = parse_alpha_report(old), parse_alpha_report(new)
    fa, fb = facts(old), facts(new)

    def new_in(x: List[str], y: List[str]) -> List[str]:
        seen = {s.lower() for s in y}
        return [s for s in x if s.lower() not in seen][:12]

    return {
        "stance": {"before": a["stance"], "after": b["stance"], "change": (b["stance_score"] - a["stance_score"])
                   if a["stance_score"] is not None and b["stance_score"] is not None else None},
        "pillars": {p: {"before": a["pillars"][p], "after": b["pillars"][p]} for p in PILLARS},
        "new_negatives": new_in(fb["neg"], fa["neg"]),
        "new_positives": new_in(fb["pos"], fa["pos"]),
        "dropped_negatives": new_in(fa["neg"], fb["neg"]),
        "sections_before": {k: strip_tags(v) for k, v in sections(old).items()},
        "sections_after": {k: strip_tags(v) for k, v in sections(new).items()},
    }


# ── Run titles and tickers ────────────────────────────────────────────────────────────────────────────────────────

def _clip(text: str, n: int = 140) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _as_tickers(items: Iterable[Any]) -> List[str]:
    """Company names or tickers -> tickers (names the mapping does not know are kept as written)."""
    from app.utils.company_mapping import get_ticker
    out: List[str] = []
    for x in items or []:
        if not isinstance(x, str) or not x.strip():
            continue
        t = (get_ticker(x.strip()) or x.strip()).upper()
        if t not in out:
            out.append(t)
    return out


def _upper(items: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for x in items or []:
        if isinstance(x, str) and x.strip() and x.strip().upper() not in out:
            out.append(x.strip().upper())
    return out


def describe_request(task_type: str, payload: Dict[str, Any]) -> Tuple[str, List[str]]:
    """(title, tickers) known when a run is queued."""
    payload = payload or {}
    if task_type == "alpha":
        tickers = _upper(payload.get("tickers") or [])
        names = ", ".join(tickers[:4]) + (f" +{len(tickers) - 4}" if len(tickers) > 4 else "")
        return (f"ALPHA · {names}" if tickers else "ALPHA analysis"), tickers
    if task_type == "compare":
        names = [payload.get(k) for k in ("company1", "company2", "company3") if payload.get(k)]
        year = f" ({payload['year']})" if payload.get("year") else ""
        return (_clip(" vs ".join(names)) + year if names else "Company comparison"), []
    query = payload.get("query") or ""
    return (_clip(query) if query else ("Quant question" if task_type == "query" else "Research question")), []


def _first_human(messages: Any) -> Optional[str]:
    for m in messages or []:
        if isinstance(m, dict) and m.get("type") in ("human", "HumanMessage") and m.get("content"):
            return str(m["content"])
        if isinstance(m, str) and m.strip():
            return m
    return None


def describe_result(task_type: str, agent_type: str, response: Dict[str, Any]) -> Dict[str, Any]:
    """{title?, tickers, summary, outcome, signals} from a finished run's stored response. `signals` is one entry per
    ALPHA ticker; the other run kinds have none."""
    response = response or {}
    out: Dict[str, Any] = {"tickers": [], "summary": None, "outcome": None, "signals": [], "title": None}
    if task_type == "alpha":
        stances: Dict[str, Optional[str]] = {}
        for item in response.get("results") or []:
            if not isinstance(item, dict) or not item.get("ticker"):
                continue
            ticker = str(item["ticker"]).upper()
            parsed = parse_alpha_report(item.get("report") or "")
            sig = item.get("signal") if isinstance(item.get("signal"), dict) else None
            if sig:
                parsed.update({
                    "stance": sig.get("stance"), "stance_score": sig.get("stance_score"),
                    "pillars": {p: (sig.get("pillars") or {}).get(p) for p in PILLARS},
                    "fact_counts": sig.get("fact_counts") or parsed["fact_counts"],
                    "key_risk": sig.get("key_risk") or parsed["key_risk"],
                    "detail": {"pillars": sig.get("pillar_detail") or {}, "partial": bool(sig.get("partial")),
                               "missing": sig.get("missing") or [], "mean_score": sig.get("mean_score"),
                               "fact_issues": sig.get("fact_issues", 0), "company": sig.get("company")},
                })
            stances[ticker] = parsed["stance"]
            out["signals"].append({"ticker": ticker, **parsed})
        out["tickers"] = _upper(response.get("tickers") or list(stances))
        out["title"] = describe_request("alpha", {"tickers": out["tickers"]})[0] if out["tickers"] else None
        counts: Dict[str, int] = {}
        for s in stances.values():
            if s:
                counts[s] = counts.get(s, 0) + 1
        out["outcome"] = {"stances": stances, "stance_counts": counts}
        if len(out["signals"]) == 1:
            out["summary"] = out["signals"][0]["summary"]
        elif stances:
            out["summary"] = " · ".join(f"{n} {s}" for s, n in sorted(counts.items(), key=lambda kv: -STANCES.get(kv[0], 0))) or None
        return out
    if agent_type == "quant":
        out["summary"] = first_sentence(response.get("response"))
        meta = response.get("metadata") or {}
        out["tickers"] = _upper(meta.get("tickers") or ([meta["ticker"]] if isinstance(meta.get("ticker"), str) else []))
        out["outcome"] = {"agent": response.get("agent_used")} if response.get("agent_used") else None
        return out
    # ask / compare
    out["summary"] = first_sentence(response.get("answer"))
    filt = response.get("company_filter")
    out["tickers"] = _as_tickers(filt if isinstance(filt, list) else [filt] if isinstance(filt, str) else [])
    if not out["tickers"] and isinstance(response.get("ticker"), str):
        out["tickers"] = _upper([response["ticker"]])
    docs = response.get("documents") or []
    out["outcome"] = {"sources": len(docs) if isinstance(docs, list) else 0, "web": bool(response.get("web_searched")),
                      "chart": bool(response.get("chart_url"))}
    if task_type == "ask":
        q = _first_human(response.get("messages"))
        if q:
            out["title"] = _clip(q)
    elif task_type == "compare":
        names = [response.get(k) for k in ("company1", "company2", "company3") if response.get(k)]
        if names:
            out["title"] = _clip(" vs ".join(map(str, names)))
    return out


# ── Failures ──────────────────────────────────────────────────────────────────────────────────────────────────────

def classify_error(message: Optional[str]) -> str:
    m = (message or "").lower()
    if "cancel" in m:
        return "cancelled"
    if "timeout" in m or "timed out" in m:
        return "timeout"
    if any(k in m for k in ("api key", "api_key", "401", "403", "rate limit", "429", "quota", "provider", "not connected", "openai", "anthropic")):
        return "provider"
    if any(k in m for k in ("no documents", "no filings", "not found", "no data")):
        return "no_documents"
    return "other"


def failed_step_of(progress: Optional[Dict[str, Any]]) -> Optional[str]:
    """'<lane> · <step label>' of the first failed step in a progress snapshot (app/utils/run_progress.py)."""
    for lane in (progress or {}).get("lanes") or []:
        for step in lane.get("steps") or []:
            if step.get("status") == "failed":
                lane_label = lane.get("label")
                return f"{lane_label} · {step.get('label')}" if lane_label and lane_label != "main" else step.get("label")
    return None
