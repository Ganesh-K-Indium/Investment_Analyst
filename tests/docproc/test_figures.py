"""Figures: pictures, charts and drawings. Finding is code; reading is one model call, on request. No real model is called here."""
import asyncio
import io
import json

import pymupdf as fitz
import pytest

from app.docproc import pipeline
from app.docproc.services.extraction import figures, structure
from app.docproc.services.reading import pdf_pages
from tests.docproc.api_stub import H, client, mk, up, wait  # noqa: F401  (client is a fixture)


def _png(w=200, h=140, colour=(30, 90, 160)):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([10, 10, w - 10, h - 10], outline=colour, width=4)
    d.line([10, h - 10, w - 10, 10], fill=colour, width=3)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _bar_chart(page, x=100, y=200, values=(35, 62, 48, 80)):
    """A bar chart drawn as vector graphics: axes, bars, tick labels. Heights are in points, 1 unit = 1.5 pt."""
    page.draw_line((x, y), (x, y + 150))
    page.draw_line((x, y + 150), (x + 260, y + 150))
    for k, v in enumerate(values):
        left = x + 20 + k * 58
        page.draw_rect(fitz.Rect(left, y + 150 - v * 1.5, left + 36, y + 150), fill=(0.2, 0.4, 0.8))
        page.insert_text((left + 8, y + 164), f"Q{k + 1}", fontsize=8)
    for t in (0, 50, 100):
        page.insert_text((x - 22, y + 150 - t * 1.5 + 3), str(t), fontsize=8)
        page.draw_line((x - 3, y + 150 - t * 1.5), (x, y + 150 - t * 1.5))                  # tick marks
    page.insert_text((x + 60, y - 8), "Revenue by quarter", fontsize=9)


def _figs(got):
    return [b for p in got["pages"]["pages"] for b in p["structure"]["blocks"] if b["type"] == "figure"]


def _page_with(path, draw):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 60), "A document with a figure", fontsize=16)
    draw(page)
    doc.save(path)
    return str(path)


# ---- finding ----------------------------------------------------------------------------------------------------------

def test_an_embedded_picture_is_a_figure(tmp_path):
    p = _page_with(tmp_path / "img.pdf", lambda pg: pg.insert_image(fitz.Rect(100, 150, 400, 360), stream=_png()))
    got = figures.find_digital(p, 1, [])
    assert len(got) == 1 and got[0]["source"] == "image" and [round(v) for v in got[0]["bbox"]] == [100, 150, 400, 360]


def test_a_chart_drawn_as_vectors_is_a_figure_and_keeps_its_labels(tmp_path):
    p = _page_with(tmp_path / "chart.pdf", _bar_chart)
    got = figures.find_digital(p, 1, [])
    assert len(got) == 1 and got[0]["source"] == "vector"
    assert {"Q1", "Q4", "100"} <= set(got[0]["labels"])


def test_a_table_drawn_with_rules_is_not_a_figure(tmp_path):
    def table(pg):
        for k in range(6):
            pg.draw_line((60, 150 + k * 24), (500, 150 + k * 24))
        for x in (60, 200, 350, 500):
            pg.draw_line((x, 150), (x, 270))
        for k in range(5):
            pg.insert_text((66, 166 + k * 24), f"Row {k}", fontsize=9)
    p = _page_with(tmp_path / "table.pdf", table)
    info = pdf_pages.analyze_page(p, 1)
    assert figures.find_digital(p, 1, info["tables"]) == []


def test_a_boxed_note_is_not_a_figure(tmp_path):
    def note(pg):
        pg.draw_rect(fitz.Rect(100, 200, 330, 300))
        for k, t in enumerate(("PLEASE ATTACH CLINICAL NOTES", "Phone: 800-352-6465", "Inpatient fax: 888-861-4413")):
            pg.insert_text((108, 222 + k * 22), t, fontsize=9)
    assert figures.find_digital(_page_with(tmp_path / "note.pdf", note), 1, []) == []


def test_a_page_of_form_boxes_is_not_a_figure(tmp_path):
    def form(pg):
        for k in range(8):
            pg.draw_rect(fitz.Rect(60, 120 + k * 40, 300, 150 + k * 40))
            pg.insert_text((64, 112 + k * 40), f"Field {k}", fontsize=8)
    assert figures.find_digital(_page_with(tmp_path / "form.pdf", form), 1, []) == []


