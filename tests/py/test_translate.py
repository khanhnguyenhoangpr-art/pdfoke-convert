import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "container"))
import pymupdf
from edit_text import edit_pdf
from test_edit_text import pdf_box

SERIF = "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"
SERIFB = "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"


def make():
    """A heading, a two-line paragraph with one bold word, and a paragraph right under it."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 80), "SERVICE AGREEMENT", fontsize=16, fontname="hb", fontfile=SERIFB)
    page.insert_text((72, 120), "This agreement is made between the Seller and the", fontsize=12, fontname="r", fontfile=SERIF)
    page.insert_text((72, 134), "Buyer on the date written below.", fontsize=12, fontname="r", fontfile=SERIF)
    page.insert_text((300, 134), "Final", fontsize=12, fontname="b", fontfile=SERIFB)
    page.insert_text((72, 170), "Payment is due within thirty days.", fontsize=12, fontname="r", fontfile=SERIF)
    return doc.tobytes()


def spans(data):
    page = pymupdf.open(stream=data, filetype="pdf")[0]
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if s["text"].strip():
                    out.append((s["text"], s["size"], s["font"], s["bbox"]))
    return out


def union(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def test_translation_replaces_text_with_one_style_and_fits_above_the_next_paragraph():
    src = make()
    box = union(pdf_box(src, "This agreement"), pdf_box(src, "Final"))
    long_vi = ("Hợp đồng này được lập giữa Bên bán và Bên mua vào ngày ghi dưới đây, "
               "có hiệu lực kể từ ngày ký và được lập thành hai bản có giá trị pháp lý như nhau, "
               "mỗi bên giữ một bản để thực hiện.")
    edits = [
        {"page": 0, "box": pdf_box(src, "SERVICE AGREEMENT"), "text": "HỢP ĐỒNG DỊCH VỤ", "mode": "translate"},
        {"page": 0, "box": box, "text": long_vi, "align": "left", "mode": "translate"},
        {"page": 0, "box": pdf_box(src, "Payment is due"), "text": "Thanh toán trong vòng ba mươi ngày.", "mode": "translate"},
    ]
    out, n = edit_pdf(src, edits)
    assert n == 3
    s = spans(out)
    text = " ".join(t for t, *_ in s)
    assert "This agreement" not in text and "SERVICE" not in text and "Payment" not in text
    assert "HỢP ĐỒNG DỊCH VỤ" in text and "Thanh toán trong vòng ba mươi ngày." in text
    para = [x for x in s if x[0] not in ("HỢP ĐỒNG DỊCH VỤ", "Thanh toán trong vòng ba mươi ngày.")]
    # dominant style: the bold "Final" did not make any translated word bold
    assert all("Bold" not in f for _, _, f, _ in para)
    # shrunk to fit: the paragraph ends above the next paragraph's top (y 170 - 0.8*12)
    assert max(b[3] for *_, b in para) <= 170 - 0.8 * 12 + 1
    assert min(sz for _, sz, _, _ in para) < 12
    # the next paragraph kept its size
    nxt = [x for x in s if x[0].startswith("Thanh toán")][0]
    assert abs(nxt[1] - 12) < 0.01


def test_translate_allows_many_paragraphs():
    src = make()
    edits = [{"page": 0, "box": pdf_box(src, "Payment is due"), "text": "Thanh toán.", "mode": "translate"}] * 600
    out, n = edit_pdf(src, edits)
    assert n == 600
