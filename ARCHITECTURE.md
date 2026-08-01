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
                 bot_identity, providers, indicateurs, smc_*, risk, database —
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
  `TF_MS`, `HTF_MAP`, `bars_per_year` — facteur d'annualisation du Sharpe
  partagé backtest/live, S4-01/S4-02).
- **Venue / classe d'actif** : `app/core/bot_identity.py` (`Venue` étendue
  S2-02 : `asset_class`, `quote_currency`, `tick_size`, `lot_size`,
  `fractional`, `allow_short` ; G2 : `calendar`, `data_provider`,
  `can_execute`, `close_at_session_end`, `fee_pct`/`fee_fixed`/`fee_min`,
  `transaction_tax_pct`, `min_notional` ; `resolve_venue` accepte aussi
  `venues.assign[symbol]`). Protocoles d'accès marché/exécution :
  `app/core/providers.py` (`MarketDataProvider`, `ExecutionProvider`, S2-01
  — `RobustExchange` s'y conforme structurellement). Les stratégies déclarent
  `asset_classes` (`BaseStrategy`, défaut crypto+equity ; `funding_flow`/
  `derivatives_reversion` = crypto only), filtré par
  `AutoOptMixin._filter_by_asset_class`.

  **La venue est le seul point d'extension multi-actifs** : aucun module du
  moteur ne teste une classe d'actif ni un suffixe de ticker. Les défauts de
  `Venue` reproduisent le comportement crypto historique, donc tout ce qui
  suit est inerte tant qu'aucune venue actions n'est déclarée.

  **La venue est aussi la source UNIQUE de vérité spot/margin (S11).**
  `venues.default` est obligatoire dès que `venues.defs` existe, et toute venue
  référencée doit exister — `app/core/config.py::_validate_venues` refuse le
  démarrage sinon. Avant, une `venues.default` vide faisait retomber la
  résolution sur `default_venue_from_cfg`, qui fabriquait une venue à partir
  des globales `exchange.margin` / `trading.margin_mode` /
  `trading.max_leverage` : sur la config livrée, cette venue portait le nom
  `margin-isolated` — celui d'une entrée de `venues.defs` — avec un levier
  différent. Deux objets homonymes et divergents. Ce repli existe toujours
  (config sans bloc `venues:`) mais son nom est préfixé `auto:`.

  Deux garde-fous complètent le modèle :
  - `_enforce_market_coherence` : une venue `spot` ne peut porter ni levier, ni
    `margin_mode`, ni taux d'emprunt — ces clés sont ramenées à leur valeur
    neutre et journalisées, plutôt qu'honorées à moitié ;
  - `Venue.borrows` / `Venue.effective_borrow_rate` : **le marché décide de
    l'emprunt**. `margin` et `perp` empruntent, le spot jamais — ni le spot
    crypto, ni les actions au comptant. Avant, `trading.borrow_rate_daily`
    était facturé inconditionnellement des deux côtés (backtest et live) :
    chaque trade SBF 120 payait ~30 %/an d'intérêt fictif sur un achat
    comptant.

  Le bot ne connaît **pas** les enveloppes fiscales (CTO, PEA) : c'est une
  notion de compte, pas de moteur. Une venue actions, c'est `market_type: spot`
  + `max_leverage: 1` + `allow_short: false`.

- **Horaires de marché (G2)** : `app/core/market_calendar.py`
  (`get_calendar` → `AlwaysOpenCalendar` 24/7 par défaut, `SessionCalendar`
  déclaratif, `XPAR` livré, adaptateur `exchange_calendars` optionnel).
  Résolution symbole → calendrier : `provider_router.market_calendar_for`
  (source unique) ; consommée par `app/live/market_hours_mixin.py`, qui
  mémoïse par venue et gate **les entrées seulement** (les positions ouvertes
  restent gérées marché fermé).
- **Contraintes et coûts d'instrument (G2)** : `app/core/execution.py`
  (`quantize_size`, `quantize_price`, `venue_trade_cost`) — partagés
  backtest ↔ live comme le reste du module, appliqués à l'ouverture, au
  scale-in et à la clôture des deux côtés.
- **Routage de providers (G2)** : `app/core/provider_router.py`
  (`build_market_provider` rend l'exchange **inchangé** si aucune venue ne
  déclare de `data_provider` ; `register_provider` pour en brancher un autre).
  Provider actions data-only : `app/core/yfinance_provider.py`.
- **Univers d'instruments (G2)** : `app/core/universe.py` +
  `data/universe/*.yaml` — liste statique versionnée, cumulée avec
  `scanner.symbols`. Le scan dynamique par volume reste un concept crypto.
- **Trailing live** : section `live.trailing` de config.yaml (S1-08) —
  dédiée, indépendante de `backtest.*` (repli sur `backtest.*` + WARNING si
  absente, pour compat).
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
- **Configuration découpée (S11)** : `config.yaml` ne porte que le sommaire
  (`include:`) ; chaque fichier de `config/` est aligné sur une brique —
  `venues.yaml` (exchange, venues), `risk.yaml` (trading, risk, live),
  `data.yaml` (scanner, providers, derivatives), `lifecycle.yaml` (lifecycle,
  capital_allocator, optimizer, forward_test, backtest), `ops.yaml` (web,
  logging, database, notifications, watchdog, ui, perf). Fusion :
  `app/core/config.py::_load_and_merge`. **Une section vit dans un seul
  fichier** — la déclarer deux fois fait échouer le chargement, plutôt que de
  laisser l'ordre de lecture trancher en silence. Une config monolithique
  (sans `include:`) reste valide.
- **Écriture config** : `app/core/yaml_io.py::update_config_yaml`
  (verrou unique partagé api/live). Route chaque section modifiée vers le
  fichier qui la porte, ne réécrit que les fichiers touchés, et préserve les
  commentaires (la vue fusionnée référence les objets mêmes des documents
  ruamel, donc les mutations en place atteignent le document d'origine).
- **Timeframes actifs** : `app/core/config.py::active_timeframes` — source
  unique (`trading.timeframes`, repli `trading.timeframe`).
- **Split IS/OOS** : `app/core/is_oos.py` ; seuils statistiques :
  `app/core/stats_thresholds.py` ; courbe de risque DD :
  `app/core/risk_curve.py`.

### Composition du LiveTrader (fichiers < 500 lignes)

`LiveTrader(PositionMixin, BalanceSyncMixin, AutoOptMixin, HealthMixin)` :

- `live_trader.py` — init, boucle principale, cycle, wrappers OHLCV.
- `position_mixin.py` — cycle de vie des positions + chemin unique
  d'ouverture (`_try_open_from_signal`, gating risque→slot→budget).
- `market_hours_mixin.py` — calendrier de marché (G2) : filtre les entrées
  hors séance, clôture avant fin de séance. **Inerte en crypto** (venue 24/7).
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

Version détaillée (composition des mixins, slots, cycle de vie, allocation) :
section « Live Trading Loop » plus bas.

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

## 🔴 Live Trading Loop

Détaille le flux résumé dans « Trading Live (LiveTrader thread) » ci-dessus :
composition des mixins, granularité des slots, cycle de vie des bots et
allocation de capital. Schéma exact d'`optimizer_results[strategy][tf][symbol]` :
docstring de `_load_strategy_configs` (`app/core/config.py`) et de
`resolve_strategy_params` (`app/core/param_resolution.py`).

### Composition (V4-J — un mixin par responsabilité)

```
LiveTrader(PositionMixin, BalanceSyncMixin, AutoOptMixin, HealthMixin)
  ├─ PositionMixin    : _try_open_from_signal (chemin unique d'ouverture),
  │                     _manage_position, _close_position, _restore_open_positions
  ├─ BalanceSyncMixin : _sync_paper_balance / _sync_spot_balance / _sync_margin_account,
  │                     _pre_execution_check
  ├─ AutoOptMixin     : _load_all_strategies, _build_active_per_tf,
  │                     reload_active_strategies, auto-opt planifiée, forward-test,
  │                     cycle de vie des bots
  └─ HealthMixin      : heartbeat/dead-man, reprise réseau, purge mémoire,
                        propriété `status` (lue par les routes API)

Composés (instances, pas des mixins) :
  OHLCVCache       : cache multi-TF des DataFrames OHLCV (TTL par TF)
  SignalPipeline    : collecte + ranking des signaux (thread de scoring borné)
  CapitalAllocator  : allocation du capital par slot `strategy::tf::symbol`
```

### Boucle `_cycle()` (un tour = un scan)

```
while self.running:
  1. Sanity checks : risk.halted ? → skip le tour
  2. Volatility brake (ATR BTC/USDC 1h)
  3. Gestion des positions ouvertes (_manage_position × N positions)
       ├─ gap prix/stop > 2%           → clôture forcée
       ├─ take-profit atteint          → clôture au TP
       ├─ check_early_exit (stratégie) → clôture pilotée par la stratégie
       ├─ trailing stop (update_stop)  → remonte le stop, replace le stop exchange
       ├─ check_scale_in (stratégie)   → pyramidage optionnel sur position gagnante
       └─ trailing déclenché           → clôture
  4. Pipeline signaux : scanner.get_symbols() → SignalPipeline.collect()
       └─ pour chaque (symbol, tf) actif : Engine.signal() par stratégie, ranking
  5. Pour chaque signal ranké (score décroissant), _try_open_from_signal :
       risk.can_trade → slot enabled → slot circuit breaker → corrélation
       → sizing (RiskManager.compute_size ; budget du slot si per_bot_sizing)
       → allocator.can_allocate (budget) → _pre_execution_check (capital)
       → _open_position (ordre + persistance DB + stop exchange optionnel)
  6. Synchro capital (paper / spot / margin selon le mode) + rapport périodique
  7. Rééquilibrage hebdomadaire des budgets (allocator.rebalance_if_due)

+ hors du tour, planification indépendante (une erreur ici ne stoppe jamais
  le bot — cf. garde-fou try/except autour de start()) :
  _maybe_auto_optimize()  : ré-optimisation planifiée par stratégie/symbole
  _maybe_forward_test()   : re-backteste les slots actifs, alimente le cône Monte-Carlo
  _maybe_lifecycle()      : dérive l'état des bots + allocation shadow (cf. ci-dessous)
```

### Slots `strategy::tf::symbol`

Un **slot** = une combinaison (stratégie, timeframe, symbole) — l'unité
atomique de budget, de circuit breaker et de cycle de vie. Deux clés
distinctes, volontairement dans un ordre différent :
```
build_slot_key(strategy, tf, symbol) → "trend_rider::4h::BTC/USDC"   (config/budget/lifecycle)
build_pos_key(symbol, strategy, tf)  → "BTC/USDC::trend_rider::4h"   (position ouverte)
```
Un slot **hérité** (sans dimension symbole, ex. `"trend_rider::4h"`) est
réputé calibré pour `DEFAULT_CONFIG_SYMBOL` (BTC/USDC) et ne s'applique PAS
aux autres symboles — cf. schéma détaillé dans `app/core/config.py`.

### Cycle de vie des bots (`SlotLifecycleManager`, Phase 2 — shadow/observationnel)

```
CANDIDAT ──edge prouvée sur backtest──▶ ESSAI ──fidélité live confirmée──▶ ACTIF
    ▲                                                                        │
    └────────────── budget sous plancher OU réel < simulation ◀─────────────┘
                         (bascule vers RETIRÉ, file de re-optimisation)
```
- **Candidat** : pas encore de trade réel ; edge (expectancy backtest) non
  prouvée (échantillon < `edge_min_trades`, ou borne basse de l'IC ≤ 0).
- **Essai** : edge prouvée (IC bas > 0, ≥ `edge_min_trades` trades sim) ;
  attend `fidelity_min_fills` fills réels pour confirmer la fidélité.
- **Actif** : edge prouvée **et** fidélité live confirmée (le réel tombe
  dans la fourchette Monte-Carlo simulée) — le budget suit le score.
- **Retiré** : le réel sort de la fourchette simulée, ou le budget
  s'effondre sous le plancher (`plancher_budget_pct`) → file de
  re-optimisation (réversible).

Les transitions sont **dérivées automatiquement**, jamais choisies à la
main — sauf forçage explicite via `lifecycle.force_active` (droit de veto
utilisateur, outrepasse la machine à états). Cf. `app/live/slot_lifecycle.py`.

⚠ **Ce que le cycle de vie ne fait pas** : il ne décide pas quels bots tradent.
La sélection vient du classement OOS — `optimizer_results` (strategies/*.yaml)
+ seuil `MIN_VIABLE_SCORE` + `trading.top_strategies_per_tf`, dans
`app/engine/opt_persistence.py::get_active_strategies_per_tf`. Le cycle de vie
pilote l'**état** d'un bot, son budget et sa mise en file de ré-optimisation.

⚠ **Portée du forçage** (D6, S11) : `force_active` court-circuite la promotion
par edge **et** les deux règles de retrait (budget effondré, live qui contredit
la simulation en perdant). Un slot forcé n'est donc jamais retiré, même
perdant, et n'entre jamais dans la file de ré-optimisation. Le défaut est `[]`
(liste vide) : 15 slots y étaient figés jusqu'en S11. L'ancien nom
`manual_active` reste lu, déprécié.

### Allocation de capital (`CapitalAllocator`)

- Budget par slot, en % du capital total ; 3 modes : `equal`, `manual`,
  `performance` (pondéré par le score du bot).
- **Shadow allocation** (par défaut) : l'allocateur calcule ce qu'il
  *ferait* selon le score courant (exposé dans `status.shadow_allocation`)
  **sans l'appliquer** — le budget réel ne bouge qu'au rééquilibrage
  manuel/planifié, sauf `continuous_allocation` activé.
- Rééquilibrage planifié (`daily`/`weekly`/`never`, `rebalance_if_due`) ou
  forcé (`POST /api/slots/rebalance`).
- Persistance : `capital_allocator.slot_budgets` dans `config.yaml`, écrite
  via le callback `_persist_allocator_budgets` (verrou unique `core/yaml_io`).

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

**Responsabilité** : Boucle de trading live/paper — orchestrateur composé de
4 mixins (V4-J). Détail de la composition, du cycle `_cycle()`, des slots et
du cycle de vie des bots : section « Live Trading Loop » plus haut.

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
  api_key: ${WEB_API_KEY}   # Résolue depuis .env (généré par scripts/setup.sh)
```

Transport de la clé (`verify_api_key`, `app/api/helpers.py`) : header
`X-API-Key` OU cookie **HttpOnly** `api_key` (posé par les pages web,
`app/api/main.py::_tpl`). Le frontend Next.js n'embarque plus de clé dans le
bundle (S1-05 : `NEXT_PUBLIC_API_KEY` supprimée) — il envoie le cookie via
`credentials: 'include'` / `EventSource(..., {withCredentials: true})`. Le
WebSocket `/ws` vérifie le cookie en premier, `?api_key=` reste un fallback
pour les clients non-navigateur (S1-04). En live (`paper_mode: false`), une
variable d'environnement `${...}` référencée mais absente **bloque le
démarrage** (`config.strict_env`, S0-04).

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