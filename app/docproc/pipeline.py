"""
End-to-end extraction for one uploaded PDF.

    work PDF  : scanned pages get a Tesseract text layer (tess.py); all geometry runs on it, the viewer shows the original
    per page  : profile -> router picks a reader -> text -> strip stamps -> classify
    documents : consecutive pages grouped; router picks an extractor per document
                template_form -> extract_document (dual readers, multi-page, grounded)   kv -> label/value pairs
    checks    : per-document rules (validation.py) + cross-document consistency (cross.py)

A page that fails to read is recorded with an error and the rest of the file carries on.

Output (stored as one JSON blob; the API serves it as two files):
    pages.json  = {"pages": [...], "documents": [...]}
    fields.json = {"forms": [...], "other_documents": [...], "validation": [...]}
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.docproc import config
from app.docproc.engines.ocr import local
from app.docproc.engines.strategy import ladder, plan as plans
from app.docproc.services.documents import classify
from app.docproc.services.quality import cross, validation
from app.docproc.services.extraction import align, digital, discover, figures, form_extractor, kv, remainder, structure
from app.docproc.services.reading import ocr, pdf_pages, router, tess

logger = logging.getLogger("app.docproc.pipeline")

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"      # built-in templates ship next to this module
PAGE_CONCURRENCY = 4
AUTO, DISCOVER = "auto", "discover"

Progress = Callable[[str, int, int], None]


def load_template(name: str) -> Dict[str, Any]:
    """Built-in templates ship with the app; custom ones belong to the workspace in force."""
    from app.docproc.services.documents import workspace_types
    f = TEMPLATE_DIR / f"{name}.json"
    if f.is_file():
        return json.loads(f.read_text())
    custom = workspace_types.template(name)
    if custom is not None:
        return custom
    raise FileNotFoundError(f"No template named {name!r}")


def type_templates() -> Dict[str, str]:
    return {d["type"]: d["template"] for d in classify.all_types() if d.get("template")}


def valid_choice(choice: str) -> bool:
    """'auto' (detect), 'discover' (read as one document with no template) or any registered type with a template."""
    return choice in (AUTO, DISCOVER) or choice in type_templates()


def work_path(path: str) -> str:
    return str(Path(path).with_suffix(".work.pdf"))


def _label_continuations(documents: List[Dict[str, Any]], by_num: Dict[int, Dict[str, Any]]) -> None:
    """A page with no title of its own that sits inside a recognised document continues it: say so, instead of calling it another
    document. Its type stays "unknown", so which pages a template reads is unchanged."""
    for d in documents:
        if d["label"] in classify.GENERIC_LABELS:
            continue
        for n in d["pages"]:
            c = by_num[n]["classification"]
            if c["type"] == "unknown" and (d["type"] != "unknown" or (n != d["pages"][0] and c.get("label") in classify.GENERIC_LABELS)):
                c.update({"label": f"{d['label']} (continued)", "by": "document"})


def _classify_page(text: str, structure_page: Dict[str, Any], reading: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The rules' answer for a page. When no type matches, say what the page is from its own top (its title, or a recognised kind
    such as an SEC filing or an invoice) rather than leaving a bare "Unclassified"; the type stays "unknown" so routing is unchanged."""
    got = classify.classify_text(text)
    if got["type"] != "unknown":
        return got
    r = reading or {}
    # a local engine that was unsure, or that another engine disagreed with, may have produced noise: do not title the page from it
    trust = (r.get("confidence") is None or r["confidence"] >= 0.8) and (r.get("agreement") is None or r["agreement"] >= 0.75)
    d = classify.describe(structure_page.get("blocks", []), text, trust)
    return {**got, "label": d.get("label") or "Other document", "title": d.get("title"), "kind": d.get("kind"), "by": "layout"}


