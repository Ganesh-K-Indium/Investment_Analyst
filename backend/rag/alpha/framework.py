"""The ALPHA framework's deterministic parts.

How a report is made (rag/graph/nodes.py alpha_generate_report):
  1. Each of the five pillars is written by a model from its own sources, with a score from -2 to +2, a one-line recommendation
     and its fact-bearing sentences tagged [[+|..]] / [[-|..]] / [[~|..]]. Pillars run in parallel; Horizon waits for
     Performance because it must not contradict it.
  2. Every pillar is fact-checked against the sources it was written from (`check_section`): figures that are not in the
     sources and are not a calculation from figures that are, and trend words that the section's own figures contradict.
     A pillar with problems is revised once; anything still unsupported is shown under the pillar.
  3. When live market data is available, the Action pillar is written here from it (`action_from_market_data`), not by a model.
  4. The stance is computed from the pillar scores (`stance_from_scores`); the summary model is told the stance and only explains
     it. A pillar that could not be written is left out and the report says so (`partial`).
  5. The report is assembled here (`assemble_report`), so the pillar text reaches the reader exactly as checked."""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

PILLARS: List[Tuple[str, str, str]] = [
    ("alignment", "A", "Alignment (Stakeholder & Insider Signals)"),
    ("liquidity", "L", "Liquidity (Macro/Micro Operating Environment)"),
    ("performance", "P", "Performance (Earnings Quality & Fundamentals)"),
    ("horizon", "H", "Horizon (Competitive Moat & Structural Opportunity)"),
    ("action", "A", "Action (Technical Timing & Valuation Context)"),
]
PILLAR_NAMES = {k: t.split(" (")[0] for k, _, t in PILLARS}

STANCES = ["Bearish", "Cautiously Bearish", "Neutral", "Cautiously Bullish", "Bullish"]
STANCE_SCORE = {"Bullish": 2, "Cautiously Bullish": 1, "Neutral": 0, "Cautiously Bearish": -1, "Bearish": -2}
MIN_PILLARS_FOR_STANCE = 3

DISCLAIMER = ("*Analysis based on SEC filings, publicly available financial data, and web-sourced market information. "
              "For informational purposes only — does not constitute investment advice or a solicitation to trade.*")

_TAG = re.compile(r"\[\[([+\-~])\|(.+?)\]\]", re.S)


def strip_tags(text: str) -> str:
    return _TAG.sub(lambda m: m.group(2), text or "")


# ── Scores and stance ───────────────────────────────────────────────────────────────────────────────────────────────

_POS = re.compile(r"\b(positive|bullish|buy|supportive|strong|tailwind|favou?rable|constructive|accumulate|upside)\b", re.I)
_NEG = re.compile(r"\b(negative|bearish|sell|weak|headwind|unfavou?rable|caution|concern|risk|deteriorat\w*|avoid|reduce|trim)\b", re.I)


def clamp_score(value: Any) -> Optional[int]:
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(-2, min(2, v))


def score_from_text(recommendation: str) -> int:
    """A score for a pillar whose model gave none: from its recommendation's wording (the model's own verdict)."""
    text = recommendation or ""
    pos, neg = len(_POS.findall(text)), len(_NEG.findall(text))
    if re.search(r"\b(neutral|hold|mixed|balanced)\b", text, re.I) and abs(pos - neg) <= 1:
        return 0
    return 1 if pos > neg else -1 if neg > pos else 0


def direction(score: Optional[int]) -> Optional[str]:
    if score is None:
        return None
    return "positive" if score > 0 else "negative" if score < 0 else "mixed"


def stance_from_scores(scores: Dict[str, Optional[int]]) -> Dict[str, Any]:
    """The overall stance from the pillars that could be written: the mean of their scores, in fixed bands. Equal weights,
    so the stance can always be explained pillar by pillar. With fewer than three pillars there is no stance."""
    used = {k: s for k, s in scores.items() if s is not None}
    missing = [k for k, _, _ in PILLARS if k not in used]
    if len(used) < MIN_PILLARS_FOR_STANCE:
        return {"stance": None, "stance_score": None, "mean": None, "used": sorted(used), "missing": missing, "partial": True}
    mean = sum(used.values()) / len(used)
    if mean >= 1.25:
        stance = "Bullish"
    elif mean >= 0.5:
        stance = "Cautiously Bullish"
    elif mean > -0.5:
        stance = "Neutral"
    elif mean > -1.25:
        stance = "Cautiously Bearish"
    else:
        stance = "Bearish"
    return {"stance": stance, "stance_score": STANCE_SCORE[stance], "mean": round(mean, 2), "used": sorted(used),
            "missing": missing, "partial": bool(missing)}


