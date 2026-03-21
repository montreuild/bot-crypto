# Crypto Bot V11 — Guide de Déploiement Oracle Cloud Free Tier

> **Stack** : Python 3.10+ · FastAPI · SQLite · systemd · nginx · Let's Encrypt  
> **Cible** : Oracle Cloud Always Free — VM.Standard.A1.Flex (ARM, 4 vCPU / 24 GB RAM)  
> **Coût** : 0 €/mois, **permanent** (offre Always Free, pas d'expiration)

---

## Table des matières

1. [Provisionnement Oracle Cloud](#1-provisionnement-oracle-cloud)
2. [Préparation du serveur](#2-préparation-du-serveur)
3. [Déploiement du bot](#3-déploiement-du-bot)
4. [Service systemd + notifications de crash](#4-service-systemd--notifications-de-crash)
5. [nginx + SSL (HTTPS)](#5-nginx--ssl-https)
6. [Configuration du bot](#6-configuration-du-bot)
7. [Multi-timeframe (5m / 15m)](#7-multi-timeframe-5m--15m)
8. [Auto-optimisation (interface web)](#8-auto-optimisation-interface-web)
9. [Sauvegarde de la base de données](#9-sauvegarde-de-la-base-de-données)
10. [Maintenance et commandes utiles](#10-maintenance-et-commandes-utiles)
11. [Architecture finale](#11-architecture-finale)

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
| Image | Ubuntu 22.04 (Canonical) |
| Shape | `VM.Standard.A1.Flex` |
| OCPUs | **4** (gratuit jusqu'à 4) |
| Memory | **24 GB** (gratuit jusqu'à 24 GB) |
| Boot volume | 50 GB (inclus dans les 200 GB gratuits) |
| SSH Key | Coller votre clé publique `~/.ssh/id_rsa.pub` |

> **Note** : Si la région `eu-paris-1` n'a plus de capacité A1, essayez `eu-frankfurt-1` ou attendez quelques heures.

### 1.3 Ouvrir les ports réseau

Dans **Networking → Security Lists → Default Security List** :

Ajouter les règles d'entrée (Ingress) suivantes :

| Source CIDR | Protocole | Port | Description |
|---|---|---|---|
| `0.0.0.0/0` | TCP | 443 | HTTPS (dashboard) |
| `0.0.0.0/0` | TCP | 80 | HTTP → redirection HTTPS |

> Le port 22 (SSH) est déjà ouvert par défaut.

### 1.4 Se connecter en SSH

```bash
ssh ubuntu@<IP_PUBLIQUE_DE_VOTRE_VM>
```

L'IP publique est visible dans **Compute → Instances → Détails de l'instance**.

---

## 2. Préparation du serveur

```bash
# Mise à jour système
sudo apt update && sudo apt upgrade -y

# Dépendances système de base
sudo apt install -y nginx certbot python3-certbot-nginx \
    apache2-utils git unzip htop software-properties-common

# ── Python 3.12 OBLIGATOIRE ────────────────────────────────────────────────
# Ubuntu 22.04 est livré avec Python 3.10 par défaut → installer 3.12 via PPA
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev

# Vérification : doit afficher "Python 3.12.x"
python3.12 --version
# → Python 3.12.x

# Note : sur Ubuntu 24.04, Python 3.12 est natif, le PPA n'est pas nécessaire
```

### 2.1 Ouvrir les ports dans le firewall Oracle (iptables)

Oracle Cloud utilise iptables en plus des Security Lists. Il faut ouvrir les ports manuellement :

```bash
# Ouvrir HTTPS et HTTP
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT

# Sauvegarder les règles (persistent après reboot)
sudo netfilter-persistent save
```

---

## 3. Déploiement du bot

### 3.1 Transférer les fichiers

Depuis votre machine locale :

```bash
# Zipper sans le cache puppeteer (inutile, ~250 MB)
zip -r crypto_bot_deploy.zip crypto_bot_v8_fixed/ \
    --exclude "*.cache/*" --exclude "*.git/*" --exclude "__pycache__/*"

# Envoyer sur le serveur
scp crypto_bot_deploy.zip ubuntu@<IP>:/tmp/
```

### 3.2 Installer le bot

Sur le serveur :

```bash
# Créer le répertoire de déploiement
sudo mkdir -p /opt/crypto_bot
sudo chown ubuntu:ubuntu /opt/crypto_bot

# Extraire
cd /opt
unzip /tmp/crypto_bot_deploy.zip -d /opt/crypto_bot
# Si le zip crée un sous-dossier, ajuster :
# mv /opt/crypto_bot/crypto_bot_v8_fixed/* /opt/crypto_bot/

# Créer le répertoire de logs
mkdir -p /opt/crypto_bot/logs

# ── Créer et activer le virtualenv Python 3.12 ────────────────────────────
python3.12 -m venv /opt/crypto_bot/.venv
source /opt/crypto_bot/.venv/bin/activate

# Vérifier que le venv utilise bien 3.12
python --version
# → Python 3.12.x

# Installer les dépendances dans le venv
cd /opt/crypto_bot
pip install --upgrade pip
pip install -r requirements.txt
```

### 3.3 Tester le bot en mode paper

```bash
cd /opt/crypto_bot
source .venv/bin/activate
python cli.py --live --paper

# Vérifier que le dashboard répond
curl http://localhost:8000/api/status
```

Arrêter avec `Ctrl+C` une fois le test concluant.

---

## 4. Service systemd + notifications de crash

### 4.1 Installer le service

```bash
# Rendre le script de notification exécutable
chmod +x /opt/crypto_bot/deploy/notify-crash.sh

# Installer le service systemd
sudo cp /opt/crypto_bot/deploy/crypto-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable crypto-bot
```

### 4.2 Configurer les notifications Telegram

Avant de démarrer le service, configurer les notifications dans `config.yaml` :

```bash
nano /opt/crypto_bot/config.yaml
```

Remplir la section `notifications` :

```yaml
notifications:
  telegram_enabled: true
  telegram_bot_token: 'VOTRE_TOKEN_ICI'    # @BotFather → /newbot
  telegram_chat_id: 'VOTRE_CHAT_ID_ICI'   # @userinfobot
  min_pnl_to_notify: 5.0
```

**Obtenir un token Telegram** :
1. Ouvrir Telegram → rechercher `@BotFather`
2. Envoyer `/newbot`, suivre les instructions
3. Copier le token fourni (format : `123456:ABCdef...`)

**Obtenir votre Chat ID** :
1. Ouvrir Telegram → rechercher `@userinfobot`
2. Envoyer `/start`
3. Copier le numéro `Id:` affiché

### 4.3 Démarrer le service

```bash
sudo systemctl start crypto-bot

# Vérifier le statut
sudo systemctl status crypto-bot

# Voir les logs en temps réel
tail -f /opt/crypto_bot/logs/bot.log
```

### 4.4 Tester la notification de crash

```bash
# Simuler un crash pour tester la notification
sudo systemctl stop crypto-bot
# → Vous devriez recevoir un message Telegram "🚨 CRASH DÉTECTÉ"

# Redémarrer
sudo systemctl start crypto-bot
```

> **Comment ça marche** : systemd appelle `deploy/notify-crash.sh` via `ExecStopPost` après chaque arrêt anormal. Le script Python lit `config.yaml`, récupère les 20 dernières lignes de log et envoie l'alerte.

---

## 5. nginx + SSL (HTTPS)

### 5.1 Certificat auto-signé temporaire (avant Certbot)

Si vous n'avez pas encore de domaine, créer un certificat auto-signé pour tester :

```bash
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/ssl/private/crypto-bot.key \
  -out /etc/ssl/certs/crypto-bot.crt \
  -subj "/CN=crypto-bot"
```

### 5.2 Créer le mot de passe du dashboard

```bash
# Créer le fichier htpasswd (remplacer 'botuser' par votre identifiant)
sudo htpasswd -c /etc/nginx/.htpasswd botuser
# Saisir et confirmer un mot de passe fort
```

### 5.3 Installer la configuration nginx

```bash
sudo cp /opt/crypto_bot/deploy/nginx.conf /etc/nginx/sites-available/crypto-bot
sudo ln -s /etc/nginx/sites-available/crypto-bot /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Vérifier la syntaxe
sudo nginx -t

# Activer nginx
sudo systemctl enable nginx
sudo systemctl start nginx
```

### 5.4 Obtenir un certificat Let's Encrypt gratuit (avec un domaine)

Si vous possédez un domaine (ou utilisez un DNS dynamique gratuit comme DuckDNS) :

```bash
# Remplacer monbot.duckdns.org par votre domaine
sudo certbot --nginx -d monbot.duckdns.org

# Certbot modifie automatiquement nginx.conf avec le vrai certificat
# Renouvellement automatique (déjà configuré par Certbot via cron)
sudo systemctl status certbot.timer
```

**Option DNS dynamique gratuite (DuckDNS)** :
1. Aller sur [duckdns.org](https://www.duckdns.org) → s'inscrire
2. Créer un sous-domaine (ex: `monbot.duckdns.org`)
3. Configurer la mise à jour automatique de l'IP :

```bash
# Cron pour mettre à jour l'IP dynamique toutes les 5 min
echo "*/5 * * * * curl -s 'https://www.duckdns.org/update?domains=monbot&token=VOTRE_TOKEN&ip=' > /dev/null" | crontab -
```

### 5.5 Accéder au dashboard

```
https://<IP_OU_DOMAINE>/
Login : botuser / votre_mot_de_passe
```

---

## 6. Configuration du bot

Éditer `/opt/crypto_bot/config.yaml` :

```yaml
exchange:
  name: binance
  api_key: 'VOTRE_CLE_API_BINANCE'
  api_secret: 'VOTRE_SECRET_BINANCE'
  margin: false                     # true pour le margin spot

trading:
  capital: 1000                     # Capital en USDC
  timeframe: 1h
  paper_mode: false                 # ← false pour le LIVE réel
  risk_per_trade: 0.01              # 1% de capital par trade
  max_positions: 5
  score_threshold: 0.55
  daily_drawdown_limit: 0.05        # Circuit breaker à -5%/jour
  max_drawdown_global: 0.20         # Arrêt global à -20%
  scan_interval: 60                 # Scan toutes les 60s

web:
  host: 0.0.0.0                     # Écoute sur toutes les interfaces
  port: 8000
  api_key: ''                       # Optionnel : clé API pour les routes POST

notifications:
  telegram_enabled: true
  telegram_bot_token: 'VOTRE_TOKEN'
  telegram_chat_id: 'VOTRE_CHAT_ID'
  min_pnl_to_notify: 5.0
```

**Créer les clés API Binance** :
1. Binance → Profil → API Management → Créer une clé
2. Cocher : **Lecture**, **Trading Spot & Margin**
3. **Ne pas cocher** : Retraits, Futures
4. Restreindre aux IP de votre serveur Oracle (optionnel mais recommandé)

### 6.1 Appliquer la config sans redémarrage

```bash
# Rechargement gracieux via l'API
curl -X POST http://localhost:8000/api/bot/stop
curl -X POST http://localhost:8000/api/bot/start
```

Ou via le dashboard web → bouton **Stop** puis **Start**.

---

## 7. Multi-timeframe (5m / 15m)

Le bot supporte nativement les timeframes 5m, 15m, 30m, 1h, 4h, 1d.  
Pour faire tourner plusieurs instances simultanément :

### 7.1 Instance 5 minutes

```bash
# Copier la config d'exemple fournie
cp /opt/crypto_bot/config-5m.yaml /opt/crypto_bot/config-5m-live.yaml
nano /opt/crypto_bot/config-5m-live.yaml
# Adapter : capital, paper_mode, clés API, port: 8001
```

### 7.2 Créer un second service systemd

```bash
sudo cp /etc/systemd/system/crypto-bot.service \
        /etc/systemd/system/crypto-bot-5m.service

sudo nano /etc/systemd/system/crypto-bot-5m.service
```

Modifier ces lignes dans le fichier :

```ini
Description=Crypto Trading Bot — 5m
ExecStart=/usr/bin/python3 cli.py --config config-5m-live.yaml --live
StandardOutput=append:/opt/crypto_bot/logs/bot_5m.log
StandardError=append:/opt/crypto_bot/logs/bot_5m.log
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now crypto-bot-5m
```

### 7.3 Ajouter l'instance 5m dans nginx

Dans `/etc/nginx/sites-available/crypto-bot`, ajouter un bloc `location` :

```nginx
location /5m/ {
    proxy_pass http://127.0.0.1:8001/;
    # ... mêmes headers que le bloc principal
}
```

### 7.4 Points d'attention par timeframe

| Timeframe | scan_interval | Capital conseillé | score_threshold | Stratégies |
|---|---|---|---|---|
| 5m | 30s | 30-40% du capital total | 0.60 | supertrend_macd, breakout |
| 15m | 60s | 30-40% du capital total | 0.58 | supertrend_macd, trend |
| 1h | 60s | 20-40% du capital total | 0.55 | fear_momentum, trend |

> La somme des capitaux des instances ne doit pas dépasser votre capital disponible réel.

---

## 8. Auto-optimisation (interface web)

L'optimisation automatique est **entièrement gérée par le bot via le dashboard web**, sans cron externe.

### 8.1 Activer l'auto-optimisation

Dans le dashboard → onglet **Optimiseur** :

1. Choisir les stratégies à optimiser (ex: `trend`, `fear_momentum`)
2. Sélectionner la méthode : **Bayesian** (recommandé) / Random / Grid
3. Définir le nombre de trials (50 recommandé, 100 pour plus de précision)
4. Cocher **Auto-apply** pour appliquer automatiquement les meilleurs paramètres
5. Cliquer **Lancer l'optimisation**

Ou via l'API :

```bash
# Lancer une optimisation Bayesian en arrière-plan avec auto-apply
curl -X POST "http://localhost:8000/api/optimize/start?\
symbol=BTC/USDC&strategies=trend,fear_momentum&\
method=bayesian&n_trials=50&auto_apply=true"
```

### 8.2 Planification automatique (dans config.yaml)

```yaml
optimizer:
  enabled: true           # Active l'auto-optimisation planifiée
  method: bayesian
  n_trials: 50
  auto_interval_h: 24     # Toutes les 24h
  out_of_sample_ratio: 0.3
```

### 8.3 Comment l'auto-apply fonctionne à chaud

Quand `auto_apply: true` est activé :
1. L'optimiseur tourne en **thread d'arrière-plan** (non-bloquant pour le trading)
2. À la fin, les meilleurs paramètres sont écrits dans `config.yaml`
3. La config en mémoire est rechargée **sans redémarrer le bot**
4. Les paramètres sont propagés au `LiveTrader` actif instantanément
5. Une notification Telegram/WA confirme : _"✅ Optimisation terminée — score +X.XXXX"_

Le fichier `params_changelog.json` conserve l'historique de tous les changements de paramètres.

### 8.4 Suivi de progression en temps réel

Le dashboard affiche la progression en temps réel via Server-Sent Events.
Vous pouvez aussi suivre via API :

```bash
# Statut de tous les jobs d'optimisation en cours
curl http://localhost:8000/api/optimize/status
```

---

## 9. Sauvegarde de la base de données

### 9.1 Sauvegarde locale (quotidienne)

```bash
# Créer le répertoire de sauvegardes
mkdir -p /opt/crypto_bot/backups

# Cron : sauvegarde SQLite chaque nuit à 03h00
(crontab -l 2>/dev/null; echo "0 3 * * * cp /opt/crypto_bot/trades.db \
  /opt/crypto_bot/backups/trades_\$(date +\%Y\%m\%d).db && \
  ls -t /opt/crypto_bot/backups/trades_*.db | tail -n +8 | xargs rm -f") \
  | crontab -
```

Garde les 7 dernières sauvegardes quotidiennes.

### 9.2 Sauvegarde vers OCI Object Storage (20 GB gratuits)

```bash
# Installer le CLI Oracle Cloud
pip3 install oci-cli --break-system-packages
oci setup config   # suivre l'assistant

# Créer un bucket dans la console Oracle : Storage → Object Storage → Create Bucket
# Nom : "crypto-bot-backups"

# Cron : backup vers OCI chaque nuit à 03h30
(crontab -l 2>/dev/null; echo "30 3 * * * oci os object put \
  --bucket-name crypto-bot-backups \
  --file /opt/crypto_bot/trades.db \
  --name trades_\$(date +\%Y\%m\%d).db \
  --force > /dev/null 2>&1") | crontab -
```

### 9.3 Sauvegarder config.yaml

```bash
(crontab -l 2>/dev/null; echo "0 3 * * * cp /opt/crypto_bot/config.yaml \
  /opt/crypto_bot/backups/config_\$(date +\%Y\%m\%d).yaml") | crontab -
```

---

## 10. Maintenance et commandes utiles

### Gestion du service

```bash
# Statut détaillé
sudo systemctl status crypto-bot

# Démarrer / Arrêter / Redémarrer
sudo systemctl start  crypto-bot
sudo systemctl stop   crypto-bot
sudo systemctl restart crypto-bot

# Logs en temps réel
journalctl -u crypto-bot -f

# Logs du bot (plus détaillés)
tail -f /opt/crypto_bot/logs/bot.log

# Voir les N derniers redémarrages
journalctl -u crypto-bot --since "7 days ago" | grep "Started\|Failed"
```

### Mise à jour du bot

```bash
# Arrêter le bot
sudo systemctl stop crypto-bot

# Sauvegarder la config et la BDD
cp /opt/crypto_bot/config.yaml /tmp/config_backup.yaml
cp /opt/crypto_bot/trades.db   /tmp/trades_backup.db

# Déployer la nouvelle version
# (répéter les étapes 3.1 et 3.2)

# Restaurer la config
cp /tmp/config_backup.yaml /opt/crypto_bot/config.yaml

# Mettre à jour les dépendances dans le venv existant
source /opt/crypto_bot/.venv/bin/activate
pip install -r /opt/crypto_bot/requirements.txt

# Redémarrer
sudo systemctl start crypto-bot
```

### Monitoring ressources

```bash
# Consommation mémoire/CPU du bot
ps aux | grep "cli.py"

# Vue en temps réel
htop -p $(pgrep -f "cli.py")

# Taille de la base de données
ls -lh /opt/crypto_bot/trades.db

# Espace disque restant
df -h /opt/crypto_bot
```

### Commandes CLI du bot

```bash
cd /opt/crypto_bot
source .venv/bin/activate

# Backtest rapide
python cli.py --backtest BTC/USDC --timeframe 1h --limit 500

# Backtest avec Walk-Forward + Monte Carlo
python cli.py --backtest BTC/USDC --walk-forward --monte-carlo --limit 1000

# Optimisation CLI (bloquant)
python cli.py --optimize trend --opt-method bayesian

# Scanner les marchés
python cli.py --scan

# Mode paper trading seul (sans serveur web)
python cli.py --live --paper
```

### Rotation des logs

```bash
# Créer une règle logrotate
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

## 11. Architecture finale

```
Internet
   │
   │ HTTPS :443
   ▼
┌──────────────────────────────────────────────────────────────────┐
│                   Oracle Cloud ARM A1 (4 vCPU / 24 GB)          │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  nginx (port 443 → 127.0.0.1:8000)                       │   │
│  │  · TLS 1.3 (Let's Encrypt ou auto-signé)                 │   │
│  │  · Basic auth (htpasswd)                                  │   │
│  │  · Proxy SSE pour l'optimiseur                            │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │ HTTP local                             │
│  ┌──────────────────────▼───────────────────────────────────┐   │
│  │  crypto-bot.service (systemd)                             │   │
│  │  python3 cli.py --live                                   │   │
│  │  ┌────────────────┐  ┌──────────────┐  ┌──────────────┐ │   │
│  │  │  LiveTrader     │  │  FastAPI :8000│  │  AutoOpt     │ │   │
│  │  │  · Scanner      │  │  Dashboard   │  │  · Bayesian  │ │   │
│  │  │  · Strategies   │  │  · Backtest  │  │  · Hot-apply │ │   │
│  │  │  · RiskManager  │  │  · Config    │  │  · SSE stream│ │   │
│  │  │  · Trailing     │  │  · Trades    │  └──────────────┘ │   │
│  │  └────────┬────────┘  └──────────────┘                   │   │
│  │           │                                               │   │
│  │  ┌────────▼────────┐  ┌──────────────┐                   │   │
│  │  │  trades.db       │  │  logs/bot.log│                   │   │
│  │  │  (SQLite)        │  │  (rotation)  │                   │   │
│  │  └─────────────────┘  └──────────────┘                   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                   │
│  Crash → ExecStopPost → notify-crash.py → Telegram / WhatsApp   │
│  Cron 03:00 → backup trades.db → OCI Object Storage (20GB free) │
└──────────────────────────────────────────────────────────────────┘
         │                              │
         ▼ API Binance/CCXT             ▼ Telegram Bot API
    Exchange (live/paper)            Votre téléphone
```

### Récapitulatif des coûts mensuels

| Service | Plan | Coût |
|---|---|---|
| Oracle Cloud ARM A1 (4 vCPU / 24 GB) | Always Free | **0 €** |
| OCI Object Storage (< 20 GB) | Always Free | **0 €** |
| DuckDNS (DNS dynamique) | Gratuit | **0 €** |
| Let's Encrypt (SSL) | Gratuit | **0 €** |
| Telegram Bot API | Gratuit | **0 €** |
| **Total** | | **0 €/mois** |

---

## Annexe — Dépannage

### Le bot ne démarre pas

```bash
# Vérifier la config manuellement
cd /opt/crypto_bot
source .venv/bin/activate
python -c "from app.core.config import load_config; print(load_config('config.yaml'))"
```

### nginx retourne 502 Bad Gateway

```bash
# Vérifier que FastAPI tourne
curl http://localhost:8000/api/status

# Si down, redémarrer le bot
sudo systemctl restart crypto-bot
```

### Erreur "Out of Memory"

```bash
# Créer un fichier de swap 4 GB (précaution pour optimisations intensives)
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### Notifications Telegram ne fonctionnent pas

```bash
# Tester manuellement
cd /opt/crypto_bot
source .venv/bin/activate
python -c "
from app.core.config import load_config
from app.core.notifications import Notifier
cfg = load_config('config.yaml')
n = Notifier(cfg)
n.send('🧪 Test de notification', async_=False)
print('Envoyé !')
"
```
