"""v2: paragraph edits on a real LibreOffice/Word-style PDF."""
import sys, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "container"))
import pymupdf
from edit_text import edit_pdf

CV = (HERE / "fixtures" / "cv.pdf").read_bytes()


def para_box(data, needles):
    """Union of the given phrases' boxes in PDF user space (like the client's paragraph box)."""
    page = pymupdf.open(stream=data, filetype="pdf")[0]
    inv = ~page.transformation_matrix
    r = pymupdf.Rect()
    for n in needles:
        r |= page.search_for(n)[0]
    a, b = r.tl * inv, r.br * inv
    return [min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y)]


def spans(data):
    page = pymupdf.open(stream=data, filetype="pdf")[0]
    return [s for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"] if s["text"].strip()]


def text(data):
    return " ".join(pymupdf.open(stream=data, filetype="pdf")[0].get_text().split())


OLD_PARA = ("Căn cứ nhu cầu thực tế, Khối Khách hàng Ưu tiên đề nghị điều chỉnh thời gian áp dụng chương trình quà tặng "
            "dành cho khách hàng đặc biệt đến hết ngày 31 tháng 12 năm 2026, đồng thời bổ sung danh mục quà tặng theo "
            "phụ lục đính kèm văn bản này.")


def test_justified_paragraph_rewraps_and_keeps_bold():
    new = OLD_PARA.replace("đến hết ngày 31 tháng 12 năm 2026", "đến hết ngày 31 tháng 03 năm 2027 theo đề xuất của các Đơn vị kinh doanh trên toàn hệ thống và ý kiến thống nhất của các Khối liên quan")
    out, n = edit_pdf(CV, [{"page": 0, "box": para_box(CV, ["Căn cứ nhu cầu", "văn bản này."]), "text": new, "align": "justify"}])
    assert n == 1
    t = text(out)
    assert "31 tháng 03 năm 2027 theo đề xuất của các Đơn vị kinh doanh" in t
    assert "năm 2026" not in t
    assert "Trân trọng." in t and "V/v" in t
    ss = spans(out)
    bold = [s for s in ss if "Ưu tiên" in s["text"]]
    assert bold and bold[0]["flags"] & 16
    # stays inside the original right edge (± rounding)
    orig_right = max(s["bbox"][2] for s in spans(CV) if s["origin"][1] < 210 and s["origin"][1] > 150)
    body = [s for s in ss if 150 < s["origin"][1] < 260 and "Trân" not in s["text"]]
    assert max(s["bbox"][2] for s in body) <= orig_right + 1
    # more text → one more line than before (5 instead of 4)
    assert len({round(s["origin"][1]) for s in body}) == 5


def test_two_line_bold_italic_title_edited_in_the_middle():
    old = "V/v : Điều chỉnh và Gia hạn thời gian chương trình quà tặng dành cho Khách hàng Đặc biệt định danh"
    new = old.replace("chương trình quà tặng", "chương trình quà tặng và ưu đãi")
    out, _ = edit_pdf(CV, [{"page": 0, "box": para_box(CV, ["V/v : Điều chỉnh", "định danh"]), "text": new, "align": "left"}])
    assert "chương trình quà tặng và ưu đãi dành cho Khách hàng Đặc biệt định danh" in text(out)
    for s in spans(out):
        if 100 < s["origin"][1] < 140:
            assert s["flags"] & 16 and s["flags"] & 2, s


def test_table_cell_wraps_at_the_cell_border():
    out, _ = edit_pdf(CV, [{"page": 0, "box": para_box(CV, ["45.000.000"]), "text": "45.000.000 đồng, đã bao gồm thuế giá trị gia tăng và chi phí vận chuyển", "align": "left"}])
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    border = max(it[1].x for d in page.get_drawings() for it in d["items"] if it[0] == "l" and abs(it[1].x - it[2].x) < 0.5)
    cell = [s for s in spans(out) if s["origin"][1] > 270]
    assert max(s["bbox"][2] for s in cell) <= border
    assert len({round(s["origin"][1]) for s in cell}) >= 2  # wrapped inside the cell
    assert "45.000.000 đồng, đã bao gồm thuế" in text(out)
    assert "Thiết kế" in text(out)


def test_centred_line_stays_centred():
    before = [s for s in spans(CV) if "CỘNG HÒA" in s["text"]][0]
    out, _ = edit_pdf(CV, [{"page": 0, "box": para_box(CV, ["CỘNG HÒA"]), "text": "CỘNG HÒA XÃ HỘI", "align": "center"}])
    after = [s for s in spans(out) if s["text"].startswith("CỘNG HÒA")][0]
    mid = lambda s: (s["bbox"][0] + s["bbox"][2]) / 2
    assert abs(mid(before) - mid(after)) < 1.5
    assert after["flags"] & 16
