# ⚡ Crypto Bot V12

Bot de trading algorithmique multi-stratégies avec interface web, backtest avancé et optimiseur de paramètres.

---

## 📋 Fonctionnalités

- **Live / Paper trading** — Exécution sur Binance (et autres exchanges via CCXT) avec gestion du risque, circuit breaker, trailing stop
- **Backtest avancé** — Jusqu'à 8 000 bougies, Walk-Forward Analysis, Monte-Carlo, comparaison multi-stratégies, graphique de prix avec signaux
- **Optimiseur** — Random Search / Bayesian UCB / Grid Search IS/OOS avec détection d'overfitting, application directe dans `config.yaml`
- **ML** — Stratégie basée sur Random Forest / Logistic Regression (optionnel)
- **Notifications** — Telegram et WhatsApp
- **Interface web** — 5 pages (Dashboard Live, Backtest, Optimiseur, Scanner, Configuration)

---

## ⚙️ Prérequis

- **Python 3.12 OBLIGATOIRE** (voir [Installation](#installation-détaillée) pour Ubuntu 22.04 / 24.04)
- pip

---

## 🚀 Installation rapide

```bash
# 1. Cloner / décompresser le projet
cd crypto_bot_v12

# 2. Créer un environnement virtuel Python 3.12
python3.12 -m venv .venv
source .venv/bin/activate      # Linux / macOS
.venv\Scripts\activate         # Windows

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Éditer la configuration
cp config.yaml config.yaml.example  # Garder une trace
# Éditer config.yaml avec vos clés API, capital, etc.
```

### Installation détaillée par OS

**Ubuntu 22.04 (Python 3.10 par défaut):**
```bash
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update && sudo apt install -y python3.12 python3.12-venv python3.12-dev
python3.12 -m venv /opt/crypto_bot/.venv
source /opt/crypto_bot/.venv/bin/activate
pip install -r requirements.txt
```

**Ubuntu 24.04 (Python 3.12 natif):**
```bash
python3.12 -m venv /opt/crypto_bot/.venv
source /opt/crypto_bot/.venv/bin/activate
pip install -r requirements.txt
```

**macOS / Windows:** Utiliser Python 3.12 depuis [python.org](https://www.python.org/downloads/), puis créer venv.

---

## 🔧 Configuration

Éditer `config.yaml` — les champs marqués `<<REQUIRED>>` sont obligatoires pour le live trading :

```yaml
exchange:
  name: binance
  api_key: ""           # Requis pour live trading UNIQUEMENT
  api_secret: ""        # Requis pour live trading UNIQUEMENT

trading:
  capital: 1000         # Capital initial en USDC
  risk_per_trade: 0.01  # 1% du capital par trade
  timeframe: "1h"       # Timeframe principal
  paper_mode: true      # ← false = LIVE RÉEL ⚠️ DANGEREUX
```

> ✅ **Backtest et optimisation ne nécessitent AUCUNE clé API** (données publiques Binance).

---

## ▶️ Démarrage

### Mode recommandé : Trading + Interface Web

```bash
python cli.py
```

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

| Page | URL | Description |
|------|-----|-------------|
| 📊 Dashboard Live | `/` | Suivi du portefeuille, positions, trades, equity curve, journal des signaux |
| 📈 Backtest | `/backtest` | Test de stratégies, Walk-Forward, Monte-Carlo, comparaison multi-stratégies |
| ⚡ Optimiseur | `/optimizer` | Optimisation des paramètres (IS/OOS), résultats en temps réel, application directe |
| 🔍 Scanner | `/scanner` | Screening des marchés par stratégie & timeframe |
| ⚙️ Configuration | `/config` | Édition des stratégies, paramètres, notifications, margin trading |

---

## 📚 Stratégies disponibles

| Fichier | Nom | Description | Paramètres optimisables |
|---------|-----|-------------|-------------------------|
| `trend.py` | `trend` | EMA cross + ADX + filtre EMA200 | ema_fast, ema_slow, adx_min |
| `pullback_trend.py` | `pullback_trend` | Trend following, entrée sur pullback | ema_period, pullback_threshold |
| `supertrend_macd.py` | `supertrend_macd` | SuperTrend + MACD confluence | atr_period, macd_fast, macd_slow |
| `breakout.py` | `breakout` | Cassure de range + volume | lookback_bars, volume_multiplier |
| `ml_dynamic_threshold.py` | `ml_dynamic_threshold` | Seuil dynamique ML-based | - (optimisation séparée) |

Pour activer une stratégie :

```yaml
strategies:
  enabled:
    - pullback_trend
    - breakout
```

---

## 🎯 Optimiseur — Guide rapide

1. Ouvrir `http://localhost:8000/optimizer`
2. Sélectionner les stratégies et timeframes
3. Choisir la méthode (**Bayesian** recommandé) et le nombre de trials (40-100)
4. Lancer — résultats IS/OOS en temps réel via Server-Sent Events
5. Cliquer **"Appliquer dans config.yaml"** pour enregistrer les meilleurs paramètres

### Paramètres optimisés vs globaux

| Type | Où | Modifiables par optimiseur ? |
|------|-----|-----|
| **Optimisés** | `config.yaml → strategy_params.<strategie>` | ✅ Oui |
| **Globaux** | `config.yaml → trading` | ❌ Non (score_threshold, capital, risk_per_trade) |

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
├── config.yaml                      ← Configuration principale
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
→ Vérifier que Binance fonctionne, augmenter le `--limit`, essayer un autre symbol

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
