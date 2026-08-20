# Crypto Bot — image API (FastAPI + moteur de trading)
# Python 3.14 requis (cf. requirements.txt).
#
# Usage simple :  scripts/docker-up.ps1   ou   bash scripts/docker-up.sh
# Build manuel :  docker compose build api
#
# ── Pourquoi un multi-stage ───────────────────────────────────────────────
# La version mono-stage embarquait `build-essential` (gcc, g++, headers) dans
# l'image finale : 307 Mo de compilateurs qu'un conteneur de trading n'exécute
# jamais. Ils restent nécessaires au BUILD — une dépendance sans wheel cp314
# manylinux doit pouvoir compiler — d'où l'étage `builder` qui les porte, et
# l'étage `runtime` qui ne reçoit que le venv déjà construit.
#
# Cibles :
#   runtime (défaut) — production, sans compilateurs ni outils de dev
#   test             — runtime + requirements-dev.txt (pytest, ruff, mypy…)

# ── base : le strict nécessaire à l'exécution ─────────────────────────────
FROM python:3.14-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Chemins stables dans le conteneur
    CRYPTO_BOT_HOME=/app \
    # Le venv construit par `builder` est copié ici et mis en tête du PATH.
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# libgomp1 : runtime OpenMP de LightGBM. curl : HEALTHCHECK.
# `build-essential` n'est PAS ici — il n'existe que dans l'étage `builder`.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── builder : compile ce qui doit l'être, dans un venv isolé ──────────────
FROM base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$VIRTUAL_ENV"

# Couche cacheable : ne se rejoue que si requirements.txt change.
COPY requirements.txt .
RUN pip install -r requirements.txt

# ── runtime : image de production ─────────────────────────────────────────
FROM base AS runtime

# Seul le venv traverse — pas gcc, pas les headers, pas le cache pip.
COPY --from=builder /opt/venv /opt/venv

# ── code + config embarqués (surchargeables par volumes en compose) ───────
COPY app/ ./app/
COPY strategies/ ./strategies/
# `recipes/` : recettes ML versionnées, lues par `app/ml/recipe.py::load_recipe`
# et par `features_catalog`. Elles n'étaient PAS copiées — ni ignorées par
# `.dockerignore`, simplement oubliées. Conséquence en production :
# `POST /api/ml/train` (et le dialog « Entraîner » du Laboratoire) échouait sur
# « recette absente », et 100 tests tombaient dans le conteneur.
COPY recipes/ ./recipes/
COPY config/ ./config/
COPY config.yaml ./
COPY cli.py optimize_runner.py pytest.ini ./
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

# ── test : image de CI, jamais déployée ───────────────────────────────────
# Repart de `runtime` pour tester EXACTEMENT ce qui part en production, en
# n'ajoutant que l'outillage. `tests/` est embarqué pour que l'image soit
# utilisable seule ; le profil compose `test` monte le répertoire par-dessus
# afin d'itérer sans rebuild.
FROM runtime AS test

USER root
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY tests/ ./tests/

# Deux fichiers hors du périmètre runtime, mais dont des tests dépendent.
# Sans eux, `docker compose --profile test` s'arrêtait à la COLLECTE — deux
# erreurs d'import qui interrompaient toute la suite, et non deux échecs
# isolés. Le défaut préexistait à la découpe en étages.
#   scripts/audit_param_space.py  → tests/test_audit_param_space.py
#   scripts/gen_frontend_types.py → tests/test_openapi_contracts.py, qui vérifie
#                                   que generated.ts ne dérive pas des schémas
#   frontend/next.config.mjs      → tests/test_legacy_redirects.py, qui compare
#                                   les 308 du front aux redirections backend
#   frontend/src/types/generated.ts → lu par ce même test de contrat
COPY scripts/audit_param_space.py scripts/gen_frontend_types.py ./scripts/
COPY frontend/next.config.mjs ./frontend/
COPY frontend/src/types/generated.ts ./frontend/src/types/

# `scripts/` doit être importable : le test fait `from audit_param_space import …`.
ENV PYTHONPATH=/app/scripts

RUN chown -R bot:bot /app
USER bot

CMD ["python", "-m", "pytest", "-q"]
