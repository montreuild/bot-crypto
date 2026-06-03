# 📝 Changelog

Historique des versions du Crypto Bot.

---

## [12.1.0] - 2026-06-03

### ✨ Nouvelle stratégie — `harmonic_regime` (confluence régime-adaptative)

Stratégie de swing **data-driven** issue d'une analyse quantitative exhaustive de
BTC 1h/4h/1d (`research/analysis_btc.py`, `research/STRATEGIE_harmonic_regime.md`).

**Edges retenus (mesurés, significatifs) :**
- LONG trend-momentum (close>EMA50>EMA200 + ADX + breakout) — t≈7-8, multi-TF.
- Clustering de volatilité (ACF|r|≈0.15-0.28) — timing d'entrée + sizing ATR.
- SHORT **défensif** en macro-bear CONFIRMÉ uniquement (propre sur 1d).
- Mean-reversion long douce en range (RSI survente). Cycle FFT + Fibonacci en
  confirmation/zones à faible poids (non significatifs comme edges autonomes).

**Posture :** longs en tendance + **FLAT en bear** (protège du DD -72 % du
Buy & Hold) + shorts opportunistes filtrés. Sizing par risque 1 %/trade, stop
ATR, trailing multi-phase (`TrailingStopManager`), max-hold.

**Backtest (7.5 ans, frais/spread/borrow réalistes) :**
- 4h : **+33.4 %**, Sharpe **5.29**, max DD **-7.3 %**, PF 1.41 ; walk-forward OOS
  consistance 60 %. BEAR 2022 : **-1.1 % vs B&H -53 %** (alpha +52 pt).
- 1d : **+11.5 %**, Sharpe **2.90**, max DD **-4.7 %**, PF 1.56 ; walk-forward OOS
  consistance **100 %**.
- ⚠️ 1h **non recommandé** : edge directionnel < coût round-trip → non rentable.

### 🔧 Fichiers ajoutés

| Fichier | Rôle |
|---------|------|
| `app/strategies/harmonic_regime.py` | Stratégie (`BaseStrategy`, score de confluence) |
| `strategies/harmonic_regime.yaml` | Params + `optimizer_results` validés (4h, 1d) |
| `tests/test_harmonic_regime.py` | Tests unitaires + intégration backtest |
| `research/analysis_btc.py` | Analyse quantitative reproductible (9 sections) |
| `research/backtest_harmonic.py` | Harnais backtest/walk-forward/split bull-bear |
| `research/STRATEGIE_harmonic_regime.md` | Rapport analyse → conception → validation |

---

## [12.0.0] - 2026-03-25

### ✨ Nouvelles fonctionnalités

#### Paper mode réaliste — slippage, capital settled, persistence

Amélioration majeure du mode simulation pour des résultats plus proches du trading réel.

**Slippage adverse :**
- Nouveau paramètre `paper_slippage` (défaut `0.001` = 0,1 %) dans `config.yaml` et l'API
- Chaque fill applique un slippage défavorable : les achats se font plus cher, les ventes moins cher
- Configurable via l'interface web (section *Paramètres de trading*)

**Suivi capital settled (`_paper_base`) :**
- Le capital settled (equity réalisée) est tracé séparément du `capital_display`
- Le PnL non réalisé des positions ouvertes est exclu du sizing du risque
- `capital_display = settled + PnL non réalisé` (synchronisé à chaque cycle paper)

**Persistence entre sessions :**
- `_restore_paper_base()` restaure le capital settled depuis la dernière `DailyStats.equity_close` en BDD
- Pas de remise à zéro du capital entre redémarrages en paper mode

**Protection capital insuffisant :**
- `_pre_execution_check()` en paper mode bloque une entrée si le capital disponible
  (`settled − notionals verrouillés`) est inférieur au notional demandé

### 🔧 Fichiers modifiés

