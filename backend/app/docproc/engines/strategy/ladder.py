"""
The escalation ladder for one scanned page: read it with the cheapest engine that can be trusted.

    1. the local OCR engine reads the page (free)
    2. its confidence and the look of the text are checked (local.assess)
    3. good enough -> keep it.  Doubtful -> two vision models read the page and are compared (the paid step)

Modes (see plan.py): "local" stops after step 2 and only flags what is doubtful; "escalate" does the whole ladder;
"vision" skips straight to step 3. A per-document token cap stops step 3 once the budget is spent: the page then keeps its
local reading, flagged for a person, rather than silently costing more.

When only a few lines of a page are doubtful, only those regions are cropped and sent (see local.plan_regions): the rest of
the page keeps its local reading. A page that is mostly doubtful, or whose doubtful lines are scattered, is read whole.

A vision service that is down never fails a page that a local engine could read: the local reading is kept and the page
says why it was not escalated.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.docproc.engines.ocr import local
from app.docproc.services.reading import ocr
from app.utils.usage_tracker import get_current_tracker

logger = logging.getLogger("app.docproc.engines.strategy.ladder")


@dataclass
class Scan:
    lines: List[ocr.OcrLine]
    source: str                      # "ocr" (vision models) | "ocr-local"
    verified: bool                   # two readers compared
    handwritten: bool
    reading: Dict[str, Any] = field(default_factory=dict)
    layout: Optional[List[Any]] = None          # the page as rows of positioned cells, when the lines are the local engine's own (see structure.py)
    reference: Optional[List[Any]] = None       # the local engine's layout of a page a vision model then read: positions to borrow (see align.py)


def tokens_used() -> int:
    t = get_current_tracker()
    return sum(e.total_tokens for e in t.entries) if t else 0


def over_budget(cap: int) -> bool:
    return cap > 0 and tokens_used() >= cap


def _local_lines(r: local.Reading) -> List[ocr.OcrLine]:
    out = []
    for text, conf in zip(r.lines, r.line_conf):
        bad = conf is not None and conf < local.LOW_WORD
        out.append(ocr.OcrLine(text=text, status="review" if bad else "agreed", alternatives=[],
                               reason="the OCR engine was unsure of this line" if bad else None))
    return out


async def _reference(jpeg: bytes, tsv: Optional[str]) -> Optional[local.Reading]:
    """Tesseract's reading of the page, used to cross-check an engine whose confidence cannot be trusted. Free when the
    work-PDF pass already ran it."""
    tess_eng = local.get("tesseract")
    if tess_eng is None or not tess_eng.available():
        return None
    try:
        return local.build_reading(local.words_from_tsv(tsv), "tesseract", size=local.tsv_size(tsv)) if tsv is not None else await tess_eng.read(jpeg)
    except Exception as e:
        logger.warning("Reference OCR failed: %s", e)
        return None


async def _read_regions(reading: local.Reading, regions: List[local.Region], jpeg: bytes, models: List[str], reader,
                        info: Dict[str, Any]) -> Optional[Scan]:
    """Have the vision models read only the doubtful regions and put their lines in place of the local ones. None when that
    cannot be done (a model call fails): the caller then decides whether the whole page should be read."""
    try:
        crops = [await asyncio.to_thread(local.crop, jpeg, g.box) for g in regions]
        pages = await asyncio.gather(*[ocr.read_page(c, models, reader) for c in crops])
    except Exception as e:
        logger.warning("Region read failed, reading the whole page instead: %s", e)
        return None
    lines: List[ocr.OcrLine] = []
    local_lines = _local_lines(reading)
    cursor = 0
    for g, page in zip(regions, pages):
        lines += local_lines[cursor:g.first]
        lines += page.lines or local_lines[g.first:g.last + 1]
        cursor = g.last + 1
    lines += local_lines[cursor:]
    replaced = sum(g.last - g.first + 1 for g in regions)
    info.update({"escalated": True, "scope": "regions", "regions": len(regions), "region_lines": replaced, "lines": len(reading.lines),
                 "models": models[:2], "local_confidence": reading.confidence})
    info["reasons"] = list(info.get("reasons", [])) + [f"{replaced} of {len(reading.lines)} lines were doubtful, so only {'that region' if len(regions) == 1 else f'those {len(regions)} regions'} went to the vision models"]
    return Scan(lines, "ocr-local", False, any(p.handwritten for p in pages), info, None, reading.line_cells)


async def read_scanned(*, mode: str, engine_id: Optional[str], jpeg: bytes, models: List[str], reader=None, threshold: float = 0.85,
                       token_cap: int = 0, force_vision: bool = False, tsv: Optional[str] = None) -> Scan:
    """Read one scanned page. `mode` is the effective mode ("local", "escalate" or "vision")."""
    eng = local.get(engine_id)
    reading: Optional[local.Reading] = None
    local_error = ""
    if mode != "vision" and not force_vision and eng is not None:
        try:
            if eng.id == "tesseract" and tsv is not None:       # the work-PDF pass already ran Tesseract on this page
                reading = local.build_reading(local.words_from_tsv(tsv), eng.id, size=local.tsv_size(tsv))
            else:
                reading = await eng.read(jpeg)
        except Exception as e:
            logger.warning("Local OCR (%s) failed: %s", eng.id, e)
            local_error = f"{eng.label} could not read the page"

    info: Dict[str, Any] = {"engine": eng.id if reading else None, "confidence": reading.confidence if reading else None,
                            "escalated": False, "reasons": []}
    if reading is not None:
        if eng.calibrated:
            verdict = local.assess(reading, threshold, jpeg)
        else:                              # this engine's own confidence is not trustworthy: Tesseract has to agree with it
            reference = await _reference(jpeg, tsv)
            if reference is not None:
                reading = local.cross_check(local.respace(reading, reference), reference)
                verdict = local.assess_cross(reading, jpeg)
                info["agreement"] = reading.agreement
            else:
                verdict = local.assess(reading, max(threshold, 0.9), jpeg)      # its own number is all there is: be strict
                if not verdict.ok:
                    verdict.reasons.append("no second OCR engine is installed to cross-check it")
        info["confidence"] = reading.confidence
        info["reasons"] = list(verdict.reasons)
        if mode == "local":
            return Scan(_local_lines(reading), "ocr-local", False, False, info, reading.line_cells)
        # Doubtful lines are re-read on their own, whether or not the page as a whole passed: an average hides one bad line.
        regions = None
        if reading.words > 0 and not ocr.text_is_garbled(reading.text):
            regions = local.plan_regions(reading, local.doubtful_lines(reading, eng.calibrated))
        if regions:
            if over_budget(token_cap):
                info["cost_capped"] = True
                info["reasons"].append("the token cap for this document was reached, so doubtful lines were not re-read by a vision model")
                return Scan(_local_lines(reading), "ocr-local", False, False, info, reading.line_cells)
            done = await _read_regions(reading, regions, jpeg, models, reader, info)
            if done is not None:
                return done
            info["reasons"].append("the vision models could not re-read the doubtful lines")
        if verdict.ok:
            return Scan(_local_lines(reading), "ocr-local", False, False, info, reading.line_cells)
        if over_budget(token_cap):
            info["cost_capped"] = True
            info["reasons"].append("the token cap for this document was reached, so a vision model was not used")
            return Scan(_local_lines(reading), "ocr-local", False, False, info, reading.line_cells)
    elif local_error:
        info["reasons"].append(local_error)
    elif force_vision:
        info["reasons"].append("you asked for this page to be re-read with the vision models")

    try:
        page = await ocr.read_page(jpeg, models, reader)
    except ocr.OcrError as e:
        if reading is not None:                                   # keep what the local engine got rather than fail the page
            info["reasons"].append("the vision models were unavailable, so the local reading was kept")
            return Scan(_local_lines(reading), "ocr-local", False, False, info, reading.line_cells)
        raise e
    info.update({"escalated": reading is not None or bool(local_error) or force_vision, "scope": "page", "engine": "vision", "models": models[:2]})
    if reading is not None:
        info["local_confidence"] = reading.confidence
    return Scan(page.lines, "ocr", page.cross_checked, page.handwritten, info, None, reading.line_cells if reading is not None else None)
