"""Synthetic fax-style packet used by the tests (no PHI): cover, blank, filled form, scan, exam, daily note."""
import io

import pymupdf as fitz
from PIL import Image

STAMP_H = "From Sample Health  1.201.555.0121   Wed Nov 11 09:21:38 2026 MST Page {n} of {t}"
STAMP_F = "PAGE {n}/{t} * RCVD AT 10/12/2026 11:21:43 AM [Eastern Standard Time] * SVR:FAX-MGA/0 * DNIS:6403 * CSID:201-447-5750 * ANI: 12015550121 * DURATION (mm-ss):07-04"


def stamp(page, n, t):
    page.insert_text((60, 20), STAMP_H.format(n=n, t=t), fontsize=7)
    page.insert_text((20, 830), STAMP_F.format(n=n, t=t), fontsize=5)


def lines(page, y, items, size=9):
    for text in items:
        page.insert_text((50, y), text, fontsize=size)
        y += 14
    return y


def box(page, x, y, checked):
    r = fitz.Rect(x, y - 8, x + 9, y + 1)
    page.draw_rect(r, width=0.8)
    if checked:
        page.draw_line(r.tl, r.br, width=1.6)
        page.draw_line(r.tr, r.bl, width=1.6)


def blank_scan_image(text_rows):
    """A page-sized raster (so the page counts as a scan) with no embedded text."""
    im = Image.new("RGB", (612, 842), "white")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def build(path, total=6):
    doc = fitz.open()
    p = doc.new_page(width=612, height=842)  # 1 cover
    stamp(p, 1, total)
    lines(p, 80, ["FAX", "From: Jordan Parker", "The information contained in this fax is legally privileged and confidential information", "intended only for the use of the individual named above."], 11)

    p = doc.new_page(width=612, height=842)  # 2 blank
    stamp(p, 2, total)

    p = doc.new_page(width=612, height=842)  # 3 form
    stamp(p, 3, total)
    p.insert_text((50, 60), "Provider Prior Authorization Request Form", fontsize=14)
    for x, y, label, on in [(50, 90, "ROUTINE", True), (150, 90, "URGENT", False), (250, 90, "TRANSPLANT", False)]:
        box(p, x, y, on)
        p.insert_text((x + 13, y), label, fontsize=10)
    p.insert_text((50, 130), "MEMBER INFORMATION", fontsize=10)
    lines(p, 150, ["Last Name*: Morgan", "First Name*: Taylor", "Date of Birth*: 07/01/1970", "Member ID #*: 9000001234"])
    p.insert_text((50, 230), "ORDERING/SERVICE PROVIDER INFORMATION", fontsize=10)
    lines(p, 250, ["Tax ID*: 99-9999999", "NPI*: 1999999999"])
    p.insert_text((50, 300), "FACILITY INFORMATION", fontsize=10)
    lines(p, 320, ["Facility Name*: SAMPLE HEALTH ASSOCIATES", "Tax ID*: 99-9999998", "NPI*: 199999999", "DX Code (1): M54.2"])
    p.insert_text((50, 400), "CPT/HCPCS", fontsize=10)

    p = doc.new_page(width=612, height=842)  # 4 scanned page (image only) -> goes to OCR
    p.insert_image(p.rect, stream=blank_scan_image([]))

    p = doc.new_page(width=612, height=842)  # 5 PT exam
    stamp(p, 5, total)
    lines(p, 60, ["Physical Therapy Initial Examination", "Patient Name: Morgan Taylor Date of Initial Examination: 08/12/2026",
                  "Date of Birth: 01/15/1975 Diagnosis: ICD10: M54.2: Cervicalgia", "Subjective", "Pain Location: L foot/ankle"])

    p = doc.new_page(width=612, height=842)  # 6 daily note
    stamp(p, 6, total)
    lines(p, 60, ["Daily Note / Billing Sheet", "Patient Name: Morgan Taylor Date of Daily Note: 08/12/2026",
                  "Date of Birth: 01/15/1975", "GP:97110 Therapeutic Exercise", "GP:97162 PT Evaluation"])
    doc.save(path)
    doc.close()
