# Audit — Backend API et persistance

> Périmètre : `app/api/` (main, middleware, helpers, state, schemas, 20 routers,
> services/scanner_service), `app/core/database.py`, `exchange.py`,
> `candle_store.py`, `providers.py`, `provider_router.py`, `events.py`,
> `notifications.py`, `metrics.py`, `audit_log.py`.

---

## Tableau de bord

| # | Sévérité | Titre | Fichier | État au 18/08 |
|---|----------|-------|---------|---------------|
| A-01 | 🟠 Majeur | Clôture non transactionnelle : fenêtre de perte de trade | `position_close_mixin.py:270-329` | ✅ résolu — une session, un commit |
| A-02 | 🟠 Majeur | Routes de calcul lourd exécutées dans le threadpool FastAPI | `routes/backtest.py:284`, `routes/replay.py`, `routes/scanner.py` | ouvert |
| A-03 | 🟠 Majeur | Un backtest par plage de dates charge 50 000 bougies en mémoire | `routes/backtest.py` (branche `use_date_range`) | ✅ résolu — `fetch_range` : backfill profondeur store, lecture filtrée |
| A-04 | 🟠 Majeur | `with_retry` bloque le thread jusqu'à 30 s par appel | `core/exchange.py:14-64` | ✅ résolu — tickers 2×0,5 s + cache ; API sans réseau |
| A-05 | 🟡 Moyen | Réponse `/api/backtest` non bornée (OHLCV + trades + folds) | `routes/backtest.py`, `walk_forward.py:126` | ouvert |
| A-06 | 🟡 Moyen | Rate limiting par IP du pair TCP : un seul seau derrière nginx | `api/state.py:32` | ✅ résolu — = S-02, `TRUSTED_PROXIES` |
| A-07 | 🟡 Moyen | `session_scope` ne gère ni commit ni rollback | `core/database.py` | ouvert |
| A-08 | 🟡 Moyen | `entry_time` est reconstruit, pas mesuré | `core/database.py` (`save_trade`) | ✅ résolu — `open_time` / ISO, crypto et actions |
| A-09 | 🟡 Moyen | `/metrics` sans authentification expose l'activité de trading | `api/main.py:135-155` | ✅ résolu — = S-01 |
| A-10 | 🟡 Moyen | `api_key` en query string du WebSocket | `routes/ws.py` (`_check_ws_auth`) | ✅ résolu — = S-03, `ALLOW_WS_QUERY_KEY` |
| A-11 | 🔵 Mineur | Deux tables de redirections héritées à maintenir en miroir | `api/main.py:314-347` | ouvert |
| A-12 | 🔵 Mineur | Le handler global renvoie le type d'exception au client | `api/middleware.py:49` | ✅ résolu — `correlation_id` |
| A-13 | 🔵 Mineur | `_is_frontend_reachable` fait un `connect()` bloquant dans la boucle async | `api/main.py:194` | ouvert |

> Détail : [`14-REVISION-2026-08-18.md`](14-REVISION-2026-08-18.md). A-03 : le backfill 50k **reste** (persisté une fois) ; seule la fenêtre part au moteur.

---

## A-01 🟠 La clôture n'est pas transactionnelle

`_close_position` (`position_close_mixin.py`) enchaîne trois transactions
indépendantes :

```python
self.ledger.release(pos_id)                                   # 268
with session_scope(self.SessionLocal) as _sess:
    delete_open_position(_sess, pos_id)                       # 270-271  ← commit 1
...
with session_scope(self.SessionLocal) as session:
    save_trade(session, trade)                                # 324      ← commit 2
    update_daily_stats(session, ...)                          # 325      ← commit 3
```

