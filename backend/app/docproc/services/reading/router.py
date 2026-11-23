"""
Pipeline router: the decision layer that picks HOW each page and each document is processed, and records why.

Two decisions, both returned with human-readable reasons so a reviewer (or a future eval) can see the trace:

  decide_reader(profile)   -> how to get the page TEXT:
        text_layer             born-digital page, embedded text is exact
        ocr_ladder             scan: local OCR first, vision models only when the local reading is doubtful
        ocr_local              scan: local OCR only (economy plan, or no model configured)
        text_layer_unverified  scan with only the scanner's own text layer, and no model available
        ocr_dual               scan / image-only page: two independent vision readers, lines cross-checked
  decide_extractor(doc)    -> how to get the document's DATA:
        template_form          fixed form with a field schema (templates/*.json)
        kv                     generic 'Label: value' pairs (clinical notes, letters, anything unmodelled)
        skip                   cover sheets and blank pages

Rules decide first because they are free, deterministic and explainable. For pages the rules cannot name,
classify.classify_with_model asks a small model to pick among the REGISTERED types only. Supporting a new document
kind is a registry entry (classify.DOC_TYPES) plus, for a fixed form, a template file; nothing here changes.
"""
from typing import Any, Dict, List

from app.docproc.services.documents import classify
from app.docproc.services.reading import ocr



def profile_page(info: Dict[str, Any]) -> Dict[str, Any]:
    """Cheap signals from analyze_page() that drive the reader decision."""
    return {
        "chars": len(info["text"]),
        "scanned": info["scanned"],
        "widgets": len(info.get("widgets", [])),
        "tables": len(info["tables"]),
    }


def decide_reader(profile: Dict[str, Any], llm_available: bool, local_available: bool = False, mode: str = "vision") -> Dict[str, Any]:
    """How to get a page's text. `mode` is the reading plan's effective mode (engines/plan.py): "vision" sends every scan
    to the vision models, "escalate" reads it locally first and escalates only when that reading is doubtful, "local"
    never calls a model."""
    reasons: List[str] = []
    if profile["scanned"]:
        reasons.append("the page is a picture, or its embedded text is unreadable, so any text layer is not trusted")
    elif profile["chars"] < ocr.MIN_TEXT_LAYER_CHARS:
        reasons.append(f"only {profile['chars']} characters of embedded text")
    else:
        reasons.append(f"{profile['chars']} characters of embedded text and no page-sized picture")
        return {"reader": "text_layer", "reasons": reasons}
    if llm_available and mode == "escalate" and local_available:
        return {"reader": "ocr_ladder", "reasons": reasons + ["local OCR reads the page first; a vision model only steps in if that reading is doubtful"]}
    if llm_available and mode != "local":
        return {"reader": "ocr_dual", "reasons": reasons + ["two vision readers transcribe the page and are compared"]}
    if local_available:
        why = "the reading plan keeps models out of it" if llm_available else "no model is configured"
        return {"reader": "ocr_local", "reasons": reasons + [f"{why}, so a local OCR engine reads the page on this machine (single reader, unverified)"]}
    return {"reader": "text_layer_unverified", "reasons": reasons + ["no model and no local OCR engine available, using the embedded text as is"]}


def decide_extractor(doc_type: str, llm_available: bool = True, all_digital: bool = False, laid_out: bool = False) -> Dict[str, Any]:
    """template_form | discover | kv | skip, with reasons.

    A registered type uses its template. A type nobody registered is not an error: with a model available its
    fields are discovered from the page itself; without one it falls back to plain 'Label: value' reading."""
    if doc_type == "blank_or_logo":
        return {"extractor": "skip", "reasons": ["blank or logo-only page"]}
    reg = {d["type"]: d for d in classify.all_types()}
    if doc_type in reg:
        kind = reg[doc_type]["extractor"]
        why = {"template_form": f"{reg[doc_type]['label']} is a known document type with a template",
               "kv": f"{reg[doc_type]['label']} is read as label and value pairs", "skip": f"{reg[doc_type]['label']} carries no data to extract"}
        return {"extractor": kind, "reasons": [why.get(kind, f"{reg[doc_type]['label']} is a known document type")]}
    if all_digital:
        return {"extractor": "discover", "reasons": ["no template matches this document; every page has real text, so its tables and labelled values are read directly from the PDF"
                                                     + (" (a model only steps in for pages code cannot read completely)" if llm_available else "")]}
    if llm_available:
        return {"extractor": "discover", "reasons": ["no template matches this document, so its fields, checkboxes and tables are read from the page itself"]}
    if laid_out:
        return {"extractor": "discover", "reasons": ["no template matches this document and no model is in use, so its tables, labelled values and check boxes are read from the layout the local OCR found "
                                                     "(single reader: every value is marked unverified)"]}
    return {"extractor": "kv", "reasons": ["no template matches this document and no model is in use, so its \"Label: value\" lines are read. Open the page in the Pages tab to see everything else on it; add a template for this type, or let a model read it, to capture more"]}


READERS = [
    {"id": "text_layer", "label": "Embedded text (read by code)", "summary": "Digital page: the PDF's own text and table cells are copied exactly, and every number in a table is checked against the page. No model is used unless code cannot prove it read a form-like page completely."},
    {"id": "ocr_dual", "label": "Two vision readers", "summary": "Scan, photo or handwriting: two models transcribe the page and their readings are compared by content. A Tesseract layer supplies positions for highlighting. Disputed values get one focused third look."},
    {"id": "ocr_ladder", "label": "Local OCR, then vision if needed", "summary": "Scan: a local OCR engine reads it first. Only if its confidence is low does a pair of vision models read the page and compare."},
    {"id": "ocr_local", "label": "Local OCR on this machine", "summary": "Scan read by a local OCR engine (Tesseract by default): nothing leaves the server. Single reader, so unverified; doubtful lines are flagged."},
    {"id": "text_layer_unverified", "label": "Scanner text, unverified", "summary": "Scan with neither a model nor Tesseract available: only the scanner's own text, if it has any, is used."},
]
