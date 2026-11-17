"""Edit existing text in a PDF (D26): remove the original characters, write the new text in place.

Each edit names a page and the box of one text run (PDF user space, as pdf.js reports it).
The original glyphs under that box are removed with a redaction that removes text only
(images and vector graphics are kept, nothing is painted over). The new text is written on
the same baseline with the closest metric-compatible font (Liberation Serif / Sans / Mono,
which cover Vietnamese), the original size and colour, bold and italic.

Refuses digitally signed PDFs: editing would break the signature (product rule D26).
"""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf

FONT_DIR = "/usr/share/fonts/truetype/liberation"
MAX_EDITS = 500
MAX_TEXT = 2000


class EditError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


@dataclass
class Style:
    size: float
    color: tuple[float, float, float]
    family: str  # serif | sans | mono
    bold: bool
    italic: bool
    origin: tuple[float, float]  # baseline start of the first matched span (MuPDF space)
    right: float


def _family(font: str, flags: int) -> str:
    f = font.lower()
    if flags & 8 or any(k in f for k in ("courier", "mono", "consol")):
        return "mono"
    if any(k in f for k in ("arial", "helv", "sans", "verdana", "tahoma", "calibri", "segoe", "roboto", "vnarial", "vnhelv", "univers")):
        return "sans"
    if flags & 4 or any(k in f for k in ("times", "serif", "roman", "georgia", "cambria", "garamond", "vntime")):
        return "serif"
    return "sans"


def _font_file(s: Style) -> str:
    name = {"serif": "LiberationSerif", "sans": "LiberationSans", "mono": "LiberationMono"}[s.family]
    style = ("Bold" if s.bold else "") + ("Italic" if s.italic else "")
    return f"{FONT_DIR}/{name}-{style or 'Regular'}.ttf"


def _rgb(c: int) -> tuple[float, float, float]:
    return (((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255)


def _is_signed(doc: pymupdf.Document) -> bool:
    try:
        if doc.get_sigflags() >= 1:
            return True
    except Exception:  # noqa: BLE001
        pass
    for page in doc:
        for w in page.widgets() or []:
            if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE and w.field_value:
                return True
    return False


def _spans_in(page: pymupdf.Page, rect: pymupdf.Rect) -> list[dict]:
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if abs(line["dir"][1]) > 1e-3:  # vertical or rotated text is not editable
                continue
            for span in line["spans"]:
                if not span["text"].strip():
                    continue
                b = pymupdf.Rect(span["bbox"])
                inter = b & rect
                if inter.is_empty:
                    continue
                # Same line: the span's baseline sits inside the requested box vertically.
                oy = span["origin"][1]
                if rect.y0 - 1 <= oy <= rect.y1 + 1 and inter.width > 0.3 * min(b.width, rect.width):
                    out.append(span)
    return sorted(out, key=lambda s: s["origin"][0])


def _style(spans: list[dict], rect: pymupdf.Rect) -> Style:
    s = spans[0]
    flags = s["flags"]
    font = s["font"]
    return Style(
        size=s["size"],
        color=_rgb(s["color"]),
        family=_family(font, flags),
        bold=bool(flags & 16) or "bold" in font.lower() or "black" in font.lower(),
        italic=bool(flags & 2) or "italic" in font.lower() or "oblique" in font.lower(),
        origin=(s["origin"][0], s["origin"][1]),
        right=max(sp["bbox"][2] for sp in spans),
    )


def edit_pdf(data: bytes, edits: list[dict]) -> tuple[bytes, int]:
    """Apply edits; returns (pdf bytes, number of edits applied)."""
    if not isinstance(edits, list) or not edits:
        raise EditError("no-edits")
    if len(edits) > MAX_EDITS:
        raise EditError("too-many-edits")
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        if doc.needs_pass:
            raise EditError("encrypted")
        if _is_signed(doc):
            raise EditError("signed")
        by_page: dict[int, list[dict]] = {}
        for e in edits:
            try:
                p = int(e["page"])
                box = [float(v) for v in e["box"]]
                text = str(e["text"])
            except (KeyError, TypeError, ValueError):
                raise EditError("bad-request")
            if p < 0 or p >= doc.page_count or len(box) != 4 or len(text) > MAX_TEXT:
                raise EditError("bad-request")
            by_page.setdefault(p, []).append({"box": box, "text": text.replace("\r", "").replace("\n", " ")})

        applied = 0
        for pno, items in by_page.items():
            page = doc[pno]
            rotation = page.rotation
            if rotation:
                page.set_rotation(0)
            tm = page.transformation_matrix  # PDF user space -> MuPDF (top-left, unrotated)
            plans = []
            for it in items:
                x0, y0, x1, y1 = it["box"]
                r = pymupdf.Rect(pymupdf.Point(x0, y0) * tm, pymupdf.Point(x1, y1) * tm)
                r.normalize()
                spans = _spans_in(page, r)
                if not spans:
                    continue
                st = _style(spans, r)
                # Remove only this line's glyphs: a band through the middle of the glyphs,
                # from the first to the last matched span, so neighbouring lines are untouched.
                base = st.origin[1]
                band = pymupdf.Rect(spans[0]["bbox"][0], base - 0.55 * st.size, st.right, base - 0.15 * st.size)
                page.add_redact_annot(band, fill=False, cross_out=False)
                plans.append((st, it["text"]))
            if not plans:
                if rotation:
                    page.set_rotation(rotation)
                continue
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
            for st, text in plans:
                if text.strip():
                    page.insert_text(
                        st.origin, text, fontsize=st.size, fontname=f"pdfoke-{st.family}{int(st.bold)}{int(st.italic)}",
                        fontfile=_font_file(st), color=st.color,
                    )
                applied += 1
            if rotation:
                page.set_rotation(rotation)
        if not applied:
            raise EditError("not-found")
        try:
            doc.subset_fonts()
        except Exception:  # noqa: BLE001 - subsetting is an optimisation only
            pass
        return doc.tobytes(garbage=3, deflate=True), applied
    finally:
        doc.close()