def test_a_shaded_band_and_a_tiny_logo_in_the_margin_are_not_figures(tmp_path):
    def decor(pg):
        pg.draw_rect(fitz.Rect(0, 0, 612, 60), fill=(0.9, 0.9, 0.95))
        pg.insert_image(fitz.Rect(40, 20, 80, 45), stream=_png(40, 25))
    assert figures.find_digital(_page_with(tmp_path / "decor.pdf", decor), 1, []) == []


def test_a_picture_on_every_page_is_a_logo_and_is_dropped():
    pages = [{"structure": {"blocks": [{"type": "figure", "digest": "abc", "bbox": [0, 0, 1, 1]}, {"type": "figure", "digest": f"own{n}", "bbox": [0, 0, 1, 1]}]}} for n in range(4)]
    assert figures.drop_logos(pages) == 4
    assert all(len([b for b in p["structure"]["blocks"] if b["type"] == "figure"]) == 1 for p in pages)
    assert [b["index"] for b in pages[0]["structure"]["blocks"]] == [0]


def test_a_scanned_page_finds_the_drawing_but_not_the_text_or_a_table():
    from PIL import Image, ImageDraw
    im = Image.new("L", (1200, 1600), 250)
    d = ImageDraw.Draw(im)
    d.rectangle([300, 500, 900, 1000], outline=30, width=6)                 # a drawing: no words in it
    d.line([300, 1000, 900, 500], fill=30, width=6)
    d.ellipse([500, 650, 700, 850], outline=30, width=6)
    for k in range(10):
        d.rectangle([100, 1150 + k * 30, 1000, 1160 + k * 30], fill=60)     # text lines: the OCR's words cover them
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    size = {"width": 600.0, "height": 800.0}
    words = [{"text": f"w{k}", "bbox": [50 + 30 * j, 575 + k * 15 - 1, 50 + 30 * j + 25, 575 + k * 15 + 7]} for k in range(10) for j in range(15)]
    got = figures.find_scanned(buf.getvalue(), words, size)
    assert len(got) == 1 and got[0]["source"] == "region"
    x0, y0, x1, y1 = got[0]["bbox"]
    assert 130 < x0 < 170 and 230 < y0 < 270 and 430 < x1 < 470 and 480 < y1 < 520


# ---- in the structure ---------------------------------------------------------------------------------------------------

def test_a_figure_becomes_a_block_with_its_caption_and_labels_and_swallows_its_junk_table():
    cells = [[("Some text before the figure on the page.", 0.1, 0.05, 0.9, 0.07)],
             [("Pin 1", 0.3, 0.2, 0.36, 0.22), ("Pin 8", 0.6, 0.2, 0.66, 0.22)],
             [("GND", 0.3, 0.25, 0.36, 0.27), ("VCC", 0.6, 0.25, 0.66, 0.27)],
             [("Figure 4-1. Package top view", 0.3, 0.38, 0.7, 0.4)],
             [("Text after the figure and its caption on the page.", 0.1, 0.5, 0.9, 0.52)]]
    junk = {"bbox": [0.28, 0.19, 0.68, 0.28], "columns": ["a", "b"], "rows": [["1", "2"]], "method": "layout"}
    fig = {"bbox": [0.25, 0.18, 0.75, 0.36], "source": "vector", "labels": ["Pin 1"]}
    page = structure.build_page(cells, tables=[junk], figures=[fig])
    kinds = [b["type"] for b in page["blocks"]]
    assert kinds == ["paragraph", "figure", "paragraph"]                    # no table from the drawing, caption not a paragraph
    f = page["blocks"][1]
    assert f["caption"] == "Figure 4-1. Package top view" and set(f["labels"]) >= {"Pin 1", "Pin 8", "GND", "VCC"} and f["reading"] is None


def test_one_caption_serves_two_figures_side_by_side():
    cells = [[("Figure 2: (left) A. (right) B.", 0.1, 0.5, 0.9, 0.52)]]
    left = {"bbox": [0.1, 0.2, 0.4, 0.45], "source": "image"}
    right = {"bbox": [0.5, 0.2, 0.9, 0.45], "source": "image"}
    blocks = structure.build_page(cells, figures=[left, right])["blocks"]
    assert [b["type"] for b in blocks] == ["figure", "figure"] and blocks[0]["caption"] == blocks[1]["caption"] == "Figure 2: (left) A. (right) B."


# ---- reading ----------------------------------------------------------------------------------------------------------

CHART_REPLY = {"kind": "chart", "title": "Revenue by quarter", "summary": "Bars for four quarters.",
               "chart": {"type": "bar", "x_axis": {"label": "Quarter", "unit": None}, "y_axis": {"label": "Revenue", "unit": "USD m"},
                         "series": [{"name": "Revenue", "points": [{"x": "Q1", "y": "35", "printed": False}, {"x": "Q2", "y": "100", "printed": True},
                                                                     {"x": "Q3", "y": "48", "printed": True}]}]},
               "components": None, "connections": None, "text": ["Q1", "Q2", "0", "50", "100"], "limits": None}


