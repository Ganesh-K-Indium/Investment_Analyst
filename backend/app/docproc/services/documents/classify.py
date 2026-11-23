"""
Page classification and document segmentation.

Rule-based on purpose: the document types are few and have stable titles, a rule is explainable to reviewers, and it
costs nothing. Rules are data (DOC_TYPES) so a new form is one more entry, not new code.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

# type -> (all-of regexes, score). First type with a hit wins; ties go to the higher score.
DOC_TYPES: List[Dict[str, Any]] = [
    {"type": "prior_auth_form", "label": "Prior authorization request form", "score": 3,
     "any": [r"prior\s+authorization\s+request\s+form"], "template": "magnacare_prior_auth", "extractor": "template_form"},
    {"type": "pt_initial_exam", "label": "Physical therapy initial examination", "score": 3,
     "all": [r"physical\s+therapy", r"initial\s+exam"], "template": "pt_initial_exam", "extractor": "template_form"},
    {"type": "daily_note", "label": "Daily note / billing sheet", "score": 3,
     "any": [r"daily\s+note\s*/?\s*billing\s+sheet", r"daily\s+note"], "template": "daily_note", "extractor": "template_form"},
    {"type": "fax_cover", "label": "Fax cover sheet", "score": 2,
     "all": [r"\bfax\b", r"(legally privileged|confidential)"], "extractor": "skip", "match": "head"},
]
MIN_CONTENT_CHARS = 40


def custom_types() -> List[Dict[str, Any]]:
    """The custom document types of the workspace in force (see workspace_types)."""
    from app.docproc.services.documents import workspace_types
    return [d for d in workspace_types.entries() if isinstance(d, dict) and d.get("type")]


def all_types() -> List[Dict[str, Any]]:
    built_in = {d["type"] for d in DOC_TYPES}
    return DOC_TYPES + [dict(d, custom=True) for d in custom_types() if d["type"] not in built_in]


def _loose(pattern: str) -> str:
    """A rule's pattern with its required spaces made optional. Some OCR engines drop the spaces between words
    ("ProviderPriorAuthorizationRequestForm"), and a title must still be recognised."""
    return pattern.replace(r"\s+", r"\s*")


def _matches(rule: Dict[str, Any], text: str) -> bool:
    if "all" in rule and not all(re.search(_loose(p), text, re.I) for p in rule["all"]):
        return False
    if "any" in rule and not any(re.search(_loose(p), text, re.I) for p in rule["any"]):
        return False
    return True


def _span(rule: Dict[str, Any], text: str) -> int:
    """Characters of `text` the rule's patterns actually matched (the title itself)."""
    if "all" in rule:
        return sum(len(m.group(0)) for p in rule["all"] if (m := re.search(_loose(p), text, re.I)))
    hits = [m for p in rule.get("any", []) if (m := re.search(_loose(p), text, re.I))]
    return max((len(m.group(0)) for m in hits), default=0)


COLUMN_SEP = " | "      # how a reading joins the pieces that sit side by side on one line


def _column_views(lines: List[str]) -> List[List[Tuple[int, str]]]:
    """The page as the whole lines, then as each column on its own. A title set beside an address block
    ("Physical Therapy / Initial / Examination" in the right column) is three short lines only when the columns
    are read apart. Each entry keeps the index of the line it came from.

    Columns are taken by slot (first piece, second, ...) and also from the right edge: a reading may cut one line into
    more pieces than its neighbours ("Testville | NJ 07001 | Initial"), which moves the title's words to different slots
    while they stay the last piece of their line. Stray marks a reading leaves in a column are ignored."""
    def real(t: str) -> bool:
        return len(re.sub(r"[^A-Za-z0-9]", "", t)) >= 2

    views: List[List[Tuple[int, str]]] = [[(i, l.strip()) for i, l in enumerate(lines)]]
    split = [l.split(COLUMN_SEP) for l in lines]
    width = max((len(c) for c in split), default=1)
    for col in range(min(width, 4)):
        cells = [(i, c[col].strip()) for i, c in enumerate(split) if len(c) > 1 and len(c) > col and real(c[col])]
        if cells:
            views.append(cells)
    last = [(i, c[-1].strip()) for i, c in enumerate(split) if len(c) > 1 and real(c[-1])]
    if last:
        views.append(last)
    return views


