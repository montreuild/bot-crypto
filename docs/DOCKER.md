# Docker — tests locaux et déploiement

## Prérequis

- Docker 24+ et Docker Compose v2
- Fichier `.env` à la racine (au minimum `WEB_API_KEY` en prod)
- Données OHLCV déjà présentes dans `./data` (Parquet versionnés ou syncés)

## Tests locaux

```bash
# Build image API
docker compose build api

# Lancer le bot en paper (API :8000)
docker compose up api

# Suite de tests dans un conteneur éphémère
docker compose --profile test run --rm test

# Cible unique
docker compose run --rm api python -m pytest -q tests/test_sec_hardening.py
```

## Stack complète (API + frontend)

```bash
docker compose --profile full up --build
# API  → http://localhost:8000
# Web  → http://localhost:3000
```

## Production

```bash
# .env
ENV=prod
WEB_API_KEY=<secret>
ALLOW_INSECURE_WEB=0

# API uniquement (nginx/SSL restent sur l'hôte, cf. deploy/nginx.conf)
docker compose up -d api
# Surcharger la commande paper :
#   command: ["python", "cli.py"]   dans un override compose
```

`ENV=prod` désactive `/api/docs` (SEC-006).  
`ALLOW_INSECURE_WEB` n'est honoré **que** via l'environnement (SEC-003), jamais via YAML.

## Volumes

| Hôte     | Conteneur    | Contenu                    |
|----------|--------------|----------------------------|
| `./data` | `/app/data`  | OHLCV Parquet, SQLite      |
| `./logs` | `/app/logs`  | journaux                   |
| `./models` | `/app/models` | artefacts LightGBM      |
| `./config` | `/app/config` | YAML de config (ro)     |

Aucun jeu de démo n'est embarqué dans l'image.

## Notes

- Image API : `python:3.14-slim-bookworm`
- Image web : Next.js standalone (`output: 'standalone'` requis dans `next.config`)
- Pour Oracle Free Tier sans Docker, rester sur `DEPLOY.md` (systemd + nginx)