def _fake(reply, calls):
    async def ask(model, jpeg, prompt):
        calls.append((model, len(jpeg), prompt))
        return json.dumps(reply)
    return ask


def test_a_figure_is_read_with_one_small_crop_and_its_values_are_marked(tmp_path):
    p = _page_with(tmp_path / "chart.pdf", _bar_chart)
    fig = figures.find_digital(p, 1, [])[0]
    calls = []
    got = asyncio.run(figures.read(p, 1, fig["bbox"], fig["labels"], "m1", _fake(CHART_REPLY, calls)))
    assert len(calls) == 1 and calls[0][0] == "m1"
    jpeg = figures.crop_jpeg(p, 1, fig["bbox"])
    from PIL import Image
    assert max(Image.open(io.BytesIO(jpeg)).size) <= figures.MAX_SIDE
    pts = {pt["x"]: pt for pt in got["chart"]["series"][0]["points"]}
    assert pts["Q1"]["estimated"] is True                                   # 35 is on no label: worked out from the bar
    assert pts["Q2"]["estimated"] is False                                  # 100 is a printed axis label
    assert pts["Q3"]["estimated"] is True                                   # claimed as printed, but 48 is written nowhere: estimated after all
    assert pts["Q1"]["value"] == 35.0 and pts["Q2"]["value"] == 100.0
    assert got["values_total"] == 3 and got["values_confirmed"] == 1 and "printed" not in pts["Q1"]
    assert "Q4" in calls[0][2]                                              # the labels code found are in the question


def test_the_same_figure_is_never_read_twice(tmp_path):
    p = _page_with(tmp_path / "chart.pdf", _bar_chart)
    fig = figures.find_digital(p, 1, [])[0]
    calls = []
    ask = _fake(CHART_REPLY, calls)
    a = asyncio.run(figures.read(p, 1, fig["bbox"], fig["labels"], "m2", ask))
    b = asyncio.run(figures.read(p, 1, fig["bbox"], fig["labels"], "m2", ask))
    assert len(calls) == 1 and a == b


def test_a_reply_that_is_not_json_or_has_odd_shapes_does_not_break_reading():
    got = figures._clean("this is not json")
    assert got["kind"] == "other" and got["chart"] is None and got["text"] == []
    got = figures._clean({"kind": "spaceship", "chart": {"series": [{"points": [{"x": 1, "y": 2}, "junk"]}]}, "components": ["junk", {"name": "A"}]})
    assert got["kind"] == "other" and got["chart"]["series"][0]["points"][0]["y"] == "2" and got["components"] == [{"name": "A", "role": None}]


# ---- the API ----------------------------------------------------------------------------------------------------------

def _enable(monkeypatch, reply, calls):
    from app.docproc import config
    from app.api import extractions
    from app.docproc.engines.providers import llm
    monkeypatch.setattr(config, "llm_enabled", lambda: True)

    class Plan:
        extract_models = ["fake-model"]
    monkeypatch.setattr(extractions.plans, "for_workspace", lambda *a, **k: Plan())

    async def complete(model, prompt, jpeg=None, *, stage, json_mode=True):
        calls.append(stage)
        return json.dumps(reply)
    monkeypatch.setattr(llm, "complete", complete)
    figures._CACHE.clear()