def _name_pages(pages: List[Dict[str, Any]]) -> None:
    """Take what every page repeats (running headers and footers) out of the structures, then describe again the pages that
    were described from their layout: the repeated banner is no longer mistaken for each page's title."""
    if not structure.strip_running([p.get("structure") for p in pages]):
        return
    for p in pages:
        if p.get("classification", {}).get("by") == "layout":
            p["classification"] = _classify_page(p["text"], p["structure"], p.get("reading"))


async def _page(path: str, work: str, n: int, models: List[str], use_llm: bool, reader, sem: asyncio.Semaphore,
                plan: "plans.Plan", mode: str, force_vision: bool = False, tsv: Optional[str] = None) -> Dict[str, Any]:
    async with sem:
        base: Dict[str, Any] = {"page": n}
        try:
            original = await asyncio.to_thread(pdf_pages.analyze_page, path, n)
            route = router.decide_reader(router.profile_page(original), use_llm, plan.local_engine is not None, mode)
            info = await asyncio.to_thread(pdf_pages.analyze_page, work, n)  # geometry from the work PDF (has OCR layer on scans)
            lines_meta: Dict[str, Any] = {}
            layout, reference = None, None
            jpeg = None
            handwritten = False
            reading: Dict[str, Any] = {"engine": "text-layer"}
            if route["reader"] in ("ocr_dual", "ocr_ladder", "ocr_local"):
                jpeg = await asyncio.to_thread(pdf_pages.render_jpeg, path, n)
                scan = await ladder.read_scanned(
                    mode={"ocr_dual": "vision", "ocr_ladder": "escalate"}.get(route["reader"], "local"), engine_id=plan.local_engine,
                    jpeg=jpeg, models=models, reader=reader, threshold=plan.escalate_below, token_cap=plan.token_cap,
                    force_vision=force_vision and route["reader"] != "ocr_local", tsv=tsv)
                raw = [l.text for l in scan.lines]
                lines_meta = {l.text: l for l in scan.lines}
                source, verified, handwritten, reading, layout, reference = scan.source, scan.verified, scan.handwritten, scan.reading, scan.layout, scan.reference
            else:
                raw = [l.strip() for l in original["text"].splitlines() if l.strip()]
                source = "text-layer" if route["reader"] == "text_layer" else "text-layer-unverified"
                verified = route["reader"] == "text_layer"
                if source != "text-layer":
                    reading = {"engine": "scanner text"}
            content, meta = pdf_pages.strip_fax_stamp(raw)
            out_lines = []
            for t in content:
                l = lines_meta.get(t)
                out_lines.append({"text": t, "status": l.status if l else "agreed",
                                  "alternatives": l.alternatives if l else [], "reason": l.reason if l else None})
            text = "\n".join(content)
            if source == "text-layer" and info["words"]:
                layout = local.build_reading([local.Word(*w["bbox"], w["text"]) for w in info["words"]], "text-layer",
                                             size=(info["size"]["width"], info["size"]["height"])).line_cells
            # figures: pictures, charts and drawings, found by code (no model); reading one is a separate, requested step
            figs: List[Dict[str, Any]] = []
            page_tables = info["tables"]
            w0, h0 = info["size"]["width"], info["size"]["height"]
            if w0 and h0:
                try:
                    if source == "text-layer":
                        raw_figs = await asyncio.to_thread(figures.find_digital, path, n, info["tables"])
                    elif source in ("ocr", "ocr-local") and jpeg is not None:
                        raw_figs = await asyncio.to_thread(figures.find_scanned, jpeg, info["words"], info["size"])
                    else:
                        raw_figs = []
                    figs = [{**f, "bbox": [f["bbox"][0] / w0, f["bbox"][1] / h0, f["bbox"][2] / w0, f["bbox"][3] / h0]} for f in raw_figs]
                    page_tables = [t for t in info["tables"] if not any(figures.overlap(t["bbox"], f["bbox"]) >= 0.5 for f in raw_figs if t.get("bbox"))]
                except Exception:
                    logger.exception("Finding figures on page %s failed", n)
            given = []
            if source == "text-layer" and info["size"]["width"] and info["size"]["height"]:
                w, h = info["size"]["width"], info["size"]["height"]
                for t in page_tables:
                    lt = digital.labelled_table(path, n, t) if t.get("bbox") else None
                    # the extractor reads tables of amounts; a table of names and codes is found better from the layout (structure.py)
                    if lt and digital.is_amount_table(t):
                        given.append({"bbox": [t["bbox"][0] / w, t["bbox"][1] / h, t["bbox"][2] / w, t["bbox"][3] / h], "columns": lt["columns"],
                                      "rows": lt["rows"], "method": lt["method"], "all_rows": t.get("rows")})
            placed, positions_from = None, None
            if not layout and source in ("ocr", "ocr-local"):
                # a vision model's text has no positions: borrow them from a local reading of the same page, when the two match well.
                # The local engine that escalated the page is the better reference; Tesseract's layer is always there as the other.
                refs = [(reference, "local engine")] if reference else []
                if info["words"] and info["size"]["width"]:
                    refs.append((local.build_reading([local.Word(*w["bbox"], w["text"]) for w in info["words"]], "tesseract",
                                                     size=(info["size"]["width"], info["size"]["height"])).line_cells, "tesseract"))
                for ref, name in refs:
                    got = align.place(raw, ref) if ref else None
                    if got:
                        layout, placed, positions_from = got[0], got[1], name
                        break
            structure_page = structure.build_page(layout, tables=given, figures=figs) if layout else structure.build_from_lines(content)
            if placed is not None:
                structure_page["positions_from"], structure_page["placement"] = positions_from, round(placed, 2)
            fillable = digital.fields_from_widgets(info["fields"], info["words"], info["size"]) if info.get("fields") and source == "text-layer" else None
            return {**base, "fillable": {"fields": fillable[0], "boxes": fillable[1]} if fillable else None,
                    "size": info["size"], "source": source, "verified": verified, "scanned": original["scanned"],
                    "has_text_layer": bool(info["words"]), "handwritten": handwritten, "page_meta": meta,
                    "classification": _classify_page(text, structure_page, reading), "text": text, "lines": out_lines,
                    "structure": structure_page, "blocks": info["blocks"], "tables": page_tables, "form_widgets": info["widgets"], "route": route, "reading": reading}
        except Exception as e:
            logger.exception("Page %s failed", n)
            msg = str(e) if isinstance(e, ocr.OcrError) else "This page could not be read."
            return {**base, "size": None, "source": "failed", "verified": False, "scanned": False, "has_text_layer": False, "handwritten": False,
                    "page_meta": {}, "classification": {"type": "unknown", "label": "Unreadable", "confidence": 0.0},
                    "text": "", "lines": [], "structure": {"blocks": [], "entities": [], "layout": False}, "blocks": [], "tables": [], "form_widgets": [], "route": None, "reading": {}, "error": msg}


