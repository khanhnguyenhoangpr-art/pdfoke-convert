import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "container"))
from fastapi.testclient import TestClient
import app as appmod
from test_edit_text import make, pdf_box

client = TestClient(appmod.app)


def post(body, name="hop-dong.pdf"):
    return client.post("/convert/edit-text", content=body, headers={"x-filename": name})


def test_edit_text_endpoint():
    src = make()
    edits = [{"page": 0, "box": pdf_box(src, "CÔNG TY ABC"), "text": "CÔNG TY XYZ"}]
    r = post(json.dumps(edits).encode() + b"\n" + src)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["x-output-name"] == "hop-dong.pdf"
    assert r.content[:5] == b"%PDF-"


def test_edit_text_errors():
    src = make()
    assert post(b"not json\n" + src).json()["code"] == "bad-request"
    assert post(src).status_code == 400
    r = post(json.dumps([{"page": 0, "box": [500, 800, 590, 840], "text": "x"}]).encode() + b"\n" + src)
    assert (r.status_code, r.json()["code"]) == (422, "not-found")
    assert post(b"[]\n" + src, name="x.docx").status_code == 415
