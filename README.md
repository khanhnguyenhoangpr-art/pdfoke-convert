# pdfoke-convert

The conversion service behind [pdfoke.com](https://pdfoke.com): PDF → Word, and Word / Excel /
PowerPoint → PDF. A Cloudflare Worker in front of a Cloudflare Container.

Published under the **GNU AGPL-3.0** because it uses [PyMuPDF](https://pymupdf.io)
(through [pdf2docx](https://github.com/ArtifexSoftware/pdf2docx)), which is AGPL-licensed.
If you use pdfoke.com's PDF ↔ Office tools, this repository is the complete source of
that service.

## How it works
- `src/index.ts` – Worker: checks a Cloudflare Turnstile token, a 25 MB size limit and a
  quota of 20 successful conversions per IP per day (Vietnam time), then forwards the file
  to one of at most 2 containers.
- `container/` – Python (FastAPI) service:
  - PDF → Word with pdf2docx
  - Word / Excel / PowerPoint → PDF with LibreOffice (headless), with metric-compatible fonts
    (Liberation, Carlito, Caladea) so layouts match Microsoft Office closely.
- Files exist only in a per-request temporary folder that is deleted **before** the response is
  sent. Nothing is stored and file contents are never logged.

## Develop
```sh
npm install
npm run check          # type-check + unit tests
# Container locally (needs LibreOffice and Python 3.12):
cd container && pip install -r requirements.txt && uvicorn app:app --port 8080
curl -H "x-filename: a.docx" --data-binary @a.docx localhost:8080/convert/word-to-pdf -o a.pdf
```
Deploys with Cloudflare Workers Builds (`npx wrangler deploy`), which builds the Dockerfile.
Secret: `TURNSTILE_SECRET`.

## License
AGPL-3.0-only, see [LICENSE](LICENSE).