`session_scope` (`core/database.py`) ne fait **que** fermer la session ; chaque
fonction appelée commite ou rollback pour son propre compte (c'est documenté).

Un crash entre le commit 1 et le commit 2 laisse donc :

- la position **supprimée** de `open_positions` (donc non reprise au démarrage) ;
- le trade **jamais enregistré** dans `trades` ;
- l'ordre de clôture **bel et bien exécuté** sur l'exchange.

Le PnL est réellement encaissé mais n'apparaît nulle part : ni dans l'historique,
ni dans `DailyStats`, ni dans les statistiques par slot qui alimentent
`get_slot_live_stats` et donc le cycle de vie des bots.

Symétriquement, `update_daily_stats` peut échouer après `save_trade`, ce qui
désynchronise `DailyStats.equity_close` — la valeur même que
`_restore_paper_base` relit au démarrage pour reconstituer le capital paper.

**Correction** : une seule session, un seul commit.

```python
with session_scope(self.SessionLocal) as sess:
    delete_open_position(sess, pos_id, commit=False)
    save_trade(sess, trade, commit=False)
    update_daily_stats(sess, ..., commit=False)
    sess.commit()
```

Les fonctions de `database.py` gagnent un paramètre `commit: bool = True` pour
rester compatibles avec les appelants existants.

---

## A-02 🟠 Les calculs lourds tournent dans le threadpool FastAPI

`run_backtest` est déclarée `def` (non `async def`) — FastAPI l'exécute donc
dans le threadpool `anyio` (40 threads par défaut). Le backtest y tourne pendant
plusieurs minutes, en tenant le GIL sur les portions Python pures (la boucle
barre par barre de `Backtester.run` n'est pas vectorisée).

Le dépôt a posé les bons garde-fous en amont :

- `state._bt_semaphore` (1 backtest simultané) ;
- refus 429 si une optimisation tourne (`routes/backtest.py`) ;
- `state._rp_semaphore` (replay), `state._smc_semaphore` (2 rejeux SMC).

Mais **pendant** ce temps, la boucle d'événements sert `/api/status` toutes les
3 s, `/health` toutes les 10 s, plus les sondages de 20+ hooks
(`use-api.ts`). Le GIL détenu par le backtest dégrade toutes ces réponses.
En pratique : l'UI « gèle » pendant un backtest, ce que le bandeau
`backtest-running-banner.tsx` habille sans le résoudre.

Plus grave : **le LiveTrader tourne dans le même process** (`init_app(config,
live_trader)`). Un backtest de 50 000 barres retarde le cycle de trading, donc
l'évaluation des stops (cf. L-01).

**Correction** : sortir les calculs lourds du process API. Le dépôt a déjà
l'infrastructure (`ml_jobs.py` : `job_id` + sondage). L'étendre au backtest, au
replay et au walk-forward, avec un `ProcessPoolExecutor` dédié comme celui de
l'optimiseur.

---

## A-03 🟠 50 000 bougies chargées pour filtrer par date

`routes/backtest.py`, branche `use_date_range` :

```python
use_date_range = bool(start_date.strip() or end_date.strip())
if use_date_range:
    limit = 50000  # max possible
else:
    limit = max(100, min(limit, 50000))
    if tf == "1d":
        limit = min(limit, 5000)
```

Dès qu'une seule des deux dates est fournie, le store charge **50 000 bougies**
puis le code filtre. En 1 m sur BTC, 50 000 bougies ≈ 35 jours ; l'utilisateur
qui demande « du 1ᵉʳ au 3 janvier » paie le chargement des 35 jours.

Le `CandleStore` stocke en Parquet par `(symbol, timeframe)` : un filtre par
plage **au niveau du scan Parquet** (`pl.scan_parquet(...).filter(...)`) coûterait
une fraction du prix. `polars` le fait nativement avec un predicate pushdown.

Note : le plafond spécial `1d → 5000` n'est appliqué que dans la branche
`else`. Un backtest journalier avec plage de dates demande donc 50 000 bougies
journalières, soit 137 ans d'historique — que le store ira chercher sur
l'exchange si le cache est incomplet.

---

## A-04 🟠 `with_retry` bloque jusqu'à 30 s par appel

`core/exchange.py:14-64` : `MAX_RETRIES = 4`, `BASE_DELAY = 2.0`, backoff
exponentiel avec `time.sleep()`.

Pire cas pour un seul appel réseau : `2 + 4 + 8 + 16 = 30 s` de sommeil
bloquant, plus les timeouts ccxt eux-mêmes.

Or `_safe_ticker` est appelé :

- une fois par position ouverte dans `_manage_position` ;
- une fois par position dans `_sync_paper_balance` ;
- une fois par position dans `_open_positions_market_value` ;
- une fois par signal dans le cycle (`live_trader.py:439`) ;
- une fois par position dans `_serialize_position` — **depuis les threads de
  l'API**, donc à chaque `/api/status`, c'est-à-dire toutes les 3 s.

Avec 5 positions et une dégradation réseau, un cycle peut consommer plusieurs
minutes en sommeil pur, pendant lesquels aucun stop n'est évalué.

**Corrections** :

1. Cacher les tickers par symbole avec un TTL court (2–5 s) et un seul
   `fetch_tickers()` groupé par cycle — ccxt le supporte, et cela divise le
   nombre d'appels par le nombre de symboles.
2. Ne **jamais** appeler l'exchange depuis un chemin API : `_serialize_position`
   doit lire le dernier prix connu du cache, pas interroger le réseau.
3. Rendre `MAX_RETRIES`/`BASE_DELAY` configurables et distinguer le budget de
   retry d'un appel « chemin critique » (ordre) de celui d'un appel
   « informatif » (ticker).

---

## A-05 🟡 Réponses API non bornées

| Endpoint | Contenu non borné |
|---|---|
| `POST /api/backtest` | `ohlcv_payload` (jusqu'à 50 000 × 5 valeurs), `trades` complets (avec `stop_trail`, `conditions`, `indicators`, `score_breakdown`), `equity_curve`, `timestamps` |
| `POST /api/backtest?walk_forward=1` | + 2 × `n_folds` résultats complets (cf. B-14) |
| `POST /api/backtest?dual_pass=1` | × 2 |
| `/api/optimize/results` | tous les trials avec leurs params |

`GZipMiddleware(minimum_size=500)` compresse bien, mais le JSON est
intégralement sérialisé en mémoire côté serveur avant compression, et
intégralement désérialisé côté navigateur. Un backtest walk-forward + dual pass
sur 20 000 barres produit une réponse de plusieurs dizaines de Mo.

**Correction** : pagination et `?fields=` sur les tableaux volumineux ; ne
renvoyer `trades` que sur demande explicite ; sous-échantillonner
`ohlcv_payload` à la résolution d'affichage (le graphique n'affiche pas
50 000 points).

---

## A-06 🟡 Un seul seau de rate limiting derrière un proxy

`api/state.py:32` :

```python
limiter = Limiter(key_func=get_remote_address, default_limits=[_RATE_LIMIT])
```

`get_remote_address` lit l'IP du **pair TCP**. C'est le bon choix pour éviter le
spoofing de `X-Forwarded-For` — et c'est cohérent avec
`helpers._extract_client_ip`, qui n'honore le header que si le pair figure dans
`TRUSTED_PROXIES`.

Mais la cohérence s'arrête là : **le limiter ignore `TRUSTED_PROXIES`**. En
production derrière nginx (`deploy/nginx.conf`), toutes les requêtes arrivent de
`127.0.0.1` : les 300 req/min sont partagées par **tous** les clients. Un seul
onglet ouvert consomme déjà ~40 req/min (sondages de `use-api.ts`) ; huit onglets
saturent le seau et le 429 frappe tout le monde.

**Correction** : `key_func` qui applique la même logique que
`_extract_client_ip` — header honoré si et seulement si le pair est un proxy
déclaré.

---

## A-07 🟡 `session_scope` ne gère pas la transaction

`core/database.py` :

```python
@contextmanager
def session_scope(SessionLocal):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
```

Pas de `commit()` en sortie de bloc, pas de `rollback()` sur exception. Le nom
`session_scope` est celui du pattern documenté par SQLAlchemy — qui, lui,
commite et rollback. Un contributeur qui écrit une nouvelle route en supposant
la sémantique standard produira du code qui ne persiste rien, silencieusement.

C'est la cause structurelle de A-01.

**Correction** : implémenter la sémantique attendue, et retirer les
`commit()`/`rollback()` internes des fonctions de `database.py`.

---

## A-08 🟡 `entry_time` est reconstruit

`save_trade` (`core/database.py`) :

```python
entry_time = close_time - _td(minutes=int(t["duration_bars"]) * mins_per_bar)
```

`entry_time` est une colonne du modèle, mais elle n'est pas mesurée : elle est
**déduite** de `duration_bars`, lui-même dérivé de l'horloge murale côté live
(cf. L-16) et de l'index de bougie côté backtest.

Sur une venue à calendrier (`euronext-paper`), la reconstruction est fausse dès
qu'une position traverse une nuit ou un week-end. Les analyses temporelles
(distribution des entrées par heure de session, une des dimensions que
`by_session` prétend fournir) reposent donc sur une valeur inventée.

`pos["open_time"]` est disponible dans le dict de position et contient le vrai
horodatage.

**Correction** : `entry_time = datetime.fromtimestamp(pos["open_time"], tz=utc)`
quand `open_time` existe, et ne recourir à la reconstruction que pour les trades
de backtest (qui portent `entry_time` en ISO).

---

## A-09 🟡 `/metrics` sans authentification

`api/main.py:135-155`. La docstring reconnaît le problème : « Il divulgue en
revanche l'activité de trading (capital, positions, PnL) — **à restreindre au
réseau d'administration côté nginx** ».

`deploy/nginx.conf` doit donc porter la restriction. Si le fichier ne contient
pas de `location /metrics { allow ...; deny all; }`, l'endpoint est public dès que
`web.host: 0.0.0.0` (le défaut de `config/ops.yaml`).

Le compromis énoncé (« un scrapeur ne porte pas d'en-tête ») n'en est pas un :
Prometheus supporte `bearer_token` et `authorization` dans sa configuration de
scrape depuis longtemps.

**Correction** : accepter `X-API-Key` **ou** un token de scrape dédié
(`METRICS_TOKEN`), et documenter la configuration Prometheus correspondante.

---

## A-10 🟡 Clé API en query string du WebSocket

`routes/ws.py`, `_check_ws_auth` : `token = websocket.cookies.get("api_key") or
api_key_query or ""`.

Le cookie est bien le chemin nominal (posé par le proxy Next,
`frontend/src/app/api/[...path]/route.ts`, `HttpOnly; SameSite=Lax`) et il est
essayé en premier. Le repli `?api_key=xxx` reste toutefois actif et une URL de
WebSocket apparaît dans :

- les journaux d'accès nginx (`$request`) ;
- l'historique du navigateur si l'URL est ouverte directement ;
- les traces de proxy intermédiaires.

**Correction** : conserver le repli mais le conditionner à une variable
d'environnement (`ALLOW_WS_QUERY_KEY=1`), et journaliser un WARNING à chaque
usage.

---

## A-11 à A-13 (mineurs)

- **A-11** : `HTML_ROUTES_TO_REDIRECT` (`api/main.py:314-347`) duplique le bloc
  `redirects()` de `frontend/next.config.mjs`. Le dépôt le sait et a écrit
  `tests/test_legacy_redirects.py` pour verrouiller la cohérence — c'est la
  bonne réponse à une duplication qu'on ne peut pas supprimer. Reste que 19
  routes héritées sont maintenues pour des URL qui, d'après le commentaire,
  « ont vécu en prod ». Une date de retrait serait utile.
- **A-12** : `_global_exception_handler` renvoie
  `{"detail": f"Erreur interne : {type(exc).__name__}"}`. Le nom de classe
  (`KeyError`, `polars.exceptions.ComputeError`…) renseigne un attaquant sur la
  pile interne. Renvoyer un identifiant de corrélation
  (`request.state.correlation_id`, déjà disponible) serait plus utile à
  l'utilisateur et moins bavard.
- **A-13** : `_is_frontend_reachable` (`api/main.py:194`) appelle
  `socket.create_connection(..., timeout=1.0)` — un appel bloquant — depuis un
  handler de route. Le cache de 60 s limite la casse à une requête sur 60,
  mais la route est `def`, donc c'est un thread du pool qui dort une seconde.

---

## Ce qui est solide

- **Couverture d'authentification complète** : les 20 routers déclarent
  `dependencies=[Depends(verify_api_key)]` sur **toutes** leurs routes
  (vérifié par recensement exhaustif). Aucun endpoint métier n'est ouvert.
  Seuls `/health` et `/metrics` sont volontairement publics.
- **`_extract_client_ip`** (`api/helpers.py:54-75`) : le raisonnement sur le
  spoofing de `X-Forwarded-For` est exact, et `TRUSTED_PROXIES` vide par défaut
  est le bon réglage. La normalisation IPv4-mapped-IPv6 traite un vrai cas de
  terrain.
- **`_incoming_correlation_id`** (`api/middleware.py:79-89`) : la clé est
  tronquée à 64 caractères et filtrée sur `[A-Za-z0-9._-]` — la défense contre
  le log forging est correcte et rarement présente.
- **`_route_template`** (`middleware.py:105-123`) : le refus d'étiqueter les
  métriques par `request.url.path` évite l'explosion de cardinalité Prometheus.
  C'est le mode de panne le plus courant d'une instrumentation, correctement
  anticipé.
- **SQLite en WAL + `busy_timeout=30000`** (`database.py`, `init_db`) avec
  `check_same_thread=False` : la configuration est adaptée à un writer et
  plusieurs lecteurs concurrents, ce qui est exactement la topologie du dépôt.
- **Index composites ciblés** (`ix_trades_strategy_tf_time`) posés d'après les
  requêtes réelles (`get_closed_trades_for_slot`), avec le commentaire qui dit
  quelle requête ils couvrent.
- **Migration de schéma idempotente** (`_migrate_schema`) : `PRAGMA table_info`
  puis `ADD COLUMN` pour les colonnes manquantes, `idx.create(checkfirst=True)`.
  Simple, sans dépendance à Alembic, et suffisant pour SQLite.
- **`RobustExchange`** : retry différencié par type d'exception ccxt
  (`RateLimitExceeded` ≠ `NetworkError` ≠ `AuthenticationError` ≠
  `InsufficientFunds`), avec reset de session TCP après N erreurs consécutives.
  La taxonomie est juste — seul le caractère bloquant pose problème (A-04).
- **Portillons de concurrence** (`_bt_semaphore`, `_opt_semaphore`,
  `_rp_semaphore`, `_smc_semaphore`) et refus croisé backtest ↔ optimisation :
  la contention CPU/mémoire a été pensée.
