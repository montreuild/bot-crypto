# ⚡ Crypto Bot V7

Bot de trading algorithmique multi-stratégies avec interface web, backtest avancé et optimiseur de paramètres.

---

## Fonctionnalités

- **Live / Paper trading** — Exécution sur Binance (et autres exchanges via CCXT) avec gestion du risque, circuit breaker, trailing stop
- **Backtest** — Jusqu'à 8 000 bougies, Walk-Forward Analysis, Monte-Carlo, comparaison multi-stratégies, graphique de prix avec signaux
- **Optimiseur** — Random Search / Bayesian UCB / Grid Search IS/OOS avec détection d'overfitting, application directe dans `config.yaml`
- **ML** — Stratégie basée sur Random Forest / Logistic Regression (optionnel)
- **Notifications** — Telegram et WhatsApp
- **Interface web** — 3 pages dédiées (Live, Backtest, Optimiseur)

---

## Installation

### Prérequis

- Python **3.10+**
- pip

### Étapes

```bash
# 1. Cloner / décompresser le projet
cd crypto_bot_v7

# 2. (Optionnel) Créer un environnement virtuel
python -m venv .venv
source .venv/bin/activate      # Linux / macOS
.venv\Scripts\activate         # Windows

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Copier et configurer
cp config.yaml config.yaml     # déjà présent, éditer directement
```

---

## Configuration

Éditer `config.yaml` — les champs obligatoires sont marqués `<<REQUIRED>>` :

```yaml
exchange:
  name: binance
  api_key: ""        # Requis pour le live trading uniquement
  api_secret: ""

trading:
  capital: 1000
  risk_per_trade: 0.01
  timeframe: "1h"
  paper_mode: true   # ← false = LIVE RÉEL ⚠
```

> **Backtest et optimisation** ne nécessitent **aucune clé API** (données publiques Binance).

---

## Lancement

```bash
# Mode recommandé : paper trading + interface web
python main.py

# Backtest / optimiseur uniquement (aucune clé API requise)
python main.py --no-bot

# Port personnalisé
python main.py --port 9000

# Force paper trading (ignore config.yaml)
python main.py --paper
```

Options disponibles :

| Option | Description |
|--------|-------------|
| `--config <path>` | Fichier de config alternatif (défaut : `config.yaml`) |
| `--paper` | Force le mode paper trading |
| `--no-bot` | Serveur web seul, sans boucle de trading |
| `--host <ip>` | Adresse d'écoute (défaut : `127.0.0.1`) |
| `--port <n>` | Port (défaut : `8000`) |
| `--reload` | Rechargement auto (développement) |

Ou directement via uvicorn :

```bash
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

Interface accessible sur **http://127.0.0.1:8000**

---

## Pages web

| Page | URL | Description |
|------|-----|-------------|
| Live / Paper | `http://localhost:8000/` | Suivi du portefeuille, positions, trades, equity curve |
| Backtest | `http://localhost:8000/backtest` | Test de stratégies sur données historiques |
| Optimiseur | `http://localhost:8000/optimizer` | Optimisation des paramètres IS/OOS |
| API docs | `http://localhost:8000/docs` | Documentation Swagger auto-générée |

---

## Stratégies disponibles

