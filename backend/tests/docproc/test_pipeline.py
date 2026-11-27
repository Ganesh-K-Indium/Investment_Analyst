import asyncio

import pytest

from app.docproc.services.extraction import checkboxes, discover, form_extractor, kv, normalize as N
from app.docproc.services.documents import classify, template_draft
from app.docproc.services.quality import cross, validation
from app.docproc.services.reading import locate, ocr, pdf_pages, router
from app.docproc import pipeline
from app.docproc.services.reading.tess import available as tess_available
from tests.docproc.make_packet import build


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    p = tmp_path_factory.mktemp("pkt") / "packet.pdf"
    build(str(p))
    return str(p)


# ---- units ------------------------------------------------------------------------------------------------------

def test_fax_stamp_split():
    lines = ["From Sample Health 1.201.555.0121 Wed Nov 11 09:21:38 2026 MST Page 3 of 9", "Last Name: Morgan",
             "PAGE 3/9 * RCVD AT 10/12/2026 11:21:43 AM [Eastern Standard Time] * SVR:FAX-MGA/0 * DNIS:6403 * CSID:201-447-5750 * ANI: 1 * DURATION (mm-ss):07-04"]
    content, meta = pdf_pages.strip_fax_stamp(lines)
    assert content == ["Last Name: Morgan"]
    assert meta["fax_page"] == 3 and meta["fax_pages"] == 9 and meta["dnis"] == "6403" and meta["duration"] == "07-04"


def test_normalize():
    assert N.normalize("07/01/1970", "date") == "1970-07-01"
    assert N.normalize("99-9999999", "tax_id") == "999999999"
    assert N.normalize(" m54.2 ", "icd10") == "M542"


def test_merge_field_statuses():
    assert form_extractor.merge_field("Morgan", "MORGAN", "text", True)["status"] == "agreed"
    r = form_extractor.merge_field("1999999999", "1999999990", "npi", True)
    assert r["status"] == "review" and r["alternatives"] == ["1999999990"]
    assert form_extractor.merge_field(None, None, "text", True)["status"] == "empty"
    assert form_extractor.merge_field("x", None, "text", False)["status"] == "single"
    assert form_extractor.merge_field("9[?]", "93", "text", True)["status"] == "review"


def test_glyph_state():
    assert checkboxes.glyph_state("☑ ROUTINE*  ☐ URGENT*", "ROUTINE") is True
    assert checkboxes.glyph_state("☑ ROUTINE*  ☐ URGENT*", "URGENT") is False
    assert checkboxes.glyph_state("nothing here", "URGENT") is None


def test_kv_parser():
    pairs = kv.parse_kv(["Patient Name: Morgan Taylor Date of Initial Examination: 08/12/2026", "Plan: Begin Plan as Outlined"])
    c = kv.canonical(pairs)
    assert c["patient.name"] == "Morgan Taylor" and c["exam_date"] == "08/12/2026"
    assert kv.cpt_codes("GP:97110 x GP:97140 GP:97110") == ["97110", "97140"]


def test_classification_and_segmentation():
    assert classify.classify_text("Provider Prior Authorization Request Form\nMEMBER INFORMATION\nLast Name: Morgan First Name: Taylor")["type"] == "prior_auth_form"
    assert classify.classify_text("")["type"] == "blank_or_logo"
    pages = [{"page": i + 1, "classification": {"type": t, "label": t}} for i, t in
             enumerate(["fax_cover", "blank_or_logo", "prior_auth_form", "pt_initial_exam", "pt_initial_exam", "daily_note"])]
    docs = classify.segment(pages)
    assert [(d["type"], d["pages"]) for d in docs] == [("fax_cover", [1, 2]), ("prior_auth_form", [3]), ("pt_initial_exam", [4, 5]), ("daily_note", [6])]


# ---- PDF-backed -------------------------------------------------------------------------------------------------

def test_pixel_checkboxes(pdf):
    out = checkboxes.pixel_states(pdf, 3, {"ROUTINE": (0, 200), "URGENT": (0, 200), "TRANSPLANT": (0, 200)})
    assert out == {"ROUTINE": True, "URGENT": False, "TRANSPLANT": False}


def test_scan_detection(pdf):
    assert pdf_pages.analyze_page(pdf, 4)["scanned"] is True
    assert pdf_pages.analyze_page(pdf, 3)["scanned"] is False


async def fake_ocr(model, jpeg):
    return ["Last Name*: Scanned", "[?] smudge"] if not model.endswith("mini") else ["Last Name*: Scanned", "7 smudge"]


