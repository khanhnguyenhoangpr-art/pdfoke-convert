"""Edit existing text in a PDF (D26, v2: paragraphs, like typing in Word).

An edit names a page, the box of a paragraph (PDF user space, as pdf.js reports it), the new
text and an alignment hint. The server:
  1. collects the characters of every line in the box with their style (font family, bold,
     italic, size, colour); a baseline is split where glyphs are far apart (table cells),
  2. maps the new text onto those styles with a character diff, so unchanged words keep their
     formatting and inserted words take the formatting of the text before them,
  3. removes the original glyphs (redaction that removes text only; images, table lines and
     backgrounds stay),
  4. lays the new text out again: same first-line and following-line indents, same line
     spacing, wrapped at the paragraph's right edge (one-line paragraphs may grow up to the next
     table line, the next text on the row or the page's text margin), aligned left, centre,
     right or justified.

Translation (mode "translate", PDF translation tool): the whole paragraph takes its dominant
style (a character diff between two languages means nothing), and the text is shrunk in 5%
steps (down to 60%) until it fits between the paragraph's top and the next text below it, so a
longer translation does not run over the following paragraph or table row.

Fonts: Liberation Serif / Sans / Mono, metric-compatible with Times New Roman / Arial /
Courier New and covering Vietnamese. Refuses digitally signed and encrypted PDFs.
"""
from __future__ import annotations

import difflib
from collections import Counter
from dataclasses import dataclass, field, replace

import pymupdf

FONT_DIR = "/usr/share/fonts/truetype/liberation"
MAX_EDITS = 500
MAX_TRANSLATE_EDITS = 4000  # a translated document sends every paragraph (30 pages at most)
MAX_TEXT = 5000


class EditError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


# ---------- styles ----------

