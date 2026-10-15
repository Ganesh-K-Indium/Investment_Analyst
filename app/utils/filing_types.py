"""
Single source of truth for the SEC filing types the platform understands.

Domestic issuers file 10-K (annual) / 10-Q (quarterly) / 8-K (events). Foreign
private issuers file the equivalents 20-F (annual) / 6-K (interim results and
events — there is no 10-Q equivalent). Keeping the tuples here (dependency-free,
safe to import from ingestion, API and RAG layers alike) stops the lists drifting
apart across modules.
"""

from typing import List, Optional, Sequence, Union

VALID_FILING_TYPES = ("10-K", "10-Q", "8-K", "20-F", "6-K")

# Annual reports: audited, multi-year comparative statements. 20-F is the foreign
# private issuer counterpart of the 10-K.
ANNUAL_FILING_TYPES = ("10-K", "20-F")
# Current/interim reports: single event or interim-results disclosures. 6-K is the
# foreign private issuer counterpart of the 8-K (and also carries quarterly /
# half-year results, since foreign issuers don't file 10-Qs).
EVENT_FILING_TYPES = ("8-K", "6-K")

# Canonical display/ordering used wherever a list of filing types is built.
FILING_TYPE_ORDER = ("10-K", "20-F", "10-Q", "8-K", "6-K")

# Forms with a fiscal-year-level comparative window (3 years of income statement).
ANNUAL_WINDOW_TYPES = ANNUAL_FILING_TYPES

# Sanity floor on rendered-PDF page count per form (anything lower almost
# certainly means a truncated render). 20-Fs are full annual reports; 6-Ks and
# 8-Ks are genuinely often 1-3 pages.
MIN_PLAUSIBLE_PAGES = {"10-K": 15, "10-Q": 10, "8-K": 1, "20-F": 15, "6-K": 1}


def order_filing_types(types: Sequence[str]) -> List[str]:
    """Return the distinct valid types in canonical order."""
    wanted = set(types or [])
    return [ft for ft in FILING_TYPE_ORDER if ft in wanted]


def group_filing_types(types: Sequence[str]) -> List[Union[str, List[str]]]:
    """
    Collapse a flat list of filing types into one retrieval group per document
    family so that equivalent forms share a single vector search (a ticker's
    collection holds either a 10-K or a 20-F, never both, so searching them
    together costs nothing extra):

        ["10-K", "20-F"]          -> [["10-K", "20-F"]]
        ["10-K", "10-Q"]          -> ["10-K", "10-Q"]
        ["10-K", "20-F", "8-K"]   -> [["10-K", "20-F"], "8-K"]

    A group with one member is returned as a plain string, so callers that only
    ever handle the legacy three types see exactly the values they did before.
    """
    ordered = order_filing_types(types)
    groups: List[Union[str, List[str]]] = []
    for family in (ANNUAL_FILING_TYPES, ("10-Q",), EVENT_FILING_TYPES):
        members = [ft for ft in ordered if ft in family]
        if len(members) == 1:
            groups.append(members[0])
        elif members:
            groups.append(members)
    return groups


def is_annual_only(types: Optional[Sequence[str]]) -> bool:
    """True when every type is an annual-report form (vacuously true for empty)."""
    return all(ft in ANNUAL_FILING_TYPES for ft in (types or []))


def web_search_form_label(types: Optional[Sequence[str]]) -> str:
    """Form name(s) to embed in a web-search query string for the resolved types."""
    ordered = order_filing_types(types or [])
    if not ordered:
        return "10-K"
    if all(ft in ANNUAL_FILING_TYPES for ft in ordered) and len(ordered) > 1:
        return " OR ".join(ordered)
    return ordered[0]
