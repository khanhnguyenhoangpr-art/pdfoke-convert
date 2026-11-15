"""pdfoke-convert container: Office ↔ PDF conversions.

POST /convert/{kind}  body = file bytes, header X-Filename = original name
  kind: pdf-to-word | word-to-pdf | excel-to-pdf | ppt-to-pdf
Files live only in a per-request temporary directory that is deleted before the
response is sent. Nothing is logged except kind, size and duration.
"""
import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

MAX_BYTES = 25 * 1024 * 1024
TIMEOUT_S = 180

KINDS = {
    "pdf-to-word": ({".pdf"}, ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "word-to-pdf": ({".doc", ".docx", ".odt", ".rtf"}, ".pdf", "application/pdf"),
    "excel-to-pdf": ({".xls", ".xlsx", ".ods", ".csv"}, ".pdf", "application/pdf"),
    "ppt-to-pdf": ({".ppt", ".pptx", ".odp"}, ".pdf", "application/pdf"),
}

app = FastAPI()
# One conversion at a time per container: LibreOffice and pdf2docx are memory hungry,
# and the Worker spreads load across up to 2 containers.
_lock = asyncio.Semaphore(1)
_LO_PROFILE = "file:///tmp/lo-profile"


def err(status: int, code: str, detail: str = "") -> JSONResponse:
    return JSONResponse({"code": code, "detail": detail[:300]}, status_code=status)


def _pdf_to_word(src: Path, out: Path) -> None:
    import pymupdf
    from pdf2docx import Converter

    with pymupdf.open(src) as d:
        if d.needs_pass:
            raise PermissionError("encrypted")
    cv = Converter(str(src))
    try:
        # Office PDFs are single-column. pdf2docx's column detection mistakes the
        # two-column letterhead (company / national motto) for a multi-column
        # layout and emits multi-column section breaks, which makes Word reflow
        # text across pages. A huge minimum section height disables it.
        cv.convert(str(out), min_section_height=10000)
    finally:
        cv.close()


async def _libreoffice_to_pdf(src: Path, outdir: Path) -> Path:
    proc = await asyncio.create_subprocess_exec(
        "soffice", "--headless", "--norestore", "--nolockcheck",
        f"-env:UserInstallation={_LO_PROFILE}",
        "--convert-to", "pdf", "--outdir", str(outdir), str(src),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    out = outdir / (src.stem + ".pdf")
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError((stderr or b"").decode(errors="ignore") or "conversion failed")
    return out


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/convert/{kind}")
async def convert(kind: str, request: Request):
    if kind not in KINDS:
        return err(404, "unsupported", kind)
    allowed, out_ext, mime = KINDS[kind]
    name = request.headers.get("x-filename", "file")
    ext = Path(name).suffix.lower()
    if ext not in allowed:
        return err(415, "unsupported", ext)
    body = await request.body()
    if not body:
        return err(400, "empty")
    if len(body) > MAX_BYTES:
        return err(413, "too-large")

    started = time.monotonic()
    size_in = len(body)
    tmp = Path(tempfile.mkdtemp(prefix="cv-"))
    try:
        src = tmp / f"input{ext}"
        src.write_bytes(body)
        del body
        async with _lock:
            if kind == "pdf-to-word":
                out = tmp / "output.docx"
                await asyncio.wait_for(asyncio.to_thread(_pdf_to_word, src, out), TIMEOUT_S)
            else:
                out = await _libreoffice_to_pdf(src, tmp)
        data = out.read_bytes()
    except PermissionError:
        return err(422, "encrypted")
    except asyncio.TimeoutError:
        return err(504, "timeout")
    except Exception as e:  # noqa: BLE001 - report a code, never the file content
        return err(422, "corrupt", str(e).replace(str(tmp), ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # delete immediately, before responding

    print(f"{kind} in={size_in} out={len(data)} ms={int((time.monotonic()-started)*1000)}", flush=True)
    stem = Path(name).stem or "file"
    return Response(
        data,
        media_type=mime,
        headers={"x-output-name": (stem + out_ext).encode("utf-8").decode("latin-1", "ignore")},
    )
