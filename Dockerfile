# The image Cloudflare runs as PixelRelay's container (wrangler.jsonc, deployed by
# .github/workflows/deploy.yml). It also runs on any other container host: without DATABASE_URL and
# STORAGE_PROVIDER=r2 it falls back to SQLite + files under /data (mount a volume there).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DATA_DIR=/data HOST=0.0.0.0 PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pixelrelay ./pixelrelay

RUN useradd --create-home --uid 10001 pixelrelay && mkdir -p /data && chown pixelrelay /data
USER pixelrelay
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz')"
CMD ["python", "-m", "pixelrelay"]
