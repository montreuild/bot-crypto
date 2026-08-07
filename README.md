# ⚡ Crypto Bot V12

Bot de trading algorithmique multi-stratégies avec interface web, backtest avancé et optimiseur de paramètres.

> **⚠ Mises à jour V12.18+ (29/07/2026)** :
> - **Python 3.14 obligatoire** (au lieu de 3.12) — voir `docs/DEMARRAGE_WINDOWS.md`
> - **Frontend Next.js officiel unique** — Jinja2 décommissionné (voir `docs/FIN_JINJA2.md`)
> - **Plan d'amélioration** — `docs/PLAN_DIRECTEUR_AMELIORATIONS.md` (8 sprints, 173 SP)
> - **Audit externe** — `docs/audit-externe/AUDIT_TECHNIQUE_BOT_CRYPTO_V12.md` (54 p.)

---

## 📋 Fonctionnalités

- **Live / Paper trading** — Exécution sur OKX (et autres exchanges via CCXT) avec gestion du risque, circuit breaker, trailing stop
- **Backtest avancé** — Jusqu'à 8 000 bougies, Walk-Forward Analysis, Monte-Carlo, comparaison multi-stratégies, graphique de prix avec signaux
- **Optimiseur** — Random Search / Bayesian UCB / Grid Search IS/OOS avec détection d'overfitting, application directe dans `config.yaml`
- **ML** — Stratégie basée sur LightGBM + Isotonic calibration (scikit-learn supprimé)
- **Notifications** — Telegram et WhatsApp
- **Interface web** — **Next.js 15 / React 19** (frontend officiel), 20 pages

---

## ⚙️ Prérequis

