# 🏗️ Architecture Crypto Bot V12

Vue d'ensemble technique, patterns de design et flux de données.

---

## 📋 Vue d'ensemble

```
┌─────────────────────────────────────────────────────────────┐
│                   Interface Web (FastAPI)                   │
│  Dashboard | Backtest | Optimizer | Scanner | Config        │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTP/WebSocket
                     ▼
         ┌───────────────────────────┐
         │  API Routes (FastAPI)     │
         │  - /api/status            │
         │  - /api/backtest          │
         │  - /api/optimize/*        │
         │  - /api/scanner/*         │
         │  - /api/candles/stats     │
         └────────┬───────────┬──────┘
                  │           │
        ┌─────────▼──┐   ┌────▼──────────┐
        │   Engine   │   │  DB + Cache   │
        │ (Signals)  │   │  (SQLAlchemy) │
        └─────────┬──┘   └───────────────┘
                  │
        ┌─────────▼──────────────────┐
        │  Trading (Live/Paper)      │
        │  - LiveTrader              │
        │  - Risk Management         │
        │  - Trailing Stop           │
        └─────────┬──────────────────┘
                  │
        ┌─────────▼──────────────────┐        ┌──────────────────────┐
        │  CandleStore (Parquet)     │◄───────│  Exchange (CCXT)     │
        │  - data/ohlcv/{sym}/{tf}   │        │  - OKX (ccxt)        │
        │  - Fetch incrémental       │        └──────────────────────┘
        │  - Thread-safe             │
        └────────────────────────────┘
```

---

## 🧱 Couches et règles de dépendance (Vague 4)

Les imports ne vont que **vers le bas**. Toute nouvelle dépendance montante
est une régression d'architecture (cf. `docs/audit/01-architecture.md`).

```
app/api         (routes FastAPI + app/api/services — peut tout importer)
   │
app/live        (LiveTrader + mixins — importe core/engine/strategies,
   │             JAMAIS app.api : l'écriture config passe par core/yaml_io)
app/engine      (Engine, Backtester, optimiseur, forward-test, scanner —
   │             importe core + strategies dynamiquement)
app/strategies  (importe core ; exception documentée : engine.BaseStrategy)
   │
app/core        (fondation pure : config, timeframes, param_resolution,
                 bot_identity, indicateurs, smc_*, risk, database —
                 n'importe AUCUNE couche supérieure)
```

Invariants vérifiables (tous tenus depuis la Vague 4) :

| Invariant | Vérification |
|---|---|
| core n'importe pas engine/live/api | `grep -rn "from app.engine\|from app.live\|from app.api" app/core` = 0 |
| live n'importe pas api | `grep -rn "from app.api" app/live` = 0 |
| strategies n'importe pas live | `grep -rn "from app.live" app/strategies` = 0 |

Sources uniques (ne jamais recopier ces littéraux) :

- **Timeframes** : `app/core/timeframes.py` (`TF_SECONDS`, `TF_MINUTES`,
  `TF_MS`, `HTF_MAP`).
- **Résolution des params** : `app/core/param_resolution.py`
  (`resolve_strategy_params`, `_select_symbol_entry`,
  `DEFAULT_CONFIG_SYMBOL`) — utilisée par le Backtester, le LiveTrader
  ET `get_active_strategies_per_tf` (aucun chemin parallèle).
- **Clés de slot/position** : `app/core/bot_identity.py`
  (`build_slot_key` = `strategy::tf[::symbol]`,
  `build_pos_key` = `symbol::strategy::tf`, `parse_slot_key`).
- **Frais** : `app/core/config.py` (`DEFAULT_TAKER_FEE`, `DEFAULT_MAKER_FEE`).
- **Données** : `app/core/config.py` (`DATA_ROOT`) → `OHLCV_DIR`,
  `FEATURES_DIR`, `DERIVATIVES_DIR` ; singletons via
  `app/core/singleton.py::lazy_singleton`.
- **Écriture config.yaml** : `app/core/yaml_io.py::update_config_yaml`
  (verrou unique partagé api/live).
- **Split IS/OOS** : `app/core/is_oos.py` ; seuils statistiques :
  `app/core/stats_thresholds.py` ; courbe de risque DD :
  `app/core/risk_curve.py`.

### Composition du LiveTrader (fichiers < 500 lignes)

`LiveTrader(PositionMixin, BalanceSyncMixin, AutoOptMixin, HealthMixin)` :

