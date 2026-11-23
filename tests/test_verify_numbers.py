from rag.verify import numbers as n

CTX = ["Net revenue (in thousands): 2024 4,215,300  2023 3,760,100", "Operating margin was 18.5% compared with 17.1% in the prior year.",
       "Total stockholders' equity was $1.2 billion at year end."]


def test_scale_aware_support():
    r = n.verify("Revenue was $4.2153 billion in 2024.", CTX)
    assert r["status"] == "verified", r
    assert n.verify("Operating margin rose to 18.5%.", CTX)["status"] == "verified"
    assert n.verify("Equity was $1.2 billion.", CTX)["status"] == "verified"


def test_scale_mismatch_is_flagged():
    r = n.verify("Revenue was 4,215,300 million in 2024.", CTX)
    assert r["status"] == "issues" and r["issues"][0]["kind"] == "scale_mismatch"


def test_unsupported_figure_flagged_not_called_wrong():
    r = n.verify("Net income was $912 million.", CTX)
    assert r["issues"][0]["kind"] == "unsupported" and "calculation" in r["issues"][0]["detail"]
    assert n.verify("Gross margin was 44.4%.", CTX)["status"] == "issues"


def test_years_pages_and_small_counts_ignored():
    assert n.verify("In fiscal 2024 (see page 31, Item 7) there were 3 segments.", CTX)["status"] == "nothing_to_check"


def test_note_modes():
    r = n.verify("Net income was $912 million.", CTX)
    assert n.note(r, "off") is None and n.note(r, "warn").startswith("_Figures") and n.note(r, "strict").startswith("**Not verified")
    assert n.note(n.verify("Operating margin rose to 18.5%.", CTX), "warn") is None


def test_chunk_unit_detection():
    assert n.chunk_unit("(in thousands, except per share)") == 1e3 and n.chunk_unit("amounts in millions") == 1e6 and n.chunk_unit("nothing") == 1.0
