"""
Financial numbers as printed: "$ 1,234.5", "(1,234)", "1,234-", "—", "12.5%", "3.4x", "1,234⁽¹⁾".

Parsing is deliberately strict: a cell that is not clearly a number is `text`, never a guessed number, so an arithmetic check cannot be
fooled by a stray word. A dash is a zero (statements print nil that way); "n/a" and "n/m" are empty, not zero.
"""
import re
from dataclasses import dataclass
from typing import Optional

_DASHES = {"-", "–", "—", "−", "— —", "nil"}
_EMPTY_WORDS = {"n/a", "na", "n/m", "nm"}
_FOOTNOTE = re.compile(r"[¹²³⁰-ⁿ*†‡§¶]+$|\s*\(\w\)$")           # superscripts, asterisks, "(a)" markers
_NUMBER = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$|^\d+(?:\.\d+)?$|^\.\d+$")


@dataclass(frozen=True)
class Parsed:
    kind: str                 # amount | percent | multiple | empty | text
    value: Optional[float]    # signed; None for empty/text
    decimals: int = 0         # digits after the point as printed (the precision of the number)
    raw: str = ""


def parse(cell) -> Parsed:
    raw = "" if cell is None else str(cell)
    s = raw.strip()
    if not s:
        return Parsed("empty", None, 0, raw)
    low = s.lower()
    if low in _EMPTY_WORDS:
        return Parsed("empty", None, 0, raw)
    if s in _DASHES:
        return Parsed("amount", 0.0, 0, raw)
    s = _FOOTNOTE.sub("", s).strip()
    s = re.sub(r"^[\$€£¥]\s*", "", s)                          # currency sign before the number or inside the parentheses handled below
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
        s = re.sub(r"^[\$€£¥]\s*", "", s)
    if s and s[0] in "-−–":
        neg, s = True, s[1:].strip()
    if s.endswith(("-", "−")):
        neg, s = True, s[:-1].strip()
    kind = "amount"
    if s.endswith("%"):
        kind, s = "percent", s[:-1].strip()
    elif s.lower().endswith("x") and len(s) > 1:
        kind, s = "multiple", s[:-1].strip()
    if not _NUMBER.match(s):
        return Parsed("text", None, 0, raw)
    v = float(s.replace(",", ""))
    return Parsed(kind, -v if neg else v, len(s.split(".")[1]) if "." in s else 0, raw)
