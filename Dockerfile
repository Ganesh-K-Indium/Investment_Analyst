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
RUN apt-get update && apt-get install -y \
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

# Explicit Tesseract binary path - overridable via ECS task definition env vars
# Debian slim puts tesseract at /usr/bin/tesseract
ENV TESSERACT_CMD=/usr/bin/tesseract

# Upgrade pip and install build tools
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/        ./app/
COPY rag/        ./rag/
COPY quant/      ./quant/
COPY ingestion/  ./ingestion/
COPY schemas/    ./schemas/
COPY alembic/    ./alembic/
COPY alembic.ini .
COPY static/     ./static/

# Copy entrypoint script and make it executable
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Expose main API + MCP server ports
EXPOSE 8000 8565 8566 8567

# Start MCP servers (background) then the main FastAPI app
ENTRYPOINT ["./docker-entrypoint.sh"]
