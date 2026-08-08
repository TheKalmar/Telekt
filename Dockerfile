FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config

RUN python -m pip install --upgrade pip && \
    python -m pip install .

RUN groupadd --system company && \
    useradd --system --gid company --home-dir /app company && \
    mkdir -p /data && chown -R company:company /app /data

USER company

EXPOSE 8421

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8421/health', timeout=2); raise SystemExit(0 if r.status == 200 else 1)"

CMD ["digital-company-web"]