async def fake_form(model, jpeg, prompt):
    """Answers per template, like a model would: the prompt names the document."""
    mini = model.endswith("mini")
    if "Prior Authorization" in prompt:
        f = {"member.last_name": "Morgan", "member.first_name": "Taylor", "member.dob": "07/01/1970", "member.member_id": "9000001234",
             "ordering_provider.tax_id": "99-9999999", "ordering_provider.npi": "1999999999",
             "facility.name": "SAMPLE HEALTH ASSOCIATES", "facility.tax_id": "99-9999998",
             "facility.npi": "199999999" if not mini else "1999999990", "clinical.dx_code_1": "M54.2"}
        return {"fields": f, "table_rows": [
            {"qty": "12 VISITS", "code": "97110", "description": "THERAPEUTIC EXERCISE", "charge": "180.00"},
            {"qty": "12 VISITS", "code": "97140", "description": "MANUAL THERAPY", "charge": "150.00"}]}
    if "initial examination" in prompt:
        return {"fields": {"patient.name": "Morgan Taylor", "patient.dob": "01/15/1975", "diagnosis": "ICD10: M54.2: Cervicalgia"}, "table_rows": []}
    if "Daily note" in prompt:
        return {"fields": {"patient.name": "Morgan Taylor", "patient.dob": "01/15/1975"},
                "table_rows": [{"code": "GP:97110", "description": "Therapeutic Exercise", "units": "1"},
                               {"code": "GP:97162", "description": "PT Evaluation", "units": "1"}]}
    return {"fields": {}, "table_rows": []}


async def fake_ask(model, prompt):
    return '{"type": "unknown"}'


@pytest.fixture(scope="module")
def result(pdf):
    return asyncio.run(pipeline.process(pdf, ocr_reader=fake_ocr, form_reader=fake_form, model_ask=fake_ask))


def test_pages_json(result):
    pages = {p["page"]: p for p in result["pages"]}
    assert len(pages) == 6
    assert pages[1]["page_meta"]["fax_pages"] == 6 and "RCVD" not in pages[1]["text"]
    assert pages[2]["classification"]["type"] == "blank_or_logo"
    assert pages[3]["source"] == "text-layer" and pages[3]["classification"]["type"] == "prior_auth_form"
    assert pages[4]["source"] == "ocr"
    review = [l for l in pages[4]["lines"] if l["status"] == "review"]
    assert review and review[0]["alternatives"]
    # the untitled scanned page 4 continues the form before it
    assert [(d["type"], d["pages"]) for d in result["documents"]] == [
        ("fax_cover", [1, 2]), ("prior_auth_form", [3, 4]), ("pt_initial_exam", [5]), ("daily_note", [6])]


def test_form_fields(result):
    form = result["forms"][0]
    f = form["fields"]
    assert f["review_type"]["value"] == "ROUTINE" and f["review_type"]["method"] == "pixel"
    assert f["member.last_name"]["status"] == "agreed" and f["member.last_name"]["bbox"] is not None
    assert f["facility.npi"]["status"] == "review"
    # same label, different sections -> different boxes
    assert f["ordering_provider.tax_id"]["bbox"] != f["facility.tax_id"]["bbox"]
    assert f["ordering_provider.tax_id"]["bbox"][1] < f["facility.tax_id"]["bbox"][1]
    assert [r["code"]["value"] for r in form["tables"]["cpt"]] == ["97110", "97140"]


def test_validation(result):
    rules = {(v["rule"], v.get("field")) for v in result["validation"]}
    assert ("cross_mismatch", "member.dob") in rules                       # form 1970 vs exam/note 1975
    assert ("required_missing", "ordering_provider.first_name") in rules
    assert ("cross_set_diff", None) in rules                               # 97140 requested, not in the daily note
    assert ("format", "facility.npi") in rules                             # 9-digit NPI
    assert any(v["rule"] == "cross_not_mentioned" for v in result["validation"])  # M54.2 absent from the daily note
    assert result["stats"]["errors"] >= 2


def test_clinical_documents_use_templates(result):
    by = {f["doc_type"]: f for f in result["forms"]}
    assert by["pt_initial_exam"]["fields"]["patient.dob"]["value"] == "01/15/1975"
    assert [r["code"]["value"] for r in by["daily_note"]["tables"]["billing"]] == ["GP:97110", "GP:97162"]


def test_pipeline_without_models(pdf):
    r = asyncio.run(pipeline.process(pdf))  # LLM disabled: checkboxes from pixels, text fields from the page's own text by their labels
    f = r["forms"][0]["fields"]
    assert f["review_type"]["value"] == "ROUTINE"
    assert f["member.last_name"]["value"] == "Morgan" and f["member.last_name"]["status"] == "single"   # one reader: not verified
    assert f["clinical.dx_code_1"]["value"] == "M54.2"
    # the ordering provider's own names are not on the page: the member's must not be borrowed
    assert f["ordering_provider.first_name"]["status"] == "empty" and f["ordering_provider.last_name"]["status"] == "empty"


# ---- router / decision layer ------------------------------------------------------------------------------------

def test_reader_decisions(pdf):
    digital = router.decide_reader(router.profile_page(pdf_pages.analyze_page(pdf, 3)), llm_available=True)
    scan = router.decide_reader(router.profile_page(pdf_pages.analyze_page(pdf, 4)), llm_available=True)
    scan_no_llm = router.decide_reader(router.profile_page(pdf_pages.analyze_page(pdf, 4)), llm_available=False)
    assert digital["reader"] == "text_layer" and scan["reader"] == "ocr_dual" and scan_no_llm["reader"] == "text_layer_unverified"
    assert scan["reasons"]


