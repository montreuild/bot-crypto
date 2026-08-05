# Crypto Bot — image API (FastAPI + moteur de trading)
# Python 3.14 requis (cf. requirements.txt).
#
# Usage simple :  scripts/docker-up.ps1   ou   bash scripts/docker-up.sh
# Build manuel :  docker compose build api

FROM python:3.14-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Chemins stables dans le conteneur
    CRYPTO_BOT_HOME=/app

WORKDIR /app

# Dépendances système minimales (cryptography, lightgbm, healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── deps Python (couche cacheable) ─────────────────────────────────────────
COPY requirements.txt .
RUN pip install -r requirements.txt

# ── code + config embarqués (surchargeables par volumes en compose) ───────
COPY app/ ./app/
COPY strategies/ ./strategies/
COPY config/ ./config/
COPY config.yaml ./
COPY cli.py optimize_runner.py pytest.ini ./
COPY tests/ ./tests/
COPY deploy/ ./deploy/
COPY docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/data /app/logs /app/models \
    && useradd -m -u 10001 bot \
    && chown -R bot:bot /app

USER bot

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
# Paper + API par défaut (sûr pour un premier démarrage).
CMD ["python", "cli.py", "--paper"]
