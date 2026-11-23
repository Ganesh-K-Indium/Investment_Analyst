"""
Stable names for discovered fields.

Without a template every reader names things its own way ("Invoice No.", "Inv #", "Invoice Number"). A downstream
system that reads the JSON needs ONE key for each of those, run after run. `key()` reduces a printed label to a
canonical snake_case key: abbreviations are expanded, filler words dropped, and a short list of true synonyms folded
together. The printed label is always kept separately; only the key is canonical.

Deliberately conservative: only words that always mean the same thing are folded. "Total Due" and "Amount Due" stay
different keys, because in some documents they are different numbers.
"""
import re

ABBREVIATIONS = {
    "no": "number", "num": "number", "nbr": "number", "nr": "number", "tel": "telephone", "ph": "phone", "amt": "amount",
    "qty": "quantity", "addr": "address", "dob": "date of birth", "inv": "invoice", "dept": "department", "desc": "description",
    "ref": "reference", "acct": "account", "pmt": "payment", "yr": "year", "mgr": "manager", "id": "id",
}
FILLER = {"the", "a", "an"}
# (canonical token sequence) <- variants, applied to the expanded token string
SYNONYMS = [
    (r"^(telephone|phone)( number)?$", "phone"),
    (r"^(zip|postal)( code)?$", "zip"),
    (r"^(e mail|email)( address)?$", "email"),
    (r"^(date of birth|birth date|birthdate|birthday)$", "date_of_birth"),
    (r"^(social security number|ssn)$", "ssn"),
    (r"^(first|given) name$", "first_name"),
    (r"^(last|family|sur) ?name$", "last_name"),
    (r"^(street )?address( line 1)?$", "address"),
]


def tokens(label: str) -> list:
    s = (label or "").lower().replace("#", " number ").replace("&", " and ")
    words = re.findall(r"[a-z0-9]+", s)
    out: list = []
    for w in words:
        if w in FILLER:
            continue
        out.extend(ABBREVIATIONS.get(w, w).split())
    return out


def key(label: str, fallback: str = "field") -> str:
    """Canonical snake_case key for a printed label."""
    text = " ".join(tokens(label))
    for pattern, canon in SYNONYMS:
        if re.match(pattern, text):
            return canon
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:48] or fallback
