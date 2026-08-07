# Docker — environnement reproductible

Objectif : **un même conteneur** pour le dev local, les tests et la prod, sans
installer Python 3.14 ni Node sur la machine hôte.

## Prérequis

- Docker 24+ et **Compose v2** (`docker compose version`)
- Ports libres : `8000` (API), `3000` (web, optionnel)

## Démarrage en 1 commande

### Windows (PowerShell)

```powershell
.\scripts\docker-up.ps1              # API paper → http://localhost:8000
.\scripts\docker-up.ps1 -Full        # + frontend → http://localhost:3000
.\scripts\docker-up.ps1 -Test        # pytest dans un conteneur
.\scripts\docker-up.ps1 -Down        # stop
```

### Linux / macOS / Git Bash / WSL

```bash
bash scripts/docker-up.sh            # API paper
bash scripts/docker-up.sh --full     # + frontend
bash scripts/docker-up.sh --test     # pytest
bash scripts/docker-up.sh --down
```

Le script :

1. crée `.env` depuis `.env.example` avec une `WEB_API_KEY` aléatoire si besoin  
2. crée `data/`, `logs/`, `models/`  
3. build + démarre les services  

## Commandes Compose manuelles

```bash
# API seule (paper)
docker compose up --build -d api

# Stack complète
docker compose --profile full up --build -d

# Tests (suite rapide, sans markers slow)
docker compose --profile test run --rm test

# Un fichier de test
docker compose --profile test run --rm test \
  python -m pytest -q tests/test_sec_hardening.py

# Logs / shell
docker compose logs -f api
docker compose exec api bash   # ou sh
```

## Production

Guide serveur complet (Oracle, TLS, secrets) : **[`DEPLOY.md`](../DEPLOY.md)**.

```bash
# .env : ENV=prod, WEB_API_KEY fort, ALLOW_INSECURE_WEB=0, NEXT_PUBLIC_WS_URL=wss://…
# API + build Next (dashboard) :
bash scripts/docker-up.sh --prod --full
# API seule :
bash scripts/docker-up.sh --prod
# ou
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile full up -d --build
```

Effets `docker-compose.prod.yml` + script `--prod` :

| Réglage | Valeur |
|---------|--------|
| `ENV` | `prod` → OpenAPI/docs off (SEC-006) |
| `ALLOW_INSECURE_WEB` | forcé `0` (SEC-003) |
| `API_HOST_BIND` | `127.0.0.1` (nginx devant, cf. `deploy/nginx.conf`) |
| Commande API | `python cli.py` (mode paper/live selon `config`) |
| Frontend | `Dockerfile.frontend` standalone `:3000` (`--full`) |
| Restart | `always` |

Nginx sur l’hôte (`deploy/nginx.conf`) :

| Chemin | Cible |
|--------|--------|
| `/`, `/api/*` | Next.js `127.0.0.1:3000` (proxy same-origin + `X-API-Key`) |
| `/ws` | FastAPI `127.0.0.1:8000` |
| `/health` | FastAPI `127.0.0.1:8000` |

Ne jamais exposer 8000/3000 publiquement sans reverse-proxy + `WEB_API_KEY`.

## Volumes

| Hôte | Conteneur | Rôle |
|------|-----------|------|
| `./data` | `/app/data` | OHLCV Parquet, SQLite |
| `./logs` | `/app/logs` | journaux |
| `./models` | `/app/models` | artefacts ML |
| `./config.yaml` + `./config/` | idem | configuration (rw en dev, ro en prod) |
| `./strategies/` | `/app/strategies` | YAML stratégies |

Aucun jeu de démo n’est embarqué dans l’image.

## Variables d’environnement

Voir `.env.example`. Principales :

| Variable | Local | Prod |
|----------|-------|------|
| `WEB_API_KEY` | générée par le script | secret fort obligatoire |
| `ENV` | `dev` | `prod` |
| `ALLOW_INSECURE_WEB` | `0` (ou `1` si debug sans clé) | toujours `0` |
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8000/ws` | `wss://domaine/ws` |
| `FRONTEND_URL` | `http://localhost:3000` | URL publique du front |

## Images

| Service | Dockerfile | Cible | Base | Taille |
|---------|------------|-------|------|--------|
| `api` | `Dockerfile` | `runtime` | `python:3.14-slim-bookworm` | ~797 Mo |
| `web` | `Dockerfile.frontend` | — | `node:22-alpine` → standalone Next.js | ~232 Mo |
| `test` | `Dockerfile` | `test` | `runtime` + `requirements-dev.txt` | ~870 Mo |

Utilisateur non-root `bot` / `nextjs` (uid 10001).

### Étages du `Dockerfile`

`builder` porte `build-essential` et construit le venv ; `runtime` ne reçoit
que ce venv. Les compilateurs (307 Mo) ne partent donc pas en production, tout
en restant disponibles au build pour une dépendance sans wheel cp314.

`test` repart de `runtime` et n'ajoute que l'outillage — on teste ainsi
exactement ce qui est déployé. Il produit `crypto-bot:test`, **jamais**
`crypto-bot:api` : sans ce tag distinct, lancer les tests écraserait l'image de
production par une variante contenant pytest, ruff et mypy.

> Les tailles sont mesurées dans le conteneur (`du -sx /`). `docker images`
> donne ici des chiffres incohérents d'une image à l'autre — ne pas s'y fier
> pour comparer.

## Dépannage

| Symptôme | Piste |
|----------|--------|
| Refus de démarrage « SANS web.api_key » | `.env` absent ou `WEB_API_KEY` vide |
| Healthcheck KO | `docker compose logs api` — config invalide ou import planté |
| Frontend 503 | API pas healthy ; `BOT_API_URL=http://api:8000` dans le réseau compose |
| Tests lents | le profile `test` exclut `-m "not slow"` ; ajouter `slow` si besoin |
| Rebuild complet | `docker compose build --no-cache api` |

## Sans Docker

Installation native : `README.md`, `scripts/setup.sh`, `docs/DEMARRAGE_WINDOWS.md`.