def test_figures_are_found_at_upload_and_read_only_when_asked(client, tmp_path, monkeypatch):
    pdf = _page_with(tmp_path / "chart.pdf", _bar_chart)
    wid = mk(client)
    eid = up(client, wid, tmp_path / "chart.pdf").json()["id"]
    got = wait(client, wid, eid)
    blocks = _figs(got)
    assert len(blocks) == 1 and blocks[0]["reading"] is None and blocks[0]["index"] == 0 and got["stats"]["figures"] == 1
    assert got["stats"]["usage"]["calls"] == 0                              # finding the figure cost nothing

    calls = []
    _enable(monkeypatch, CHART_REPLY, calls)
    url = f"/api/workspaces/{wid}/extractions/{eid}/figures"
    r = client.post(f"{url}/read", json={"page": 1, "index": 0}, headers=H)
    assert r.status_code == 200 and r.json()["read"] == 1 and len(calls) == 1
    again = client.post(f"{url}/read", json={"page": 1, "index": 0}, headers=H).json()
    assert again["read"] == 0 and len(calls) == 1                           # already read: no second call
    forced = client.post(f"{url}/read", json={"page": 1, "index": 0, "force": True}, headers=H).json()
    assert forced["read"] == 1
    got = client.get(f"/api/workspaces/{wid}/extractions/{eid}", headers=H).json()
    fig = _figs(got)[0]
    assert fig["kind"] == "chart" and fig["reading"]["chart"]["series"][0]["points"][0]["estimated"] is True
    assert got["stats"]["figures_read"] == 1 and got["stats"]["usage"]["by_stage"].get("figures") is not None
    img = client.get(f"{url}/1/0.jpg", headers=H)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content[:2] == b"\xff\xd8"
    assert client.post(f"{url}/read", json={"page": 1, "index": 5}, headers=H).status_code == 404
    sj = client.get(f"/api/workspaces/{wid}/extractions/{eid}/structure.json", headers=H).json()
    assert any(b["type"] == "figure" and b["reading"] for d in sj["documents"] for pg in d["pages"] for b in pg["blocks"])


def test_reading_all_figures_is_capped_and_a_logo_is_marked(client, tmp_path, monkeypatch):
    def many(pg):
        for k in range(3):
            pg.insert_image(fitz.Rect(60 + k * 170, 120, 200 + k * 170, 260), stream=_png(140, 140, (30 + k * 60, 90, 160)))
    _page_with(tmp_path / "many.pdf", many)
    wid = mk(client)
    eid = up(client, wid, tmp_path / "many.pdf").json()["id"]
    assert wait(client, wid, eid)["stats"]["figures"] == 3
    monkeypatch.setenv("FIGURE_READ_MAX", "2")
    calls = []
    _enable(monkeypatch, {"kind": "logo"}, calls)
    r = client.post(f"/api/workspaces/{wid}/extractions/{eid}/figures/read", json={"all": True}, headers=H).json()
    assert r["read"] == 2 and r["skipped"] == 1 and len(calls) == 2
    got = client.get(f"/api/workspaces/{wid}/extractions/{eid}", headers=H).json()
    figs = _figs(got)
    assert sum(1 for b in figs if b.get("decorative")) == 2


def test_reading_a_figure_without_a_model_is_refused_plainly(client, tmp_path):
    _page_with(tmp_path / "chart.pdf", _bar_chart)
    wid = mk(client)
    eid = up(client, wid, tmp_path / "chart.pdf").json()["id"]
    wait(client, wid, eid)
    r = client.post(f"/api/workspaces/{wid}/extractions/{eid}/figures/read", json={"page": 1, "index": 0}, headers=H)
    assert r.status_code == 503 and "vision model" in r.json()["detail"]


# ---- cluttered and awkward pages: figures drawn in pieces, icon fields -----------------------------------------------

_LINE = "The quarterly figures show steady growth across every region, driven mostly by services and subscriptions."


def _paras(pg, y, n):
    for k in range(n):
        pg.insert_text((60, y + k * 13), _LINE, fontsize=9)


def _chart_with_legend(pg, x, y, legend_gap):
    pg.draw_line((x, y), (x, y + 130))
    pg.draw_line((x, y + 130), (x + 230, y + 130))
    for t in range(0, 101, 25):
        yy = y + 130 - t * 1.3
        pg.draw_line((x - 3, yy), (x, yy))
        pg.insert_text((x - 24, yy + 3), str(t), fontsize=7)
    for k, v in enumerate((35, 62, 48, 80)):
        left = x + 16 + k * 52
        pg.draw_rect(fitz.Rect(left, y + 130 - v * 1.3, left + 32, y + 130), fill=(0.2, 0.4, 0.8))
        pg.insert_text((left + 6, y + 142), f"Q{k + 1}", fontsize=7)
    lx = x + 230 + legend_gap
    pg.draw_rect(fitz.Rect(lx, y + 10, lx + 9, y + 19), fill=(0.2, 0.4, 0.8))
    pg.insert_text((lx + 13, y + 18), "Revenue", fontsize=7)
    return [x - 26, y, lx + 60, y + 150]


def _iou(a, b):
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    i = w * h
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


@pytest.mark.parametrize("gap", [0, 25, 45])
def test_a_legend_drawn_apart_from_its_chart_stays_with_the_figure(tmp_path, gap):
    truth = []

    def draw(pg):
        _paras(pg, 90, 4)
        truth.append(_chart_with_legend(pg, 120, 200, gap))
    p = _page_with(tmp_path / f"legend{gap}.pdf", draw)
    got = figures.find_digital(p, 1, [])
    assert len(got) == 1 and _iou(got[0]["bbox"], truth[0]) >= 0.8 and "Revenue" in got[0]["labels"]


