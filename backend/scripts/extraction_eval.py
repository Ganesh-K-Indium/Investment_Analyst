#!/usr/bin/env python3
"""
Measure financial-statement extraction against known answers. No model is called (local OCR only), so it costs nothing.

Three statements with consistent numbers (balance sheet, income statement, cash flow) are drawn as PDFs, then read as: the digital PDF, and as
scans at decreasing quality (300 dpi clean, 200 dpi clean, 120 dpi, noisy and skewed, fax-like). For every figure we know the answer to, the
extracted table is compared and each figure lands in exactly one bucket:

    exact            read correctly
    wrong_flagged    read wrongly, but marked for review (a person would be asked): safe
    wrong_silent     read wrongly and NOT marked: the dangerous bucket
    missing          not found in the extracted tables

    python scripts/extraction_eval.py [--out report.json] [--keep-dir DIR]
"""
import argparse
import asyncio
import difflib
import json
import sys
import tempfile
from pathlib import Path

import fitz
import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.docproc import pipeline                                           # noqa: E402
from app.docproc.engines.strategy import plan as plans                      # noqa: E402
from app.docproc.services.finance.numbers import parse                      # noqa: E402
from app.services.user_docs import chunks as ch, normalize                  # noqa: E402

STATEMENTS = {
    "balance_sheet": ("ZETA Corp consolidated balance sheet (in thousands)", ["", "FY2025", "FY2024"], [
        ("Cash and cash equivalents", [1200450, 1000300]), ("Accounts receivable", [800120, 700550]), ("Inventory", [500230, 450800]),
        ("Total current assets", [2500800, 2151650]), ("Property and equipment", [3000900, 2900400]), ("Total assets", [5501700, 5052050]),
        ("Accounts payable", [900300, 800150]), ("Long-term debt", [2100500, 2000250]), ("Total liabilities", [3000800, 2800400]),
        ("Common stock", [500100, 500100]), ("Retained earnings", [2000800, 1751550]), ("Total stockholders equity", [2500900, 2251650]),
        ("Total liabilities and equity", [5501700, 5052050])]),
    "income_statement": ("ZETA Corp consolidated statements of operations (in thousands)", ["", "FY2025", "FY2024"], [
        ("Net revenue", [912450, 801200]), ("Cost of revenue", [(-512300), (-455100)]), ("Gross profit", [400150, 346100]),
        ("Research and development", [(-120400), (-101500)]), ("Selling and administrative", [(-136450), (-125700)]), ("Total operating expenses", [(-256850), (-227200)]),
        ("Operating income", [143300, 118900]), ("Interest expense", [(-18300), (-17100)]), ("Income before taxes", [125000, 101800]),
        ("Income tax expense", [(-26240), (-20550)]), ("Net income", [98760, 81250])]),
    "cash_flow": ("ZETA Corp consolidated statements of cash flows (in thousands)", ["", "FY2025", "FY2024"], [
        ("Net income", [98760, 81250]), ("Depreciation and amortization", [45200, 41800]), ("Change in working capital", [(-12400), 9300]),
        ("Net cash from operating activities", [131560, 132350]), ("Purchases of equipment", [(-60300), (-52100)]), ("Net cash from investing activities", [(-60300), (-52100)]),
        ("Repayment of debt", [(-40000), (-50000)]), ("Net cash from financing activities", [(-40000), (-50000)]),
        ("Net increase in cash", [31260, 30250]), ("Cash at beginning of period", [1169190, 970050]), ("Cash at end of period", [1200450, 1000300])]),
}


def fmt(v):
    return f"({abs(v):,})" if v < 0 else f"{v:,}"


def draw_statement(path, title, header, rows, ruled=True):
    doc = fitz.open(); page = doc.new_page(width=595, height=842)
    page.insert_text((50, 60), title, fontsize=13)
    x = [50, 330, 440, 545]; y = 100; rh = 22
    for ci, h in enumerate(header[1:], 1):
        page.insert_text((x[ci] + 10, y + 15), h, fontsize=10.5)
    y += rh
    top = y - rh
    for label, vals in rows:
        bold = label.lower().startswith(("total", "net", "gross", "operating income", "income before"))
        page.insert_text((x[0] + 6, y + 15), label, fontsize=10.5, fontname="helv" if not bold else "hebo")
        for ci, v in enumerate(vals, 1):
            s = fmt(v); w = fitz.get_text_length(s, fontsize=10.5)
            page.insert_text((x[ci + 1] - 8 - w, y + 15), s, fontsize=10.5)
        y += rh
    if ruled:
        for k in range(len(rows) + 2):
            page.draw_line((x[0], top + k * rh), (x[-1], top + k * rh), width=0.5)
        for xi in x:
            page.draw_line((xi, top), (xi, top + (len(rows) + 1) * rh), width=0.5)
    doc.save(path)