@dataclass(frozen=True)
class Style:
    family: str  # serif | sans | mono
    bold: bool
    italic: bool
    size: float
    color: int

    @property
    def rgb(self) -> tuple[float, float, float]:
        c = self.color
        return (((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255)

    @property
    def fontname(self) -> str:
        return f"pdfoke-{self.family}{int(self.bold)}{int(self.italic)}"

    @property
    def fontfile(self) -> str:
        name = {"serif": "LiberationSerif", "sans": "LiberationSans", "mono": "LiberationMono"}[self.family]
        suffix = ("Bold" if self.bold else "") + ("Italic" if self.italic else "")
        return f"{FONT_DIR}/{name}-{suffix or 'Regular'}.ttf"


_FONTS: dict[str, pymupdf.Font] = {}


def _width(text: str, st: Style) -> float:
    f = _FONTS.get(st.fontfile)
    if f is None:
        f = _FONTS[st.fontfile] = pymupdf.Font(fontfile=st.fontfile)
    return f.text_length(text, fontsize=st.size)


def _family(font: str, flags: int) -> str:
    f = font.lower()
    if flags & 8 or any(k in f for k in ("courier", "mono", "consol")):
        return "mono"
    if any(k in f for k in ("arial", "helv", "sans", "verdana", "tahoma", "calibri", "segoe", "roboto", "vnarial", "vnhelv", "univers")):
        return "sans"
    if flags & 4 or any(k in f for k in ("times", "serif", "roman", "georgia", "cambria", "garamond", "vntime")):
        return "serif"
    return "sans"


def _style_of(span: dict) -> Style:
    font, flags = span["font"], span["flags"]
    low = font.lower()
    return Style(
        family=_family(font, flags),
        bold=bool(flags & 16) or "bold" in low or "black" in low,
        italic=bool(flags & 2) or "italic" in low or "oblique" in low,
        size=round(span["size"], 2),
        color=span["color"],
    )


# ---------- reading the page ----------

@dataclass
class Line:
    baseline: float
    x0: float
    x1: float
    size: float
    chars: list[tuple[str, Style]] = field(default_factory=list)


def _page_lines(page: pymupdf.Page) -> list[Line]:
    """Horizontal text by baseline, left to right, split where glyphs are far apart (MuPDF space).

    Word often stores Vietnamese letters in a second font, so one visual line is several spans;
    they are joined here regardless of font.
    """
    pieces = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            if abs(line["dir"][1]) > 1e-3 or line["dir"][0] < 0:
                continue
            for span in line["spans"]:
                st = _style_of(span)
                for ch in span["chars"]:
                    if ch["c"]:
                        pieces.append((span["origin"][1], ch["bbox"][0], ch["bbox"][2], ch["c"], st))
    pieces.sort(key=lambda p: (round(p[0], 1), p[1]))
    lines: list[Line] = []
    cur: Line | None = None
    last_x1 = 0.0
    for base, cx0, cx1, c, st in pieces:
        same_row = cur is not None and abs(cur.baseline - base) < 0.25 * st.size
        if c.isspace():
            if same_row and cur.chars and not cur.chars[-1][0].isspace():
                cur.chars.append((" ", cur.chars[-1][1]))
            continue
        if same_row and cx0 - last_x1 <= 1.2 * st.size:
            if cx0 - last_x1 > 0.15 * st.size and not cur.chars[-1][0].isspace():
                cur.chars.append((" ", cur.chars[-1][1]))
            cur.chars.append((c, st))
            cur.x1 = max(cur.x1, cx1)
            cur.size = max(cur.size, st.size)
        else:
            if cur is not None:
                while cur.chars and cur.chars[-1][0].isspace():
                    cur.chars.pop()
            cur = Line(base, cx0, cx1, st.size, [(c, st)])
            lines.append(cur)
        last_x1 = cx1
    for ln in lines:
        while ln.chars and ln.chars[-1][0].isspace():
            ln.chars.pop()
    return [l for l in lines if l.chars]


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


def _vertical_edges(page: pymupdf.Page) -> list[tuple[float, float, float]]:
    """Vertical table lines and cell edges as (x, y_top, y_bottom)."""
    out = []
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001
        return out
    for d in drawings:
        for it in d.get("items", []):
            if it[0] == "l":
                p, q = it[1], it[2]
                if abs(p.x - q.x) < 0.5 and abs(p.y - q.y) > 3:
                    out.append((p.x, min(p.y, q.y), max(p.y, q.y)))
            elif it[0] == "re":
                r = it[1]
                if r.height > 3:
                    if r.width < 1.5:
                        out.append(((r.x0 + r.x1) / 2, r.y0, r.y1))
                    else:
                        out.append((r.x0, r.y0, r.y1))
                        out.append((r.x1, r.y0, r.y1))
    return out


def _right_limit(edges, lines: list[Line], ln: Line, right: float, margin: float) -> float:
    limit = max(margin, right)
    for ex, ey0, ey1 in edges:
        if ey0 - 1 <= ln.baseline <= ey1 + 1 and ex > right - 0.5:
            limit = min(limit, ex - 0.25 * ln.size)
    for o in lines:
        if o is not ln and abs(o.baseline - ln.baseline) < 0.5 * ln.size and o.x0 > right:
            limit = min(limit, o.x0 - 0.5 * ln.size)
    return max(limit, right)


def _left_limit(edges, lines: list[Line], ln: Line, margin: float) -> float:
    limit = min(margin, ln.x0)
    for ex, ey0, ey1 in edges:
        if ey0 - 1 <= ln.baseline <= ey1 + 1 and ex < ln.x0 + 0.5:
            limit = max(limit, ex + 0.25 * ln.size)
    for o in lines:
        if o is not ln and abs(o.baseline - ln.baseline) < 0.5 * ln.size and o.x1 < ln.x0:
            limit = max(limit, o.x1 + 0.5 * ln.size)
    return min(limit, ln.x0)


# ---------- mapping new text onto old styles ----------

def _restyle(old: list[tuple[str, Style]], new_text: str) -> list[tuple[str, Style]]:
    old_text = "".join(c for c, _ in old)
    sm = difflib.SequenceMatcher(a=old_text, b=new_text, autojunk=False)
    styles: list[Style | None] = [None] * len(new_text)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(j2 - j1):
                styles[j1 + k] = old[i1 + k][1]
        elif tag == "replace":
            for k in range(j2 - j1):
                styles[j1 + k] = old[min(i1 + k, i2 - 1)][1]
    last = old[0][1]
    for i, s in enumerate(styles):
        if s is None:
            styles[i] = last
        else:
            last = s
    return list(zip(new_text, styles))  # type: ignore[arg-type]


def _dominant(old: list[tuple[str, Style]], new_text: str) -> list[tuple[str, Style]]:
    counts = Counter(st for c, st in old if not c.isspace())
    st = counts.most_common(1)[0][0] if counts else old[0][1]
    return [(c, st) for c in new_text]


# ---------- layout ----------

def _runs(word: list[tuple[str, Style]]) -> list[tuple[str, Style]]:
    runs: list[tuple[str, Style]] = []
    for c, st in word:
        if runs and runs[-1][1] == st:
            runs[-1] = (runs[-1][0] + c, st)
        else:
            runs.append((c, st))
    return runs


def _word_width(word) -> float:
    return sum(_width(t, st) for t, st in _runs(word))


def layout(chars, first_x: float, rest_x: float, right: float, base0: float, leading: float, align: str):
    """Place styled text: returns [(x, baseline, text, style)] and the number of lines."""
    tokens: list = []
    cur: list = []
    for c, st in chars:
        if c == "\n" or c.isspace():
            if cur:
                tokens.append(cur)
                cur = []
            if c == "\n":
                tokens.append("\n")
        else:
            cur.append((c, st))
    if cur:
        tokens.append(cur)

    rows: list[tuple[list, bool]] = []
    row: list = []
    width = 0.0
    start = first_x
    for tok in tokens:
        if tok == "\n":
            rows.append((row, True))
            row, width, start = [], 0.0, rest_x
            continue
        w = _word_width(tok)
        add = w if not row else _width(" ", tok[0][1]) + w
        if row and start + width + add > right + 0.5:
            rows.append((row, False))
            row, width, start = [tok], w, rest_x
        else:
            row.append(tok)
            width += add
    rows.append((row, True))

    placed = []
    for n, (words, hard) in enumerate(rows):
        if not words:
            continue
        base = base0 + n * leading
        left = first_x if n == 0 else rest_x
        widths = [_word_width(w) for w in words]
        spaces = [_width(" ", w[0][1]) for w in words[1:]]
        natural = sum(widths) + sum(spaces)
        avail = right - left
        extra = 0.0
        if align == "center":
            x = left + max(0.0, (avail - natural) / 2)
        elif align == "right":
            x = left + max(0.0, avail - natural)
        else:
            x = left
            if align == "justify" and not hard and len(words) > 1 and natural < avail:
                extra = (avail - natural) / (len(words) - 1)
        for i, w in enumerate(words):
            runs = _runs(w)
            if i < len(words) - 1:
                # write the real space glyph so copy/paste and search keep word breaks
                t, st = runs[-1]
                runs[-1] = (t + " ", st)
            for j, (text, st) in enumerate(runs):
                placed.append((x, base, text, st))
                x += _width(text.rstrip(" ") if j == len(runs) - 1 and i < len(words) - 1 else text, st)
            if i < len(words) - 1:
                x += spaces[i] + extra
    return placed, len(rows)


def fit_layout(chars, first_x: float, rest_x: float, right: float, base0: float, leading: float, align: str,
               top: float, bound: float):
    """Like layout(), shrinking the text (and line spacing) until the last line sits above `bound`."""
    placed: list = []
    for step in range(9):  # 100%, 95% … 60%
        k = 1 - 0.05 * step
        scaled = [(c, replace(st, size=round(st.size * k, 2))) for c, st in chars] if step else chars
        b0 = top + (base0 - top) * k
        placed, n = layout(scaled, first_x, rest_x, right, b0, leading * k, align)
        size = max((st.size for _, st in scaled), default=0)
        if b0 + (n - 1) * leading * k + 0.22 * size <= bound:
            return placed
    return placed


# ---------- main ----------

def _paragraph(lines: list[Line], rect: pymupdf.Rect) -> list[Line]:
    return sorted(
        (l for l in lines if rect.y0 - 1 <= l.baseline <= rect.y1 + 1 and l.x1 > rect.x0 + 0.5 and l.x0 < rect.x1 - 0.5),
        key=lambda l: l.baseline,
    )


def edit_pdf(data: bytes, edits: list[dict]) -> tuple[bytes, int]:
    """Apply paragraph edits; returns (pdf bytes, number of edits applied)."""
    if not isinstance(edits, list) or not edits:
        raise EditError("no-edits")
    translating = all(isinstance(e, dict) and e.get("mode") == "translate" for e in edits)
    if len(edits) > (MAX_TRANSLATE_EDITS if translating else MAX_EDITS):
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
                align = str(e.get("align", "left"))
            except (KeyError, TypeError, ValueError):
                raise EditError("bad-request")
            if p < 0 or p >= doc.page_count or len(box) != 4 or len(text) > MAX_TEXT:
                raise EditError("bad-request")
            if align not in ("left", "center", "right", "justify"):
                align = "left"
            by_page.setdefault(p, []).append({"box": box, "text": text.replace("\r", ""), "align": align, "translate": e.get("mode") == "translate"})

        applied = 0
        for pno, items in by_page.items():
            page = doc[pno]
            rotation = page.rotation
            if rotation:
                page.set_rotation(0)
            tm = page.transformation_matrix
            lines = _page_lines(page)
            edges = _vertical_edges(page)
            margin_r = max((l.x1 for l in lines), default=page.rect.width - 72)
            margin_l = min((l.x0 for l in lines), default=72)
            bottom = page.rect.height - 18
            plans = []
            for it in items:
                x0, y0, x1, y1 = it["box"]
                r = pymupdf.Rect(pymupdf.Point(x0, y0) * tm, pymupdf.Point(x1, y1) * tm)
                r.normalize()
                para = _paragraph(lines, r)
                if not para:
                    continue
                old: list[tuple[str, Style]] = []
                for ln in para:
                    if old:
                        old.append((" ", old[-1][1]))
                    old.extend(ln.chars)
                first_x = para[0].x0
                rest_x = min((l.x0 for l in para[1:]), default=first_x)
                right = max(l.x1 for l in para)
                align = it["align"]
                if len(para) == 1:
                    ln = para[0]
                    lim_r = _right_limit(edges, lines, ln, right, margin_r)
                    if align == "center":
                        mid = (ln.x0 + ln.x1) / 2
                        half = max(min(lim_r - mid, mid - _left_limit(edges, lines, ln, margin_l)), (ln.x1 - ln.x0) / 2)
                        first_x = rest_x = mid - half
                        right = mid + half
                    elif align == "right":
                        first_x = rest_x = _left_limit(edges, lines, ln, margin_l)
                    else:
                        right = lim_r
                # several pieces can share a baseline (a table row split at wide gaps): count distinct baselines
                bases = sorted({round(l.baseline, 1) for l in para})
                leading = (bases[-1] - bases[0]) / (len(bases) - 1) if len(bases) > 1 else 1.2 * para[0].size
                for ln in para:
                    band = pymupdf.Rect(ln.x0, ln.baseline - 0.55 * ln.size, ln.x1, ln.baseline - 0.15 * ln.size)
                    page.add_redact_annot(band, fill=False, cross_out=False)
                fit = None
                if it["translate"]:
                    # room below: up to the top of the nearest text under this paragraph in the same columns
                    left = min(first_x, rest_x)
                    under = [l.baseline - 0.8 * l.size for l in lines
                             if l.baseline > para[-1].baseline + 0.5 and l.x1 > left + 0.5 and l.x0 < right - 0.5 and l not in para]
                    fit = (para[0].baseline - 0.8 * para[0].size, min(under, default=bottom))
                restyled = _dominant(old, it["text"]) if it["translate"] else _restyle(old, it["text"])
                plans.append((restyled, first_x, rest_x, right, para[0].baseline, leading, align, fit))
            if not plans:
                if rotation:
                    page.set_rotation(rotation)
                continue
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
            for chars, fx, rx, right, base, leading, align, fit in plans:
                if chars:
                    if fit:
                        placed = fit_layout(chars, fx, rx, right, base, leading, align, *fit)
                    else:
                        placed, _ = layout(chars, fx, rx, right, base, leading, align)
                    for x, y, text, st in placed:
                        page.insert_text((x, y), text, fontsize=st.size, fontname=st.fontname, fontfile=st.fontfile, color=st.rgb)
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