# ── Market data (Action pillar) ─────────────────────────────────────────────────────────────────────────────────────

def _money(value: float, symbol: str) -> str:
    return f"{symbol}{value:,.2f}"


def _big(value: float, symbol: str) -> str:
    a = abs(value)
    if a >= 1e12:
        return f"{symbol}{value / 1e12:.2f}T"
    if a >= 1e9:
        return f"{symbol}{value / 1e9:.2f}B"
    if a >= 1e6:
        return f"{symbol}{value / 1e6:.2f}M"
    return f"{symbol}{value:,.0f}"


def action_from_market_data(ticker: str, md: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The Action pillar written from live market data (price, 200-day average, RSI-14, P/E, EBITDA), or None when the core
    figures are missing. No model involved, so these figures cannot be misquoted."""
    price, sma, rsi = md.get("price"), md.get("sma200"), md.get("rsi")
    if price is None or sma is None or rsi is None:
        return None
    t = ticker.upper()
    sym = md.get("price_symbol") or "$"
    fin_sym = md.get("financial_symbol") or sym
    above = price >= sma
    gap = (price / sma - 1) * 100 if sma else 0.0
    s1 = (f"{t}'s current stock price ({_money(price, sym)}) is {'above' if above else 'below'} its 200-day moving average "
          f"({_money(sma, sym)}), {abs(gap):.1f}% {'higher' if above else 'lower'}, which points to a "
          f"{'positive' if above else 'negative'} long-term trend.")
    if rsi < 30:
        rsi_read, rsi_tag, rsi_score = "oversold, which has often preceded a rebound", "+", 1
    elif rsi > 70:
        rsi_read, rsi_tag, rsi_score = "overbought, which raises the risk of a pullback", "-", -1
    else:
        rsi_read, rsi_tag, rsi_score = "in neutral territory (between 30 and 70)", "~", 0
    s2 = f"{t}'s 14-day RSI is {rsi:.2f}, {rsi_read}."
    pe, ebitda = md.get("pe"), md.get("ebitda")
    s3 = f"{t}'s trailing P/E ratio is {pe:.2f}." if pe else None
    s4 = f"{t}'s trailing twelve-month EBITDA is {_big(ebitda, fin_sym)}." if ebitda else None
    parts = [f"[[{'+' if above else '-'}|{s1}]]", f"[[{rsi_tag}|{s2}]]"]
    parts += [f"[[~|{s}]]" for s in (s3, s4) if s]
    missing = [name for name, v in (("P/E", pe), ("EBITDA", ebitda)) if not v]
    if missing:
        parts.append(f"{' and '.join(missing)} {'was' if len(missing) == 1 else 'were'} not available in the market data.")
    as_of = md.get("as_of")
    if as_of:
        parts.append(f"(Market data as of {as_of}.)")
    score = max(-2, min(2, (1 if above else -1) + rsi_score))
    trend = "above" if above else "below"
    if score >= 1:
        rec = f"Constructive timing: price {trend} its 200-day average with RSI {rsi:.0f}."
    elif score <= -1:
        rec = f"Cautious timing: price {trend} its 200-day average with RSI {rsi:.0f}; wait for confirmation before adding."
    else:
        rec = f"Neutral timing: price {trend} its 200-day average but RSI {rsi:.0f} does not confirm."
    return {"analysis": " ".join(parts), "key_points": [strip_tags(p) for p in parts[:4]], "recommendation": rec, "score": score,
            "source": "market_data"}


# ── Fact checks ─────────────────────────────────────────────────────────────────────────────────────────────────────

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[(\"'])")
_TREND_FLAT = re.compile(r"\b(stable|flat|unchanged|steady|consistent|held|maintained)\b", re.I)
_SCALE = {"t": 1e12, "trillion": 1e12, "b": 1e9, "bn": 1e9, "billion": 1e9, "m": 1e6, "mn": 1e6, "million": 1e6, "k": 1e3, "thousand": 1e3}
_FROM_TO = re.compile(
    r"(?P<label>revenues?|net sales|total revenues?|operating income|income from operations|net income|gross profit)\b[^.]*?"
    r"(?P<c1>[$€£¥])?\s?(?P<a>\d[\d,]*(?:\.\d+)?)\s*(?P<as>trillion|billion|million|thousand|bn|mn|[tbmk]\b)?\s*\((?:fy\s?)?(?P<y1>(?:19|20)\d{2})\)"
    r"[^.]*?(?P<c2>[$€£¥])?\s?(?P<b>\d[\d,]*(?:\.\d+)?)\s*(?P<bs>trillion|billion|million|thousand|bn|mn|[tbmk]\b)?\s*\((?:fy\s?)?(?P<y2>(?:19|20)\d{2})\)",
    re.I)


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT.split(text or "") if s.strip()]


def _num(raw: str, scale: Optional[str]) -> float:
    return float(raw.replace(",", "")) * _SCALE.get((scale or "").lower(), 1.0)


def _derivable(target: float, values: List[float], pct: bool) -> bool:
    """Whether `target` is a calculation from two of `values`: a change, a ratio, a CAGR (percentages), or a sum/difference."""
    vals = [v for v in values if v]
    tol = lambda x: abs(x - target) <= max(0.11, 0.006 * abs(target))  # noqa: E731
    for i, a in enumerate(vals):
        for j, b in enumerate(vals):
            if i == j:
                continue
            if pct:
                if tol((b / a - 1) * 100) or tol(abs(b / a - 1) * 100) or tol(b / a * 100) or tol(b - a) or tol(abs(b - a)):
                    return True
                if a > 0 and b > 0:
                    for n in range(2, 11):
                        if tol(((b / a) ** (1 / n) - 1) * 100):
                            return True
            elif tol(a + b) or tol(abs(a - b)):
                return True
    return False


def implied_margins(text: str) -> Dict[str, Dict[str, float]]:
    """Margins the section's own figures imply: {"operating": {"2023": 27.4, "2025": 32.0}, ...}."""
    found: Dict[str, Dict[str, float]] = {}
    for m in _FROM_TO.finditer(text or ""):
        label = m.group("label").lower()
        key = "revenue" if label.startswith(("revenue", "net sales", "total revenue")) else (
            "operating" if label in ("operating income", "income from operations") else "net" if label == "net income" else "gross")
        try:
            found.setdefault(key, {})[m.group("y1")] = _num(m.group("a"), m.group("as"))
            found.setdefault(key, {})[m.group("y2")] = _num(m.group("b"), m.group("bs"))
        except ValueError:
            continue
    margins: Dict[str, Dict[str, float]] = {}
    rev = found.get("revenue", {})
    for key in ("operating", "net", "gross"):
        for year, value in found.get(key, {}).items():
            if rev.get(year):
                margins.setdefault(key, {})[year] = round(100 * value / rev[year], 1)
    return margins


def trend_issues(text: str) -> List[Dict[str, str]]:
    """Trend words the section's own figures contradict: a margin called stable while it moved by more than a point."""
    plain = strip_tags(text)
    issues: List[Dict[str, str]] = []
    margins = implied_margins(plain)
    for sentence in _sentences(plain):
        if not (_TREND_FLAT.search(sentence) and re.search(r"margin", sentence, re.I)):
            continue
        pcts = [float(p) for p in re.findall(r"(\d+(?:\.\d+)?)\s*%", sentence)]
        if len(pcts) >= 2 and max(pcts) - min(pcts) > 1.0:
            issues.append({"text": sentence, "kind": "trend", "detail": f"calls a margin stable while quoting {min(pcts)}% and {max(pcts)}%"})
            continue
        kind = "operating" if re.search(r"operating", sentence, re.I) else "net" if re.search(r"\bnet\b", sentence, re.I) else "gross" if re.search(r"gross", sentence, re.I) else None
        series = margins.get(kind or "operating", {})
        if len(series) >= 2:
            years = sorted(series)
            first, last = series[years[0]], series[years[-1]]
            if abs(last - first) > 1.0:
                issues.append({"text": sentence, "kind": "trend",
                               "detail": f"by this section's own figures the {kind or 'operating'} margin moved from {first}% ({years[0]}) "
                                         f"to {last}% ({years[-1]}), so it is not stable"})
    return issues


def check_section(text: str, sources: Iterable[str]) -> List[Dict[str, str]]:
    """Problems in one pillar's text: figures not found in its sources (unless calculated from figures that are) and trend
    words its own figures contradict. Uses the shared number verifier (rag/verify/numbers.py); no model."""
    from rag.verify import numbers
    plain = strip_tags(text)
    sources = [s for s in sources if s]
    result = numbers.verify(plain, sources)
    issues: List[Dict[str, str]] = []
    for issue in result["issues"]:
        if issue["kind"] == "unsupported":
            # A calculation from figures the section itself quotes (growth, CAGR, a margin from two quoted lines) is fine.
            target_nums = numbers.extract(issue["text"])
            if target_nums:
                t = target_nums[0]
                is_pct = issue["text"].endswith("%")
                nums = [n for n in numbers.extract(plain) if not numbers._ignorable(n, plain)]
                absolutes = [n["value"] for n in nums if not n["pct"]]
                pool = absolutes + ([n["printed"] for n in nums if n["pct"] and n["raw"] + "%" != issue["text"]] if is_pct else [])
                if _derivable(t["printed"] if is_pct else t["value"], pool, is_pct):
                    continue
        issues.append(issue)
    return issues + trend_issues(text)


def issues_note(issues: List[Dict[str, str]]) -> Optional[str]:
    """One line under a pillar listing what could not be confirmed after revision."""
    if not issues:
        return None
    figs = [i["text"] for i in issues if i["kind"] != "trend"][:6]
    trends = [i["detail"] for i in issues if i["kind"] == "trend"][:2]
    parts = []
    if figs:
        parts.append(f"not found in the sources this section was written from: {', '.join(figs)}")
    if trends:
        parts.append("; ".join(trends))
    return "_Fact check — " + "; ".join(parts) + "._"


# ── Report ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def display_name(ticker: str, long_name: Optional[str], mapped_name: Optional[str]) -> str:
    if long_name:
        return long_name
    name = (mapped_name or "").strip()
    if not name or name.lower() == ticker.lower():
        return ticker.upper()
    return " ".join(w if w.isupper() and len(w) > 1 else w.capitalize() for w in name.split())


def assemble_report(company: str, ticker: str, pillars: Dict[str, Dict[str, Any]], stance: Dict[str, Any],
                    summary: str, notes_view: Optional[str] = None) -> str:
    """The report as the reader sees it. Pillar text is used as written and checked; nothing is rewritten here."""
    lines = [f"# Indium's ALPHA Framework Analysis: {company} ({ticker.upper()})", ""]
    if stance.get("missing"):
        names = ", ".join(PILLAR_NAMES[k] for k in stance["missing"])
        basis = ("so no overall stance is given" if stance.get("stance") is None
                 else f"the stance is based on the other {len(stance['used'])} pillars")
        lines += [f"> **Partial analysis:** {names} could not be completed from the available data; {basis}.", ""]
    for key, letter, title in PILLARS:
        p = pillars.get(key) or {}
        lines.append(f"## {letter} — {title}")
        if p.get("status") == "ok":
            lines.append(p["analysis"].strip())
            note = issues_note(p.get("issues") or [])
            if note:
                lines += ["", note]
        else:
            lines.append(f"_Not available: {p.get('reason') or 'not enough data for this pillar'}._")
        lines.append("")
    lines += ["---", "## Indium's ALPHA Summary — Overall Investment Stance", summary.strip()]
    if notes_view:
        lines += ["", notes_view.strip()]
    lines += ["", "---", DISCLAIMER]
    return "\n".join(lines)


def fallback_summary(company: str, stance: Dict[str, Any], pillars: Dict[str, Dict[str, Any]]) -> str:
    """A plain summary when the summary model is unavailable: the stance and each pillar's own verdict."""
    bits = [f"{PILLAR_NAMES[k]}: {p.get('recommendation') or 'no verdict'}" for k, p in pillars.items() if p.get("status") == "ok"]
    head = " ".join(f"{b}." if not b.endswith(".") else b for b in bits)
    if stance.get("stance"):
        tag = "+" if stance["stance_score"] > 0 else "-" if stance["stance_score"] < 0 else "~"
        return f"{head} [[{tag}|Overall, the investment stance for {company} is {stance['stance']}.]]"
    return f"{head} There is not enough data to give {company} an overall stance."


def signal_record(stance: Dict[str, Any], pillars: Dict[str, Dict[str, Any]], key_risk: Optional[str]) -> Dict[str, Any]:
    """What the dashboards store for one ticker (app/services/run_insights.py reads this instead of parsing the report)."""
    from collections import Counter
    counts: Counter = Counter()
    for p in pillars.values():
        for sign in _TAG.findall(p.get("analysis") or ""):
            counts[{"+": "pos", "-": "neg", "~": "neutral"}[sign[0]]] += 1
    return {
        "stance": stance.get("stance"), "stance_score": stance.get("stance_score"), "mean_score": stance.get("mean"),
        "partial": bool(stance.get("missing")), "missing": stance.get("missing") or [],
        "pillars": {k: direction(p.get("score")) if p.get("status") == "ok" else None for k, _, _ in PILLARS for p in [pillars.get(k) or {}]},
        "pillar_detail": {k: {"status": p.get("status"), "score": p.get("score"), "recommendation": p.get("recommendation"),
                              "source": p.get("source"), "issues": len(p.get("issues") or []), "revised": bool(p.get("revised"))}
                          for k, p in pillars.items()},
        "fact_counts": {"pos": counts["pos"], "neg": counts["neg"], "neutral": counts["neutral"]},
        "fact_issues": sum(len(p.get("issues") or []) for p in pillars.values()),
        "key_risk": key_risk,
    }


def is_finite(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False