- **Python 3.14 OBLIGATOIRE** (voir [Installation](#installation-détaillée) pour Ubuntu 22.04 / 24.04 / Windows / macOS)
- Node.js 20+ (pour le frontend Next.js)
- pip


---

## 🚀 Installation rapide

### Option A — Docker (recommandé, environnement reproductible)

```powershell
# Windows
.\scripts\docker-up.ps1          # API paper → http://localhost:8000
.\scripts\docker-up.ps1 -Full    # + frontend → http://localhost:3000
.\scripts\docker-up.ps1 -Test    # pytest dans un conteneur
```

```bash
# Linux / macOS / WSL / Git Bash
bash scripts/docker-up.sh
bash scripts/docker-up.sh --full
bash scripts/docker-up.sh --test
```

Guide détaillé : [`docs/DOCKER.md`](docs/DOCKER.md) (local, tests, production).

### Option B — Installation native

```bash
# 1. Cloner / décompresser le projet
cd crypto_bot_v12

# 2. Créer un environnement virtuel Python 3.14
python3.14 -m venv .venv
source .venv/bin/activate      # Linux / macOS
.venv\Scripts\activate         # Windows (Git Bash)

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Éditer la configuration
cp config.yaml config.yaml.example  # Garder une trace
# Éditer config.yaml avec vos clés API, capital, etc.
```

> 💡 **Recommandé** : exécuter `bash scripts/setup.sh` qui automatise tout
> (détection OS, venv 3.14, .env avec WEB_API_KEY, frontend Next.js).

### Installation détaillée par OS

**Ubuntu 22.04 (Python 3.10 par défaut):**
```bash
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update && sudo apt install -y python3.14 python3.14-venv python3.14-dev
python3.14 -m venv /opt/crypto_bot/.venv
source /opt/crypto_bot/.venv/bin/activate
pip install -r requirements.txt
```

**Ubuntu 24.04 (Python 3.12 natif, 3.14 via deadsnakes):**
```bash
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update && sudo apt install -y python3.14 python3.14-venv python3.14-dev
python3.14 -m venv /opt/crypto_bot/.venv
source /opt/crypto_bot/.venv/bin/activate
pip install -r requirements.txt
```

**macOS:**
```bash
brew install python@3.14
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows:** Voir [`docs/DEMARRAGE_WINDOWS.md`](docs/DEMARRAGE_WINDOWS.md) pour le guide
complet (Git Bash, WSL2, PowerShell). Résumé :

```powershell
# Git Bash ou PowerShell
py -3.14 -m venv .venv
source .venv/Scripts/activate   # Git Bash
# OU  .venv\Scripts\Activate.ps1   # PowerShell
pip install -r requirements.txt
```

### Frontend Next.js (frontend officiel)

```bash
cd frontend
npm install
npm run dev    # http://localhost:3000
# OU
npm run build  # build de production
```

Voir `docs/FIN_JINJA2.md` pour la fin officielle de Jinja2 (frontend Next.js
devient l'unique frontend). Les templates Jinja2 (`app/web/templates/`) sont
décommissionnés et seront supprimés à la fin du Sprint 6.

---

## 🔧 Configuration

La configuration est **découpée par responsabilité** : `config.yaml` ne porte
que le sommaire (`include:`), et chaque fichier de `config/` correspond à une
brique du bot — c'est ce qui permet de savoir où écrire sans relire 340 lignes.

| Fichier | Contenu | Brique |
|---|---|---|
| `config/venues.yaml` | où et comment on exécute : spot/margin OKX, actions Euronext, coûts | `app/core/bot_identity.py` |
| `config/risk.yaml` | combien engager, quand refuser, où mettre le stop | `app/core/risk_gate.py` |
| `config/data.yaml` | ce que le bot regarde : symboles, univers, fournisseurs | `app/engine/scanner.py` |
| `config/lifecycle.yaml` | quels bots vivent, avec quel capital | `app/live/slot_lifecycle.py` |
| `config/ops.yaml` | API web, logs, base, alertes | `app/api/` |

Les **paramètres de stratégies** ne sont dans aucun de ces fichiers : chaque
stratégie porte les siens dans `strategies/<nom>.yaml`, avec ses
`optimizer_results`.

Deux règles : une section YAML vit dans **un seul** fichier (la déclarer deux
fois fait échouer le chargement, plutôt que de laisser l'ordre de lecture
trancher en silence), et une config monolithique reste valide — sans
`include:`, tout peut revenir dans `config.yaml`.

Les secrets ne vivent **pas** dans les fichiers de config : ils référencent des
variables d'environnement (`${VAR}`), résolues au chargement. Le plus simple
est de laisser `scripts/setup.sh` créer un fichier **`.env`** (jamais
versionné, cf. `.gitignore`) avec une `WEB_API_KEY` générée, puis d'y ajouter
vos clés exchange pour le live :

```bash
# .env (créé par scripts/setup.sh, à compléter pour le live)
WEB_API_KEY=<générée automatiquement>   # protège l'API web (/api/*)
OKX_API_KEY=...                          # live uniquement
OKX_API_SECRET=...                       # live uniquement
OKX_API_PASSWORD=...                     # passphrase OKX — 3e credential, live uniquement
```

```yaml
# config/venues.yaml (extraits)
exchange:
  name: okx
  api_key: ${OKX_API_KEY}          # résolu depuis l'environnement / .env
  api_secret: ${OKX_API_SECRET}
  api_password: ${OKX_API_PASSWORD}

venues:
  default: margin-isolated   # ← ce qui décide spot vs margin (OBLIGATOIRE)
  defs:
    spot:            {market_type: spot,   max_leverage: 1, allow_short: false}
    margin-isolated: {market_type: margin, margin_mode: isolated, max_leverage: 1}

# config/risk.yaml (extraits)
trading:
  capital: 1000         # Capital initial en USDC
  risk_per_trade: 0.01  # 1% du capital par trade
  timeframe: "1h"       # Timeframe principal
  paper_mode: true      # ← false = LIVE RÉEL ⚠️ DANGEREUX

# config/ops.yaml (extraits)
web:
  api_key: ${WEB_API_KEY}
```

> 🏛 **Spot ou margin, c'est la venue qui décide** — pas `exchange.margin`.
> `venues.default` est obligatoire dès que `venues.defs` existe, et le
> démarrage est refusé si une venue référencée n'existe pas. Une venue `spot`
> n'emprunte jamais : ni en crypto, ni sur les actions au comptant. Pour du
> margin réel, `market_type: margin` **et** `max_leverage > 1`.

> ⚠️ **En live (`paper_mode: false`), une variable `${...}` référencée mais
> absente/vide bloque le démarrage** (erreur explicite plutôt que des
> credentials vides et des échecs d'authentification silencieux). En paper,
> simple WARNING. Opt-out : `config.strict_env: false`.
>
> ✅ **Backtest et optimisation ne nécessitent AUCUNE clé API** (données publiques OKX).
>
> 🔁 **Migration depuis Binance** (MiCA) : voir [`docs/MIGRATION_OKX.md`](docs/MIGRATION_OKX.md).
> Le bot reste multi-exchange via CCXT — il suffit de changer `exchange.name`.

---

## ▶️ Démarrage

### Mode recommandé : Trading + Interface Web

```bash
python cli.py
```

> ⚠ **Dev local** : si `config.yaml` a `web.host: 0.0.0.0` sans `web.api_key`,
> le démarrage est refusé (l'API de trading serait ouverte au réseau — OPS-02).
> Pour des tests en local uniquement, mettre dans `config.yaml` :
>
> ```yaml
> web:
>   allow_insecure: true   # dev local uniquement
> ```
>
> (La variable d'environnement `ALLOW_INSECURE_WEB=1` fonctionne aussi.)
>
> Avant toute exposition réseau, définir `web.api_key`
> (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).

Lance automatiquement :
- Trader (live ou paper selon `config.yaml`)
- Serveur web sur `http://127.0.0.1:8000`

### Autres modes

```bash
# Forcer le mode paper trading (ignore config.yaml)
python cli.py --paper

# Backtest CLI
python cli.py --backtest BTC/USDC --timeframe 1h --limit 500

# Backtest avec Walk-Forward Analysis
python cli.py --backtest BTC/USDC --walk-forward

# Backtest avec Monte-Carlo
python cli.py --backtest BTC/USDC --monte-carlo

# Optimiser une stratégie
python cli.py --optimize pullback_trend --opt-method bayesian

# Scanner les marchés
python cli.py --scan

# Port personnalisé
python cli.py --port 9000

# Config fichier alternatif
python cli.py --config my_config.yaml
```

### Arguments disponibles

| Argument | Description |
|----------|-------------|
| `--backtest SYMBOL` | Lance un backtest CLI (ex: `BTC/USDC`) |
| `--timeframe TF` | Timeframe (défaut: config) |
| `--limit N` | Nombre de bougies (défaut: 500) |
| `--walk-forward` | Activer Walk-Forward Analysis |
| `--monte-carlo` | Activer Monte-Carlo (ex: perte probable) |
| `--optimize STRAT` | Optimiser une stratégie |
| `--opt-method METHOD` | Méthode d'optimisation: `grid`, `random`, `bayesian` (défaut: bayesian) |
| `--scan` | Scanner les marchés |
| `--live` | Forcer le mode live réel (désactive paper mode) |
| `--paper` | Forcer le mode paper trading |
| `--web` | Démarrer le serveur web seul (sans démarrer le bot de trading) |
| `--config PATH` | Fichier config alternatif |
| `--host IP` | Adresse d'écoute (défaut: `127.0.0.1`) |
| `--port N` | Port du serveur (défaut: `8000`) |

---

## 🌐 Interface Web

Accessible sur **http://127.0.0.1:8000**

L'UI tient en 5 pages méta à onglets. Les anciennes routes (`/scanner`,
`/optimizer`, `/config`…) restent valables : ce sont des redirections 308 vers
l'onglet correspondant.

| Page | URL | Description |
|------|-----|-------------|
| 📊 Portefeuille | `/portfolio` | Suivi du portefeuille, positions, trades, equity curve, journal des signaux et des notifications |
| 🤖 Mes Bots | `/bots` | Portefeuille de stratégies et cycle de vie (candidat → essai → actif → retiré) |
| 🧪 Laboratoire | `/lab` | Backtest, optimiseur (IS/OOS temps réel), entraînement ML, replay interactif, batch multi-TF, comparatif — un onglet chacun |
| 🔍 Marché | `/market` | Scanner SMC/ICT, Smart Graph, Smart Replay, dérivés (funding, OI, LSR) |
| ⚙️ Réglages | `/settings` | Presets de risque, timeframes, exchange, notifications, données, audit, préférences UI |

Les anciennes URLs suffixées (`/portfolio-v2`, `/bots-v2`, `/settings-v2`)
restent valables : ce sont des redirections 308 vers la page sans suffixe,
retiré en S11.

Onglets du Laboratoire : `backtest`, `optimizer`, `ml`, `replay`, `batch`
(*Multi-TF*), `compare`.

> ⚠ Les **paramètres par stratégie** ne sont éditables par aucune page :
> l'éditeur a disparu avec `config.html` (fin de Jinja2) sans être reconstruit.
> Passer par l'optimiseur (qui calcule puis applique) ou par les fichiers
> `strategies/*.yaml`.

Lien profond vers un onglet précis : `/lab?tab=optimizer`,
`/lab?tab=batch`, `/market?tab=smartgraph`, `/settings?tab=ui`…

---

## 📚 Stratégies disponibles

| Fichier | Nom | Description | Paramètres optimisables |
|---------|-----|-------------|-------------------------|
| `trend.py` | `trend` | EMA cross + ADX + filtre EMA200 | ema_fast, ema_slow, adx_min |
| `pullback_trend.py` | `pullback_trend` | Trend following, entrée sur pullback | ema_period, pullback_threshold |
| `supertrend_macd.py` | `supertrend_macd` | SuperTrend + MACD confluence | atr_period, macd_fast, macd_slow |
| `breakout.py` | `breakout` | Cassure de range + volume | lookback_bars, volume_multiplier |
| `ml_dynamic_threshold.py` | `ml_dynamic_threshold` | Seuil dynamique ML-based | - (optimisation séparée) |
| `*_no_ml.py` | `<nom>_no_ml` | Jumeaux **sans ML** des stratégies Opus Omnibus / seuil dynamique — même routing, mais `p_event`/`p_up` calculés par des proxys d'indicateurs (aucun modèle, aucun entraînement) | seuils de setups + coefficients de proxy (`p_up_gain`, `p_event_gain`, `p_event_center`) |

> **Jumeaux `_no_ml`** : `opus_omnibus_v8_no_ml`, `opus_omnibus_v10_no_ml`,
> `opus_omnibus_v11_no_ml`, `opus_omnibus_v11_followsetup_no_ml`,
> `ml_dynamic_threshold_no_ml`. Chacun est **autonome** (aucun import croisé, aucun
> modèle) et remplace les sorties ML par des proxys déterministes lus en O(1)
> depuis les colonnes `_pre_*` de `app/core/indicators.py` (`precompute_df`).
> Coût d'entraînement/maintenance nul. Voir le CHANGELOG 12.6.0.

Pour activer une stratégie :

```yaml
strategies:
  enabled:
    - pullback_trend
    - breakout
```

---

## 🎯 Optimiseur — Guide rapide

1. Ouvrir `http://localhost:3000/lab?tab=optimizer` (l'onglet Optimizer du
   Laboratoire ; `http://localhost:8000/optimizer` y redirige aussi)
2. Sélectionner les stratégies et timeframes
3. Choisir la méthode (**Bayesian** recommandé) et le nombre de trials (40-100)
4. Lancer — résultats IS/OOS en temps réel via Server-Sent Events
5. Cliquer **"Appliquer dans config.yaml"** pour enregistrer les meilleurs paramètres

### En ligne de commande — `optimize_runner.py`

Pour optimiser les stratégies **une à une sans l'interface** (même moteur que l'UI :
baseline → recherche → sauvegarde dans `strategies/<nom>.yaml`) :