def test_extractor_decisions():
    assert router.decide_extractor("prior_auth_form")["extractor"] == "template_form"
    assert router.decide_extractor("daily_note")["extractor"] == "template_form"
    assert router.decide_extractor("fax_cover")["extractor"] == "skip"
    assert router.decide_extractor("blank_or_logo")["extractor"] == "skip"
    assert router.decide_extractor("something_new")["extractor"] == "discover"          # unregistered: fields are discovered
    assert router.decide_extractor("something_new", llm_available=False)["extractor"] == "kv"  # no model: plain label/value reading


def test_routes_are_recorded(result):
    assert result["pages"][3]["route"]["reader"] == "ocr_dual"
    assert all("route" in d for d in result["documents"])
    assert {d["type"]: d["route"]["extractor"] for d in result["documents"]}["prior_auth_form"] == "template_form"


def test_model_classifier_only_picks_registered_types():
    async def good(model, prompt):
        return '{"type": "daily_note"}'

    async def invented(model, prompt):
        return '{"type": "radiology_report"}'

    async def boom(model, prompt):
        raise RuntimeError("down")

    assert asyncio.run(classify.classify_with_model("Daily note: patient seen today", ["m"], good))["type"] == "daily_note"
    assert asyncio.run(classify.classify_with_model("x", ["m"], invented))["type"] == "unknown"
    assert asyncio.run(classify.classify_with_model("x", ["m"], boom))["type"] == "unknown"


def test_model_checkbox_fallback():
    agreed = form_extractor.model_checkbox("enum", "ROUTINE", "ROUTINE", True)
    assert agreed["value"] == "ROUTINE" and agreed["status"] == "agreed" and agreed["method"] == "model"
    split = form_extractor.model_checkbox("enum", "ROUTINE", "URGENT", True)
    assert split["status"] == "review" and split["alternatives"]
    lone = form_extractor.model_checkbox("checkbox", True, None, False)
    assert lone["value"] is True and lone["status"] == "review"
    multi = form_extractor.model_checkbox("multi_enum", ["Office"], ["Office"], True)
    assert multi["value"] == ["Office"] and multi["status"] == "agreed"
    assert form_extractor.model_checkbox("enum", None, None, True) is None


# ---- Tesseract layer / highlighting on a real scan -----------------------------------------------------------------

def _scan_pdf(path):
    """A page that is only a picture of text, as a fax is."""
    import pymupdf as fitz
    src = fitz.open()
    p = src.new_page(width=612, height=792)
    p.insert_text((60, 120), "Member ID 9000001234", fontsize=16)
    p.insert_text((60, 160), "Facility Name SAMPLE HEALTH ASSOCIATES", fontsize=16)
    png = p.get_pixmap(dpi=200).tobytes("png")
    out = fitz.open()
    q = out.new_page(width=612, height=792)
    q.insert_image(q.rect, stream=png)
    out.save(path)


@pytest.mark.skipif(not tess_available(), reason="Tesseract not installed")
def test_tesseract_layer_makes_scans_highlightable(tmp_path):
    from app.docproc.services.reading import locate
    src, work = str(tmp_path / "scan.pdf"), str(tmp_path / "scan.work.pdf")
    _scan_pdf(src)
    assert pdf_pages.analyze_page(src, 1)["words"] == []              # a picture: nothing to highlight
    assert pdf_pages.build_work_pdf(src, work, {1}) == {1: "ok"}
    box = locate.find(work, 1, "9000001234", "Member ID")
    assert box and 50 < box[0] < 400 and 100 < box[1] < 130           # found where it was drawn
    assert locate.find(work, 1, "9000001235", "Member ID")            # one wrong digit still lands on the right place
    assert locate.find(work, 1, "no such value anywhere", None) is None


def test_prefers_reading_that_matches_page_text():
    cell = {"value": "900001234", "status": "review", "alternatives": ["9000001234"]}
    form_extractor._prefer_page_text(cell, "text", locate.alnum("Member ID 9000001234 Last Name"))
    assert cell["value"] == "9000001234" and cell["alternatives"] == ["900001234"] and cell["status"] == "review"
    both = {"value": "aaaa1", "status": "review", "alternatives": ["bbbb2"]}
    form_extractor._prefer_page_text(both, "text", locate.alnum("nothing relevant"))
    assert both["value"] == "aaaa1"


# ---- cross-document checks are template-driven ------------------------------------------------------------------------

