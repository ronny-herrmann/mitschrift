FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY bench ./bench
COPY scripts ./scripts

ENV DATA_DIR=/data \
    MODELS_DIR=/models \
    PYTHONUNBUFFERED=1

VOLUME ["/data", "/models"]
EXPOSE 8000

# Nicht als root laufen
RUN useradd --create-home --uid 1000 mitschrift && mkdir -p /data /models && chown -R mitschrift /data /models /app
USER mitschrift

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