def test_a_wide_block_diagram_is_a_figure_not_a_band(tmp_path):
    def diagram(pg):
        for i in range(3):
            x = 80 + i * 130
            pg.draw_rect(fitz.Rect(x, 250, x + 80, 300))
            pg.insert_text((x + 10, 280), f"Block {i + 1}", fontsize=8)
            if i:
                pg.draw_line((x - 50, 275), (x, 275))
                pg.draw_line((x - 6, 270), (x, 275))
                pg.draw_line((x - 6, 280), (x, 275))
    got = figures.find_digital(_page_with(tmp_path / "flow.pdf", diagram), 1, [])
    assert len(got) == 1 and _iou(got[0]["bbox"], [80, 250, 380, 300]) >= 0.8


def test_a_grid_of_small_icons_is_not_a_figure(tmp_path):
    def icons(pg):
        for r in range(4):
            for c in range(4):
                ix, iy = 100 + c * 22, 250 + r * 22
                pg.draw_circle((ix + 7, iy + 7), 7, fill=(0.9, 0.5, 0.1))
                pg.draw_line((ix + 3, iy + 7), (ix + 11, iy + 7))
    assert figures.find_digital(_page_with(tmp_path / "icons.pdf", icons), 1, []) == []


def test_a_banner_across_the_top_of_the_page_is_not_a_figure_but_a_narrow_flow_is(tmp_path):
    def banner(pg):
        pg.draw_rect(fitz.Rect(0, 0, 595, 40), fill=(0.9, 0.9, 0.95))
        for k in range(20):
            pg.draw_line((20 + k * 28, 10), (30 + k * 28, 30))
    assert figures.find_digital(_page_with(tmp_path / "banner.pdf", banner), 1, []) == []


# scans and photographs

def _degraded_scan(tmp_path, name, draw, variant, seed=3):
    """Draw a page, scan it (turn it a little, add noise, uneven light and blur for a 'photo') and return (path, [rotated truth boxes])."""
    import math
    import numpy as np
    from PIL import Image, ImageFilter
    doc = fitz.open()
    pg = doc.new_page()
    truths = draw(pg)
    pix = pg.get_pixmap(dpi=150)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    angle = {"clean": 0, "tilt": 1.5, "photo": 2.5}[variant]
    im = im.rotate(angle, fillcolor=240, resample=Image.BICUBIC)
    a = np.asarray(im).astype(float)
    rng = np.random.default_rng(seed)
    if variant != "clean":
        a += rng.normal(0, 7 if variant == "tilt" else 10, a.shape)
    if variant == "photo":
        y, x = np.mgrid[0:a.shape[0], 0:a.shape[1]]
        a = a * (0.7 + 0.3 * (x / a.shape[1]) * (1 - 0.3 * y / a.shape[0]))
    im = Image.fromarray(np.clip(a, 0, 255).astype("uint8"))
    if variant == "photo":
        im = im.filter(ImageFilter.GaussianBlur(0.9))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=60 if variant == "photo" else 80)
    out = fitz.open()
    sp = out.new_page(width=595, height=842)
    sp.insert_image(sp.rect, stream=buf.getvalue())
    path = str(tmp_path / f"{name}_{variant}.pdf")
    out.save(path)

    def turn(b):
        r = math.radians(-angle)
        c = (297.5, 421)
        pts = [(b[0], b[1]), (b[2], b[1]), (b[0], b[3]), (b[2], b[3])]
        q = [(c[0] + (x - c[0]) * math.cos(r) - (y - c[1]) * math.sin(r), c[1] + (x - c[0]) * math.sin(r) + (y - c[1]) * math.cos(r)) for x, y in pts]
        return [min(p[0] for p in q), min(p[1] for p in q), max(p[0] for p in q), max(p[1] for p in q)]
    return path, [turn(t) for t in truths]


def _scan_figures(path):
    work = path.replace(".pdf", ".work.pdf")
    pdf_pages.build_work_pdf(path, work, {1})
    info = pdf_pages.analyze_page(work, 1)
    return figures.find_scanned(pdf_pages.render_jpeg(path, 1), info["words"], info["size"])


