"""Value normalisation by field type. Comparison and validation work on the normalised form; the raw text is kept."""
import re
from datetime import datetime
from typing import Any, Optional

_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y")


def parse_date(s: str) -> Optional[str]:
    s = re.sub(r"\s+", " ", (s or "").strip())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# Marks that change what a number means. Stripping them made ".2%" equal "2%" and "7-20" equal "720",
# so two readers that disagreed on a decimal point or a range counted as agreeing. Kept only where they touch a digit:
#   .5 and 9.5 (decimal point)   7-20 and 10/12 (range, date)   20% (percent)
# A currency sign is not one of them: "$ 1,200" and "1,200" are the same amount, and statements print the sign in its own cell.
# A sentence-ending "1992." or a bullet "- 1 min" is not one of these, so it still does not count as a difference.
_MARKS = {"-": "\ue000", "/": "\ue001", ".": "\ue002", "%": "\ue003"}
_MARKS["("], _MARKS[")"] = "\ue005", "\ue006"   # sign of a number, see _NEG_RE
_MARKS_BACK = {v: k for k, v in _MARKS.items()}
_MARK_RE = re.compile(r"(?<=\d)[-/](?=\d)|\.(?=\d)|(?<=\d)%")


# Sign of a number. "(1,234)" and "-1,234" are negative in a financial statement; losing the sign turns a loss into a
# profit. A dash glued to a digit at the start of a word ("-5", "$-5") is a minus; "- 5 min" (a bullet) is not.
_NEG_RE = re.compile(r"(?<![\w$])-(?=\.?\d)|(?<![\w])\(\s*\$?\s*(?=[.]?\d)|(?<=\d)\)(?![\w])")


_FRACTIONS = {"\u00bd": "1/2", "\u2153": "1/3", "\u2154": "2/3", "\u00bc": "1/4", "\u00be": "3/4", "\u2155": "1/5", "\u215b": "1/8"}
_FRACTION_RE = re.compile("([" + "".join(_FRACTIONS) + "])")


def _fractions(s: str) -> str:
    """"1\u00bd" and "1 1/2" are the same amount: spell the vulgar fraction out so both compare equal and neither loses its half."""
    return _FRACTION_RE.sub(lambda m: (" " if m.start() and s[m.start() - 1].isdigit() else "") + _FRACTIONS[m.group(1)], s)


def text_form(s: str) -> str:
    """Comparison form of free text: lower case, letters and digits, plus the digit-bearing marks above."""
    s = _fractions(s)
    s = _NEG_RE.sub(lambda m: "\ue005" if m.group() != ")" else "\ue006", s)
    s = _MARK_RE.sub(lambda m: _MARKS[m.group()], re.sub(r"\s+", "", s.lower()))
    s = re.sub(r"[^a-z0-9\ue000-\ue006]", "", s)
    return re.sub(r"[\ue000-\ue006]", lambda m: _MARKS_BACK[m.group()], s)


def digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def normalize(value: Any, ftype: str) -> Optional[str]:
    """Canonical string for comparison; None when there is no value."""
    if value is None:
        return None
    if isinstance(value, list):
        return "|".join(sorted(str(v).strip().lower() for v in value)) or None
    s = str(value).strip()
    if not s:
        return None
    if ftype == "date":
        return parse_date(s) or re.sub(r"[^a-z0-9]", "", s.lower())
    if ftype in ("phone", "npi", "tax_id", "zip"):
        return digits(s) or None
    if ftype in ("state", "icd10"):
        return re.sub(r"[^A-Z0-9]", "", s.upper()) or None
    return text_form(s) or None