| Fichier | Nom | Description |
|---------|-----|-------------|
| `trend.py` | `trend` | EMA cross + ADX + filtre EMA200, anti-overextension |
| `pullback_trend.py` | `pullback_trend` | Trend following avec entrée sur pullback EMA |
| `supertrend_macd.py` | `supertrend_macd` | Confluence SuperTrend + MACD (4 cas d'entrée) |
| `breakout.py` | `breakout` | Cassure de range avec confirmation volume/ATR |
| `ml_dynamic_threshold.py` | `ml_dynamic_threshold` | Seuil dynamique basé sur ML |

Pour activer une stratégie, l'ajouter dans `config.yaml` :
```yaml
strategies:
  enabled:
    - pullback_trend
    - breakout
```

---

## Optimiseur — Guide rapide

1. Ouvrir `http://localhost:8000/optimizer`
2. Sélectionner les stratégies à optimiser
3. Choisir la méthode (**Bayesian** recommandé) et le nombre de trials
4. Lancer — les résultats IS/OOS apparaissent en temps réel
5. Cliquer **"Appliquer dans config.yaml"** pour enregistrer les meilleurs paramètres

### Paramètres optimisés vs globaux

| Type | Où | Exemple |
|------|----|---------|
| **Optimisés** | `config.yaml → strategy_params.<strategie>` | `ema_fast`, `adx_min`, `cooldown`… |
| **Globaux** (non touchés) | `config.yaml → trading` | `score_threshold`, `risk_per_trade`, `capital` |

---

## Structure du projet

```
crypto_bot_v7/
├── config.yaml                  ← Configuration principale
├── requirements.txt
├── README.md
└── app/
    ├── api/
    │   └── main.py              ← Routes FastAPI (dashboard, backtest, optimizer, API)
    ├── core/
    │   ├── config.py            ← Chargement et validation de la config
    │   ├── database.py          ← SQLAlchemy (trades, stats journalières)
    │   ├── exchange.py          ← Connexion CCXT
    │   ├── indicators.py        ← RSI, EMA, ADX, ATR…
    │   ├── risk.py              ← Gestion du risque, circuit breaker
    │   └── trailing.py          ← Trailing stop
    ├── engine/
    │   ├── engine.py            ← Moteur de signal (BaseStrategy)
    │   ├── backtest.py          ← Backtester, WalkForward, MonteCarlo
    │   └── scanner.py           ← Scanner de marché
    ├── strategies/
    │   ├── pullback_trend.py
    │   ├── breakout.py
    │   └── ml_dynamic_threshold.py
    ├── optimizer/
    │   ├── optimizer.py         ← StrategyOptimizer (Random/Bayesian/Grid)
    │   └── auto_optimizer.py    ← Jobs asynchrones, Server-Sent Events
    ├── live/
    │   └── live_trader.py       ← Boucle de trading live/paper
    ├── ml/
    │   ├── model.py
    │   ├── features.py
    │   └── predictor.py
    ├── notifications/
    │   └── notifier.py          ← Telegram, WhatsApp
    └── web/
        └── templates/
            ├── dashboard.html   ← Page Live / Paper
            ├── backtest.html    ← Page Backtest
            └── optimizer.html   ← Page Optimiseur
```

---

## API REST — Endpoints principaux

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/api/status` | État du bot, capital, PnL, positions |
| `GET` | `/api/trades` | Liste des trades (filtres : symbol, strategy, limit) |
| `GET` | `/api/trades/export` | Export CSV |
| `GET` | `/api/stats/daily` | Stats journalières (30 jours) |
| `POST` | `/api/backtest` | Lance un backtest |
| `GET` | `/api/backtest/settings` | Paramètres disponibles |
| `POST` | `/api/optimize/start` | Lance l'optimisation (async) |
| `GET` | `/api/optimize/status` | État des jobs d'optimisation |
| `GET` | `/api/optimize/stream` | SSE : progression temps réel |
| `POST` | `/api/optimize/apply` | Applique les meilleurs params |
| `GET` | `/api/optimize/spaces` | Espaces de paramètres par stratégie |
| `POST` | `/api/risk/reset-halt` | Réinitialise le circuit breaker |
| `GET` | `/api/risk` | État du gestionnaire de risque |

---

## Notes importantes

- En **paper mode**, aucun ordre réel n'est passé — idéal pour tester avant de passer en live
- L'optimiseur **ne modifie jamais** `score_threshold`, `capital` ni `risk_per_trade` (paramètres globaux dans `[trading]`)
- Un backup `config.yaml.bak` est créé automatiquement avant chaque application de paramètres
- Les stratégies ML (`ml_dynamic_threshold`) sont **exclues** de l'optimiseur classique (coût prohibitif par trial) — utiliser `/api/ml/optimize` séparément
