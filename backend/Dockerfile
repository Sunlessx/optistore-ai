# OptiStore AI backend (FastAPI + scikit-learn + Sentence Transformers, CPU only)
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/data/hf-cache

WORKDIR /app

# curl: healthcheck; ffmpeg: video metadata + measured transcoding; tesseract: OCR of scanned PDFs/images
RUN apt-get update && apt-get install -y --no-install-recommends curl ffmpeg tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

# Run as an unprivileged user; /app/data holds the SQLite fallback, models and outputs.
RUN useradd --create-home --uid 1000 optistore && mkdir -p /app/data && chown -R optistore /app/data
USER optistore

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