| Fichier | Changement |
|---------|------------|
| `app/core/config.py` | `paper_slippage: 0.001` ajouté aux defaults |
| `app/live/live_trader.py` | `_paper_base`, `_restore_paper_base()`, `_sync_paper_balance()`, `_pre_execution_check()` |
| `app/live/position_mixin.py` | Slippage appliqué aux fills paper |
| `app/api/routes/config.py` | `paper_slippage` exposé dans l'API de configuration |
| `app/web/templates/config.html` | Champ *Paper slippage %* dans l'interface |

### 🗄️ Structure V12

```
app/
└── live/
    ├── live_trader.py     ← _paper_base, _restore_paper_base, _sync_paper_balance
    └── position_mixin.py  ← slippage adverse sur fills paper
```

---

## [11.0.0] - 2026-03-18

### ✨ Nouvelles fonctionnalités

#### CandleStore — Stockage Parquet persistant des bougies OHLCV

Nouveau module `app/core/candle_store.py` qui centralise tous les accès aux données OHLCV.

**Architecture :**
```
data/
└── ohlcv/
    ├── BTC_USDC/
    │   ├── 1h.parquet    (~80 KB pour 2 000 bougies)
    │   ├── 4h.parquet
    │   └── 1d.parquet
    ├── ETH_USDC/
    │   └── ...
    └── ...
```

**Principe de fetch :**
```
1er démarrage   → fetch complet depuis l'exchange (paginé si > 1 000 bougies)
                  → persistence Parquet (compression zstd)

Cycles suivants → lecture Parquet locale (< 5 ms)
                  → fetch incrémental : uniquement les nouvelles bougies
                  → merge + déduplication + persistence
```

**Couverture complète — tous les callers :**

| Module | Avant | Après |
|--------|-------|-------|
| `MarketScanner.fetch_ohlcv()` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| `engine.Scanner._scan_pair()` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| `engine.Scanner.get_ohlcv_df()` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| `API /api/backtest` | `fetch_ohlcv_paged()` | `CandleStore.fetch()` |
| `API /api/optimize/start` | `fetch_ohlcv_paged()` | `CandleStore.fetch()` |
| `API /api/ml/train` | `fetch_ohlcv_paged()` | `CandleStore.fetch()` |
| `CLI --backtest` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| `CLI --optimize` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| LiveTrader (tous les cas) | via `scanner.fetch_ohlcv` | via `MarketScanner` → store |

**Bénéfices :**
- Indépendance exchange : backtest, optimizer, ML training utilisent le cache local
- Historique croissant automatiquement à chaque cycle live
- Aucune nouvelle dépendance (`polars` supporte Parquet nativement via PyArrow)
- Thread-safe : verrou par fichier (live trader multi-thread)
- Nouveau endpoint `GET /api/candles/stats` pour inspecter le cache

#### Découverte automatique des stratégies (`app/strategies/registry.py`)

Chaque stratégie porte ses propres métadonnées d'optimisation en attributs de classe.
L'optimiseur les découvre automatiquement — aucun fichier central à modifier
pour ajouter une nouvelle stratégie.

### 🏗️ Refactorisation (optimizer.py)

- `STRATEGY_TIMEFRAMES`, `PARAM_SPACES`, `FIXED_PARAMS` ne sont plus codés en dur
  dans `optimizer.py`. Ces dicts sont construits dynamiquement par le registre.
- Chaque `Strategy` déclare maintenant directement :
  - `timeframes`   : `List[str]` — TFs recommandés pour l'optimisation
  - `param_space`  : `Dict[str, List]` — espace de recherche des hyperparamètres
  - `fixed_params` : `Dict[str, Any]` — paramètres fixes (non optimisables)
- `BaseStrategy` expose ces attributs avec des valeurs par défaut vides.
- `RECOMMENDED_LIMIT` (config globale par TF) reste dans `optimizer.py`.
- Rétrocompatibilité totale : tous les imports existants fonctionnent.

### 🔧 Impact pour ajouter une nouvelle stratégie

**Avant (V10)** : 4 fichiers à modifier (stratégie + optimizer.py + config.yaml + doc).

