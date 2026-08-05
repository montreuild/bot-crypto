# Crypto Bot V12 — Guide de déploiement (production)

> **Stack** : Python 3.14 · FastAPI · Next.js 15 · SQLite · Docker (recommandé) ou systemd · nginx · Let's Encrypt  
> **Cible** : Oracle Cloud Always Free — VM.Standard.A1.Flex (ARM, 4 vCPU / 24 GB RAM)  
> **Coût** : 0 €/mois, **permanent** (offre Always Free, pas d'expiration)  
> **Docker local / tests** : voir aussi [`docs/DOCKER.md`](docs/DOCKER.md)

### Objectifs prod (à ne pas oublier)

| Réglage | Valeur | Pourquoi |
|---------|--------|----------|
| `ENV` | `prod` | **OpenAPI off** : `/api/docs`, `/api/redoc`, `/api/openapi.json` désactivés (SEC-006) |
| `ALLOW_INSECURE_WEB` | `0` | Interdit un host ouvert sans clé (SEC-003) |
| `WEB_API_KEY` | secret fort | Auth API ; injectée par le **proxy Next serveur**, pas le navigateur |
| `API_HOST_BIND` | `127.0.0.1` | API non exposée publiquement (nginx devant) |
| Frontend | **build Next** standalone `:3000` | UI officielle (Jinja2 retiré) |
| Reverse-proxy | **nginx** TLS + basic auth | `/` → Next:3000, `/ws` → FastAPI:8000 |

---

## Table des matières

1. [Provisionnement Oracle Cloud](#1-provisionnement-oracle-cloud)
2. [Préparation du serveur](#2-préparation-du-serveur)
3. [Déploiement Docker (recommandé)](#3-déploiement-docker-recommandé)
4. [nginx + SSL (HTTPS)](#4-nginx--ssl-https)
5. [Configuration du bot](#5-configuration-du-bot)
6. [Sauvegarde de la base de données](#6-sauvegarde-de-la-base-de-données)
7. [Maintenance et commandes utiles](#7-maintenance-et-commandes-utiles)
8. [Architecture finale](#8-architecture-finale)
9. [Annexe A — Déploiement natif (systemd, sans Docker)](#annexe-a--déploiement-natif-systemd-sans-docker)
10. [Annexe B — Multi-timeframe & auto-optimisation](#annexe-b--multi-timeframe--auto-optimisation)
11. [Annexe C — Dépannage](#annexe-c--dépannage)

---

## 1. Provisionnement Oracle Cloud

### 1.1 Créer un compte Oracle Cloud

1. Aller sur [cloud.oracle.com](https://cloud.oracle.com) → **Start for Free**
2. Créer un compte (carte bancaire requise pour vérification, **aucun débit**)
3. Choisir la région la plus proche (ex: `eu-paris-1` ou `eu-frankfurt-1`)

### 1.2 Créer l'instance ARM A1

Dans la console Oracle Cloud :

**Compute → Instances → Create Instance**

| Paramètre | Valeur recommandée |
|---|---|
| Name | `crypto-bot` |
| Image | Ubuntu 22.04 ou 24.04 (Canonical) |
| Shape | `VM.Standard.A1.Flex` |
| OCPUs | **4** (gratuit jusqu'à 4) |
| Memory | **24 GB** (gratuit jusqu'à 24 GB) |
| Boot volume | 50 GB (inclus dans les 200 GB gratuits) |
| SSH Key | Coller votre clé publique `~/.ssh/id_rsa.pub` |

> **Note** : Si la région `eu-paris-1` n'a plus de capacité A1, essayez `eu-frankfurt-1` ou attendez quelques heures.

### 1.3 Ouvrir les ports réseau

Dans **Networking → Security Lists → Default Security List** :

| Source CIDR | Protocole | Port | Description |
|---|---|---|---|
| `0.0.0.0/0` | TCP | 443 | HTTPS (dashboard Next via nginx) |
| `0.0.0.0/0` | TCP | 80 | HTTP → redirection HTTPS |

> Le port 22 (SSH) est déjà ouvert par défaut.  
> **Ne pas** ouvrir 8000 ni 3000 sur Internet — seuls nginx (80/443) et le bind local Docker/systemd.

### 1.4 Se connecter en SSH

```bash
ssh ubuntu@<IP_PUBLIQUE_DE_VOTRE_VM>
```

---

## 2. Préparation du serveur

```bash
sudo apt update && sudo apt upgrade -y

# nginx + Certbot + outils
sudo apt install -y nginx certbot python3-certbot-nginx \
    apache2-utils git curl htop ca-certificates

# Docker Engine + Compose v2 (recommandé)
# Doc officielle : https://docs.docker.com/engine/install/ubuntu/
sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker ubuntu
# Se déconnecter / reconnecter pour le groupe docker
```

### 2.1 Firewall Oracle (iptables)

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT
sudo netfilter-persistent save   # si le paquet est installé
```

---

## 3. Déploiement Docker (recommandé)

Un même jeu d’images pour le local et la prod : API (`Dockerfile`) + frontend Next standalone (`Dockerfile.frontend`).  
Scripts one-shot : `scripts/docker-up.sh` / `scripts/docker-up.ps1` (il n’y a pas de `dev.sh` dédié).

### 3.1 Récupérer le code

```bash
sudo mkdir -p /opt/crypto_bot
sudo chown ubuntu:ubuntu /opt/crypto_bot
cd /opt
git clone <URL_DU_DEPOT> crypto_bot
# ou : scp / rsync d’un zip du dépôt (sans node_modules, .venv, data volumineux)
cd /opt/crypto_bot
mkdir -p data logs models backups
```

### 3.2 Fichier `.env` production

```bash
cp .env.example .env
# Générer une clé forte
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
nano .env
```

Valeurs minimales :

```env
ENV=prod
WEB_API_KEY=<secret fort, pas le placeholder>
ALLOW_INSECURE_WEB=0
API_HOST_BIND=127.0.0.1
API_PORT=8000
WEB_PORT=3000
FRONTEND_URL=https://votre-domaine.example
NEXT_PUBLIC_WS_URL=wss://votre-domaine.example/ws
```

Effets :

- **`ENV=prod`** → OpenAPI / Swagger **désactivés** (`docs_url` / `openapi_url` = `None` dans `app/api/main.py`, SEC-006).
- **`API_HOST_BIND=127.0.0.1`** → ports API (et web) visibles seulement en local sur l’hôte ; nginx publie 443.
- **`WEB_API_KEY`** obligatoire (le script `--prod` refuse le placeholder).

### 3.3 Build + démarrage (API + Next)

```bash
cd /opt/crypto_bot

# Stack production : compose de base + docker-compose.prod.yml
# --full = profile frontend (build Next standalone)
bash scripts/docker-up.sh --prod --full

# Équivalent manuel :
# export API_HOST_BIND=127.0.0.1 ENV=prod ALLOW_INSECURE_WEB=0
# docker compose -f docker-compose.yml -f docker-compose.prod.yml \
#   --profile full up -d --build
```

Vérifications locales (sur le serveur) :

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000/
# OpenAPI doit être off en prod :
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/docs
# → 404
```

### 3.4 Ce que fait `docker-compose.prod.yml`

| Service | Comportement prod |
|---------|-------------------|
| `api` | `ENV=prod`, `ALLOW_INSECURE_WEB=0`, commande `python cli.py` (paper/live selon `config`), `restart: always` |
| `web` | Next standalone, `BOT_API_URL=http://api:8000`, `WEB_API_KEY` requis, `NEXT_PUBLIC_WS_URL` en `wss://…` |

Volumes persistants (hôte) : `./data`, `./logs`, `./models`, `./config.yaml`, `./config/`, `./strategies/`.

### 3.5 Mode paper d’abord

Par défaut en compose de base la commande est `--paper`. En prod le override retire `--paper` : le mode suit `config` (`paper_mode` / CLI).  
Pour forcer le paper en prod le temps des tests, dans un override local :

```yaml
# docker-compose.override.yml (non versionné)
services:
  api:
    command: ["python", "cli.py", "--paper"]
```

---

## 4. nginx + SSL (HTTPS)

Le fichier de référence est `deploy/nginx.conf` :

| Chemin | Upstream | Rôle |
|--------|----------|------|
| `/` | `127.0.0.1:3000` | UI **Next.js** + proxy same-origin `/api/*` (injection `X-API-Key`) |
| `/api/optimize/stream` | `127.0.0.1:3000` | SSE optimiseur, **buffering off** |
| `/ws` | `127.0.0.1:8000` | WebSocket FastAPI (Next ne proxy pas les WS) |
| `/health` | `127.0.0.1:8000` | Healthcheck (auth basic off) |

### 4.1 Certificat auto-signé temporaire

```bash
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/ssl/private/crypto-bot.key \
  -out /etc/ssl/certs/crypto-bot.crt \
  -subj "/CN=crypto-bot"
```

### 4.2 Mot de passe dashboard (basic auth)

```bash
sudo htpasswd -c /etc/nginx/.htpasswd botuser
```

### 4.3 Installer la config nginx

```bash
sudo cp /opt/crypto_bot/deploy/nginx.conf /etc/nginx/sites-available/crypto-bot
sudo ln -sf /etc/nginx/sites-available/crypto-bot /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl reload nginx
```

### 4.4 Let's Encrypt (domaine)

```bash
sudo certbot --nginx -d monbot.duckdns.org
sudo systemctl status certbot.timer
```

### 4.5 Accès

```
https://<IP_OU_DOMAINE>/
Login nginx : botuser / votre_mot_de_passe
# L’API reste protégée par WEB_API_KEY (gérée par le proxy Next)
# /api/docs → 404 en ENV=prod
```

---

## 5. Configuration du bot

Éditer `/opt/crypto_bot/config/venues.yaml` (exchange) et
`config/risk.yaml` (capital, risque) — `config.yaml` n’est qu’un sommaire `include:` (S11).

Points sensibles en live :

```yaml
# config/venues.yaml — extrait
exchange:
  name: okx
  # préférer les variables d'env OKX_API_* si possible
  margin: false

# config/risk.yaml — extrait
trading:
  capital: 1000
  timeframe: 1h
  paper_mode: true          # false uniquement quand prêt pour le live
  risk_per_trade: 0.01
  max_positions: 5
  daily_drawdown_limit: 0.05
  max_drawdown_global: 0.20
```

Notifications : `config/ops.yaml` → section `notifications` (Telegram).

### 5.1 Appliquer sans rebuild image

Les volumes montent la config : modifier les YAML puis redémarrer le conteneur API si besoin :

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart api
# ou hot-reload via API (si routes start/stop actives) :
curl -X POST http://127.0.0.1:8000/api/bot/stop -H "X-API-Key: $WEB_API_KEY"
curl -X POST http://127.0.0.1:8000/api/bot/start -H "X-API-Key: $WEB_API_KEY"
```

**Créer les clés API OKX** : permissions **Trade** uniquement (pas Withdraw), IP restreinte au serveur, passphrase → `api_password` / `OKX_API_PASSWORD`.

---

## 6. Sauvegarde de la base de données

### 6.1 Sauvegarde locale (quotidienne) — SEC-05

`deploy/backup.sh` sauvegarde `trades.db` (via `sqlite3.backup()`, cohérent sous WAL), `config.yaml` **et** `config/`, et `strategies/*.yaml`, avec rétention (7 jours par défaut).

```bash
chmod +x /opt/crypto_bot/deploy/backup.sh
/opt/crypto_bot/deploy/backup.sh

(crontab -l 2>/dev/null; echo "0 3 * * * /opt/crypto_bot/deploy/backup.sh \
  >> /opt/crypto_bot/logs/backup.log 2>&1") | crontab -
```

### 6.2 Sauvegarde vers OCI Object Storage (20 GB gratuits)

```bash
pip3 install oci-cli --break-system-packages
oci setup config

(crontab -l 2>/dev/null; echo "30 3 * * * oci os object put \
  --bucket-name crypto-bot-backups \
  --file \$(ls -t /opt/crypto_bot/backups/trades_*.db | head -1) \
  --name trades_\$(date +\%Y\%m\%d).db \
  --force > /dev/null 2>&1") | crontab -
```

---

## 7. Maintenance et commandes utiles

### Docker

```bash
cd /opt/crypto_bot

# Statut / logs
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
docker compose logs -f api
docker compose logs -f web

# Redémarrage
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart api web

# Mise à jour code
git pull
bash scripts/docker-up.sh --prod --full   # rebuild + recreate

# Arrêt
bash scripts/docker-up.sh --down
```

### nginx

```bash
sudo nginx -t && sudo systemctl reload nginx
sudo tail -f /var/log/nginx/crypto-bot-error.log
```

### Rotation des logs

```bash
sudo tee /etc/logrotate.d/crypto-bot << 'EOF'
/opt/crypto_bot/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 640 ubuntu ubuntu
}
EOF
```

---

## 8. Architecture finale

```
Internet
   │ HTTPS :443
   ▼
┌──────────────────────────────────────────────────────────────────┐
│              Oracle Cloud ARM A1 (4 vCPU / 24 GB)                 │
│                                                                   │
│  nginx (TLS + basic auth)                                         │
│    / , /api/*  ──────────────►  Next.js :3000 (container web)     │
│    /api/optimize/stream ─────►  Next (SSE, buffering off)         │
│    /ws  ─────────────────────►  FastAPI :8000 (container api)     │
│    /health ──────────────────►  FastAPI :8000                     │
│                                                                   │
│  docker compose (prod)                                            │
│    api  : ENV=prod → OpenAPI off · WEB_API_KEY · paper/live       │
│    web  : standalone Next · proxy /api + X-API-Key serveur        │
│    volumes : data/ logs/ models/ config* strategies/              │
│                                                                   │
│  Cron 03:00 → deploy/backup.sh → (optionnel) OCI Object Storage │
└──────────────────────────────────────────────────────────────────┘
         │                              │
         ▼ API OKX/CCXT                 ▼ Telegram
    Exchange (live/paper)            Notifications
```

### Récapitulatif des coûts mensuels

| Service | Plan | Coût |
|---|---|---|
| Oracle Cloud ARM A1 (4 vCPU / 24 GB) | Always Free | **0 €** |
| OCI Object Storage (< 20 GB) | Always Free | **0 €** |
| DuckDNS / Let's Encrypt / Telegram | Gratuit | **0 €** |
| **Total** | | **0 €/mois** |

---

## Annexe A — Déploiement natif (systemd, sans Docker)

Utile si Docker n’est pas disponible. **Python 3.14** et **Node 20+** requis sur l’hôte.

```bash
# Python 3.14 (Ubuntu 22.04 : PPA deadsnakes ou binaire officiel)
sudo apt install -y python3.14 python3.14-venv python3.14-dev
# Node 20+ (nodesource ou nvm) pour le build Next

cd /opt/crypto_bot
python3.14 -m venv .venv
source .venv/bin/activate
pip install -U pip && pip install -r requirements.txt

# .env comme en §3.2 (ENV=prod, WEB_API_KEY, …)
cp .env.example .env

# Build frontend Next (standalone)
cd frontend
npm ci
export BOT_API_URL=http://127.0.0.1:8000
export NEXT_PUBLIC_WS_URL=wss://votre-domaine/ws
export WEB_API_KEY="$(grep ^WEB_API_KEY= ../.env | cut -d= -f2-)"
npm run build
# Démarrer le serveur standalone (ex. via systemd user) :
# node .next/standalone/server.js  → port 3000
```

Service API : `deploy/crypto-bot.service` (adapter `WorkingDirectory`, venv, `Environment=ENV=prod`).  
Notifications de crash : `deploy/notify-crash.sh` via `ExecStopPost`.  
nginx : **identique** au §4 (Next:3000 + FastAPI:8000).

```bash
chmod +x /opt/crypto_bot/deploy/notify-crash.sh
sudo cp /opt/crypto_bot/deploy/crypto-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now crypto-bot
```

Test paper :

```bash
cd /opt/crypto_bot && source .venv/bin/activate
set -a; source .env; set +a
python cli.py --paper
```

---

## Annexe B — Multi-timeframe & auto-optimisation

### Multi-timeframe

Plusieurs instances (ports 8000, 8001, …) avec configs dédiées et blocs `location` nginx si besoin.  
La somme des capitaux ne doit pas dépasser le capital réel.

| Timeframe | scan_interval | Capital conseillé | score_threshold |
|---|---|---|---|
| 5m | 30s | 30-40 % | 0.60 |
| 15m | 60s | 30-40 % | 0.58 |
| 1h | 60s | 20-40 % | 0.55 |

### Auto-optimisation

Gérée par le bot (dashboard Next → Lab / Optimiseur) ou API, sans cron externe.  
Planification : section `optimizer` dans la config (intervalle, bayesian, auto-apply).  
SSE : `GET /api/optimize/stream` (nginx buffering off, via Next).

---

## Annexe C — Dépannage

### Le bot / l’API ne démarre pas

```bash
docker compose logs api
# ou natif :
cd /opt/crypto_bot && source .venv/bin/activate
python -c "from app.core.config import load_config; print(load_config('config.yaml'))"
```

Refus « SANS web.api_key » → `.env` absent ou `WEB_API_KEY` vide / placeholder.

### nginx 502 Bad Gateway

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000/
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
sudo systemctl reload nginx
```

### `/api/docs` accessible en prod

`ENV` n’est pas `prod` dans le conteneur :

```bash
docker compose exec api printenv ENV
# doit afficher : prod
```

Corriger `.env` + `docker-compose.prod.yml`, puis recreate.

### Frontend 503 / API injoignable

- Conteneur `api` unhealthy : `docker compose logs api`
- `BOT_API_URL` doit être `http://api:8000` **dans** le réseau Compose (déjà posé par compose)
- Navigateur : appels same-origin `/api/*` via Next, pas d’appel direct au port 8000

### WebSocket KO

- `NEXT_PUBLIC_WS_URL=wss://votre-domaine/ws` (rebuild web si la valeur a changé au build)
- nginx `location /ws` → `127.0.0.1:8000` avec `Upgrade` / `Connection`

### Out of Memory

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### Notifications Telegram

Voir `config/ops.yaml` ; test via `app.core.notifications.Notifier` en shell Python dans le venv ou `docker compose exec api …`.
