#!/usr/bin/env bash
# Démarrage Docker du Crypto Bot (Linux / macOS / Git Bash / WSL).
#
# Usage :
#   bash scripts/docker-up.sh              # API paper (défaut)
#   bash scripts/docker-up.sh --full       # API + frontend
#   bash scripts/docker-up.sh --test       # pytest dans un conteneur
#   bash scripts/docker-up.sh --prod       # stack prod (compose.prod)
#   bash scripts/docker-up.sh --down       # arrête les services
#   bash scripts/docker-up.sh --build      # force rebuild
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FULL=0
TEST=0
PROD=0
DOWN=0
BUILD_FLAGS=(--build)
COMPOSE=(docker compose)
EXTRA=()

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --full|-f)   FULL=1 ;;
    --test|-t)   TEST=1 ;;
    --prod|-p)   PROD=1 ;;
    --down|-d)   DOWN=1 ;;
    --no-build)  BUILD_FLAGS=() ;;
    --build)     BUILD_FLAGS=(--build) ;;
    -h|--help)   usage ;;
    *)           EXTRA+=("$1") ;;
  esac
  shift
done

if ! command -v docker >/dev/null 2>&1; then
  echo "✖ Docker introuvable. Installez Docker Desktop / Engine." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "✖ Docker Compose v2 requis (docker compose …)." >&2
  exit 1
fi

# ── .env ──────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null \
      || python -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null \
      || openssl rand -base64 32 | tr -d '=\n+/')"
    # shellcheck disable=SC2002
    sed "s/change-me-generate-a-secret/${KEY}/" .env.example > .env
    echo "✓ .env créé avec une WEB_API_KEY aléatoire"
  else
    echo "WEB_API_KEY=$(openssl rand -hex 32)" > .env
    echo "ENV=dev" >> .env
    echo "✓ .env minimal créé"
  fi
fi

mkdir -p data logs models

if [ "$DOWN" -eq 1 ]; then
  ${COMPOSE[@]} down
  exit 0
fi

FILES=(-f docker-compose.yml)
if [ "$PROD" -eq 1 ]; then
  FILES+=(-f docker-compose.prod.yml)
  # Garde-fou : clé non placeholder + bind local
  # shellcheck disable=SC1091
  set -a; . ./.env; set +a
  if [ -z "${WEB_API_KEY:-}" ] || [ "$WEB_API_KEY" = "change-me-generate-a-secret" ]; then
    echo "✖ WEB_API_KEY manquante ou placeholder — refuse le mode prod." >&2
    exit 1
  fi
  export API_HOST_BIND="${API_HOST_BIND:-127.0.0.1}"
  export ENV="${ENV:-prod}"
  export ALLOW_INSECURE_WEB=0
fi

if [ "$TEST" -eq 1 ]; then
  echo "→ Tests (profile test)…"
  ${COMPOSE[@]} "${FILES[@]}" --profile test run --rm "${BUILD_FLAGS[@]}" test "${EXTRA[@]}"
  exit $?
fi

if [ "$FULL" -eq 1 ]; then
  echo "→ Stack complète API + web…"
  ${COMPOSE[@]} "${FILES[@]}" --profile full up -d "${BUILD_FLAGS[@]}"
  echo ""
  echo "  API  → http://localhost:${API_PORT:-8000}/health"
  echo "  Web  → http://localhost:${WEB_PORT:-3000}"
  echo "  Docs → http://localhost:${API_PORT:-8000}/api/docs  (si ENV≠prod)"
  exit 0
fi

echo "→ API paper…"
${COMPOSE[@]} "${FILES[@]}" up -d "${BUILD_FLAGS[@]}" api
echo ""
echo "  API  → http://localhost:${API_PORT:-8000}/health"
echo "  Logs → docker compose logs -f api"
echo "  Stop → bash scripts/docker-up.sh --down"