def test_cross_checks_use_only_template_declarations():
    tpl = {"canonical": {"id.owner": {"kind": "name", "label": "Owner", "fields": ["owner"]},
                         "id.date": {"kind": "date", "label": "Issue date", "fields": ["issued"]}}}
    def doc(i, owner, issued):
        return {"doc_index": i, "doc_type": "invoice" if i == 0 else "receipt", "pages": [i + 1], "template": tpl, "text": "",
                "fields": {"owner": {"value": owner}, "issued": {"value": issued}}, "tables": {}}
    ok = cross.check([doc(0, "Acme Corp", "01/02/2026"), doc(1, "ACME corp", "2026-01-02")])
    assert ok == []
    bad = cross.check([doc(0, "Acme Corp", "01/02/2026"), doc(1, "Other Ltd", "01/03/2026")])
    assert {v["detail"].split(" is ")[0] for v in bad} == {"Owner", "Issue date"} and all(v["severity"] == "error" for v in bad)


def test_third_opinion_flags_shared_misread():
    cell = {"value": "NEUROMUSCLAR", "status": "agreed", "alternatives": []}
    form_extractor._third_opinion(cell, ([0, 0, 1, 1], "NEUROMUSCULAR", 0.96))
    assert cell["status"] == "review" and cell["alternatives"] == ["NEUROMUSCULAR"]
    exact = {"value": "ROUTINE VISIT", "status": "agreed", "alternatives": []}
    form_extractor._third_opinion(exact, ([0, 0, 1, 1], "ROUTINE VISIT", 1.0))
    assert exact["status"] == "agreed"
    absent = {"value": "SOMETHING LONG", "status": "agreed", "alternatives": []}
    form_extractor._third_opinion(absent, None)
    assert absent["status"] == "agreed"          # Tesseract not finding it is not evidence of an error


def test_line_merge_ignores_layout_but_keeps_real_differences():
    a = ["Patient Name: Morgan Taylor", "Date of Initial Examination: 08/12/2026", "Testville NJ 00001-1718"]
    b = ["Patient Name: Morgan Taylor Date of Initial Examination: 08/12/2026", "Testville NJ 07001-1718"]
    lines = ocr.merge_readings(a, b)
    by = {l.text: l for l in lines}
    assert by["Patient Name: Morgan Taylor"].status == "agreed"                 # same content, different line breaks
    assert by["Date of Initial Examination: 08/12/2026"].status == "agreed"
    assert by["Testville NJ 00001-1718"].status == "review"                     # a real digit difference survives
    assert by["Testville NJ 00001-1718"].alternatives == ["Testville NJ 07001-1718"]
    assert all(l.text != "Patient Name: Morgan Taylor Date of Initial Examination: 08/12/2026" for l in lines)


# ---- template-free discovery ---------------------------------------------------------------------------------------------

def test_discover_parse_is_defensive():
    assert discover.parse("not json")["fields"] == []
    d = discover.parse('{"title": "Invoice", "fields": [{"label": "Invoice No.", "value": " 42 "}, {"label": ""}, "junk"], '
                       '"tables": [{"columns": ["Item", "Qty"], "rows": [["Bolt", "2"], ["Nut", null]]}, {"columns": []}]}')
    assert d["title"] == "Invoice" and d["fields"][0]["value"] == "42" and len(d["fields"]) == 1
    assert len(d["tables"]) == 1 and d["tables"][0]["rows"][1] == ["Nut", None]


def test_discover_merges_two_readers_by_label():
    a = discover.parse({"fields": [{"label": "Invoice No.", "value": "1001", "section": None}, {"label": "Total", "value": "$215.00"},
                                   {"label": "Notes", "value": "see attached"}]})["fields"]
    b = discover.parse({"fields": [{"label": "Invoice No", "value": "1001"}, {"label": "Total", "value": "$215.80"},
                                   {"label": "PO Number", "value": "77"}]})["fields"]
    got = discover.merge_fields(a, b)
    assert got["invoice_number"]["status"] == "agreed"                              # "Invoice No." and "Invoice No" are one key
    assert got["total"]["status"] == "review" and got["total"]["alternatives"] == ["$215.80"]
    assert got["notes"]["status"] == "review" and "one reader" in got["notes"]["reason"]   # only the first reader listed it
    assert got["po_number"]["value"] == "77" and got["po_number"]["status"] == "review"


def test_discover_tables_compare_cell_by_cell():
    a = [{"title": "Items", "columns": ["Item", "Qty"], "rows": [["Bolt", "2"], ["Nut", "5"]]}]
    b = [{"title": "Items", "columns": ["Item", "Qty"], "rows": [["Bolt", "2"], ["Nut", "6"]]}]
    tables, cols = discover.merge_tables(a, b)
    assert cols == {"items": ["Item", "Qty"]}
    assert tables["items"][0]["quantity"]["status"] == "agreed" and tables["items"][1]["quantity"]["status"] == "review"   # "Qty" is the key "quantity"
    one_sided, _ = discover.merge_tables(a, [])
    assert all(c["status"] == "review" for r in one_sided["items"] for c in r.values())


