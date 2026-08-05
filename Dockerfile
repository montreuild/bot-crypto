# Crypto Bot — image API (FastAPI + moteur de trading)
# Python 3.14 requis (cf. requirements.txt).
#
# Build :
#   docker build -t crypto-bot:api .
# Run (paper, dev) :
#   docker run --rm -p 8000:8000 --env-file .env \
#     -v "$(pwd)/data:/app/data" -v "$(pwd)/logs:/app/logs" \
#     crypto-bot:api
#
# Les données OHLCV (Parquet) et la DB SQLite sont montées en volumes —
# rien n'est embarqué dans l'image (pas de jeu de démo).

FROM python:3.14-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dépendances système minimales (lxml, cryptography, lightgbm)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── deps Python (couche cacheable) ─────────────────────────────────────────
COPY requirements.txt .
RUN pip install -r requirements.txt

# ── code application ──────────────────────────────────────────────────
COPY app/ ./app/
COPY strategies/ ./strategies/
COPY config/ ./config/
COPY cli.py optimize_runner.py ./
COPY deploy/ ./deploy/

# Répertoires runtime (montés en volumes en prod)
RUN mkdir -p /app/data /app/logs /app/models

# Non-root
RUN useradd -m -u 10001 bot && chown -R bot:bot /app
USER bot

EXPOSE 8000

# Healthcheck API
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Par défaut : paper + API. Surcharger via CMD / docker-compose.
CMD ["python", "cli.py", "--paper"]
