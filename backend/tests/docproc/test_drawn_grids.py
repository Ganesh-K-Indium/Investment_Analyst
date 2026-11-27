"""Tables drawn as separate line strokes (reading/drawn_grids.py)."""

import pymupdf

from app.docproc.services.reading import drawn_grids


def _page(cols, rows, x0=50, y0=100, w=60, h=20, bar=None):
    """A page with a grid drawn as one short stroke per cell edge, as some forms do."""
    doc = pymupdf.open()
    page = doc.new_page()
    for r in range(rows):
        for c in range(cols):
            x, y = x0 + c * w, y0 + r * h
            page.draw_line((x, y), (x + w, y), width=0.5)
            page.draw_line((x, y), (x, y + h), width=0.5)
    for c in range(cols):
        page.draw_line((x0 + c * w, y0 + rows * h), (x0 + c * w + w, y0 + rows * h), width=0.5)
    page.draw_line((x0 + cols * w, y0), (x0 + cols * w, y0 + rows * h), width=0.5)
    if bar:
        page.draw_rect(pymupdf.Rect(x0, y0 - 18, x0 + cols * w, y0 - 1), color=None, fill=(0, 0, 0))
        page.insert_text((x0 + 4, y0 - 6), bar, fontsize=9, color=(1, 1, 1))
    return page


def test_separate_strokes_become_one_grid_with_its_cells():
    page = _page(3, 4)
    (g,) = drawn_grids.find_grids(page, [])
    assert g.col_count == 3 and len(g.rows) == 4
    assert all(c is not None for r in g.rows for c in r.cells)


def test_two_sections_under_title_bars_are_two_grids_named_by_their_bars():
    page = _page(3, 3, y0=100, bar="Hepatitis B")
    # a second section further down the same page
    for r in range(4):
        for c in range(3):
            x, y = 50 + c * 60, 200 + r * 20
            page.draw_line((x, y), (x + 60, y), width=0.5)
            page.draw_line((x, y), (x, y + 20), width=0.5)
    for c in range(3):
        page.draw_line((50 + c * 60, 280), (110 + c * 60, 280), width=0.5)
    page.draw_line((230, 200), (230, 280), width=0.5)
    page.draw_rect(pymupdf.Rect(50, 182, 230, 199), color=None, fill=(0, 0, 0))
    page.insert_text((54, 194), "Rotavirus", fontsize=9, color=(1, 1, 1))
    words = [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]
    titles = [g.title for g in drawn_grids.find_grids(page, words)]
    assert "Hepatitis B" in titles and "Rotavirus" in titles


def test_a_lone_highlight_does_not_replace_a_real_table():
    """One dark frame on a page is a callout, not a record card: it may not take the place of the table the other reader found."""
    page = _page(3, 3, bar="YOUR RESPONSIBILITY")
    words = [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]
    grids = drawn_grids.find_grids(page, words)
    assert sum(1 for g in grids if g.title) < 2