def _invoice_pdf(path):
    import pymupdf as fitz
    d = fitz.open()
    p = d.new_page(width=612, height=792)
    p.insert_text((50, 60), "INVOICE", fontsize=18)
    for y, t in [(100, "Invoice No: 1001"), (120, "Total Due: $215.00"), (140, "Customer: Acme Corp")]:
        p.insert_text((50, y), t, fontsize=11)
    d.save(path)


async def fake_discover(model, jpeg, prompt):
    return {"title": "Invoice", "fields": [{"label": "Invoice No", "value": "1001", "type": "id"},
                                           {"label": "Total Due", "value": "$215.00" if not model.endswith("mini") else "$215.80", "type": "currency"},
                                           {"label": "Customer", "value": "Acme Corp"}],
            "checkboxes": [], "tables": []}


def test_unregistered_document_is_discovered_not_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCOVER_DIGITAL_MODEL", "always")        # this test is about the two-reader model path
    pdf = str(tmp_path / "inv.pdf")
    _invoice_pdf(pdf)
    r = asyncio.run(pipeline.process(pdf, form_reader=fake_discover, model_ask=fake_ask))
    assert [d["route"]["extractor"] for d in r["documents"]] == ["discover"]
    form = r["forms"][0]
    assert form["discovered"] and form["label"] == "Invoice"
    f = form["fields"]
    assert f["invoice_number"]["status"] == "agreed" and f["invoice_number"]["bbox"] is not None       # highlighted on the page
    assert f["total_due"]["status"] == "review" and f["total_due"]["label"] == "Total Due"
    assert any(v["rule"] == "needs_review" for v in r["validation"])


def test_user_choice_forces_how_a_file_is_read(tmp_path, pdf):
    inv = str(tmp_path / "inv.pdf")
    _invoice_pdf(inv)
    forced = asyncio.run(pipeline.process(inv, form_reader=fake_form, template_choice="daily_note"))
    assert [d["type"] for d in forced["documents"]] == ["daily_note"]                      # user said so; no guessing
    assert forced["pages"][0]["classification"]["by"] == "user"
    free = asyncio.run(pipeline.process(inv, form_reader=fake_discover, template_choice="discover"))
    assert free["forms"][0]["discovered"] and free["documents"][0]["type"] == "unknown"
    assert pipeline.valid_choice("auto") and pipeline.valid_choice("discover") and pipeline.valid_choice("prior_auth_form")
    assert not pipeline.valid_choice("no_such_type")


# ---- templates drafted from examples -------------------------------------------------------------------------------

def test_draft_template_from_blank_form_reply():
    reply = {"title": "Vendor Application", "fields": [
        {"label": "Company Name", "section": "Applicant", "type": "text", "required": True},
        {"label": "Date Founded", "section": "Applicant", "type": "date", "required": False},
        {"label": "Company Name", "section": "Billing", "type": "text"}],
        "choice_groups": [{"label": "Entity type", "section": "Applicant", "options": ["LLC", "Corp"], "multiple": False, "required": True}],
        "tables": [{"title": "References", "columns": [{"label": "Name", "required": True}, {"label": "Phone"}]}]}
    got = discover.parse(reply)
    tpl = template_draft.build(got["title"], got["fields"], got["choice_groups"],
                               [{"title": t["title"], "columns": [{"label": c, "required": False} for c in t["columns"]]} for t in got["tables"]], True)
    keys = [f["key"] for s in tpl["sections"] for f in s["fields"]]
    assert keys == ["applicant.company_name", "applicant.date_founded", "applicant.entity_type", "billing.company_name"]
    assert tpl["doc_type"] == "vendor_application" and tpl["sections"][0]["header"] == "Applicant"
    assert template_draft.validate_template(tpl) == []
    assert [c["key"] for c in tpl["tables"][0]["columns"]] == ["name", "phone"]


def test_template_validation_blocks_unsafe_or_broken_templates():
    ok = {"doc_type": "my_form", "title": "My form", "sections": [{"id": "main", "header": None, "fields": [{"key": "a", "label": "A", "type": "text"}]}], "tables": []}
    assert template_draft.validate_template(ok) == []
    assert template_draft.validate_template({**ok, "doc_type": "prior_auth_form"})                 # clashes with a built-in
    assert template_draft.validate_template({**ok, "doc_type": "../evil"})                         # path-like names
    dup = {**ok, "sections": [{"id": "m", "header": None, "fields": [{"key": "a", "label": "A", "type": "text"}, {"key": "a", "label": "B", "type": "text"}]}]}
    assert any("Duplicate" in e for e in template_draft.validate_template(dup))
    pat = {**ok, "sections": [{"id": "m", "header": None, "fields": [{"key": "a", "label": "A", "type": "text", "pattern": "(a+)+$"}]}]}
    assert template_draft.validate_template(pat)                                                    # no user regexes (ReDoS)
    assert template_draft.validate_template({**ok, "sections": [], "tables": []})                   # empty


