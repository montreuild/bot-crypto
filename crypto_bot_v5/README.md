# Crypto Bot V5 — Architecture Pro

Bot de trading algorithmique modulaire avec 8 modules complets.

## Installation

```bash
pip install -r requirements.txt
# Optionnel :
pip install xgboost optuna python-telegram-bot
```

## Démarrage rapide

```bash
# 1. Configurer config.yaml
# 2. Backtest CLI complet
python main.py --backtest BTC/USDC --timeframe 15m --limit 1000
python main.py --backtest BTC/USDC --walk-forward --monte-carlo

# 3. Scanner les marchés
python main.py --scan

# 4. Optimiser une stratégie
python main.py --optimize trend --opt-method bayesian
python main.py --optimize breakout --opt-method grid

# 5. Dashboard web seul (backtest/scanner)
python main.py
# → http://127.0.0.1:8000

# 6. Live trading (paper mode par défaut)
python main.py --live
```

## Modules

| Module | Description |
|--------|-------------|
| `core/config.py` | Validation stricte de la config au démarrage |
| `core/risk.py` | Risk manager dynamique + circuit breaker |
| `core/notifications.py` | Telegram + WhatsApp |
| `scanner/scanner.py` | Scanner multi-actifs USDC, détection régime |
| `engine/backtest.py` | Backtest Pro : spread, latence, MAE/MFE, Walk-Forward, Monte-Carlo |
| `optimizer/optimizer.py` | Grid/Random/Bayesian + OOS validation |
| `ml/model.py` | Logistic, Random Forest, XGBoost + blend |
| `live/live_trader.py` | Live trading sécurisé + circuit breaker |
| `api/main.py` | API FastAPI complète |

## API REST

```
GET  /api/status              — Statut du bot
GET  /api/config              — Config (sans clés)
GET  /api/trades              — Historique des trades
GET  /api/stats/daily         — Stats journalières
GET  /api/risk                — État du risk manager
POST /api/risk/reset-halt     — Réinitialiser le circuit breaker
POST /api/backtest            — Backtest par stratégie
POST /api/backtest?walk_forward=true&monte_carlo=true
GET  /api/scanner             — Scanner en temps réel
POST /api/optimize            — Optimiseur de paramètres
POST /api/ml/train            — Entraîner le modèle ML
GET  /api/backtest/settings   — Paramètres courants
```

## Circuit Breaker

Le bot s'arrête automatiquement si :
- DD journalier ≥ `daily_drawdown_limit` (défaut : 5%)
- DD global ≥ `max_drawdown_global` (défaut : 20%)
- Trop de trades/minute (`max_trades_per_minute`)

Réinitialisation : `POST /api/risk/reset-halt`

## Notifications

Configurer dans `config.yaml` :
```yaml
notifications:
  telegram_enabled: true
  telegram_bot_token: "BOT_TOKEN"
  telegram_chat_id: "CHAT_ID"
  whatsapp_enabled: true
  whatsapp_token: "CALLMEBOT_API_KEY"
  whatsapp_number: "+33XXXXXXXXX"
```

## Module ML

```yaml
ml:
  enabled: true
  model: "random_forest"   # logistic | random_forest | xgboost
  blend_weight: 0.3        # 30% ML, 70% règles
  min_samples: 200
```

Entraîner : `python main.py` puis `POST /api/ml/train?symbol=BTC/USDC`