def _title_position(rule: Dict[str, Any], lines: List[str]) -> Optional[int]:
    """Index of the first line where `rule` matches as a TITLE: one to three consecutive lines that are mostly the
    title and little else. Anywhere on the page, so a form below a letter is found; but a sentence that merely
    mentions the form's name ("please return the Prior Authorization Request Form within ten days") is not a title."""
    best: Optional[int] = None
    for view in _column_views(lines):
        for i in range(len(view)):
            for size in (1, 2, 3):
                window = " ".join(x for _, x in view[i:i + size] if x)
                if not window or not _matches(rule, window):
                    continue
                if len(window) <= _span(rule, window) * 2.2 + 15:
                    best = view[i][0] if best is None else min(best, view[i][0])
                    break
            else:
                continue
            break
    return best


def classify_text(text: str) -> Dict[str, Any]:
    """{'type', 'label', 'confidence'} for one page's content text (fax stamps already removed).

    A type whose title appears as a title anywhere on the page names the page; with several, the topmost wins (a page
    holding two different forms is classified by the first). Rules marked "match": "head" (a cover sheet has no title)
    look at the top of the page only."""
    text = text or ""
    if len(re.sub(r"\s+", "", text)) < MIN_CONTENT_CHARS:
        return {"type": "blank_or_logo", "label": "Blank or logo-only page", "confidence": 0.9}
    lines = text.splitlines()
    found = []
    for rule in all_types():
        if rule.get("match") == "head":
            if _matches(rule, text[:600]):
                found.append((len(lines), -rule.get("score", 1), 0 if rule.get("custom") else 1, rule))  # sorts after any real title
            continue
        pos = _title_position(rule, lines)
        if pos is not None:
            found.append((pos, -rule.get("score", 1), 0 if rule.get("custom") else 1, rule))
    if not found:
        return {"type": "unknown", "label": "Unclassified", "confidence": 0.0}
    # Topmost title wins; then the higher score; then the workspace's own type over a built-in one with the same title
    # (someone who added their own template for a form means to use it).
    found.sort(key=lambda f: f[:3])
    best = found[0][3]
    return {"type": best["type"], "label": best["label"], "confidence": 0.9 if len(found) == 1 else 0.6}


# What a page with no template is, from the top of it. Kinds are broad, widely used document types; each needs words that
# appear together, so a letter that merely mentions an invoice is not called an invoice. Checked on the first lines only.
_SEC_FORM = re.compile(r"\bFORM\s+(10-K|10-Q|8-K|S-1|S-3|S-4|20-F|40-F|6-K|11-K|DEF\s*14A)\b", re.I)
_KINDS: List[Dict[str, Any]] = [
    {"label": "SEC filing", "all": [r"SECURITIES\s+AND\s+EXCHANGE\s+COMMISSION"]},
    {"label": "Invoice", "all": [r"\binvoice\b", r"invoice\s*(no|number|#|date)|bill(ed)?\s+to|amount\s+due|total\s+due"]},
    {"label": "Receipt", "all": [r"\breceipt\b", r"total|amount|paid"]},
    {"label": "Purchase order", "all": [r"purchase\s+order"]},
    {"label": "Account statement", "all": [r"(account|bank|card)\s+statement|statement\s+of\s+account|statement\s+period"]},
    {"label": "Contract", "all": [r"\b(agreement|contract)\b", r"\bbetween\b|\bparties\b|hereinafter|by\s+and\s+among"]},
    {"label": "Meeting minutes", "all": [r"minutes\s+of\s+(the\s+)?meeting|meeting\s+minutes"]},
    {"label": "Resume", "all": [r"curriculum\s+vitae|\bresum[eé]\b"]},
    {"label": "Datasheet", "all": [r"\bdata\s*sheet\b"]},
    {"label": "Test report", "all": [r"\btest\s+(report|results)\b"]},
    {"label": "Report", "all": [r"\b(annual|quarterly|monthly|technical|status|progress|inspection|audit)\s+report\b"]},
    {"label": "Letter", "all": [r"(?m)^\s*dear\s+\w+"]},
]
GENERIC_LABELS = ("Unclassified", "Untemplated document", "Other document")
_HEAD_LINES = 14


_STRUCTURAL = re.compile(r"^(part|item|section|chapter|article|page|table(?!\s+of\s+contents)|figure|appendix|exhibit)\b[\s\w.\-]{0,12}$", re.I)
_BOILERPLATE = re.compile(r"^(page\s*\d+(\s*(of|/)\s*\d+)?|form\s+\S+\s*(\(.*\))?\s*(page\s*\d+)?)$", re.I)     # "Page 2", "Form W-9 (Rev. 3-2024)": a page's running line


_LITTLE = {"of", "the", "and", "for", "in", "to", "a", "an", "on", "or", "at", "by", "is", "as"}      # short words every title has


