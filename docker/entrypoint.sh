#!/bin/sh
# Entrypoint image API — prépare les répertoires runtime et lance la commande.
set -eu

mkdir -p /app/data /app/logs /app/models

if [ -z "${WEB_API_KEY:-}" ] && [ "${ALLOW_INSECURE_WEB:-0}" != "1" ]; then
  echo "[docker] ⚠ WEB_API_KEY vide et ALLOW_INSECURE_WEB≠1." >&2
  echo "[docker]   Copiez .env.example → .env ou lancez scripts/docker-up.*" >&2
fi

exec "$@"
