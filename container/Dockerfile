FROM python:3.12-slim

# LibreOffice (headless) + fonts that are metric-compatible with common Office fonts:
# Liberation (Times New Roman, Arial, Courier New), Carlito (Calibri), Caladea (Cambria).
# DejaVu/Noto cover Vietnamese diacritics and other scripts.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
      fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea \
      fonts-dejavu-core fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py edit_text.py ./

# Warm up LibreOffice's user profile at build time so the first conversion is faster.
RUN mkdir -p /tmp/warm && echo "warm" > /tmp/warm/w.txt \
    && soffice --headless --norestore -env:UserInstallation=file:///tmp/lo-profile \
       --convert-to pdf --outdir /tmp/warm /tmp/warm/w.txt >/dev/null 2>&1 || true \
    && rm -rf /tmp/warm

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