- `live_trader.py` — init, boucle principale, cycle, wrappers OHLCV.
- `position_mixin.py` — cycle de vie des positions + chemin unique
  d'ouverture (`_try_open_from_signal`, gating risque→slot→budget).
- `balance_sync.py` — synchronisation du capital (paper/spot/margin).
- `auto_opt_mixin.py` — registre de stratégies, auto-optimisation
  planifiée, forward-test glissant (exécuté par
  `app/engine/forward_test.py`), cycle de vie des bots.
- `health_mixin.py` — heartbeat/dead-man, reprise réseau, purge,
  `status` (API), agrégats DB.

Le moteur SMC est scindé de même : `app/core/smc.py` est une **façade**
(`smc_primitives` / `smc_structure` / `smc_geometry` / `smc_volume` /
`smc_sessions`), et la logique métier des routes scanner vit dans
`app/api/services/scanner_service.py`.

---

## 🔄 Flux de données

### Démarrage (cli.py)

```
1. parse_args() 
   └─> load_config() [YAML]
2. setup_logging()
3. Route mode :
   - Backtest CLI    : Backtester.run()
   - Optimizer CLI   : StrategyOptimizer.search()
   - Scanner CLI     : MarketScanner.screen()
   - Trading normal  : LiveTrader.start() (thread daemon) + FastAPI server
4. uvicorn.run() → API + Web
```

### Trading Live (LiveTrader thread)

```
while trader.running:
  1. scanner.fetch_ohlcv(symbol, tf)
       └─> CandleStore.fetch()
             ├─> Lecture Parquet local (< 5 ms)
             └─> Fetch incrémental exchange (nouvelles bougies seulement)
  2. For each strategy:
     - Engine.signal()
     - Check risk (circuit breaker, margin)
  3. Execute trades
  4. Update DB
  5. Emit notifications
  6. Sleep(scan_interval)
```

### Backtest (Backtester)

```
Backtester(engine, cfg)
  ├─> CandleStore.fetch()        ← V12 : depuis le cache local si disponible
  │     ├─> Lecture Parquet      (instantané si déjà fetché par le live trader)
  │     └─> Fetch exchange       (uniquement si nouvelles bougies)
  ├─> Polars DataFrame processing
  ├─> For each candle:
  │    └─> Engine.signal()
  │        └─> Update equity
  │        └─> Record trades
  └─> Results (by_strategy stats)
```

### CandleStore — Flux de données V12

```
1er fetch (symbol, tf inconnu)
  CandleStore.fetch(exchange, "BTC/USDC", "1h", 1500)
    ├─> _load()              → DataFrame vide (fichier inexistant)
    ├─> _fetch_full()        → 1500 bougies paginées depuis l'exchange (OKX)
    ├─> merge + filtre
    └─> _save()              → data/ohlcv/BTC_USDC/1h.parquet

Fetch suivant (même jour)
  CandleStore.fetch(exchange, "BTC/USDC", "1h", 1500)
    ├─> _load()              → 1500 bougies depuis Parquet (< 5 ms)
    ├─> _fetch_incremental() → 2 nouvelles bougies depuis last_ts
    ├─> merge + dédup
    └─> _save()              → 1502 bougies persistées

Après 30 jours de live trading
  data/ohlcv/BTC_USDC/1h.parquet → 720+ bougies accumulées automatiquement
  Optimizer reçoit 30 jours d'historique réel sans appel paginé
```

---

## 🗂️ Modules clés

### `app/core/config.py`

**Responsabilité** : Charger et valider la configuration YAML

```python
load_config(path)
  ├─> yaml.safe_load()
  ├─> Validation schema
  └─> Return dict config
```

**Points importants** :
- ✅ Lecture YAML sécurisée
- ✅ Validation des valeurs numériques, énums
- ❌ NE PAS charger secrets depuis code → variables d'env

---

### `app/core/candle_store.py` ← V11

**Responsabilité** : Stockage Parquet persistant des bougies OHLCV

```python
class CandleStore:
    def fetch(exchange, symbol, tf, total) -> pl.DataFrame
        ├─> _load(path)              # Parquet local
        ├─> _fetch_incremental()     # ou _fetch_full() si vide
        ├─> merge + unique + sort + filter
        └─> _save(path)              # zstd compression

get_store() -> CandleStore           # Singleton thread-safe
```

