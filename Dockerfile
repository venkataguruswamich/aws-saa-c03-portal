FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py auth.py index.html manifest.webmanifest sw.js ./
COPY icons ./icons
RUN useradd --create-home --uid 10001 appuser && mkdir -p /app/data && chown appuser /app/data
# Users, sessions and the audit log live in /app/data/portal.db. Mount a volume here to keep them.
VOLUME ["/app/data"]
USER appuser

EXPOSE 5000
# One worker = one monitor thread and one session store per container. No --preload (threads do not survive fork).
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "30", "app:app"]