def _wordlike(text: str) -> bool:
    """Does this read as words? OCR noise ("Me thn it it mE") does not: most of its pieces are too short or have no vowel.
    A part number or a code ("LM358B", "W-9", "5.1") is neither words nor noise, so it is left out of the count."""
    tokens = [t for t in text.split() if not re.search(r"\d", t)]
    words = [w for t in tokens for w in re.findall(r"[A-Za-z][A-Za-z'&.-]*", t) if w.lower() not in _LITTLE]
    if not words:
        return False
    good = [w for w in words if len(re.sub(r"[^A-Za-z]", "", w)) >= 3 and re.search(r"[aeiouyAEIOUY]", w)]
    stray = len(re.findall(r"[^\w\s&.,'()/:\-]", text))
    return len(good) / len(words) >= 0.7 and stray <= 1


def _usable_title(title: str) -> Optional[str]:
    """A title as it reads on a page, or None: it must be words, and start like a title (a capital or a number), not like the middle of a sentence."""
    title = re.sub(r"\s+", " ", title.split(" | ")[0]).strip(" |:-")      # a heading read across cells: its first cell
    if len(title) < 3 or not _wordlike(title) or not (re.match(r"[A-Z0-9(]", title) or re.match(r"[a-z]+\d", title)) or re.search(r"\.{4,}", title) or _BOILERPLATE.match(title) \
            or title.split()[-1].lower() in _LITTLE or (len(title.split()) == 1 and len(title) <= 4):                # "Tax and": a label cut short, not a title
        return None
    return (title[:77] + "...") if len(title) > 80 else title


def _title_of(blocks: List[Dict[str, Any]], text: str) -> Optional[str]:
    """The page's own title: the most specific of its top headings, else a short first line. Headings that are only a
    section marker ("PART I", "Item 1") are passed over; where several headings of one level stack up, broad to specific
    ("PART I. FINANCIAL INFORMATION", "INCOME STATEMENTS"), the last is the one that says what is on this page.
    A heading that does not read as a title (an equation, the tail of a sentence) is skipped for the next one."""
    heads = [(i, b) for i, b in enumerate(blocks[:8]) if b.get("type") == "heading" and len(re.sub(r"[^A-Za-z]", "", b.get("text", ""))) >= 3
             and not _STRUCTURAL.match(b["text"].strip())]
    if heads:
        level = min(b["level"] for _, b in heads)
        top = [i for i, b in heads if b["level"] == level]
        run = [top[0]]
        for i in top[1:]:
            if i == run[-1] + 1:
                run.append(i)                            # headings stacked one under another
            else:
                break
        title = blocks[run[-1]]["text"]
        if len(run) == 1 and run[0] + 1 < len(blocks):
            nxt = blocks[run[0] + 1]
            if nxt.get("type") == "heading" and nxt["level"] == level and len(title) + len(nxt["text"]) <= 80:
                title += " " + nxt["text"]               # a title set over two lines
        found = [t for t in [_usable_title(title)] + [_usable_title(b["text"]) for _, b in heads] if t]
        if found:
            # a single word set in capitals ("INSTRUMENTS" under a logo) is a poor title when a heading of several words is there
            return next((t for t in found if len(t.split()) >= 2), found[0])
    lines = [l.strip() for l in text.splitlines() if l.strip()][:6]
    for k, line in enumerate(lines):
        if re.fullmatch(r"\d{1,2}(\.\d{1,2})*", line) and k + 1 < len(lines):
            line = f"{line} {lines[k + 1]}"             # a section number on a line of its own, then its name
        if len(line) <= 70 and ":" not in line and not re.search(r"[.,;]\s*$", line):
            got = _usable_title(line)
            if got:
                return got
    return None


_FORM_ID = re.compile(r"^Form\b\s*([A-Z0-9][\w-]{0,9})?\s*$")
_FORM_SKIP = re.compile(r"^(\(?rev\.|\d{4}$|omb\b|department\b|internal revenue|go to\b|give\b|before\b|see\b|print\b)", re.I)


def _form_label(top: List[str]) -> Optional[Tuple[str, Optional[str]]]:
    """Agency forms open with "Form W-9" or "Form" over "1040" and then the form's name ("Request for Taxpayer Identification
    Number and Certification"). Returns ("Form W-9", "Request for ...") from the top lines, or None."""
    for i, line in enumerate(top[:8]):
        m = _FORM_ID.match(line)
        if not m:
            continue
        ident, j = m.group(1), i + 1
        if not ident and j < len(top) and re.fullmatch(r"[A-Z0-9][\w-]{0,9}", top[j]):
            ident, j = top[j], j + 1
        if not ident or not re.search(r"\d", ident):
            return None
        name: List[str] = []
        for line in top[j:j + 5]:
            if _FORM_SKIP.match(line):
                if name:
                    break
                continue
            if _wordlike(line) and len(line.split()) >= 2 and not line.endswith(".") and len(" ".join(name + [line])) <= 80:
                name.append(line)
                if len(name) == 2:
                    break
            elif name:
                break
        return f"Form {ident}", (" ".join(name) or None)
    return None


