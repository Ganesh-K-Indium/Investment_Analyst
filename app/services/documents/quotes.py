"""
Cited-quote selection: the sentence (or table row) on a cited chunk that best supports an answer.

Done once, on the server, when the answer is generated; only the short quote is saved with the message, so the
Inspector can highlight it later without keeping chunk text in chat history. No LLM call: token overlap with numbers
weighted double. Mirrors features/documents/citations.ts (used only for chats saved before this existed).
"""
import re
from typing import Optional

TABLES_MARKER = "\n\n[TABLES]\n"
_TOKEN = re.compile(r"[a-z0-9$%.]+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])|\n{2,}")
_STOP = {"the", "and", "for", "that", "with", "from", "this", "was", "were", "are", "has", "have", "its", "our",
         "their", "than", "which"}
MIN_SENTENCE, MAX_SENTENCE = 40, 400
MAX_QUOTE_CHARS = 400


def _tokens(text: str) -> list:
    return [t for t in (m.strip(".") for m in _TOKEN.findall((text or "").lower())) if t]


def _answer_tokens(answer: str) -> set:
    return {t for t in _tokens(answer) if t not in _STOP}


def pick_sentence(page_text: str, answer_tokens: set) -> Optional[str]:
    best, best_score = None, 0.0
    for raw in _SENTENCE_SPLIT.split(page_text or ""):
        s = " ".join(raw.split())
        if not MIN_SENTENCE <= len(s) <= MAX_SENTENCE:
            continue
        toks = [t for t in _tokens(s) if t not in _STOP]
        if len(toks) < 5:
            continue
        weight = hit = 0
        for t in toks:
            w = 2 if any(c.isdigit() for c in t) else 1
            weight += w
            if t in answer_tokens:
                hit += w
        score = hit / weight
        if hit >= 4 and score > best_score:
            best, best_score = s, score
    return best if best_score >= 0.3 else None


def pick_table_row(tables_md: str, answer_tokens: set) -> Optional[str]:
    best, best_score = None, 0
    for line in (tables_md or "").split("\n"):
        if "|" not in line or re.fullmatch(r"[\s|:-]+", line):
            continue
        s = " ".join(c.strip() for c in line.split("|") if c.strip())
        toks = _tokens(s)
        if len(toks) < 3:
            continue
        score = sum((2 if any(c.isdigit() for c in t) else 1) for t in toks if t in answer_tokens)
        if score >= 3 and score > best_score:
            best, best_score = s, score
    return best


def pick_quote(chunk_text: str, answer: str) -> Optional[str]:
    """Best sentence of the chunk's text, else its best table row, else None."""
    if not chunk_text or not answer:
        return None
    if chunk_text.startswith(TABLES_MARKER.lstrip("\n")):
        text, tables = "", chunk_text
    else:
        text, _, tables = chunk_text.partition(TABLES_MARKER)
    tokens = _answer_tokens(answer)
    quote = pick_sentence(text, tokens) or pick_table_row(tables, tokens)
    return quote[:MAX_QUOTE_CHARS] if quote else None
