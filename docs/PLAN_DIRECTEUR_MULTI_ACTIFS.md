# Plan Directeur — Généralisation Multi-Actifs (Crypto + Actions SBF120) & Améliorations

> **Objectif :** document de référence unique fusionnant (A) la généralisation du bot
> vers le trading d'actions type SBF120, et (B) le plan d'amélioration existant,
> **vérifié item par item contre le code** (HEAD `63f7d11`, 2026-07-17).
> Chaque item est formulé pour être exécutable par un agent autonome.

---

## Table des matières

1. [Corrections au plan d'amélioration (vérification code)](#1-corrections-au-plan-daméliorations-vérification-code)
2. [Architecture cible multi-actifs](#2-architecture-cible-multi-actifs)
3. [Chantier G — Généralisation (détail exécutable)](#3-chantier-g--généralisation-détail-exécutable)
4. [Roadmap fusionnée par sprints](#4-roadmap-fusionnée-par-sprints)
5. [Récapitulatif des priorités](#5-récapitulatif-des-priorités)

---

## 0. Note d'exécution — flakiness pré-existante de la suite complète

En validant les Sprints 0-1, `pytest tests/` (610 tests) s'est bloqué de façon
non-déterministe (~1 run sur 3-4) lors de l'exécution de la suite COMPLÈTE en
un seul process — jamais en lançant les fichiers individuellement ou par
moitiés. Root-cause isolée par bisection : **le blocage se reproduit à
l'identique sur le code d'AVANT le Sprint 1** (`git stash` → HEAD Sprint 0
seul, sans aucun des verrous `RLock` ajoutés) — donc **non lié** aux
correctifs `RiskManager`/`OHLCVCache`/`CapitalAllocator` de ce plan. C'est une
flakiness pré-existante de l'environnement sandboxé (probable throttling CPU
cgroup sous charge de threads multiples — `test_allocator_thread_safety.py`
préexistant en est un candidat). Sans accès à l'infra CI réelle, hors
périmètre des sprints ci-dessous ; mitigé pragmatiquement en réduisant la
charge de `test_risk_thread_safety.py` (moins d'itérations, `time.sleep(0)`
dans la boucle de lecture). Recommandation de suivi : exécuter `pytest` avec
`pytest-timeout` en CI pour transformer un blocage en échec explicite plutôt
qu'un run qui ne se termine jamais.

---

## 1. Corrections au plan d'amélioration (vérification code)

Le plan d'amélioration (91 items) a été audité contre le code actuel. La branche
qu'il visait (`claude/new-session-q8yfi6`) est mergée dans `main` : certains
items sont confirmés, d'autres sont **déjà couverts, obsolètes ou requalifiés**.
Les items non listés ci-dessous sont repris tels quels (non re-vérifiés en
détail, priorité conservée).

### 1.1 Items CONFIRMÉS (à exécuter tels quels)

| Item | Preuve dans le code | Priorité |
|------|--------------------|----------|
| 1.1 Spot sync ne propage pas l'équité à l'allocateur | `balance_sync.py:92` appelle `risk.update_equity()` mais pas `allocator.update_equity()` (le paper le fait, lignes 69-70) | **P0** |
| 1.2 Retour de `create_order` non validé | `position_mixin.py:305` enchaîne `order.get("price")` sans vérifier `None`/`status`. Nuance : `RobustExchange.create_order` lève des exceptions sur la plupart des échecs — le cas résiduel est un ordre retourné avec `status` `rejected`/`canceled` | **P0** |
| 1.6 CORS bloque DELETE | `main.py:113` : `allow_methods=["GET", "POST"]` alors que `optimizer.py:354` expose `@router.delete("/api/optimize/job")` | **P0** |
| 2.1 Clé API en query string WebSocket | `ws.py:48-53` : auth par `?api_key=xxx` documentée et implémentée | P1 |
| 2.2 `NEXT_PUBLIC_API_KEY` dans le bundle client | `frontend/src/lib/api.ts:11`, `ws-provider.tsx:11` | P1 |
| 3.2 Caches OHLCV sans verrou | Aucun `Lock` dans `ohlcv_cache.py` | P1 |
| 3.3 `RiskManager` sans verrou | Aucun `Lock`/`RLock` dans `risk.py` (624 lignes, accès thread trading + threads API) | P1 |
| 3.5 `update_equity`/`check_correlation` non verrouillées | `capital_allocator.py:418` : `update_equity` sans le décorateur `@_locked` présent ailleurs | P1 |
| 6.1 Trailing live lu depuis `cfg["backtest"]` | `live_trader.py:67-76` | P2 |
| 14.1 `_expand_env` silencieux (`""` si var absente) | `config.py:72-86`, aucune notion de `strict_env` | P1 |
| 15.5 `datetime.utcnow()` déprécié | 7 occurrences dans `app/` | P3 |
| 16.6 Frontend Jinja2 toujours chargé | `main.py:132-136` instancie encore `Jinja2Templates` en parallèle du Next.js | P3 |

### 1.2 Items INVALIDÉS ou DÉJÀ COUVERTS

| Item | Constat | Reste à faire |
|------|---------|---------------|
| 1.3 Scale-in sans validation du notional cumulé | **FAUX** : `can_allocate` (`capital_allocator.py:314-329`) compare `used_notional + notional` au budget du slot, et `_scale_in_position` appelle bien `register_open(slot_key, add_notional)` (`position_mixin.py:797`) — le cumul est donc tracé | Transformer en **test de régression** (P3) : 3 scale-ins successifs, vérifier le rejet au dépassement de budget |
| 6.2 Absence de rate-limiting exchange | **REQUALIFIÉ** : `create_exchange` active `enableRateLimit: True` (`exchange.py:351`) — ccxt applique déjà le throttle par exchange. Un token bucket maison serait une duplication | P3 : exposer une métrique de latence des appels ; batcher les `fetch_ticker` par cycle via `fetch_tickers` (1 appel au lieu de N) |
| 11.1 « Tests WebSocket manquants » | **OBSOLÈTE** : `tests/test_websocket.py` existe (10 tests), ainsi que `tests/test_api_routes.py` (10 tests) | Refaire l'inventaire réel des routes non couvertes avant d'ouvrir des chantiers de tests (portfolio, replay, derivatives, trades restent à vérifier) |
| 14.6 `allow_insecure` par défaut | **PARTIEL** : le garde-fou OPS-02 existe (`config.py:318-332`) — le démarrage est refusé sur host ouvert sans clé, sauf opt-in explicite `allow_insecure: true` | Reste : `setup.sh` génère une clé aléatoire dans `.env` ; WARNING au boot si `allow_insecure: true` + bind `0.0.0.0` |
| 16.5 `venues.assign: {}` non documenté | **ABSORBÉ** par le chantier G (généralisation) : la section `venues` devient le socle du `VenueRegistry` (voir §3.2) | Rien à faire isolément |
| 3.7 Routes backtest/replay/optimizer bloquantes | **FAUX** : les routes `def` (sync) de FastAPI sont automatiquement déportées dans le threadpool AnyIO par Starlette — elles ne bloquent PAS la boucle asyncio. Vérifié empiriquement (serveur uvicorn réel : une route `async` reste réactive en ~0.08s pendant qu'une route sync dort 1.5s). `optimizer_start` retourne déjà immédiatement (`start_async` lance des threads en arrière-plan) ; `backtest`/`replay` ont chacun leur sémaphore anti-chevauchement | Aucune (un `asyncio.to_thread()` ici serait une double-mise-en-thread sans bénéfice) |
| ARCH-04 (144 imports scopés `from app.`) | **CHIFFRES FAUX ET PROBLÈME INEXISTANT** : compte réel 153-161, mais `scanner.py` a 0 import scopé (pas 19), `live_trader.py` en a 3 (pas 18). Surtout : `grep` ne trouve **aucune violation de couche** — `from app.(engine\|live\|api)` dans `core` = 0, `from app.api` dans `live` = 0, `from app.live` dans `strategies` = 0. Les imports scopés restants sont concentrés dans `app/api/*` (autorisé à tout importer) ou sont du lazy-loading intra-couche légitime | Aucune — refactor aveugle sans gain, risque de casser des contournements de cycle intentionnels |
| ARCH-05 (découper smc.py 1083L / smart_money.py 1178L) | **DÉJÀ FAIT** : `smc.py` fait 65 lignes, façade documentée « V4-L / ARCH-14 » réexportant `smc_primitives/smc_structure/smc_geometry/smc_volume/smc_sessions` (tous < 450L). `smart_money.py` délègue déjà `_signal_at`/`_build_trade` à `smart_money_signals.py` (ligne 776-777) | Aucune |
| ARCH-06 (déplacer `_save_yaml` vers yaml_io.py, live→api) | **DÉJÀ FAIT** : `live_trader.py` fait 484 lignes (pas 591+), 0 référence à `app.api` — commentaire ligne 457 : « V4-D : plus aucune dépendance à app.api — self.cfg EST l'objet » | Aucune |

### 1.3 Items conservés sans re-vérification détaillée

Les items suivants sont plausibles et conservés à leur priorité d'origine :
1.4/1.5 (cohérence Sharpe live/backtest — chantier « métriques » du Sprint Q),
5.1-5.6, 7.x, 8.x, 9.x, 10.x, 12.x, 13.x, 14.2-14.5, 14.7-14.8, 15.1-15.4,
16.1-16.4, 16.7.

Deux d'entre eux sont **promus** car ils conditionnent la généralisation :

- **8.2** (symbole BTC hardcodé dans `ml/trainer.py`) → intégré au chantier G
  (le réentraînement doit être par instrument, pas « le symbole qui contient BTC »).
- **7.8** (paramètres `smart_money` spécialisés BTC/USDC) → le mécanisme
  `symbol_params` existant est la réponse ; à généraliser à tout instrument.

---

## 2. Architecture cible multi-actifs

### 2.1 Ce qui est déjà générique (ne pas toucher)

- **Stratégies** : `BaseStrategy.analyze()` consomme des DataFrames OHLCV —
  aucune dépendance crypto dans l'interface.
- **Moteur/backtester/optimiseur/lifecycle** : tout est déjà indexé par slot
  `strategy::tf::symbol` (`bot_identity.py`). Le slot
  `pullback_trend::1d::AIR.PA` est représentable sans modification.
- **CandleStore** : stockage Parquet par `(symbol, tf)`, agnostique de la source.
- **Couplage ccxt confiné à 5 fichiers** : `core/exchange.py`,
  `core/candle_store.py`, `core/derivatives.py`, `live/position_mixin.py`,
  `api/routes/derivatives.py`.
- **Config `venues`** : la section existe déjà (`config.yaml:7-24`) avec
  `defs` + `assign` — c'est le point d'extension naturel.

### 2.2 Les 4 points de couplage crypto à casser

**(a) Couche marché/exécution.** Extraire deux protocoles dans `app/core` :

```python
class MarketDataProvider(Protocol):
    def fetch_ohlcv(self, symbol, tf, limit, since) -> list: ...
    def fetch_ticker(self, symbol) -> dict: ...
    def fetch_tickers(self, symbols) -> dict: ...
    def load_markets(self) -> dict: ...

class ExecutionProvider(Protocol):
    def create_order(self, symbol, type, side, amount, price, params) -> dict: ...
    def cancel_order(self, order_id, symbol) -> dict: ...
    def fetch_order(self, order_id, symbol) -> dict: ...
    def fetch_balance_detail(self) -> dict: ...
```

`RobustExchange` devient l'implémentation ccxt (les deux protocoles). Le paper
trading étant déjà simulé localement (`exchange.py:181`), un provider
**data-only** suffit pour backtester et paper-trader les actions.

**(b) Modèle d'instrument et devise de cotation.** USDC/USDT est codé en dur
dans les balances (`exchange.py:284,298`), le scanner filtre
`s.endswith("/USDC")` (`scanner.py:74`), `min_volume_usdc_24h`,
`DEFAULT_CONFIG_SYMBOL = BTC/USDC`. Introduire :

```python
@dataclass(frozen=True)
class Instrument:
    symbol: str            # "BTC/USDC" ou "AIR.PA"
    asset_class: str       # "crypto" | "equity"
    venue: str             # clé dans venues.defs
    quote_currency: str    # "USDC" | "EUR"
    tick_size: float
    lot_size: float        # 1.0 pour action entière, 1e-6 crypto
    fractional: bool
```

résolu par un `VenueRegistry` construit depuis `venues` (voir §3.2).

**(c) Calendrier de marché — le point le plus structurant.** La boucle live
suppose un marché 24/7 (`scan_interval: 60`, aucun gating dans `_cycle()`).
Euronext : 9h00–17h30 CET, fermé week-ends/fériés. Un `MarketCalendar` par
venue (lib `exchange_calendars`, calendrier `XPAR`) doit être consulté par :

- la boucle live (skip des instruments hors séance) ;
- la fraîcheur des bougies dans `CandleStore` (ne pas attendre de bougie la nuit) ;
- la planification du forward-test et de l'auto-opt ;
- la gestion des **gaps** : la règle « gap prix/stop > 2 % → clôture forcée »
  de `position_mixin` est une anomalie en crypto mais un événement quotidien
  normal sur actions (gap d'ouverture) — le seuil doit être par classe d'actif ;
- les stops : pas de stop exchange actif la nuit sur actions au comptant — le
  risque overnight est soit assumé (stop géré à l'ouverture), soit éliminé
  (clôture en fin de séance, mode `day_trading` par slot).

**(d) Coûts et sizing.** `compute_size` retourne des fractions
(`risk.py:477`) : sur actions il faut arrondir au `lot_size` de l'instrument
et respecter le `tick_size` pour les stops. Le modèle taker/maker doit devenir
un **modèle de frais par venue** : courtage fixe + variable, et surtout la
**TTF française (0,4 % à l'achat)** qui s'applique à la quasi-totalité du
SBF120 (capitalisation > 1 Md€) — l'ignorer fausse tous les backtests.
Le short : `max_shorts`, le borrow OKX (`fetch_margin_account`) sont
crypto-spécifiques → `allow_short: false` sur la venue actions au départ
(le SRD est un chantier séparé, hors périmètre initial).

### 2.3 Compatibilité des stratégies

Les features dérivés (funding, OI, long/short ratio) sont crypto-only et déjà
opt-in. Ajouter un attribut de classe sur `BaseStrategy` :

```python
class BaseStrategy:
    asset_classes: frozenset = frozenset({"crypto", "equity"})  # défaut : les deux
```

`funding_flow`, `derivatives_reversion` (et toute stratégie consommant
`funding_z`/`oi_change_pct`/`lsr_z`/`taker_z`) déclarent
`asset_classes = frozenset({"crypto"})`. `_build_active_per_tf`
(`auto_opt_mixin.py`) filtre les couples (stratégie, instrument) incompatibles.
Le lifecycle existant (candidat → essai → actif) fait ensuite naturellement le
tri des stratégies qui survivent au passage sur actions.

### 2.4 Fournisseurs pour le SBF120

| Rôle | Option recommandée | Alternative |
|------|--------------------|-------------|
| Données 1h/1d | `yfinance` (tickers `.PA`, gratuit, suffisant pour valider) | EOD Historical Data (payant, plus fiable, intraday complet) |
| Exécution réelle | Interactive Brokers via `ib_insync` (Euronext couvert, API robuste) | Saxo OpenAPI (Alpaca = US-only ; Degiro = pas d'API officielle) |
| Calendrier | `exchange_calendars` (XPAR) | `pandas_market_calendars` |
| Liste SBF120 | Liste statique versionnée `data/universe/sbf120.yaml` (composition d'indice, révisée trimestriellement) | Scraping Euronext (fragile, à éviter) |

Contraintes `yfinance` à intégrer : intraday 1m limité à 7 jours, < 1d limité
à 60 jours → les timeframes réalistes pour le SBF120 sont **1h et 1d**.

---

## 3. Chantier G — Généralisation (détail exécutable)

### G1 — Abstractions sans changement de comportement (Phase 1)

1. **`app/core/providers.py`** : protocoles `MarketDataProvider` +
   `ExecutionProvider` (signatures ci-dessus). `RobustExchange` les implémente
   déjà de facto — aucune modification de son code, juste la conformité.
2. **`app/core/instruments.py`** : dataclass `Instrument` + `VenueRegistry` :
   - construit depuis `cfg["venues"]` étendu :
     ```yaml
     venues:
       default: okx-spot
       defs:
         okx-spot:
           provider: ccxt          # nouveau champ
           exchange: okx
           market_type: spot
           asset_class: crypto
           quote_currency: USDC
           calendar: 24/7
           fees: {taker: 0.001, maker: 0.0008}
           allow_short: false
         euronext-paper:
           provider: yfinance
           asset_class: equity
           quote_currency: EUR
           calendar: XPAR
           fees: {fixed: 1.0, pct: 0.0005, fta_pct: 0.004}   # TTF à l'achat
           allow_short: false
           fractional: false
       assign:
         "AIR.PA": euronext-paper       # le reste hérite de default
     ```
   - `registry.resolve(symbol) -> Instrument` (fallback : venue `default`).
3. **Neutraliser la devise de cotation** :
   - `exchange.py:284,298` : la devise cherchée dans les balances vient de
     `Instrument.quote_currency` (ou de la venue), plus de littéral USDC/USDT ;
   - `scanner.py:74` : le filtre `endswith("/USDC")` devient
     `registry.is_scannable(s)` (crypto : filtre volume ; equity : liste statique) ;
   - `min_volume_usdc_24h` → `min_volume_quote_24h` (alias rétro-compatible) ;
   - `DEFAULT_CONFIG_SYMBOL` reste `BTC/USDC` (rétro-compat des YAML hérités).
4. **`asset_classes` sur `BaseStrategy`** + marquage des stratégies dérivés
   (§2.3) + filtre dans `_build_active_per_tf`.
5. **Critère de sortie** : suite de tests existante 100 % verte, aucun
   changement de comportement en crypto (golden test : un backtest BTC/USDC
   avant/après donne un résultat identique au centime).

### G2 — Actions en paper (Phase 2)

1. **`app/core/providers_equity.py`** : `YFinanceProvider(MarketDataProvider)`
   → alimente `CandleStore` au même format Parquet
   (`data/ohlcv/AIR_PA/1d.parquet`). Mapping tf bot ↔ intervalles yfinance,
   gestion des limites d'historique intraday.
2. **`app/core/market_calendar.py`** : wrapper `exchange_calendars` ;
   `is_open(venue, ts)`, `next_close(venue)`, `session_bounds(venue, date)`.
   Intégration :
   - `SignalPipeline.collect()` : skip des instruments dont la venue est fermée ;
   - `CandleStore` : pas de refetch hors séance ;
   - `_manage_position` : seuil de gap par classe d'actif
     (`gap_force_close_pct` par venue, défaut crypto 2 %, equity 8 %) ;
   - mode `close_at_session_end: true|false` par slot equity.
3. **Sizing entier** : `compute_size` reçoit l'`Instrument` et arrondit
   `size` au `lot_size` (floor), rejette si notional < 1 action.
   `_update_exchange_stop` arrondit les prix au `tick_size`.
4. **Modèle de frais par venue** : `trade_fees()` prend l'`Instrument` ;
   equity = fixe + % + TTF à l'achat. Backtester ET live partagent la même
   fonction (parité déjà exigée par BT-03).
5. **Univers SBF120** : `data/universe/sbf120.yaml` (liste des tickers `.PA`) ;
   le scanner en mode equity lit cette liste au lieu du scan par volume.
6. **ML/optimiseur** : corriger 8.2 (`ml/trainer.py` : symbole de
   réentraînement par instrument configuré, plus de `"BTC" in s`).
7. **Critère de sortie** : backtest + optimisation + paper trading d'au moins
   3 valeurs SBF120 sur 1h/1d pendant une semaine, avec calendrier respecté
   (aucun scan le week-end, log de skip hors séance) et frais TTF visibles
   dans les trades.

### G3 — Exécution réelle actions (Phase 3)

1. **`IBKRExecutionProvider`** (`ib_insync`) ou Saxo OpenAPI : create/cancel/
   fetch order + balance EUR. L'idempotence par `clientOrderId`
   (`exchange.py:141-178`) se transpose (IBKR : `orderRef`).
2. **Synchro de solde** : `BalanceSyncMixin` par venue (le solde EUR IBKR et
   le solde USDC OKX coexistent ; l'équité globale est la somme convertie).
3. **Risque multi-classes** : `check_correlation` groupe par classe d'actif ;
   nouveau plafond `max_asset_class_exposure_pct` dans `capital_allocator`.
4. **SRD/short actions** : chantier séparé, non planifié ici.

---

## 4. Roadmap fusionnée par sprints

Ordre de priorité : d'abord sécuriser ce qui tourne (P0/P1), puis les
abstractions G1 (elles ne changent rien au comportement donc peuvent avancer
en parallèle), puis G2/G3 entrelacés avec la dette qualité.

### Sprint 0 — Correctifs immédiats (P0) — parallélisable

| ID | Item | Fichiers | Origine |
|----|------|----------|---------|
| S0-01 | Spot sync → `allocator.update_equity()` | `balance_sync.py` | 1.1 ✔ vérifié |
| S0-02 | Validation retour `create_order` (None/status rejeté) sur les 5 sites d'appel | `position_mixin.py` | 1.2 ✔ vérifié |
| S0-03 | CORS : ajouter DELETE/PUT à `allow_methods` | `api/main.py:113` | 1.6 ✔ vérifié |
| S0-04 | `_expand_env` strict en live (`ValueError` si var absente et `paper_mode: false`) | `core/config.py` | 14.1 ✔ vérifié |
| S0-05 | `setup.sh` génère une clé API ; WARNING si `allow_insecure` + `0.0.0.0` | `scripts/setup.sh`, `config.py` | 14.6 (reliquat) |

### Sprint 1 — Concurrence & sécurité (P1) — parallélisable

| ID | Item | Fichiers | Origine |
|----|------|----------|---------|
| S1-01 | `RLock` sur `RiskManager` (can_trade, register_open/close, update_equity, status_dict) | `core/risk.py` | 3.3 ✔ |
| S1-02 | Verrou caches OHLCV | `live/ohlcv_cache.py` | 3.2 ✔ |
| S1-03 | `@_locked` sur `update_equity`/`check_correlation` | `live/capital_allocator.py` | 3.5 ✔ |
| S1-04 | Auth WS par cookie HttpOnly (query param en fallback) | `routes/ws.py`, `ws-provider.tsx` | 2.1 ✔ |
| S1-05 | Supprimer `NEXT_PUBLIC_API_KEY` (cookie only) | `frontend/src/lib/api.ts` | 2.2 ✔ |
| S1-06 | Routes backtest/replay/optimizer non bloquantes (`asyncio.to_thread`) | `routes/*.py` | 3.7 |
| S1-07 | `_pre_execution_check` : budget slot + marge/solde réels | `balance_sync.py` | 6.4 |
| S1-08 | Section `live.trailing` (fallback `backtest` + WARNING) | `live_trader.py`, `config.yaml` | 6.1 ✔ |

### Sprint 2 — Chantier G1 : abstractions (comportement inchangé)

| ID | Item | Fichiers |
|----|------|----------|
| S2-01 | Protocoles `MarketDataProvider`/`ExecutionProvider` | `core/providers.py` (nouveau) |
| S2-02 | `Instrument` + `VenueRegistry` + extension section `venues` | `core/instruments.py` (nouveau), `config.yaml` |
| S2-03 | Neutralisation USDC (balances, scanner, `min_volume_quote_24h`) | `core/exchange.py`, `engine/scanner.py`, `core/config.py` |
| S2-04 | `asset_classes` sur `BaseStrategy` + marquage dérivés + filtre | `engine/engine.py`, `strategies/funding_flow.py`, `strategies/derivatives_reversion.py`, `live/auto_opt_mixin.py` |
| S2-05 | Golden test backtest BTC/USDC avant/après (parité au centime) | `tests/test_generic_parity.py` (nouveau) |

### Sprint 3 — Chantier G2 : actions en paper

| ID | Item | Fichiers |
|----|------|----------|
| S3-01 | `YFinanceProvider` → CandleStore Parquet | `core/providers_equity.py` (nouveau) |
| S3-02 | `MarketCalendar` (XPAR) + gating boucle live/CandleStore/forward-test | `core/market_calendar.py` (nouveau), `live/signal_pipeline.py`, `core/candle_store.py` |
| S3-03 | Sizing entier (`lot_size`) + stops au `tick_size` | `core/risk.py`, `live/position_mixin.py` |
| S3-04 | Frais par venue (fixe + % + TTF 0,4 %) partagés backtest/live | `core/config.py`, `engine/backtest.py`, `live/position_mixin.py` |
| S3-05 | Gap par classe d'actif + `close_at_session_end` | `live/position_mixin.py` |
| S3-06 | Univers `data/universe/sbf120.yaml` + scanner equity | `engine/scanner.py` |
| S3-07 | ML : réentraînement par instrument (fix 8.2) | `ml/trainer.py` |
| S3-08 | Validation : 1 semaine de paper sur 3 valeurs SBF120 (1h/1d) | — |

### Sprint 4 — Qualité des métriques & optimiseur (P1-P2)

| ID | Item | Origine |
|----|------|---------|
| S4-01 | Sharpe live sur rendements, aligné backtest | 1.4 |
| S4-02 | Annualisation Sharpe backtest (barres avec trade / resampling daily) | 1.5 |
| S4-03 | Data leakage ML final (mode `is_only` optionnel + score OOS-only) | 5.1 |
| S4-04 | Deflated Sharpe Ratio dans `opt_scoring` | 5.2 |
| S4-05 | Métriques par stratégie en une passe (`defaultdict`) | 5.3 |
| S4-06 | `_load_db_stats` en SQL agrégé | 5.6 |
| S4-07 | Test de régression scale-in/budget (remplace l'item 1.3 invalidé) | §1.2 |

### Sprint 5 — Chantier G3 : exécution réelle actions

| ID | Item |
|----|------|
| S5-01 | `IBKRExecutionProvider` (`ib_insync`) : ordres + idempotence `orderRef` |
| S5-02 | Balance multi-venue (EUR + USDC, équité globale convertie) |
| S5-03 | `max_asset_class_exposure_pct` + corrélation par classe |
| S5-04 | Runbook incidents étendu aux actions (15.1) |

### Sprint 6 — Fond de dette (P2-P3) — au fil de l'eau

Découpage `risk.py` (4.1), `AppState` (3.1), Pydantic (9.1), Prometheus (13.3),
logs JSON (13.1), health check détaillé (13.4), nettoyage stratégies Opus
(16.1), doublons save_trade (12.3), backup config.yaml (14.7), watchdog chemin
absolu (14.8), deps dev/prod (14.3), suppression Jinja2 (16.6), items P3
restants du plan d'origine.

---

## 5. Récapitulatif des priorités

| Sprint | Contenu | Dépendances |
|--------|---------|-------------|
| 0 | 5 correctifs P0 (sécurité financière + config) | aucune |
| 1 | 8 items concurrence/sécurité P1 | aucune |
| 2 | G1 : abstractions multi-actifs (comportement inchangé) | aucune (parallèle aux sprints 0-1) |
| 3 | G2 : SBF120 en paper (données, calendrier, frais, sizing) | Sprint 2 |
| 4 | Métriques/optimiseur (Sharpe, DSR, leakage ML) | aucune |
| 5 | G3 : exécution réelle actions (IBKR/Saxo) | Sprint 3 validé en paper |
| 6 | Dette de fond P2-P3 | au fil de l'eau |

**Corrections notables au plan d'origine** : l'item 1.3 (scale-in) est déjà
couvert par le code ; l'item 6.2 (rate-limiting) est requalifié car ccxt
throttle déjà ; l'item 11.1 sous-estimait la couverture de tests existante ;
l'item 14.6 est partiellement traité (garde OPS-02 en place) ; l'item 16.5
(`venues.assign`) est absorbé par la généralisation.