def _chart_picture_table_page(pg):
    from PIL import Image, ImageDraw
    pg.insert_text((60, 60), "Annual report", fontsize=16)
    _paras(pg, 90, 4)
    chart = _chart_with_legend(pg, 110, 170, 0)
    im = Image.new("RGB", (200, 120))
    d = ImageDraw.Draw(im)
    for k in range(200):
        d.line([(k, 0), (k, 120)], fill=(k, 120, 255 - k))
    d.ellipse([60, 30, 140, 90], outline=(0, 0, 0), width=4)
    b = io.BytesIO()
    im.save(b, "PNG")
    picture = [330, 400, 530, 520]
    pg.insert_image(fitz.Rect(*picture), stream=b.getvalue())
    _paras(pg, 545, 5)
    for k in range(6):
        pg.draw_line((60, 640 + k * 20), (500, 640 + k * 20))
    for x in (60, 210, 360, 500):
        pg.draw_line((x, 640), (x, 740))
    for r in range(5):
        for c, t in enumerate(("Region", "Q1", "Q2")):
            pg.insert_text((66 + c * 150, 654 + r * 20), f"{t} {r}", fontsize=8)
    return [chart, picture]


need_tess = pytest.mark.skipif(not __import__("app.docproc.services.reading.tess", fromlist=["x"]).available(), reason="Tesseract is not installed")


@need_tess
@pytest.mark.parametrize("variant", ["tilt", "photo"])
def test_a_scanned_page_gives_its_chart_and_picture_but_not_its_table(tmp_path, variant):
    path, truth = _degraded_scan(tmp_path, "mixed", _chart_picture_table_page, variant)
    got = _scan_figures(path)
    assert len(got) == 2, [g["bbox"] for g in got]                              # the ruled table, the text and the noise are not figures
    for t in truth:
        assert max(_iou(g["bbox"], t) for g in got) >= 0.6


@need_tess
@pytest.mark.parametrize("name,draw", [
    ("text", lambda pg: (pg.insert_text((60, 60), "Annual report", fontsize=16), [_paras(pg, 100 + 0, 1)] and [pg.insert_text((60, 100 + k * 16), _LINE, fontsize=9) for k in range(40)], [])[2]),
    ("form", lambda pg: ([(pg.draw_rect(fitz.Rect(60, 100 + k * 55, 300, 130 + k * 55)), pg.insert_text((64, 96 + k * 55), f"Field {k} name", fontsize=8),
                          pg.draw_rect(fitz.Rect(320, 100 + k * 55, 520, 130 + k * 55)), pg.insert_text((324, 96 + k * 55), f"Date {k}", fontsize=8)) for k in range(10)], [])[1]),
])
def test_a_photographed_page_of_text_or_form_boxes_has_no_figures(tmp_path, name, draw):
    path, _ = _degraded_scan(tmp_path, name, draw, "photo", seed=5)
    assert _scan_figures(path) == []


@need_tess
@pytest.mark.parametrize("variant", ["clean", "photo"])
def test_a_block_diagram_on_a_scanned_page_is_found_even_when_tesseract_misreads_the_layout(tmp_path, variant):
    def diagram(pg):
        pg.insert_text((60, 60), "System overview", fontsize=16)
        _paras(pg, 90, 3)
        for i in range(3):
            x = 70 + i * 130
            pg.draw_rect(fitz.Rect(x, 200, x + 80, 250))
            pg.insert_text((x + 10, 230), f"Block {i + 1}", fontsize=8)
            if i:
                pg.draw_line((x - 50, 225), (x, 225))
                pg.draw_line((x - 6, 220), (x, 225))
                pg.draw_line((x - 6, 230), (x, 225))
        _paras(pg, 300, 6)
        return [[70, 200, 410, 250]]
    path, truth = _degraded_scan(tmp_path, "flow", diagram, variant, seed=5)
    work = path.replace(".pdf", ".work.pdf")
    pdf_pages.build_work_pdf(path, work, {1})
    assert len(pdf_pages.analyze_page(work, 1)["words"]) > 100                    # the sparse-text retry read the page (the first reading found a couple of words)
    got = _scan_figures(path)
    assert len(got) == 1 and _iou(got[0]["bbox"], truth[0]) >= 0.6


# ---- correcting a figure's reading by hand ------------------------------------------------------------------------------

def _read_chart(client, tmp_path, monkeypatch):
    _page_with(tmp_path / "chart.pdf", _bar_chart)
    wid = mk(client)
    eid = up(client, wid, tmp_path / "chart.pdf").json()["id"]
    wait(client, wid, eid)
    calls = []
    _enable(monkeypatch, CHART_REPLY, calls)
    base = f"/api/workspaces/{wid}/extractions/{eid}"
    assert client.post(f"{base}/figures/read", json={"page": 1, "index": 0}, headers=H).json()["read"] == 1
    return base, calls


