"""
Does the table add up? The strongest guard against a misread digit.

A statement's totals are redundant with its lines, so when OCR misreads one digit the arithmetic stops holding. These checks need no model:

    totals        a "Total ..." row equals the sum of the rows above it (nested totals are summed as totals)
    balance sheet total assets = total liabilities + equity (or total liabilities and equity)
    income        gross profit = revenue - cost of revenue;  operating income = gross profit - operating expenses
    cash flow     cash at end = cash at beginning + net change

Rounding is allowed for: the tolerance is half a unit of the last printed digit per row summed, so a one-digit error in a figure is caught
while a statement that merely rounds is not. A finding says which rule, where, what was expected and found, and which cells to look at.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from .numbers import Parsed, parse

_TOTAL = re.compile(r"^\s*(total|subtotal|sub-total)\b", re.I)
_TOTAL_ANY = re.compile(r"\btotal\b", re.I)

STATEMENT_WORDS = {
    "balance_sheet": ("total assets", "total liabilities", "stockholders", "shareholders", "retained earnings", "current assets", "accounts payable", "total equity"),
    "income_statement": ("revenue", "net sales", "cost of", "gross profit", "gross margin", "operating income", "income before", "net income", "earnings per share", "operating expenses"),
    "cash_flow": ("operating activities", "investing activities", "financing activities", "cash and cash equivalents", "depreciation", "net cash", "net increase", "net decrease"),
}


def statement_type(rows: List[List[Any]]) -> Optional[str]:
    """balance_sheet | income_statement | cash_flow | None, by the words in the row labels (best score of at least 3 hits)."""
    labels = " | ".join(str(r[0]).lower() for r in rows if r)
    scores = {k: sum(1 for w in ws if w in labels) for k, ws in STATEMENT_WORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 3 else None


def _table(rows: List[List[Any]]) -> List[Tuple[str, List[Parsed]]]:
    out = []
    for r in rows:
        if not r:
            continue
        out.append((str(r[0] or "").strip(), [parse(c) for c in r[1:]]))
    return out


def _tol(k: int, decimals: int) -> float:
    return (10 ** -decimals) * (0.5 * k + 0.5)


def _num(p: Parsed) -> bool:
    return p.kind == "amount" and p.value is not None


def _find(table, pattern: str, from_idx: int = 0) -> Optional[int]:
    rx = re.compile(pattern, re.I)
    return next((i for i in range(from_idx, len(table)) if rx.search(table[i][0])), None)


def _finding(rule: str, row: int, col: int, label: str, expected: float, found: float, detail: str, cells: List[Tuple[int, int]]) -> Dict[str, Any]:
    return {"rule": rule, "severity": "warn", "row": row, "col": col, "label": label, "expected": round(expected, 4), "found": round(found, 4),
            "detail": detail, "cells": sorted(set(cells))}


def check_totals(table) -> List[Dict[str, Any]]:
    """Every "Total ..." row against the rows it should sum (suffix windows of the running list, so subtotals nest)."""
    out: List[Dict[str, Any]] = []
    width = max((len(v) for _, v in table), default=0)
    running: List[int] = []                       # indexes of rows that can be summed into the next total
    for i, (label, vals) in enumerate(table):
        numeric_cols = [j for j in range(width) if j < len(vals) and _num(vals[j])]
        if not numeric_cols:
            running = []                          # a heading or blank row starts a new block
            continue
        if _TOTAL.match(label) and len(running) >= 2:
            best_k, best_hits = None, -1
            for k in range(2, len(running) + 1):
                win = running[-k:]
                hits = 0
                for j in numeric_cols:
                    parts = [table[r][1][j] for r in win if j < len(table[r][1]) and _num(table[r][1][j])]
                    if len(parts) < 2:
                        continue
                    s = sum(p.value for p in parts)
                    if abs(s - vals[j].value) <= _tol(len(parts), max(vals[j].decimals, *(p.decimals for p in parts))):
                        hits += 1
                if hits > best_hits:
                    best_k, best_hits = k, hits
                if hits == len(numeric_cols):
                    break
            win = running[-best_k:] if best_k else running[-min(6, len(running)):]
            if best_hits <= 0:
                # No window explains ANY column. Say something only when a window is close (a misread digit); otherwise this "total" is not a
                # sum of the lines above (e.g. its components are not printed), and a finding would be noise.
                close = False
                for k in range(2, len(running) + 1):
                    for j in numeric_cols:
                        parts = [table[r][1][j] for r in running[-k:] if j < len(table[r][1]) and _num(table[r][1][j])]
                        if len(parts) >= 2 and vals[j].value and abs(sum(p.value for p in parts) - vals[j].value) <= 0.05 * abs(vals[j].value):
                            close, win = True, running[-k:]
                if not close:
                    running.append(i)
                    continue
            for j in numeric_cols:
                parts = [(r, table[r][1][j]) for r in win if j < len(table[r][1]) and _num(table[r][1][j])]
                if len(parts) < 2:
                    continue
                s = sum(p.value for _, p in parts)
                if abs(s - vals[j].value) > _tol(len(parts), max(vals[j].decimals, *(p.decimals for _, p in parts))):
                    out.append(_finding("total_does_not_add_up", i, j, label, s, vals[j].value,
                                        f'"{label}" is {vals[j].raw.strip()} but the lines above add up to {s:,.{vals[j].decimals}f}. A digit may have been misread.',
                                        [(i, j)] + [(r, j) for r, _ in parts]))
            if best_k:
                running = running[:-best_k]       # the total replaces the rows it summed, so a higher total sums subtotals
        running.append(i)
    return out


def check_identities(table, kind: Optional[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    width = max((len(v) for _, v in table), default=0)

    def val(i: Optional[int], j: int) -> Optional[Parsed]:
        return table[i][1][j] if i is not None and j < len(table[i][1]) and _num(table[i][1][j]) else None

    def rel(rule, label_row, lhs, rhs_terms, j, text):
        a, b = val(label_row, j), [val(t, j) for t in rhs_terms]
        if a is None or any(x is None for x in b):
            return
        s = sum(x.value for x in b)
        dec = max(a.decimals, *(x.decimals for x in b))
        if abs(s - a.value) > _tol(len(b), dec):
            out.append(_finding(rule, label_row, j, table[label_row][0], s, a.value, text.format(found=a.raw.strip(), expected=f"{s:,.{dec}f}"),
                                [(label_row, j)] + [(t, j) for t in rhs_terms]))

    if kind in (None, "balance_sheet"):
        ta = _find(table, r"^\s*total assets\b")
        tle = _find(table, r"^\s*total liabilities\s*(and|&)\s*(stockholders|shareholders|equity|members|partners)")
        tl = _find(table, r"^\s*total liabilities\s*$|^\s*total liabilities\b(?!\s*(and|&))")
        te = _find(table, r"^\s*total (stockholders|shareholders|members|partners)?.{0,12}equity\b|^\s*total (stockholders|shareholders)")
        for j in range(width):
            if ta is not None and tle is not None:
                rel("balance_sheet_does_not_balance", ta, None, [tle], j, "Total assets is {found} but total liabilities and equity is {expected}: a figure on the balance sheet may be misread.")
            elif ta is not None and tl is not None and te is not None:
                rel("balance_sheet_does_not_balance", ta, None, [tl, te], j, "Total assets is {found} but liabilities plus equity add up to {expected}: a figure on the balance sheet may be misread.")
    if kind in (None, "income_statement"):
        rev = _find(table, r"^\s*(total\s+)?(net\s+)?(revenues?|net sales|sales)\b")
        cogs = _find(table, r"^\s*(total\s+)?cost of (revenues?|sales|goods sold)")
        gp = _find(table, r"^\s*gross (profit|margin)\b")
        opx = _find(table, r"^\s*total (operating )?expenses\b|^\s*total operating costs")
        oi = _find(table, r"^\s*(operating (income|profit)|income from operations)\b")
        for j in range(width):
            if None not in (rev, cogs, gp) and val(rev, j) and val(cogs, j) and val(gp, j):
                a, c, g = val(rev, j), val(cogs, j), val(gp, j)
                cv = abs(c.value)                  # cost is printed positive or in parentheses depending on the filer
                if abs((a.value - cv) - g.value) > _tol(2, max(a.decimals, c.decimals, g.decimals)):
                    out.append(_finding("gross_profit_does_not_match", gp, j, table[gp][0], a.value - cv, g.value,
                                        f"Gross profit is {g.raw.strip()} but revenue less cost of revenue is {a.value - cv:,.{g.decimals}f}.", [(rev, j), (cogs, j), (gp, j)]))
            if None not in (gp, opx, oi) and val(gp, j) and val(opx, j) and val(oi, j):
                g, x, o = val(gp, j), val(opx, j), val(oi, j)
                if abs((g.value - abs(x.value)) - o.value) > _tol(2, max(g.decimals, x.decimals, o.decimals)):
                    out.append(_finding("operating_income_does_not_match", oi, j, table[oi][0], g.value - abs(x.value), o.value,
                                        f"Operating income is {o.raw.strip()} but gross profit less operating expenses is {g.value - abs(x.value):,.{o.decimals}f}.", [(gp, j), (opx, j), (oi, j)]))
    if kind in (None, "cash_flow"):
        beg = _find(table, r"cash.{0,40}(at )?(the )?beginning|beginning of (the )?(period|year)")
        end = _find(table, r"cash.{0,40}(at )?(the )?end\b|end of (the )?(period|year)")
        net = _find(table, r"net (increase|decrease|change).{0,40}cash")
        for j in range(width):
            if None not in (beg, end, net) and val(beg, j) and val(end, j) and val(net, j):
                b, e, n = val(beg, j), val(end, j), val(net, j)
                if abs((b.value + n.value) - e.value) > _tol(2, max(b.decimals, e.decimals, n.decimals)):
                    out.append(_finding("cash_does_not_reconcile", end, j, table[end][0], b.value + n.value, e.value,
                                        f"Cash at the end is {e.raw.strip()} but beginning cash plus the net change is {b.value + n.value:,.{e.decimals}f}.", [(beg, j), (net, j), (end, j)]))
    return out


def check_table(rows: List[List[Any]]) -> Dict[str, Any]:
    """{"statement": type|None, "findings": [...]} for one table given as rows of cells (first cell of each row is the label)."""
    t = _table(rows)
    kind = statement_type(rows)
    # "checked" says whether there was anything to check: a table with no totals and no recognisable statement is simply not covered
    checked = kind is not None or any(_TOTAL.match(label) for label, _ in t)
    return {"statement": kind, "checked": checked, "findings": check_totals(t) + check_identities(t, kind)}