def test_grid_as_table_in_one_reader_and_flat_fields_in_the_other_agree():
    table_reader = discover.parse({"tables": [{"title": "Shoulder AROM", "columns": ["", "Right", "Left"],
                                               "rows": [["Flexion", "170°", "Not Tested"], ["Abduction", "160°", "Not Tested"]]}]})
    field_reader = discover.parse({"fields": [{"label": "Flexion Right", "value": "170°", "section": "Shoulder AROM"},
                                              {"label": "Flexion Left", "value": "Not Tested"},
                                              {"label": "Abduction Right", "value": "165°"}]})   # a genuine disagreement
    conf_a, conf_b = discover.reconcile(table_reader, field_reader)
    assert [f["label"] for f in field_reader["fields"]] == ["Abduction Right"]      # the confirmed ones live in the table now
    tables, _ = discover.merge_tables(table_reader["tables"], [], conf_a, conf_b)
    row = tables["shoulder_arom"][0]
    assert row["right"]["status"] == "agreed" and row["left"]["status"] == "agreed"
    assert row["item"]["status"] == "agreed"                                      # the row label ("Flexion") rides on its values
    assert tables["shoulder_arom"][1]["right"]["status"] == "review"              # 160 vs 165 was never confirmed


def test_blank_one_sided_fields_are_not_flagged():
    a = discover.parse({"fields": [{"label": "Notes", "value": None}, {"label": "Name", "value": "Ann"}]})["fields"]
    b = discover.parse({"fields": [{"label": "Name", "value": "Ann"}]})["fields"]
    got = discover.merge_fields(a, b)
    assert got["notes"]["status"] == "empty" and got["name"]["status"] == "agreed"


# ---- forms inside other content ------------------------------------------------------------------------------------------------

LETTER = "\n".join(f"Paragraph line {i}: our clinic is writing about the pending authorization for this patient." for i in range(14))


def test_a_title_is_found_anywhere_on_the_page_but_a_mention_is_not():
    below = LETTER + "\nProvider Prior Authorization Request Form\nMEMBER INFORMATION\nLast Name: Morgan"
    assert classify.classify_text(below)["type"] == "prior_auth_form"                  # form below a long letter, same page
    mention = "Dear Sir,\nPlease complete the Provider Prior Authorization Request Form and return it within ten days.\n" + LETTER
    assert classify.classify_text(mention)["type"] == "unknown"                          # named in a sentence: not a form
    split = LETTER + "\nPhysical Therapy\nInitial\nExamination\nPatient Name: Morgan Taylor"
    assert classify.classify_text(split)["type"] == "pt_initial_exam"                   # a title broken over three lines
    both = "Daily Note / Billing Sheet\n" + LETTER + "\nProvider Prior Authorization Request Form\nMEMBER INFORMATION"
    assert classify.classify_text(both)["type"] == "daily_note"                         # two titles: the topmost names the page
    cover = "FAX\nThis transmission is legally privileged and confidential information intended only for the person named above."
    assert classify.classify_text(cover)["type"] == "fax_cover"                         # cover sheets have no title: top-of-page rule


def test_segmentation_respects_single_page_templates():
    def pg(n, t): return {"page": n, "classification": {"type": t, "label": t}}
    single = {"prior_auth_form"}
    docs = classify.segment([pg(1, "prior_auth_form"), pg(2, "unknown"), pg(3, "blank_or_logo")], single)
    assert [(d["type"], d["pages"]) for d in docs] == [("prior_auth_form", [1]), ("unknown", [2, 3])]   # an untitled page is not a form's continuation
    two_copies = classify.segment([pg(1, "prior_auth_form"), pg(2, "prior_auth_form")], single)
    assert [d["pages"] for d in two_copies] == [[1], [2]]                                                  # two copies of a form are two documents
    exam = classify.segment([pg(1, "pt_initial_exam"), pg(2, "pt_initial_exam"), pg(3, "unknown")], single)
    assert [(d["type"], d["pages"]) for d in exam] == [("pt_initial_exam", [1, 2, 3])]                     # a multi-page type keeps absorbing


# ---- reading the rest of a templated document ------------------------------------------------------------------------------------

def _disc_cell(label, value, page=1, bbox=None, status="agreed"):
    return {"label": label, "value": value, "status": status, "alternatives": [], "page": page, "bbox": bbox}


