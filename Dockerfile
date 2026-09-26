FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY bench ./bench
COPY scripts ./scripts

ENV DATA_DIR=/data \
    MODELS_DIR=/models \
    MITSCHRIFT_ENV_FILE=/app/.env \
    PYTHONUNBUFFERED=1

VOLUME ["/data", "/models"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=300s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