def describe(blocks: List[Dict[str, Any]], text: str, trust_text: bool = True) -> Dict[str, Any]:
    """{"label", "title", "kind"} for a page no template names, from its top; {} when nothing can be said.
    The label is what a reader would call it: a recognised kind ("SEC Form 10-Q", "Invoice"), else its title.
    `trust_text` is False when the reading was poor (a faint scan, handwriting): a title picked out of noise would be
    worse than none, and a kind needs several words to agree, so only the kind is offered."""
    top = "\n".join([l for l in (text or "").splitlines() if l.strip()][:_HEAD_LINES])
    kind = None
    m = _SEC_FORM.search(top)
    if m:
        kind = "SEC Form " + re.sub(r"\s+", "", m.group(1).upper())
    else:
        for rule in _KINDS:
            if all(re.search(p, top, re.I) for p in rule["all"]):
                kind = rule["label"]
                break
    title = _title_of(blocks, text) if trust_text else None
    if not kind and trust_text:
        form = _form_label([l.strip() for l in top.splitlines()])
        if form:
            kind = form[0]
            title = form[1] or title
            return {"label": f"{kind}: {title}" if title else kind, "title": title, "kind": kind}
    label = kind or title
    return {"label": label, "title": title, "kind": kind} if label else {}


async def classify_with_model(text: str, models: List[str], ask=None, previous_text: str = "") -> Dict[str, Any]:
    """Second opinion for a page the rules could not name. The model may only choose a REGISTERED type (or 'unknown'),
    so its answer is always routable, and it says whether the page continues the previous page's document or starts a
    new one (with a short title). `ask(model, prompt) -> str` is injectable for tests."""
    import json
    choices = {d["type"]: d["label"] for d in all_types()}
    prompt = ("Classify one page from a PDF that may hold several documents. Answer with JSON "
              '{"type": <one of ' + ", ".join(f'"{t}" ({l})' for t, l in choices.items())
              + ' or "unknown">, "title": "<short title of this page\'s document, or null>", '
              '"continues_previous": <true if this page is a continuation of the previous page\'s document, else false>}.\n\n'
              + (f"Previous page (top):\n{previous_text[:400]}\n\n" if previous_text else "")
              + "This page (top):\n" + (text or "")[:1200])

    async def default_ask(model: str, prompt: str) -> str:
        from app.docproc.engines.providers import llm
        return await llm.complete(model, prompt, None, stage="classify")

    ask = ask or default_ask
    unknown = {"type": "unknown", "label": "Unclassified", "confidence": 0.0}
    try:
        data = json.loads(await ask(models[-1], prompt))  # the smaller model: this is a cheap, short call
    except Exception:
        return unknown
    if not isinstance(data, dict):
        return unknown
    t = data.get("type")
    title = data.get("title") if isinstance(data.get("title"), str) and data["title"].strip() else None
    if t in choices:
        return {"type": t, "label": choices[t], "confidence": 0.7, "by": "model"}
    return {**unknown, "label": title or "Unclassified", "title": title, "by": "model",
            "continuation": bool(data.get("continues_previous"))}


def segment(pages: List[Dict[str, Any]], single_page_types: Optional[set] = None) -> List[Dict[str, Any]]:
    """Group consecutive pages into logical documents.

    A blank/logo page continues the document before it. An unclassified page continues it too (a continuation sheet with
    no title belongs to the form it follows), unless a model said it starts a new one, or the previous document is of a
    single-page type (its template never continues, so an untitled page after it is something else). Two consecutive
    pages of a single-page type are two copies of the form, not one document; of a multi-page type, one document.
    """
    single = (single_page_types or set()) | {"fax_cover"}
    docs: List[Dict[str, Any]] = []
    for p in pages:
        t = p["classification"]["type"]
        prev = docs[-1]["type"] if docs else None
        starts_new = t == "unknown" and (p["classification"].get("continuation") is False or prev in single)
        cont = t in ("blank_or_logo", "unknown") and docs and not starts_new
        if cont:
            docs[-1]["pages"].append(p["page"])
            continue
        if docs and prev == t and t not in single and t not in ("unknown",):
            docs[-1]["pages"].append(p["page"])  # a multi-page exam / note
            continue
        label = p["classification"]["label"]
        docs.append({"doc_index": len(docs), "type": t, "label": label, "pages": [p["page"]]})
    for i, d in enumerate(docs):
        d["doc_index"] = i
    return docs