def test_rest_subtracts_what_the_template_already_covers():
    from app.docproc.services.extraction import remainder
    tpl = pipeline.load_template("magnacare_prior_auth")
    form = {"doc_index": 0, "pages": [1], "tables": {"cpt": [{"code": {"value": "97110", "page": 1, "bbox": [50, 400, 90, 410]}}]},
            "fields": {"member.member_id": {"value": "9000001234", "page": 1, "bbox": [300, 100, 380, 112]},
                       "member.last_name": {"value": "Morgan", "page": 1, "bbox": [60, 120, 100, 132]}}}
    found = {"readers": ["a", "b"], "title": None, "table_columns": {"cpt": ["QTY", "CPT/HCPCS", "Description of Service", "U&C Charge"], "notes": ["Item", "Owner"]},
             "fields": {
                 "last_name": _disc_cell("Last Name", "Morgan"),                                       # same label as a template field
                 "member_ref": _disc_cell("Member Reference", "9000001234", bbox=[301, 101, 379, 111]),  # sits on a template value
                 "dup_value": _disc_cell("Account", "9000001234"),                                       # same long value elsewhere
                 "blank": _disc_cell("Notes", None, status="empty"),                                     # empty blank: nothing to extract
                 "ref": _disc_cell("Reference number", "RX-7781", bbox=[50, 600, 130, 612]),             # genuinely new
                 "box": {"label": "Urgent", "value": False, "status": "agreed", "alternatives": [], "page": 1, "bbox": None, "method": "pixel"},  # an option of the template
             },
             "tables": {"cpt": [{"qty": _disc_cell("QTY", "12")}],                                        # the template's own table
                        "notes": [{"item": _disc_cell("Item", "Call back"), "owner": _disc_cell("Owner", "Dana")}]}}
    rest = remainder.subtract(found, form, tpl)
    assert list(rest["fields"]) == ["ref"]
    assert list(rest["tables"]) == ["notes"]


def _form_below_a_letter(path):
    import pymupdf as fitz
    d = fitz.open()
    p = d.new_page(width=612, height=792)
    y = 50
    for i in range(14):
        p.insert_text((50, y), f"Paragraph line {i}: our clinic is writing about the pending authorization for this patient.", fontsize=9)
        y += 12
    p.insert_text((50, y + 20), "Provider Prior Authorization Request Form", fontsize=14)
    for k, t in enumerate(["MEMBER INFORMATION", "Last Name*: Morgan", "First Name*: Taylor", "Date of Birth*: 07/01/1970"]):
        p.insert_text((50, y + 50 + k * 12), t, fontsize=9)
    p.insert_text((50, y + 130), "Reference number: RX-7781", fontsize=9)
    d.save(path)


async def fake_rest_reader(model, jpeg, prompt):
    if "extracting ALL the data" in prompt:      # the discovery pass over the same page
        return {"title": None, "checkboxes": [], "tables": [], "fields": [
            {"label": "Last Name", "value": "Morgan"}, {"label": "First Name", "value": "Taylor"},
            {"label": "Date of Birth", "value": "07/01/1970", "type": "date"},
            {"label": "Reference number", "value": "RX-7781"}, {"label": "Notes", "value": None}]}
    return await fake_form(model, jpeg, prompt)


def test_form_below_other_text_is_a_form_and_the_rest_is_read_separately(tmp_path):
    pdf = str(tmp_path / "mixed.pdf")
    _form_below_a_letter(pdf)
    without = asyncio.run(pipeline.process(pdf, form_reader=fake_rest_reader))
    assert [d["type"] for d in without["documents"]] == ["prior_auth_form"]            # found below the letter
    assert [f["doc_type"] for f in without["forms"]] == ["prior_auth_form"] and without["rest_checked"] == []

    full = asyncio.run(pipeline.process(pdf, form_reader=fake_rest_reader, read_rest=True))
    assert [f["doc_type"] for f in full["forms"]] == ["prior_auth_form", "other_content"]
    rest = full["forms"][1]
    assert rest["remainder"] and rest["remainder_of"] == full["forms"][0]["doc_index"] and rest["doc_index"] != full["forms"][0]["doc_index"]
    assert [c["label"] for c in rest["fields"].values()] == ["Reference number"]        # not Last Name etc., not the empty blank
    assert full["rest_checked"] == [full["forms"][0]["doc_index"]]
    assert full["forms"][0]["fields"]["member.last_name"]["value"] == "Morgan"            # the form itself is untouched


def test_a_workspaces_own_type_beats_a_builtin_with_the_same_title():
    from app.docproc.services.documents import workspace_types
    page = "Provider Prior Authorization Request Form\nMEMBER INFORMATION\nLast Name: Morgan"
    assert classify.classify_text(page)["type"] == "prior_auth_form"                    # nothing custom: the built-in
    mine = {"type": "my_prior_auth", "label": "My prior auth", "score": 3, "template": "my_prior_auth", "extractor": "template_form",
            "any": [r"provider\s+prior\s+authorization\s+request\s+form"]}
    token = workspace_types._ITEMS.set([{"entry": mine, "template": {"doc_type": "my_prior_auth"}}])
    try:
        assert classify.classify_text(page)["type"] == "my_prior_auth"                  # the uploaded template is used
    finally:
        workspace_types._ITEMS.reset(token)
    assert classify.classify_text(page)["type"] == "prior_auth_form"