async def process(path: str, progress: Optional[Progress] = None, ocr_reader=None, form_reader=None, model_ask=None,
                  template_choice: str = AUTO, read_rest: bool = False, adjudicator=None, plan: Optional["plans.Plan"] = None,
                  force_vision: Iterable[int] = ()) -> Dict[str, Any]:
    """`plan` says how scans are read (engines/plan.py); without one the server's defaults apply. `force_vision` lists pages
    to send straight to the vision models whatever the plan says (a person asked for a better reading of them)."""
    plan = plan or plans.current()
    with plans.use(plan):
        result = await _process(path, progress, ocr_reader, form_reader, model_ask, template_choice, read_rest, adjudicator, plan, set(force_vision))
    result["reading"] = plan.describe()
    return result


async def _process(path: str, progress: Optional[Progress], ocr_reader, form_reader, model_ask, template_choice: str, read_rest: bool,
                   adjudicator, plan: "plans.Plan", force_vision: "set[int]") -> Dict[str, Any]:
    progress = progress or (lambda *_: None)
    n_pages = await asyncio.to_thread(pdf_pages.page_count, path)
    if n_pages > config.max_pages():
        raise ocr.OcrError(f"PDF has {n_pages} pages; the limit is {config.max_pages()}.", 413)
    # "local" plans never call a model. Otherwise a model is used when one is configured (or a test supplied a reader).
    use_llm = (plan.llm or ocr_reader is not None) and plan.mode != "local"
    mode = plan.mode if use_llm else "local"
    ocr_models = plan.read_models if use_llm else []
    form_models = plan.extract_models if (plan.mode != "local" and (plan.llm or form_reader is not None)) else []
    if ocr_reader is not None and not ocr_models:
        ocr_models = ["reader-a", "reader-b"]
    if form_reader is not None and not form_models:
        form_models = ["reader-a", "reader-b"]

    # Text layer for scans first: everything geometric (highlighting, label search, checkboxes, tables) depends on it.
    progress("preparing", 0, n_pages)
    work = work_path(path)
    needs = await asyncio.to_thread(pdf_pages.scanned_pages, path)
    tsv_pages: Dict[int, str] = {}
    layer = await asyncio.to_thread(pdf_pages.build_work_pdf, path, work, needs, tsv_pages)
    layer_errors = {n: m for n, m in layer.items() if m != "ok"}

    sem = asyncio.Semaphore(PAGE_CONCURRENCY)
    done = 0

    async def tracked(n):
        nonlocal done
        p = await _page(path, work, n, ocr_models, use_llm, ocr_reader, sem, plan, mode, n in force_vision, tsv_pages.get(n))
        done += 1
        progress("pages", done, n_pages)
        return p

    pages = sorted(await asyncio.gather(*[tracked(n) for n in range(1, n_pages + 1)]), key=lambda p: p["page"])
    for p in pages:
        if p["page"] in layer_errors:
            p["text_layer_error"] = layer_errors[p["page"]]
    figures.drop_logos(pages)
    _name_pages(pages)
    if template_choice != AUTO:
        # The user said what this file is. Every page that has content becomes that document; no guessing.
        reg = {d["type"]: d for d in classify.all_types()}
        forced = ({"type": template_choice, "label": reg[template_choice]["label"], "confidence": 1.0, "by": "user"}
                  if template_choice in reg else {"type": "unknown", "label": "Untemplated document", "confidence": 1.0, "by": "user"})
        for p in pages:
            if p["classification"]["type"] != "blank_or_logo" and p["source"] != "failed":
                p["classification"] = dict(forced)
                if forced["type"] == "unknown":
                    d = classify.describe(p.get("structure", {}).get("blocks", []), p["text"], p.get("reading", {}).get("confidence", 1) is None or p.get("reading", {}).get("confidence", 1) >= 0.8)
                    if d.get("label"):
                        p["classification"].update({"label": d["label"], "title": d.get("title"), "kind": d.get("kind")})
    elif use_llm and ocr_models:
        streak = 0  # consecutive pages the model also could not name; a long untyped document stops costing a call per page
        for i, p in enumerate(pages):  # rules could not name a non-trivial page: ask a small model, restricted to registered types
            if p["classification"]["type"] == "unknown" and p["text"].strip():
                if streak >= config.classify_give_up_after():
                    continue
                prev = pages[i - 1]["text"] if i else ""
                got = await classify.classify_with_model(p["text"], ocr_models, model_ask, prev)
                if got["type"] == "unknown" and got["label"] == "Unclassified":     # the model had nothing better: keep what the page's own top says
                    got = {**p["classification"], **({"continuation": got["continuation"]} if "continuation" in got else {})}
                p["classification"] = got
                streak = streak + 1 if p["classification"]["type"] == "unknown" else 0
            elif p["classification"]["type"] != "unknown":
                streak = 0
    single_page = set()
    for t, name in type_templates().items():
        try:
            if load_template(name).get("single_page"):
                single_page.add(t)
        except FileNotFoundError:
            pass
    handwritten = {p["page"] for p in pages if p.get("handwritten")}
    documents = classify.segment(pages, single_page)
    by_num = {p["page"]: p for p in pages}
    _label_continuations(documents, by_num)

    forms, others, templated = [], [], []
    templates = type_templates()
    for i, doc in enumerate(documents):
        progress("documents", i, len(documents))
        doc_pages = [by_num[n] for n in doc["pages"]]
        texts = {p["page"]: p["text"] for p in doc_pages}
        decision = router.decide_extractor(doc["type"], llm_available=bool(form_models),
                                           all_digital=all(p["source"] == "text-layer" for p in doc_pages),
                                           laid_out=all(p["source"] in ("text-layer", "ocr-local") and p["structure"].get("layout") for p in doc_pages if p["classification"]["type"] != "blank_or_logo"))
        doc["route"] = decision
        tpl_name = templates.get(doc["type"]) if decision["extractor"] == "template_form" else None
        if tpl_name:
            tpl = load_template(tpl_name)
            usable = [p["page"] for p in doc_pages if p["classification"]["type"] == doc["type"]] or doc["pages"]
            # Box glyphs (☑ ☐) are only evidence on a born-digital page. In a vision model's transcript they are the model's
            # own guess about the box, so on scans the boxes are judged from the pixels instead.
            glyph_texts = {p["page"]: (p["text"] if p["source"] == "text-layer" else "") for p in doc_pages}
            res = await form_extractor.extract_document(work, usable, glyph_texts, tpl, form_models, form_reader, handwritten, adjudicator,
                                                        page_texts={n: by_num[n]["text"] for n in usable})
            forms.append({"doc_index": doc["doc_index"], "doc_type": doc["type"], "template_id": tpl["template_id"],
                          "pages": doc["pages"], "readers": res["readers"], "fields": res["fields"], "tables": res["tables"],
                          "validation": validation.validate_form(tpl, res["fields"], res["tables"]),
                          "_template": tpl, "_text": "\n".join(texts.values())})
            templated.append((forms[-1], tpl))
        elif decision["extractor"] == "discover":
            usable = [p["page"] for p in doc_pages if p["source"] != "failed" and p["classification"]["type"] != "blank_or_logo"] or doc["pages"]
            digital_pages = {}
            for n in usable:
                pg = by_num[n]
                laid_out_scan = pg["source"] == "ocr-local" and not form_models and pg["structure"].get("layout")
                if pg["source"] != "text-layer" and not laid_out_scan:
                    continue
                blocks, size = pg["structure"]["blocks"], pg.get("size") or {}
                r = pg.get("reading") or {}
                doubtful = (r.get("confidence") is not None and r["confidence"] < 0.8) or (r.get("agreement") is not None and r["agreement"] < 0.75)
                digital_pages[n] = {"ocr": ("doubtful" if doubtful else "single") if laid_out_scan else None, "lines": [l["text"] for l in pg["lines"]],
                                    # amounts by the extractor, everything else by the layout; and the pairs and boxes the layout found
                                    "tables": digital.titled([t for t in pg["tables"] if digital.is_amount_table(t)], blocks, size) + digital.tables_from_structure(blocks, size)
                                    if not laid_out_scan else digital.tables_from_structure(blocks, size),
                                    "facts": digital.facts_from_structure(blocks, size),
                                    "boxes": (pg.get("fillable") or {}).get("boxes") or digital.boxes_from_structure(blocks, size),
                                    "form_fields": (pg.get("fillable") or {}).get("fields", [])}
            # A page a local OCR engine read well is structured from its text: a few hundred tokens instead of a page image.
            ocr_text = {n: by_num[n]["text"] for n in usable if form_models and by_num[n]["source"] == "ocr-local" and by_num[n]["text"].strip()}
            res = await discover.discover_document(work, usable, form_models, form_reader, handwritten, adjudicator,
                                                   text_pages=ocr_text, digital_pages=digital_pages)
            if res["title"] and doc["type"] == "unknown" and doc["label"] in classify.GENERIC_LABELS and res["readers"] != ["ocr-local"]:
                doc["label"] = res["title"]               # only when the first page said nothing itself: a table's caption is no document title
            forms.append({"doc_index": doc["doc_index"], "doc_type": doc["type"], "template_id": "discovered", "discovered": True,
                          "label": doc["label"] if (doc["label"] not in classify.GENERIC_LABELS or res["readers"] == ["ocr-local"]) else (res["title"] or doc["label"]), "pages": doc["pages"], "readers": res["readers"],
                          "fields": res["fields"], "tables": res["tables"], "table_columns": res["table_columns"],
                          "validation": validation.review_notes(res["fields"], res["tables"])
                                        + [{"rule": "incomplete_table", "severity": "warn", "detail": n} for n in res.get("unverified", []) if n.startswith("page ")],
                          "_template": {"canonical": {}}, "_text": "\n".join(texts.values())})
        elif decision["extractor"] == "kv":
            lines = [l["text"] for p in doc_pages for l in p["lines"]]
            pairs = kv.parse_kv(lines)
            others.append({"doc_index": doc["doc_index"], "doc_type": doc["type"], "pages": doc["pages"],
                           "pairs": pairs, "canonical": kv.canonical(pairs)})

    rest_checked: List[int] = []
    if read_rest and form_models:
        # Text and structure around a template's own fields: read it with discovery, minus what the template covers.
        progress("rest", 0, len(templated))
        next_index = len(documents)               # indexes after the real documents, so nothing collides
        for n, (f, tpl) in enumerate(templated):
            rest_checked.append(f["doc_index"])
            extra = await remainder.remainder_form(work, f, tpl, form_models, form_reader)
            if extra:
                extra.update({"doc_index": next_index, "_template": {"canonical": {}}, "_text": ""})
                next_index += 1
                forms.append(extra)
            progress("rest", n + 1, len(templated))
    findings: List[Dict[str, Any]] = []
    for f in forms:
        findings += [{**v, "doc_index": f["doc_index"]} for v in f["validation"]]
    findings += cross.check([{"doc_index": f["doc_index"], "doc_type": f["doc_type"], "pages": f["pages"], "fields": f["fields"],
                              "tables": f["tables"], "text": f["_text"], "template": f["_template"]} for f in forms])
    for f in forms:  # internal helpers, not part of the stored output
        f.pop("_template"), f.pop("_text")
    progress("done", n_pages, n_pages)
    return {
        "pages": pages, "documents": documents, "forms": forms, "other_documents": others, "validation": findings,
        "rest_checked": rest_checked,
        "stats": {"pages": n_pages, "forms": len(forms),
                  "figures": sum(1 for p in pages for b in (p.get("structure") or {}).get("blocks", []) if b.get("type") == "figure"),
                  "needs_review": sum(1 for f in forms for c in list(f["fields"].values()) + [x for rows in f["tables"].values() for r in rows for x in r.values()]
                                      if c.get("status") in ("review", "undetermined")),
                  "errors": sum(1 for v in findings if v["severity"] == "error"),
                  "failed_pages": sum(1 for p in pages if p["source"] == "failed"),
                  # how the file was read, for the Documents page: code (digital), models (scans), handwriting seen
                  "digital_pages": sum(1 for p in pages if p["source"] == "text-layer"),
                  "scanned_pages": sum(1 for p in pages if p["source"] in ("ocr", "ocr-local", "text-layer-unverified")),
                  "local_ocr_pages": sum(1 for p in pages if p["source"] == "ocr-local"),
                  "vision_pages": sum(1 for p in pages if p["source"] == "ocr"),
                  "escalated_pages": sum(1 for p in pages if (p.get("reading") or {}).get("escalated")),
                  "region_pages": sum(1 for p in pages if (p.get("reading") or {}).get("scope") == "regions"),
                  "handwritten_pages": sum(1 for p in pages if p.get("handwritten")),
                  "unverified_tables": sum(1 for f in forms for rows in f["tables"].values()
                                           if any(c.get("method") == "text-layer" and c.get("status") == "review" for r in rows for c in r.values()))},
    }
