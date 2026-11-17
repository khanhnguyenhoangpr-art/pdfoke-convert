import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "container"))
import pymupdf, pytest
from edit_text import edit_pdf, EditError

SERIF = "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"
SANSB = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"


def make(rotate=0):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(pymupdf.Rect(60, 90, 400, 160), color=None, fill=(0.85, 0.92, 1))  # coloured table cell
    page.insert_text((72, 110), "Số tiền: 1.250.000 đồng", fontsize=12, fontname="vns", fontfile=SERIF, color=(0, 0, 0))
    page.insert_text((72, 125), "Ngày 20 tháng 11 năm 2026", fontsize=12, fontname="vns", fontfile=SERIF)  # tight line spacing
    page.insert_text((72, 200), "CÔNG TY ABC", fontsize=16, fontname="vnb", fontfile=SANSB, color=(0.8, 0, 0))
    if rotate:
        page.set_rotation(rotate)
    return doc.tobytes()


def pdf_box(data, needle, pno=0):
    """Box of a text run in PDF user space, as pdf.js would give it."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    page = doc[pno]
    rot = page.rotation
    page.set_rotation(0)
    r = page.search_for(needle)[0]
    inv = ~page.transformation_matrix
    p0, p1 = r.tl * inv, r.br * inv
    page.set_rotation(rot)
    return [min(p0.x, p1.x), min(p0.y, p1.y), max(p0.x, p1.x), max(p0.y, p1.y)]


def text_of(data):
    return pymupdf.open(stream=data, filetype="pdf")[0].get_text()


def test_replaces_text_and_removes_the_old_glyphs():
    src = make()
    out, n = edit_pdf(src, [{"page": 0, "box": pdf_box(src, "1.250.000 đồng"), "text": "Số tiền: 2.000.000 đồng"}])
    t = text_of(out)
    assert n == 1
    assert "2.000.000" in t and "1.250.000" not in t
    assert "Ngày 20 tháng 11 năm 2026" in t  # neighbouring line untouched
    assert "CÔNG TY ABC" in t


def test_keeps_style_and_background():
    src = make()
    out, _ = edit_pdf(src, [{"page": 0, "box": pdf_box(src, "CÔNG TY ABC"), "text": "CÔNG TY XYZ"}])
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    spans = [s for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"] if "XYZ" in s["text"]]
    assert spans
    s = spans[0]
    assert abs(s["size"] - 16) < 0.1
    assert s["color"] == 0xCC0000
    assert s["flags"] & 16 or "Bold" in s["font"]
    assert abs(s["origin"][1] - 200) < 0.5 and abs(s["origin"][0] - 72) < 0.5
    # the blue cell drawing is still there
    assert any(d.get("fill") and abs(d["fill"][2] - 1) < 0.01 for d in page.get_drawings())


def test_rotated_page():
    src = make(rotate=90)
    out, n = edit_pdf(src, [{"page": 0, "box": pdf_box(src, "Ngày 20"), "text": "Ngày 21 tháng 11 năm 2026"}])
    doc = pymupdf.open(stream=out, filetype="pdf")
    assert n == 1 and doc[0].rotation == 90
    assert "Ngày 21" in doc[0].get_text() and "Ngày 20" not in doc[0].get_text()


def test_empty_text_deletes():
    src = make()
    out, _ = edit_pdf(src, [{"page": 0, "box": pdf_box(src, "CÔNG TY ABC"), "text": ""}])
    assert "CÔNG TY" not in text_of(out)


def test_errors():
    src = make()
    with pytest.raises(EditError) as e:
        edit_pdf(src, [{"page": 0, "box": [500, 800, 590, 840], "text": "x"}])
    assert e.value.code == "not-found"
    with pytest.raises(EditError) as e:
        edit_pdf(src, [{"page": 3, "box": [0, 0, 1, 1], "text": "x"}])
    assert e.value.code == "bad-request"
    with pytest.raises(EditError) as e:
        edit_pdf(src, [])
    assert e.value.code == "no-edits"


def test_signed_pdf_is_refused(monkeypatch):
    src = make()
    monkeypatch.setattr(pymupdf.Document, "get_sigflags", lambda self: 3)
    with pytest.raises(EditError) as e:
        edit_pdf(src, [{"page": 0, "box": pdf_box(src, "CÔNG TY ABC"), "text": "x"}])
    assert e.value.code == "signed"
