FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
RUN python -m pip install --upgrade pip && \
    python -m pip install "openai-agents[litellm]>=0.8.0" "pydantic>=2.10" \
      "python-dotenv>=1.0" "fastapi>=0.115" "uvicorn>=0.34" \
      "python-multipart>=0.0.20" "temporalio>=1.30,<2" \
      "psycopg[binary,pool]>=3.3,<4"

# Keep slow dependency resolution cached when only application code changes.
COPY src ./src
COPY config ./config
RUN python -m pip install . --no-deps

RUN groupadd --system company && \
    useradd --system --gid company --home-dir /app company && \
    mkdir -p /data && chown -R company:company /app /data

USER company

EXPOSE 8421

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8421/health', timeout=2); raise SystemExit(0 if r.status == 200 else 1)"

CMD ["digital-company-web"]
