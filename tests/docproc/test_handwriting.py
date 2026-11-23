"""Handwriting and poor-scan handling: strict number comparison, value-based pairing, de-duplication,
the one-call third look at disputed values, and the handwriting flag. All offline; data is generic."""
import asyncio

from app.docproc.services.extraction import adjudicate, discover
from app.docproc.services.reading import ocr
from app.docproc.services.extraction import normalize as N


def test_marks_that_change_a_number_are_not_ignored():
    assert N.normalize(".2% solution", "text") != N.normalize("2% solution", "text")     # leading decimal point
    assert N.normalize("7-20 min", "text") != N.normalize("720 min", "text")             # range
    assert N.normalize("(1,234)", "text") != N.normalize("1,234", "text")                # a loss is not a profit
    assert N.normalize("-5", "text") != N.normalize("5", "text")
    assert N.normalize("$ 1,200", "text") == N.normalize("1,200", "text")                # a currency sign is formatting
    assert N.normalize("2,500,000*", "text") == N.normalize("2500000", "text")           # thousands comma, star: noise
    assert N.normalize("Closed 1992.", "text") == N.normalize("closed 1992", "text")     # sentence period: noise


def test_ocr_line_compare_catches_a_dropped_decimal_point():
    assert ocr._compare(".2% mix", "2% mix") == "numbers differ"
    assert ocr._compare("7-20% mix", "7-20% mix") is None


def test_handwriting_flag_comes_from_the_same_reply():
    lines = ocr.parse_lines('{"handwriting": true, "lines": ["a", "b"]}')
    assert list(lines) == ["a", "b"] and lines.handwriting is True
    assert ocr.parse_lines('["a"]').handwriting is False and ocr.parse_lines("plain\ntext").handwriting is False

    async def reader(model, jpeg):
        out = ocr.Lines(["x 1"])
        out.handwriting = model == "a"
        return out
    page = asyncio.run(ocr.read_page(b"", ["a", "b"], reader))
    assert page.handwritten is True


def test_same_value_under_different_labels_is_one_field_not_two_disagreements():
    a = discover.parse({"fields": [{"label": "Approved by", "value": "Jordan Rivera"}, {"label": "Total", "value": "42.5 kg"}]})["fields"]
    b = discover.parse({"fields": [{"label": "Name", "value": "Jordan Rivera"}, {"label": "Weight", "value": "42.5 kg"}]})["fields"]
    got = discover.merge_fields(a, b)
    assert set(got) == {"approved_by", "total"}
    assert all(c["status"] == "agreed" for c in got.values())


def test_dedupe_drops_repeats_but_keeps_distinct_values():
    def cell(v, label, status="agreed", bbox=None):
        return {"value": v, "label": label, "status": status, "type": "text", "bbox": bbox, "alternatives": []}
    fields = {"corporation": cell("Acme Holdings Ltd", "Corporation", "review"),
              "corporation_name": cell("Acme Holdings Ltd", "Corporation Name"),      # label inside the other label
              "name": cell("Sam Lee", "Name", bbox=[1, 2, 3, 4]), "signer": cell("Sam Lee", "Signer", bbox=[1, 2, 3, 4]),  # same place
              "a": cell("Open", "Door"), "b": cell("Open", "Window"),                  # same short word, different fields: kept
              "note": cell("increase the load slowly over ten minutes", "Note")}
    tables = {"steps": [{"c": cell("Increase the load slowly over ten minutes then hold", "Step")}]}
    dropped = discover.dedupe(fields, tables)
    assert set(fields) == {"corporation_name", "name", "a", "b"} and dropped == 3


def _cell(value, alts=(), status="review", reason="readers disagree"):
    return {"value": value, "alternatives": list(alts), "status": status, "reason": reason, "type": "text", "normalized": None}


def test_third_look_settles_only_disputed_cells_with_one_call():
    calls = []

    async def third(model, jpeg, prompt):
        calls.append(prompt)
        return {"1": "Smith", "2": "Tolan", "3": "[?]"}
    cells = [("a", _cell("Smyth", ["Smith"]), "text"), ("b", _cell("Tom", ["Tim"]), "text"), ("c", _cell("7?5", ["725"]), "text"),
             ("d", _cell("fine", [], "agreed", None), "text")]
    changed = asyncio.run(adjudicate.settle(b"img", cells, third))
    assert len(calls) == 1 and "fine" not in calls[0]                       # agreed values are never sent
    a, b, c, d = (x[1] for x in cells)
    assert (a["value"], a["status"], a["alternatives"]) == ("Smith", "agreed", ["Smyth"])   # matches a candidate: two of three
    assert (b["value"], b["status"]) == ("Tolan", "review") and set(b["alternatives"]) == {"Tom", "Tim"}   # a new reading stays doubtful
    assert c["status"] == "review" and c["value"] == "7?5"                  # the third reader was unsure too: unchanged
    assert d["status"] == "agreed" and changed == 2


def test_no_dispute_means_no_call():
    async def third(*a):
        raise AssertionError("must not be called")
    assert asyncio.run(adjudicate.settle(b"img", [("a", _cell("x", [], "agreed", None), "text")], third)) == 0


def test_a_failed_third_look_leaves_disputes_for_a_person():
    async def broken(*a):
        raise RuntimeError("down")
    cell = _cell("Tom", ["Tim"])
    assert asyncio.run(adjudicate.settle(b"img", [("a", cell, "text")], broken)) == 0 and cell["status"] == "review"


def test_a_marker_never_counts_as_a_match():
    cell = _cell("2[?]5", ["265"])
    assert adjudicate.apply(cell, "255") is True and cell["value"] == "255" and cell["status"] == "review"


def test_empty_title_and_sentence_fields_are_pruned_but_real_blanks_and_filled_values_stay():
    def cell(v, label, status):
        return {"value": v, "label": label, "status": status, "type": "text", "alternatives": []}
    fields = {"title": cell(None, "Weekly Site Report", "empty"),
              "sentence": cell(None, "Increase the load slowly and hold for one minute", "empty"),
              "blank": cell(None, "Signature", "empty"),
              "long_but_filled": cell("ok", "Increase the load slowly and hold for one minute", "agreed")}
    assert discover.prune_empty_noise(fields, "Weekly Site Report") == 2
    assert set(fields) == {"blank", "long_but_filled"}