def _points(client, base):
    fig = _figs(client.get(base, headers=H).json())[0]
    return fig, fig["reading"]["chart"]["series"][0]["points"]


def test_a_value_corrected_by_hand_is_changed_in_place_and_marked_with_no_model_call(client, tmp_path, monkeypatch):
    base, calls = _read_chart(client, tmp_path, monkeypatch)
    _, before = _points(client, base)
    assert before[0]["estimated"] is True and "manually_corrected" not in before[0]
    tokens_before = client.get(base, headers=H).json()["stats"]["usage"]["tokens"]

    assert client.patch(f"{base}/figures", json={"page": 1, "index": 0, "field": "s0.p0.y", "value": " 36 "}, headers=H).json() == {"ok": True}
    assert client.patch(f"{base}/figures", json={"page": 1, "index": 0, "field": "s0.name", "value": "Net revenue"}, headers=H).status_code == 200
    assert client.patch(f"{base}/figures", json={"page": 1, "index": 0, "field": "summary", "value": "Four quarters of revenue."}, headers=H).status_code == 200

    got = client.get(base, headers=H).json()
    fig, points = _points(client, base)
    assert (points[0]["y"], points[0]["value"], points[0]["estimated"], points[0]["manually_corrected"]) == ("36", 36.0, False, True)
    assert points[1]["y"] == before[1]["y"] and "manually_corrected" not in points[1]                  # nothing else moved
    series = fig["reading"]["chart"]["series"][0]
    assert series["name"] == "Net revenue" and series["manually_corrected"] is True
    assert fig["reading"]["summary"] == "Four quarters of revenue." and fig["reading"]["manually_corrected"] is True
    assert not [k for k in points[0] if k.startswith("original")] and "edited" not in fig["reading"]    # the part is updated; no extra fields are made
    assert len(calls) == 1 and got["stats"]["usage"]["tokens"] == tokens_before                         # the edits cost nothing
    # the same values are in the downloadable JSON
    sj = client.get(f"{base}/structure.json", headers=H).json()
    blocks = [b for d in sj["documents"] for pg in d["pages"] for b in pg["blocks"] if b["type"] == "figure"]
    assert blocks[0]["reading"]["chart"]["series"][0]["points"][0]["y"] == "36"
    pj = client.get(f"{base}/pages.json", headers=H).json()
    pfig = [b for p in pj["pages"] for b in p["structure"]["blocks"] if b["type"] == "figure"][0]
    assert pfig["reading"]["chart"]["series"][0]["points"][0]["y"] == "36" and pfig["reading"]["summary"] == "Four quarters of revenue."


def test_a_bad_edit_is_refused_and_reading_again_replaces_the_edits(client, tmp_path, monkeypatch):
    base, calls = _read_chart(client, tmp_path, monkeypatch)
    send = lambda **kw: client.patch(f"{base}/figures", json={"page": 1, "index": 0, **kw}, headers=H)       # noqa: E731
    assert send(field="chart", value="x").status_code == 422                       # not a part that can be edited
    assert send(field="s0.p0.y", value="   ").status_code == 422                  # blank: type a value
    assert send(field="s0.p9.y", value="1").status_code == 404                     # no such point
    assert send(field="s4.name", value="x").status_code == 404                     # no such series
    assert client.patch(f"{base}/figures", json={"page": 1, "index": 7, "field": "summary", "value": "x"}, headers=H).status_code == 404
    assert send(field="s0.p0.y", value="36").status_code == 200
    again = client.post(f"{base}/figures/read", json={"page": 1, "index": 0, "force": True}, headers=H).json()
    assert again["read"] == 1 and len(calls) == 2
    _, points = _points(client, base)
    assert points[0]["y"] == "35" and points[0]["estimated"] is True and "manually_corrected" not in points[0]     # the new reading, not the edit


def test_a_figure_that_has_not_been_read_cannot_be_edited(client, tmp_path):
    _page_with(tmp_path / "chart.pdf", _bar_chart)
    wid = mk(client)
    eid = up(client, wid, tmp_path / "chart.pdf").json()["id"]
    wait(client, wid, eid)
    r = client.patch(f"/api/workspaces/{wid}/extractions/{eid}/figures", json={"page": 1, "index": 0, "field": "summary", "value": "x"}, headers=H)
    assert r.status_code == 404


