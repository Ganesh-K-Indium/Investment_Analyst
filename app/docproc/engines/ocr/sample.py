"""A small clean page with a few labelled values, drawn here so engines and models can be tested without any file."""
import io
from typing import List

LINES: List[str] = ["Invoice No. 48213", "Date: 03/14/2026", "Total due $1,250.75", "Ref AB-9071"]
TOKENS: List[str] = ["48213", "03/14/2026", "1,250.75", "AB-9071"]


def page() -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (900, 420), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=40)
    for i, t in enumerate(LINES):
        d.text((50, 40 + i * 85), t, fill="black", font=font)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()


def found(text: str) -> float:
    """The share of the sample's values that appear in `text`."""
    compact = text.replace(" ", "").lower()
    return sum(1 for t in TOKENS if t.replace(" ", "").lower() in compact) / len(TOKENS)
