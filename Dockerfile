FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/data/hf-cache

WORKDIR /app
RUN useradd --create-home --uid 1000 app

COPY requirements.txt .
# CPU-only PyTorch keeps the image several GB smaller than the default CUDA build.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install -r requirements.txt

COPY app ./app
COPY evaluation ./evaluation
COPY data/samples ./data/samples
COPY data/eval ./data/eval
RUN mkdir -p data/uploads data/chroma data/hf-cache && chown -R app:app /app

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