```bash
python optimize_runner.py                      # toutes les stratégies, TFs du config
python optimize_runner.py --no-ml-only --apply # uniquement les jumeaux _no_ml, et applique
python optimize_runner.py --strategies opus_omnibus_v11_no_ml --tfs 1h --trials 30 --jobs 2
```

Exécution **séquentielle** (un job à la fois), **anti-veille** (empêche la mise en
veille du PC : `caffeinate`/`SetThreadExecutionState`/`systemd-inhibit`),
**thread-safe** (verrou exclusif : une seule instance) et **discrète** (priorité
processus abaissée, threads de calcul bornés via `--jobs`). `--help` pour toutes
les options.

### Paramètres optimisés vs globaux

| Type | Où | Modifiables par optimiseur ? |
|------|-----|-----|
| **Optimisés** | `strategies/<strategie>.yaml → params` | ✅ Oui |
| **Globaux** | `config/risk.yaml → trading` | ❌ Non (score_threshold, capital, risk_per_trade) |

---

## 📡 API REST — Endpoints principaux

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/api/status` | État du bot, capital, PnL, positions |
| `GET` | `/api/trades` | Liste des trades (filtres: symbol, strategy, limit) |
| `GET` | `/api/trades/export` | Export CSV des trades |
| `GET` | `/api/stats/daily` | Stats journalières (30 jours) |
| `POST` | `/api/backtest` | Lance un backtest |
| `GET` | `/api/backtest/settings` | Paramètres disponibles |
| `POST` | `/api/optimize/start` | Lance l'optimisation (async) |
| `GET` | `/api/optimize/status` | État des jobs d'optimisation |
| `GET` | `/api/optimize/stream` | SSE : progression temps réel |
| `POST` | `/api/optimize/apply` | Applique les meilleurs params |
| `GET` | `/api/config` | Configuration actuelle du bot |
| `POST` | `/api/config/strategies` | Change les stratégies activées |
| `POST` | `/api/config/timeframes` | Change les timeframes actifs |
| `POST` | `/api/bot/start` | Démarre le trading |
| `POST` | `/api/bot/stop` | Arrête le trading |
| `POST` | `/api/risk/reset-halt` | Réinitialise le circuit breaker |

---

## 📁 Architecture du projet

```
crypto_bot_v12/
├── cli.py                          ← Point d'entrée (CLI)
├── optimize_runner.py              ← Optimisation séquentielle CLI (anti-veille, verrou)
├── config.yaml                      ← Sommaire (`include:`)
├── config/                          ← Configuration par responsabilité
│   ├── venues.yaml                 ← Exécution : spot/margin, actions, coûts
│   ├── risk.yaml                   ← Sizing, vetos, trailing
│   ├── data.yaml                   ← Symboles, univers, fournisseurs
│   ├── lifecycle.yaml              ← Cycle de vie, budgets, optimiseur
│   └── ops.yaml                    ← Web, logs, base, notifications
├── requirements.txt                 ← Dépendances Python 3.12
├── README.md                        ← Ce fichier
├── ARCHITECTURE.md                  ← Documentation détaillée
├── CHANGELOG.md                     ← Historique des versions
├── CONTRIBUTING.md                  ← Guide contributeurs
├── docs/
│   ├── SETUP.md                    ← Installation détaillée
│   ├── API.md                      ← Documentation API
│   ├── STRATEGIES.md               ← Écrire une stratégie
│   └── TROUBLESHOOTING.md          ← Dépannage
├── app/
│   ├── __init__.py
│   ├── api/
│   │   └── main.py                 ← Routes FastAPI (v12)
│   ├── core/
│   │   ├── config.py               ← Chargement config YAML
│   │   ├── logger.py               ← Setup logging structuré
│   │   ├── database.py             ← SQLAlchemy ORM
│   │   ├── exchange.py             ← Connexion CCXT
│   │   ├── indicators.py           ← RSI, EMA, ADX, ATR…
│   │   ├── risk.py                 ← Gestion risque, circuit breaker
│   │   ├── trailing.py             ← Trailing stop
│   │   └── notifications.py        ← Telegram, WhatsApp, Email
│   ├── engine/
│   │   ├── engine.py               ← Moteur de signal
│   │   ├── backtest.py             ← Backtester, WalkForward, MonteCarlo
│   │   └── scanner.py              ← Scanner de marché
│   ├── strategies/
│   │   ├── base.py                 ← Classe de base
│   │   ├── indicators.py           ← Lib indicateurs partagés
│   │   ├── trend.py
│   │   ├── pullback_trend.py
│   │   ├── supertrend_macd.py
│   │   ├── breakout.py
│   │   └── ml_dynamic_threshold.py
│   ├── optimizer/
│   │   ├── optimizer.py            ← Grid/Random/Bayesian search
│   │   └── auto_optimizer.py       ← Jobs async, SSE
│   ├── live/
│   │   └── live_trader.py          ← Boucle live/paper trading
│   ├── ml/
│   │   └── trainer.py              ← MLStrategyTrainer (cycle de vie BaseStrategyML)
│   ├── utils/
│   │   ├── serializers.py          ← Fonctions sérialisation JSON
│   │   └── cache.py                ← Caching stratégies découvertes
│   └── web/
│       └── templates/
│           ├── base.html           ← Template de base (Jinja2)
│           ├── dashboard.html      ← Page Live
│           ├── backtest.html       ← Page Backtest
│           ├── optimizer.html      ← Page Optimiseur
│           ├── scanner.html        ← Page Scanner
│           └── config.html         ← Page Config
├── tests/
│   ├── test_backtest.py
│   ├── test_optimizer.py
│   ├── test_api.py
│   └── test_strategies.py
├── logs/                            ← Logs (créé automatiquement)
└── deploy/                          ← Scripts déploiement
    ├── systemd/crypto-bot.service  ← Service Ubuntu
    └── docker/Dockerfile           ← Conteneur Docker