def test_read_again_asks_the_model_again_instead_of_returning_the_remembered_answer(tmp_path):
    p = _page_with(tmp_path / "chart.pdf", _bar_chart)
    fig = figures.find_digital(p, 1, [])[0]
    calls = []
    ask = _fake(CHART_REPLY, calls)
    asyncio.run(figures.read(p, 1, fig["bbox"], fig["labels"], "m3", ask))
    asyncio.run(figures.read(p, 1, fig["bbox"], fig["labels"], "m3", ask))
    assert len(calls) == 1                                                    # remembered
    asyncio.run(figures.read(p, 1, fig["bbox"], fig["labels"], "m3", ask, fresh=True))
    assert len(calls) == 2                                                    # asked to read again: the model is asked again


DIAGRAM_REPLY = {"kind": "diagram", "title": "Transformer", "summary": "Encoder and decoder stacks.",
                 "chart": {"type": "bar", "x_axis": {"label": "Quarter", "unit": None}, "y_axis": {"label": "Revenue", "unit": "USD m"},
                           "series": [{"name": "Revenue", "points": [{"x": "Q1", "y": "35", "printed": False}, {"x": "Q2", "y": "62", "printed": False}]}]},
                 "components": [{"name": "Input Embedding", "role": "tokens to vectors"}, {"name": "Softmax", "role": None}, {"name": "Linear", "role": None}],
                 "connections": [{"from": "Inputs", "to": "Input Embedding", "label": None}, {"from": "Linear", "to": "Softmaxx", "label": "logits"},
                                 {"from": "Softmax", "to": "Outputs", "label": None}],
                 "text": [], "limits": "Some arrows are implied."}


def test_every_part_of_a_reading_can_be_corrected_and_a_wrong_item_removed(client, tmp_path, monkeypatch):
    _page_with(tmp_path / "chart.pdf", _bar_chart)
    wid = mk(client)
    eid = up(client, wid, tmp_path / "chart.pdf").json()["id"]
    wait(client, wid, eid)
    calls = []
    _enable(monkeypatch, DIAGRAM_REPLY, calls)
    base = f"/api/workspaces/{wid}/extractions/{eid}"
    client.post(f"{base}/figures/read", json={"page": 1, "index": 0}, headers=H)
    send = lambda **kw: client.patch(f"{base}/figures", json={"page": 1, "index": 0, **kw}, headers=H)       # noqa: E731
    reading = lambda: _figs(client.get(base, headers=H).json())[0]["reading"]                                  # noqa: E731

    for field, value in (("title", "The Transformer"), ("limits", "Arrows are implied."), ("xaxis.label", "Fiscal quarter"), ("yaxis.unit", "USD millions"),
                         ("c0.name", "Input embedding"), ("c0.role", "Turns tokens into vectors"), ("c1.role", "Probabilities"),
                         ("l1.to", "Softmax"), ("l1.label", "scores"), ("l0.from", "Inputs (shifted)")):
        assert send(field=field, value=value).status_code == 200, field
    r = reading()
    assert r["title"] == "The Transformer" and r["limits"] == "Arrows are implied." and r["manually_corrected"] is True
    assert r["chart"]["x_axis"]["label"] == "Fiscal quarter" and r["chart"]["x_axis"]["manually_corrected"] is True and r["chart"]["y_axis"]["unit"] == "USD millions"
    assert r["components"][0]["name"] == "Input embedding" and r["components"][0]["role"] == "Turns tokens into vectors" and r["components"][0]["manually_corrected"] is True
    assert r["components"][1]["role"] == "Probabilities" and "manually_corrected" not in r["components"][2]            # untouched stays untouched
    assert (r["connections"][1]["to"], r["connections"][1]["label"]) == ("Softmax", "scores") and r["connections"][0]["from"] == "Inputs (shifted)"

    assert send(field="c2", remove=True).status_code == 200 and send(field="l2", remove=True).status_code == 200      # a wrong part, a wrong connection
    assert send(field="s0.p1", remove=True).status_code == 200                                                        # a wrong chart point
    r = reading()
    assert [c["name"] for c in r["components"]] == ["Input embedding", "Softmax"] and len(r["connections"]) == 2
    assert [p["x"] for p in r["chart"]["series"][0]["points"]] == ["Q1"]
    assert send(field="c0", remove=True).status_code == 200 and send(field="c0", remove=True).status_code == 200
    assert reading()["components"] is None                                                                           # nothing left: no empty list

    assert send(field="summary", remove=True).status_code == 422          # only a part, a connection or a point can be removed
    assert send(field="c9", remove=True).status_code == 404
    assert send(field="c9.name", value="x").status_code == 404
    assert send(field="yaxis.label", value="  ").status_code == 422
    assert len(calls) == 1                                                # none of it called the model