def test_template_links_are_validated_and_cannot_carry_regexes():
    base = {"doc_type": "linked_form", "title": "Linked form", "tables": [{"key": "items", "columns": [{"key": "sku", "label": "SKU"}]}],
            "sections": [{"id": "m", "header": None, "fields": [{"key": "dob", "label": "DOB", "type": "date"}, {"key": "start", "label": "Start", "type": "date"}, {"key": "end", "label": "End", "type": "date"}]}]}
    good = {**base, "canonical": {"person.dob": {"kind": "date", "label": "Date of birth", "fields": ["dob"]},
                                  "codes": {"kind": "set", "label": "Item codes", "table": "items", "column": "sku"}},
            "rules": [{"type": "date_order", "start": "start", "end": "end"}]}
    assert template_draft.validate_template(good) == []
    evil = {**good, "canonical": {"codes": {"kind": "set", "table": "items", "column": "sku", "pattern": "(a+)+$"}}}
    assert any("patterns cannot" in e for e in template_draft.validate_template(evil))
    assert template_draft.validate_template({**good, "canonical": {"x": {"kind": "date", "fields": ["nope"]}}})
    assert template_draft.validate_template({**good, "canonical": {"x": {"kind": "whatever", "fields": ["dob"]}}})
    assert template_draft.validate_template({**good, "canonical": {"x": {"kind": "set", "table": "items", "column": "missing"}}})
    assert template_draft.validate_template({**good, "rules": [{"type": "date_order", "start": "start", "end": "nope"}]})


# ---- checkboxes on scans are read from pixels, not from a transcript's glyphs --------------------------------------------

@pytest.mark.skipif(not tess_available(), reason="Tesseract not installed")
def test_scanned_checkboxes_ignore_glyphs_in_the_ocr_transcript(tmp_path):
    import pymupdf as fitz
    from tests.docproc.make_packet import box
    src = fitz.open()
    p = src.new_page(width=612, height=792)
    p.insert_text((50, 60), "Provider Prior Authorization Request Form", fontsize=14)
    for x, label, on in [(50, "ROUTINE", True), (170, "URGENT", False), (290, "TRANSPLANT", False)]:
        box(p, x, 100, on)
        p.insert_text((x + 14, 100), label, fontsize=11)
    png = p.get_pixmap(dpi=220).tobytes("png")
    scan = fitz.open()
    q = scan.new_page(width=612, height=792)
    q.insert_image(q.rect, stream=png)                       # a scan: the page is only a picture
    pdf = str(tmp_path / "scan.pdf")
    scan.save(pdf)

    async def lying_ocr(model, jpeg):                        # the transcript puts the tick on the WRONG option
        return ["Provider Prior Authorization Request Form", "☐ ROUTINE  ☑ URGENT  ☐ TRANSPLANT"]
    out = asyncio.run(pipeline.process(pdf, ocr_reader=lying_ocr, model_ask=fake_ask))
    cell = out["forms"][0]["fields"]["review_type"]
    assert cell["value"] == "ROUTINE" and cell["method"] == "pixel"      # the pixels win; the transcript's glyphs are ignored


def test_drafted_fields_are_located_on_the_blank_form(tmp_path):
    import pymupdf as fitz
    d = fitz.open()
    p = d.new_page(width=612, height=792)
    p.insert_text((50, 60), "MEMBER INFORMATION", fontsize=11)
    p.insert_text((50, 90), "First Name:", fontsize=10)
    p.insert_text((50, 160), "PROVIDER INFORMATION", fontsize=11)
    p.insert_text((50, 190), "First Name:", fontsize=10)
    p.insert_text((50, 220), "Fax Number:", fontsize=10)
    pdf = str(tmp_path / "blank.pdf")
    d.save(pdf)
    tpl = template_draft.build("Test form", [
        {"label": "First Name", "section": "MEMBER INFORMATION", "type": "text"},
        {"label": "First Name", "section": "PROVIDER INFORMATION", "type": "text"},
        {"label": "Fax Number", "section": "PROVIDER INFORMATION", "type": "text"},
        {"label": "Something absent", "section": "PROVIDER INFORMATION", "type": "text"}], [], [], True)
    template_draft.locate_fields(pdf, tpl)
    fields = {f["key"]: f for s in tpl["sections"] for f in s["fields"]}
    member, provider = fields["member_information.first_name"], fields["provider_information.first_name"]
    assert member["page"] == 1 and member["bbox"][1] < 100 < provider["bbox"][1]          # same label, each in its own block
    assert fields["provider_information.fax_number"]["bbox"][1] > provider["bbox"][1]
    assert "bbox" not in fields["provider_information.something_absent"]                   # not on the form: no location, no crash


def test_cells_carry_type_and_options_for_the_ui(pdf=None):
    form = asyncio.run(pipeline.process(_make_packet_path(), form_reader=fake_form))["forms"][0]
    assert form["fields"]["review_type"]["type"] == "enum" and form["fields"]["review_type"]["options"] == ["ROUTINE", "URGENT", "TRANSPLANT"]
    assert form["fields"]["member.last_name"]["type"] == "text" and "options" not in form["fields"]["member.last_name"]


def _make_packet_path():
    import tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "p.pdf")
    from tests.docproc.make_packet import build
    build(p)
    return p
