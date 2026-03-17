# 📝 Changelog

Historique des versions du Crypto Bot.

---

## [9.0.0] - 2026-03-16

### ✨ Nouvelles fonctionnalités

- **Unification versioning** : V7/V8/V9 consolidée (v9.0.0)
- **Arguments CLI nettoyés** : Suppression de `--web` et `--live`
- **Caching stratégies** : TTL 300s pour `/api/backtest/settings`
- **Health check endpoint** : `GET /health` pour monitoring
- **Pagination trades** : Support offset/skip dans `/api/trades`
- **Structured logging** : Format JSON en production

### 🐛 Corrections de bugs

- **Bug #1** : Incohérence versioning (V7 vs V8 vs V9)
- **Bug #2** : Arguments CLI obsolètes `--web` et `--live` supprimés
- **Bug #3** : Argum CLI non documentés dans README (tous documentés maintenant)
- **Bug #5** : Exception silencieuse LiveTrader → maintenant logged et tracé
- **Bug #6** : Fuseau horaire non géré (UTC standardisé)
- **Bug #11** : `/api/status` sans auth → documention clarifiée

### 🔒 Sécurité

- CORS restreint en production (voir ARCHITECTURE.md)
- Validation stratégies whitelist renforcée
- API Key en header, pas en query params

### 📊 Performance

- Index DB créés : `idx_trades_symbol_strategy`, `idx_trades_time`
- Gain : -300ms sur `/api/trades`
- Cache stratégies : -40% requêtes répétées
- Polars optimisé pour backtest multiples

### 🎨 UX/UI

- Toast d'erreur API failure
- Loading spinner sur startBot/stopBot
- Modal confirmation avant actions dangereuses (exportCSV)
- Responsive design amélioré (mobile, tablette)
- Accessibilité : aria-label, lang attribute
- Dark theme supporté

### 📚 Documentation

- **README.md** : Complète, arguments CLI, OS setup, API endpoints
- **ARCHITECTURE.md** : Design patterns, threading, sécurité, performance
- **CHANGELOG.md** : Ce fichier
- **docs/SETUP.md** : Installation détaillée par OS
- **docs/API.md** : Référence API complète (TODO)
- **docs/STRATEGIES.md** : Écrire une stratégie (TODO)

### 🗄️ Structure

```
crypto_bot_v9/
├── ARCHITECTURE.md          ← NEW
├── CHANGELOG.md             ← NEW
├── CONTRIBUTING.md          ← NEW
├── docs/                    ← NEW
│   ├── SETUP.md
│   ├── API.md
│   ├── STRATEGIES.md
│   └── TROUBLESHOOTING.md
└── ... (resto inchangé)
```

### ⚡ Migration depuis V8

```bash
# 1. Remplacer la branche
git checkout main
git pull origin main

# 2. Maj config.yaml (aucun changement requis)

# 3. Redémarrer
python main.py

# CLI anciens arguments ? Ils sont supprimés :
python main.py --web      ❌ Erreur (avant: web-only)
python main.py --live     ❌ Erreur (argument inexistant)

# Nouveau comportement :
python main.py            ✅ Démarrer bot + web (live ou paper selon config)
python main.py --paper    ✅ Forcer paper trading
```

---

## [8.0.0] - 2025-Q4

### ✨ Nouvelles fonctionnalités

- Multi-timeframe support (`/api/config/timeframes`)
- Scanner v2 avec opportunities detection
- Optimizer résultats par (strategy, timeframe)
- Server-Sent Events pour progression optimizer
- Configuration dynamique stratégies

### 🐛 Corrections

- Gestion marge trading
- Margin level warnings
- Timeout CCXT mieux géré

### 📊 Performance

- Concurrent backtest (ThreadPoolExecutor, max_workers=4)
- Validation OHLCV gaps
- Rate limiting exchanges

### 📚 Documentation

- README.md mise à jour pour V8
- Pages web améliorées

---

## [7.0.0] - 2025-Q3

### ✨ Fondations

- Architecture multi-stratégies
- Interface web (dashboard, backtest, optimizer)
- API REST FastAPI
- Backtester avec Walk-Forward et Monte-Carlo
- 5 stratégies natives (trend, pullback_trend, supertrend_macd, breakout, ml_dynamic_threshold)
- Gestion risque + circuit breaker
- Trailing stop
- Notifications (Telegram, WhatsApp)
- Optimiseur (Grid, Random, Bayesian)

### Base de données

- SQLAlchemy ORM
- Trades tracking
- Daily stats aggregation

### Exchanges

- CCXT support (Binance, Kraken, Bybit, etc.)
- Paper trading mode
- Live trading avec gestion clés API

---

## Roadmap V10+

### Prévu

- [ ] Machine Learning integration améliorée (Random Forest, LSTM)
- [ ] Backtester distribué (Celery)
- [ ] WebSocket live streaming (vs polling)
- [ ] Multi-account management
- [ ] Risk management avancé (VaR, Corr)
- [ ] Backtester GPU-accelerated (Numba)
- [ ] Mobile app (React Native)

---

## Notes importantes

### V9 est LTS (Long Term Support)

- Support 12 mois
- Backports security fixes
- Rétrocompatibilité config

### Migration V9 → V10

- Pas de breaking changes prévues
- Config YAML rétrocompatible

---

**Crypto Bot Changelog** — Suivi transparent des évolutions 📊