**Après (V11)** : 1 seul fichier :
```python
# app/strategies/ma_nouvelle_strategie.py
class Strategy(BaseStrategy):
    name         = "ma_nouvelle_strategie"
    timeframes   = ["1h", "4h"]
    param_space  = {"period": [10, 20, 30], "rr_min": [1.3, 1.5, 2.0]}
    fixed_params = {}
    # ... min_bars_required(), score() ...
```
L'optimiseur, l'API et le live trader la détectent automatiquement.

### 🗄️ Structure V11

```
app/
└── core/
    ├── candle_store.py    ← NOUVEAU — stockage Parquet OHLCV
    ├── indicators.py
    ├── database.py
    └── exchange.py

data/
└── ohlcv/                 ← NOUVEAU — données Parquet (gitignore)
    └── {SYMBOL}/{TF}.parquet
```

---

## [10.0.0] - 2026-03-18

### ✨ Nouvelles fonctionnalités

- **Fichier indicateurs unifié** : `app/strategies/indicators.py` est **supprimé**.
  `app/core/indicators.py` est le seul et unique module d'indicateurs. Toutes les stratégies,
  le moteur et le live trader importent directement depuis `app.core.indicators`.
- **`__version__ = "10.0.0"`** dans `app/core/indicators.py` pour traçabilité programmatique.

### ⚡ Performance — Portage maximum vers Polars

Toutes les fonctions d'indicateurs sont désormais en Polars pur ; NumPy est limité à la seule
boucle séquentielle du SuperTrend (dépendance `upper[i] = f(upper[i-1])` incontournable).

| Fonction | Avant (v9) | Après (v10) |
|---|---|---|
| `_true_range` | `np.maximum` + 3 × `to_numpy()` | `pl.max_horizontal` dans DataFrame temporaire |
| `rsi` | `to_numpy()` + `np.where` + `pl.Series(arr)` | `.clip(lower_bound=1e-10)` pur Polars |
| `adx` | 6 × round-trip numpy, `np.where`, `pl.Series(arr)` | Multiplication booléenne `(up > dn).cast(Float64)` + `.clip()` |
| `supertrend` | TR/ATR calculés en numpy + boucle | TR/ATR via `_true_range()` Polars ; boucle seule en numpy |
| `precompute_df` | `np.maximum` + `pl.when(Series)` mélangé | Entièrement Polars Series + `.clip()` |

### 🐛 Corrections de bugs

- **`precompute_df`** : `pl.when(Series)` retournait un `Expr` mélangé à des `Series`, source
  d'ambiguïtés lors de l'évaluation dans `with_columns`. Remplacé par des opérations Series pures.
- **`rsi`** (standalone) : La conversion numpy masquait les `None` initiaux ; la version Polars
  les propage correctement.

### 📚 Documentation

- **`app/core/indicators.py`** : En-tête de module avec changelog détaillé des changements v10.
- **`CHANGELOG.md`** : Ce fichier — entrée v10.
- **`README.md`** : Référence mise à jour vers V10.

### 🗄️ Structure

```
app/
└── core/
    └── indicators.py    ← SOURCE UNIQUE — v10.0.0 (tous indicateurs ici)
                           app/strategies/indicators.py SUPPRIMÉ
```

### ⚡ Migration depuis V9

```python
# Ancien code (V9) — importait depuis deux modules selon le contexte :
from app.core.indicators import detect_regime, adx_val, volume_ratio
from app.strategies.indicators import rsi, atr, adx, pre_val

# Nouveau code (V10) — un seul module source :
from app.core.indicators import detect_regime, adx_val, volume_ratio, rsi, atr_val, pre_val

# app/strategies/indicators.py est supprimé — importer directement depuis app.core.indicators
# Exemple de mapping des alias courants :
#   atr_val as calc_atr     (remplace : atr as calc_atr du shim)
#   adx_val as calc_adx     (remplace : adx as calc_adx du shim)
```

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
python cli.py

# CLI anciens arguments ? Ils sont supprimés :
python cli.py --web      ❌ Erreur (avant: web-only)
python cli.py --live     ❌ Erreur (argument inexistant)

# Nouveau comportement :
python cli.py            ✅ Démarrer bot + web (live ou paper selon config)
python cli.py --paper    ✅ Forcer paper trading
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