**Stockage** :
```
data/ohlcv/{SYMBOL}/{TF}.parquet
  Exemple : data/ohlcv/BTC_USDC/1h.parquet
  Taille   : ~80 KB pour 2 000 bougies (zstd)
  Lecture  : < 5 ms (Polars natif)
```

**Thread-safety** : verrou par fichier via `_get_file_lock(path)` — indispensable
pour le live trader qui appelle `fetch_ohlcv` depuis plusieurs threads.

---

### `app/core/database.py`

**Responsabilité** : ORM SQLAlchemy, gestion des trades et stats

**Modèles** :
- `Trade` — Chaque trade exécuté
- `DailyStats` — Aggégation journalière (PnL, DD, etc)

**Bonnes pratiques** :
- ✅ Connection pooling
- ✅ Transactions ACID
- ❌ NE PAS faire N+1 queries

---

### `app/engine/engine.py`

**Responsabilité** : Moteur de signaux central

```python
class Engine:
    def register(self, strategy: BaseStrategy)
    def signal(self, ohlcv_row) -> Signal
       ├─> Check pre-conditions
       ├─> strategy.analyze()
       ├─> Risk filtering
       └─> Return Signal(side, size, stop, reason)
```

**Points clés** :
- ✅ Single responsibility (signaux uniquement, pas d'execution)
- ✅ Pluggable stratégies
- ❌ NE PAS faire appels réseau dans signal()

---

### `app/live/live_trader.py`

**Responsabilité** : Boucle de trading live/paper

```python
class LiveTrader:
    def start(self):
        while self.running:
            1. Scan markets
            2. Generate signals
            3. Manage positions
            4. Execute orders
            5. Update stats
            6. Sleep(interval)
```

**Thread-safety** :
- ✅ Locks pour config reload
- ✅ Queue pour commandes de stop/start
- ❌ NE PAS modifier config sans lock

---

### `app/optimizer/optimizer.py`

**Responsabilité** : Optimisation paramètres (Grid/Random/Bayesian)

```
StrategyOptimizer(strategy, cfg, df_train, df_test, param_space)
  ├─> grid_search()    [Brute force]
  ├─> random_search()  [Monte Carlo]
  └─> bayesian_search()[UCB + GP]
      └─> Compute IS/OOS score
          └─> Detect overfitting
```

**Caching** :
- ✅ In-memory cache des backtests (~5s par run)
- ✅ Invalidate quand config change
- ❌ NE PAS cacher trop agressivement

---

### `app/api/main.py`

**Responsabilité** : Routes FastAPI, orchestration

```
@app.get("/api/status")         [Public, no auth]
@app.get("/api/trades")         [Auth required]
@app.post("/api/backtest")      [Auth required]
@app.post("/api/optimize/start" [Auth required]
@app.get("/api/optimize/stream" [SSE]
```

**Patterns** :
- ✅ CleanJSONResponse (NaN/Inf sanitization)
- ✅ Semaphores (limite concurrent backtests/optimizations)
- ✅ Dependency injection (verify_api_key)
- ❌ NE PAS bloquer le thread API

---

## 🔐 Sécurité

### API Key

```yaml
web:
  api_key: "your-secret-key"  # Ajouter à config.yaml
```

**Endpoints non-autentifiés** :
- `GET /api/status` (info publique, sans détails sensibles)

**Endpoints autentifiés** :
- Tous les `/api/config/*`
- Tous les `/api/bot/*`
- `/api/trades`, `/api/backtest`, `/api/optimize/*`

### CORS

⚠️ **Actuellement permissif pour dev** :
```python
allow_origins=["http://localhost", "http://127.0.0.1", ...]
```

**Production** : Adapter à votre domaine
```python
allow_origins=["https://yourdomain.com"]
```

### Injection YAML

❌ **DANGEREUX** :
```python
yaml.dump(disk_cfg)  # Si params non validés
```

✅ **Sûr** :
```python
allowed_strats = _discover_strategies()
if strategy not in allowed_strats:
    raise HTTPException(400, "Stratégie inconnue")
```

---

## 🚀 Performance

### Caching stratégies découvertes

```python
_discover_strategies()  # TTL 300s
  └─> glob.glob() sur app/strategies/*.py
```

**Optimization** :
```python
@cache(ttl=300)  # Nouveau en V9
def _discover_strategies():
    ...
```

### Index DB

```sql
CREATE INDEX idx_trades_symbol_strategy ON trades(symbol, strategy);
CREATE INDEX idx_trades_time ON trades(time DESC);
```

**Impact** : -300ms sur `/api/trades` avec 100k+ trades

### Pagination

```python
def list_trades(limit: int = 100, offset: int = 0):
    trades = session.query(Trade).offset(offset).limit(limit).all()
```

---

## 🔄 Threading Model

```
┌────────────────────────────────────────┐
│  Main Thread (FastAPI/uvicorn)         │
│  - Handle HTTP requests                │
│  - Serve pages                         │
└────────────────────────────────────────┘
         ▲
         │ Queue(config_changes)
         │
┌────────▼────────────────────────────────┐
│  LiveTrader Thread (Daemon)             │
│  - Scan markets                         │
│  - Manage positions                     │
│  - Execute trades                       │
└────────────────────────────────────────┘
```

**Synchronization** :
- ✅ Lock pour config reload
- ✅ Semaphore pour backtest/optimizer exclusivity
- ❌ NE PAS lock trop longtemps

---

## 📊 Patterns de design

### Dependency Injection

```python
# app/api/main.py
async def verify_api_key(request: Request):
    ...

@app.get("/api/trades", dependencies=[Depends(verify_api_key)])
def list_trades():
    ...
```

### Strategy Pattern

```python
class BaseStrategy:
    def analyze(self, ohlcv) -> Signal
    
class TrendStrategy(BaseStrategy):
    def analyze(self, ohlcv) -> Signal
        # Implémentation spécifique
```

### Observer Pattern (Notifications)

```python
notifier = Notifier(cfg)
trader.risk.attach_notifier(notifier)  # Circuit breaker → notifications
```

### Factory Pattern (Exchange)

```python
create_exchange(cfg)
  └─> ccxt.okx() (exchange cible) — autres exchanges ccxt via routage générique
      (params margin/credentials adaptés par exchange — passphrase OKX)
```

---

## 🔧 Configuration à chaud

### Stratégies

```
POST /api/config/strategies?enabled=pullback_trend,breakout
  ├─> cfg["strategies"]["enabled"] = [...]
  ├─> trader.reload_strategies(...)  [if trader running]
  └─> Save config.yaml
```

### Timeframes

```
POST /api/config/timeframes?timeframes=5m,1h,4h
  ├─> cfg["trading"]["timeframes"] = [...]
  ├─> trader.tf = "5m"
  ├─> trader._build_active_per_tf()
  └─> Save config.yaml
```

**Attention** : Changement de TF peut créer des NaN dans buffer historique

### Paramètres trading

```
POST /api/config/trading?score_threshold=0.6&risk_per_trade=0.02
  ├─> cfg["trading"][key] = value
  ├─> Propagate à trader si running
  └─> Save config.yaml
```

---

## 📈 Monitoring

### Structured Logging

```python
logger.info("[Main] Trading démarré", extra={
    "mode": "PAPER",
    "capital": 1000,
    "strategies": ["pullback_trend"]
})
```

### Health Check Endpoint

```python
@app.get("/health")
def health():
    return {
        "status": "ok",
        "db": db_ok,
        "exchange": exchange_ok,
        "trader": trader.running if trader else None
    }
```

---

## 🐛 Debugging

### Enable Debug Logging

```python
# app/core/logger.py
if cfg.get("debug"):
    logging.getLogger().setLevel(logging.DEBUG)
```

### Trace API Calls

```bash
# Voir tous les appels CCXT
export CCXT_DEBUG=1
python cli.py
```

---

## 🚀 Déploiement

### Systemd Service

```ini
[Unit]
Description=Crypto Bot V12
After=network.target

[Service]
Type=simple
User=cryptobot
WorkingDirectory=/opt/crypto_bot
ExecStart=/opt/crypto_bot/.venv/bin/python cli.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "cli.py"]
```

---

## 📝 Checklist avant production

- [ ] CORS configuré pour domaine prod
- [ ] `api_key` défini (sinon API ouverte)
- [ ] `paper_mode: false` si live réel
- [ ] Backup DB mis en place
- [ ] Logging à la journée (logs/)
- [ ] Monitoring health check en place
- [ ] Rate limiting sur API
- [ ] HTTPS/SSL activé
- [ ] Secrets en env vars, pas en config.yaml

---

**Architecture Crypto Bot V12** — Bien pensée, scalable, et testable 🏗️