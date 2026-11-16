FROM python:3.13-slim

# FFmpeg (Videoschnitt) und eine Schrift für die Untertitel
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/tmp/autoshorts \
    MUSIC_DIR=/app/music

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY music ./music

# Nicht als root laufen
RUN useradd --create-home --uid 10001 appuser && mkdir -p /tmp/autoshorts && chown appuser /tmp/autoshorts
USER appuser

EXPOSE 10000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*'"]