def degrade(png_in, png_out, mode):
    img = Image.open(png_in).convert("L")
    rng = np.random.default_rng(7)
    if mode == "noisy_skew":
        img = img.rotate(0.8, expand=False, fillcolor=255).filter(ImageFilter.GaussianBlur(0.7))
        arr = np.array(img).astype(float) + rng.normal(0, 16, (img.height, img.width))
        img = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
    elif mode == "fax":
        arr = np.array(img.filter(ImageFilter.GaussianBlur(0.6)))
        arr = np.where(arr < 150, 0, 255).astype("uint8")
        flip = rng.random(arr.shape) < 0.012
        arr[flip] = 255 - arr[flip]
        img = Image.fromarray(arr)
    img.save(png_out)


VARIANTS = [("digital", None, None), ("scan_300dpi", 300, None), ("scan_200dpi", 200, None), ("scan_120dpi", 120, None), ("scan_noisy_skew", 200, "noisy_skew"), ("scan_fax", 150, "fax")]


def extracted_rows(result, corrections=None):
    rows = []
    for p in result["pages"]:
        for t in ch.page_tables(result, p["page"], corrections or {}):
            rows += [(t["cell_ids"][i], r) for i, r in enumerate(t["rows"])]
    return rows


def score(result, truth_rows):
    flagged = {i["id"] for i in ch.review_items(result) if i["kind"] == "cell"}
    pending_any = bool(ch.review_items(result))
    got = extracted_rows(result)
    out = {"exact": 0, "wrong_flagged": 0, "wrong_silent": 0, "missing": 0, "cells": 0}
    labels = [str(r[0]).lower() for _, r in got]
    for label, vals in truth_rows:
        m = difflib.get_close_matches(label.lower(), labels, n=1, cutoff=0.75)
        ids, row = got[labels.index(m[0])] if m else (None, None)
        for ci, v in enumerate(vals):
            out["cells"] += 1
            if row is None or ci + 1 >= len(row):
                out["missing"] += 1; continue
            p = parse(row[ci + 1])
            if p.kind == "amount" and p.value == v:
                out["exact"] += 1
            elif ids[ci + 1] in flagged or (p.kind != "amount" and pending_any):
                out["wrong_flagged"] += 1
            else:
                out["wrong_silent"] += 1
    out["held_for_review"] = pending_any
    return out


async def run_one(pdf_path):
    return await pipeline.process(str(pdf_path), plan=plans.resolve({"strategy": "economy"}))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out"); ap.add_argument("--keep-dir"); ap.add_argument("--variants", help="comma-separated subset, e.g. scan_noisy_skew,scan_fax")
    a = ap.parse_args()
    work = Path(a.keep_dir or tempfile.mkdtemp()); work.mkdir(parents=True, exist_ok=True)
    variants = [v for v in VARIANTS if not a.variants or v[0] in a.variants.split(",")]
    report, totals = {}, {}
    for sname, (title, header, rows) in STATEMENTS.items():
        base = work / f"{sname}.pdf"; draw_statement(str(base), title, header, rows)
        for vname, dpi, mode in variants:
            if dpi is None:
                src = base
            else:
                png = work / f"{sname}_{vname}.png"; fitz.open(str(base))[0].get_pixmap(dpi=dpi).save(str(png))
                if mode:
                    degrade(str(png), str(png), mode)
                src = work / f"{sname}_{vname}.pdf"; normalize.image_to_pdf(str(png), str(src))
            res = asyncio.run(run_one(src))
            s = score(res, rows); report[f"{sname}/{vname}"] = s
            t = totals.setdefault(vname, {"exact": 0, "wrong_flagged": 0, "wrong_silent": 0, "missing": 0, "cells": 0}); [t.__setitem__(k, t[k] + s[k]) for k in t]
            print(f"{sname:17} {vname:16} exact {s['exact']:>2}/{s['cells']} flagged {s['wrong_flagged']:>2} SILENT {s['wrong_silent']:>2} missing {s['missing']:>2}  held={s['held_for_review']}", flush=True)
    print("\nTOTAL by quality (cells = figures with a known answer)")
    for v, t in totals.items():
        print(f"  {v:16} exact {100*t['exact']/t['cells']:5.1f}%  wrong-but-flagged {100*t['wrong_flagged']/t['cells']:4.1f}%  WRONG-SILENT {100*t['wrong_silent']/t['cells']:4.1f}%  missing {100*t['missing']/t['cells']:4.1f}%")
    if a.out:
        Path(a.out).write_text(json.dumps({"per_document": report, "totals": totals}, indent=2))


if __name__ == "__main__":
    main()