```

---

## ⚠️ Notes importantes

- 🔴 **Paper mode par défaut** — Aucun ordre réel n'est passé tant que `paper_mode: false` n'est pas défini
- 📌 L'optimiseur **ne modifie jamais** les paramètres globaux (`score_threshold`, `capital`, `risk_per_trade`)
- 💾 Un backup `config.yaml.bak` est créé automatiquement avant chaque application de paramètres
- 🛡️ **Authentification API** — Définir une clé dans `web.api_key` pour sécuriser les endpoints sensibles
- 🔒 **CORS restreint en production** — Adapter `allow_origins` dans `app/api/main.py`
- 📊 Les stratégies ML (`ml_dynamic_threshold`) sont exclues de l'optimiseur classique — utiliser `/api/ml/optimize` séparément

---

## 🐛 Dépannage

### LiveTrader non initialisé ?
```
[Main] Erreur initialisation LiveTrader : ...
```
→ Vérifier la connexion CCXT, clés API, format exchange

### Pas de données historiques ?
```
[API] Backtest error : Aucune donnée reçue
```
→ Vérifier que l'exchange (OKX) fonctionne, augmenter le `--limit`, essayer un autre symbol

### Performance lente ?
- Réduire `--limit` pour backtest
- Utiliser moins de trials pour optimiseur (40 au lieu de 100)
- Vérifier CPU/RAM disponible

Voir [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) pour plus de détails.

---

## 📖 Documentation complète

- [**ARCHITECTURE.md**](ARCHITECTURE.md) — Vue d'ensemble technique
- [**CHANGELOG.md**](CHANGELOG.md) — Historique V7 → V8 → V9 → V10 → V11 → V12
- [**CONTRIBUTING.md**](CONTRIBUTING.md) — Contribution au projet
- [**docs/SETUP.md**](docs/SETUP.md) — Installation détaillée par OS
- [**docs/API.md**](docs/API.md) — Documentation API complète
- [**docs/STRATEGIES.md**](docs/STRATEGIES.md) — Écrire une stratégie personnalisée

---

## 📜 Licence

MIT License (voir LICENSE file)

---

## 🤝 Support

- 📧 Email: contact@example.com
- 💬 GitHub Issues: [Signaler un bug](../../issues)
- 📚 Wiki: [Discussions](../../discussions)

---

**Crypto Bot V12** — Bon trading ! 🚀
