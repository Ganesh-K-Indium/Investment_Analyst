# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
#   - gcc, libpq-dev          : C build tools & PostgreSQL headers
#   - libjpeg-dev, libpng-dev,
#     zlib1g-dev               : Image processing libs (Pillow / kaleido)
#   - chromium, chromium-driver: Headless browser for kaleido chart rendering
#   - tesseract-ocr            : OCR engine required by pytesseract (ingestion)
# Extra Tesseract languages as Tesseract codes, e.g. --build-arg TESSERACT_LANGS="deu fra" (then TESSERACT_LANG=eng+deu+fra)
ARG TESSERACT_LANGS=""
RUN apt-get update && apt-get install -y \
    $(for l in $TESSERACT_LANGS; do printf 'tesseract-ocr-%s ' "$l"; done) \
    gcc \
    libpq-dev \
    libjpeg-dev \
    libpng-dev \
    zlib1g-dev \
    chromium \
    chromium-driver \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Set Chromium as the browser for Kaleido
ENV PYPPETEER_CHROMIUM_REVISION=1263111
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true

# Unbuffered stdout/stderr so log output (and any remaining print()) appears
# immediately in `docker logs` instead of sitting in Python's block buffer
# until it fills up or the process exits.
ENV PYTHONUNBUFFERED=1

# The MCP servers run from their own folders and import the `app` package, so put the code root on the path.
ENV PYTHONPATH=/app

# Explicit Tesseract binary path (TESSERACT_CMD, overridable at runtime)
# Debian slim puts tesseract at /usr/bin/tesseract
ENV TESSERACT_CMD=/usr/bin/tesseract

# Everything the app writes at runtime (filings, uploads, keys, logs) lives under here; mount a volume on it.
ENV DATA_DIR=/app/data

# Upgrade pip and install build tools
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Optional OCR engines, e.g. --build-arg OCR_ENGINES="rapidocr doctr" (also paddleocr; x86-64 only). Exact versions from
# requirements-ocr/<engine>.txt. Their models download on first use into DATA_DIR/docproc/models, on the mounted volume.
COPY requirements-ocr/ ./requirements-ocr/
ARG OCR_ENGINES=""
RUN if [ -n "$OCR_ENGINES" ]; then \
        apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/* && \
        for e in $(printf '%s\n' $OCR_ENGINES | grep -vx paddleocr) $(printf '%s\n' $OCR_ENGINES | grep -x paddleocr); do pip install --no-cache-dir -r "requirements-ocr/$e.txt" || exit 1; done; \
    fi

# Copy application source
COPY app/        ./app/
COPY rag/        ./rag/
COPY quant/      ./quant/
COPY ingestion/  ./ingestion/
COPY schemas/    ./schemas/
COPY alembic/    ./alembic/
COPY alembic.ini .

# Create the data directory so the app starts cleanly; mount a persistent volume here (docker-compose mounts ./data)
RUN mkdir -p /app/data/macro /app/data

# Copy entrypoint script and make it executable
COPY docker/ ./docker/
RUN chmod +x docker/*.sh

# The API. MCP servers (8565-8568, 8570) listen on loopback inside the container; the workers share its network.
EXPOSE 8000

# Start MCP servers (background), then the FastAPI app (it migrates the database on startup)
ENTRYPOINT ["./docker/entrypoint.sh"]
