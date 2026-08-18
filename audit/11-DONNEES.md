# Audit — Intégrité et cycle de vie des données

> Périmètre : `app/core/candle_store.py`, `providers.py`, `provider_router.py`,
> `yfinance_provider.py`, `feature_store.py`, `derivatives.py`,
> `market_calendar.py`, `universe.py`, `backtest_history.py`,
> `oos_tracker.py`, `app/live/ohlcv_cache.py`, et le contenu réel de `data/`.

---

## Inventaire réel de `data/`

| Répertoire | Fichiers | Taille | Plus ancien | Plus récent |
|---|---|---|---|---|
| `ohlcv/` | 1 200 | **441 Mo** | 2026-06-20 | 2026-08-07 |
| `features/` | 12 | **386 Mo** | 2026-06-20 | 2026-08-07 |
| `derivatives/` | 20 | 0,2 Mo | 2026-08-05 | 2026-08-07 |
| `universe/` | 1 (`sbf120.yaml`) | — | 2026-08-05 | 2026-08-05 |
| racine | 4 (`backtest_history`, `oos_tracker`, `smc_signals_recent`, `heartbeat`) | 0,4 Mo | 2026-08-07 | 2026-08-07 |

**129 symboles** (BTC_USDC, ETH_USDC + ~127 actions `.PA` / `.AS` / `.F`),
**8 timeframes** (1m, 5m, 15m, 30m, 1h, 2h, 4h, 1d).

---

## Tableau de bord

| # | Sévérité | Titre | Fichier | État au 18/08 |
|---|----------|-------|---------|---------------|
| D-01 | 🟠 Majeur | La bougie en formation est persistée dans le cache Parquet | `core/candle_store.py` vs `live/ohlcv_cache.py:128` | ✅ résolu — `drop_forming_candle` avant save |
| D-02 | 🟠 Majeur | `unique("time")` sans `keep` : la version conservée n'est pas déterministe | `core/candle_store.py:214,262` | ✅ résolu — `keep=last` incrémental, `keep=first` historique |
| D-03 | 🟡 Moyen | Aucune détection de trou à l'écriture, seulement à la lecture par l'API | `api/helpers.py:168-187` | ✅ résolu — calendrier + sidecar |
| D-04 | 🟡 Moyen | 386 Mo de features en cache sans politique d'éviction ni versionnage | `core/feature_store.py` | ✅ résolu — hash + éviction |
| D-05 | 🟡 Moyen | `oos_tracker.json` réécrit intégralement par slot | `core/oos_tracker.py` | ✅ résolu — atomique + batch |
| D-06 | 🟡 Moyen | Les résultats persistés ne portent pas la version du code qui les a produits | `core/backtest_history.py`, `oos_tracker.py` | ✅ résolu — `schema_version` + `git_commit` |
| D-07 | 🔵 Mineur | Aucune donnée fraîche depuis le 2026-08-07 | `data/` | ouvert (rebaseline après merge) |

---

## D-01 🟠 La bougie en formation entre dans le cache

`app/live/ohlcv_cache.py:128-155` implémente `_drop_forming_candle` — et le
raisonnement est excellent :

> Une bougie d'ouverture `t` couvre `[t, t+Δ)` ; elle est close dès que
> `t + Δ <= now`. Si la dernière bougie est encore ouverte, son `close` est
> provisoire (repaint) — on la retire pour que le scoring live se fasse sur des
> bougies clôturées, comme le backtest.

Le commentaire va jusqu'à documenter le piège de fuseau horaire résolu
(`epoch_ms` plutôt que `.timestamp()` sur une colonne naïve portant de l'UTC).

Mais **`CandleStore` ne fait pas la même chose**. Son filtre `_valid_bars`
(`candle_store.py:115-127`) ne retient que `close > 0` et, en crypto,
`volume > 0`. Rien sur la clôture de la bougie.

Or `CandleStore` est le chemin :

- du **backtest** (`api/routes/backtest.py` → `get_store().fetch(...)`) ;
- de l'**optimiseur** (via `auto_optimizer` → même store) ;
- du **forward-test** (`forward_test.py` → `fetch_ohlcv`) ;
- et de la **persistance Parquet**.

Conséquences :

1. La dernière barre d'un backtest peut être une bougie incomplète, dont le
   `close` provisoire alimente le signal de la dernière itération et la clôture
   forcée `end_of_data`.
2. Plus grave : **elle est écrite dans le Parquet**. Le cache conserve donc une
   barre dont le `high`/`low`/`close` sont ceux d'une bougie tronquée — et rien
   ne la corrige (cf. D-02).

**Correction** : appliquer l'élagage dans `_valid_bars` ou juste avant
`self._save(path, df_merged)`, en réutilisant la fonction existante. Une seule
implémentation, comme pour le reste du dépôt.

---

## D-02 🟠 `unique("time")` ne dit pas quelle version garder

`candle_store.py:214` et `:262` :

```python
df_merged = _valid_bars(
    pl.concat([df_cached, df_new]).unique("time").sort("time"),
    exchange, symbol,
)
```

En polars, `DataFrame.unique(subset)` utilise `keep="any"` par défaut, ce qui
signifie explicitement **« l'une des lignes en double, sans garantie »** — le
choix dépend de l'ordre de parcours interne et peut varier.

Sur la barre de recouvrement entre le cache et le fetch incrémental — celle qui
était en formation lors de l'écriture précédente (D-01) — la version conservée
peut donc être :

- la **nouvelle** (complète) : correct ;
- l'**ancienne** (tronquée) : la barre reste fausse **définitivement**, puisque
  les fetchs suivants ne la retoucheront plus.

C'est un bug silencieux et persistant : rien ne le signale, et il contamine
tous les backtests ultérieurs sur cette plage.

**Correction** :

```python
pl.concat([df_cached, df_new])          # df_new en dernier
  .unique("time", keep="last")          # explicite : la plus récente gagne
  .sort("time")
```

Et pour le backfill historique (`:262`), l'inverse : `df_old` porte des barres
que le cache ne connaît pas, donc `keep="first"` avec `df_cached` en premier.

Un test suffit à verrouiller : concaténer deux versions de la même barre avec
des `close` différents et vérifier laquelle survit.

---

## D-03 🟡 Détection de trous côté lecture seulement

`api/helpers.py:168-187` fournit `detect_ohlcv_gaps(df, timeframe)` : elle
compare les écarts de temps successifs à `1,5 × Δ` et renvoie la liste des
trous. Elle est utilisée par les routes de diagnostic (`/api/data/status`).

Rien d'équivalent **à l'écriture**. Le store fusionne, déduplique, trie et
sauvegarde sans jamais vérifier la continuité. Un fetch incrémental qui
manque une plage (rate limit, pagination interrompue à 10 pages —
`candle_store.py:420`) produit un Parquet troué que le backtest traitera
comme une série continue : les indicateurs à fenêtre glissante enjamberont le
trou sans le savoir.

Ce risque est spécialement présent sur les actions, où le calendrier XPAR
produit des discontinuités **légitimes** (nuits, week-ends, fériés) qu'il faut
distinguer des trous **accidentels**. `app/core/market_calendar.py` sait le
faire (499 lignes, calendrier XPAR) — mais `detect_ohlcv_gaps` utilise une
table `tf_mins` locale de 8 entrées et ignore complètement le calendrier.

**Correction** :

1. Faire passer `detect_ohlcv_gaps` par `market_calendar` pour les venues à
   séance ;
2. Journaliser un WARNING à l'écriture quand un trou non calendaire apparaît,
   avec le nombre de barres manquantes ;
3. Exposer le taux de complétude par `(symbol, tf)` dans `/api/data/status`.

---

## D-04 🟡 386 Mo de features sans éviction ni version

`data/features/` : **12 fichiers, 386 Mo** — soit ~32 Mo par fichier, presque
autant que les 441 Mo de 1 200 fichiers OHLCV.

`app/core/feature_store.py` (492 lignes) met en cache les features
pré-calculées par `(symbol, tf)`. Deux manques :

1. **Aucune politique d'éviction.** Le répertoire croît avec le nombre de
   couples `(symbole, TF)` explorés. Avec 129 symboles × 8 TF, le potentiel est
   de ~1 000 fichiers, soit plusieurs dizaines de Go.
2. **Aucun versionnage du catalogue.** Si `features_catalog.py` change
   (ajout, retrait ou correction d'une feature — comme la correction du lissage
   ADX/ATR Wilder documentée dans `indicators_precompute.py`), les fichiers en
   cache portent l'**ancienne** définition. Rien ne les invalide.

Le registre ML, lui, a résolu exactement ce problème avec `recipe_hash`
(`model_registry.py:154`). La même idée s'applique : nommer le fichier de
features `{symbol}_{tf}_{catalog_hash8}.parquet`.

---

## D-05 🟡 `oos_tracker.json` réécrit par slot

`core/oos_tracker.py`, `_save_record` :

```python
with _lock:
    data = load_oos_tracker()      # relit les 264 Ko
    data[slot_key] = record
    with open(_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)   # réécrit tout
```

Appelée une fois par slot dans `run_forward_test` (`forward_test.py:259`).
Avec 254 slots enregistrés, une passe complète fait 254 lectures et 254
écritures d'un fichier de 264 Ko — 67 Mo d'E/S pour écrire 254 enregistrements.

Deux risques au-delà de la performance :

- **écriture non atomique** : `open(..., "w")` tronque avant d'écrire. Une
  interruption au mauvais moment laisse un JSON tronqué. `load_oos_tracker`
  gère bien `json.JSONDecodeError` en renvoyant `{}` — mais cela signifie
  **perdre les 254 enregistrements** silencieusement ;
- le verrou est un `threading.Lock` : il ne protège pas contre deux **process**
  (l'API et un worker d'optimisation).

**Correction** : écrire dans un `.tmp` puis `os.replace` (le motif est déjà
utilisé, correctement, par `watchdog.write_heartbeat`), et n'écrire qu'une fois
en fin de boucle.

---

## D-06 🟡 Les résultats persistés ne datent pas le code

`data/backtest_history.json` porte `run_date`, `n_bars`, `slot_key`. Ni la
version du code, ni le hash de configuration, ni les paramètres utilisés.

C'est ce qui rend ce fichier difficile à interpréter aujourd'hui : il contient
un mélange de campagnes du 2026-06-09 au 2026-08-07, produites par des versions
différentes du calcul de Sharpe et du Monte-Carlo. L'analyse de la section F-03
a dû **inférer** la version à partir de la signature des valeurs (p5 == p95).

Le registre ML fait exactement ce qu'il faut (`ArtifactRef.git_commit`,
`recipe_hash`, `created_at`, `source`). Le même traitement appliqué à
`backtest_history` et `oos_tracker` rendrait les données auto-descriptives :

```python
{"schema_version": 2, "git_commit": git_commit(), "cost_model": {...}, ...}
```

Sans cela, chaque correctif de métrique invalide silencieusement tout
l'historique, et l'UI continue d'afficher les anciennes valeurs comme des
mesures courantes.

---

## D-07 🔵 Données figées au 2026-08-07

Aucun fichier de `data/` n'a été modifié depuis le **2026-08-07**, soit une
semaine avant la date de cet audit. Ce n'est pas un défaut de code — le bot
n'a simplement pas tourné — mais deux conséquences méritent d'être notées :

1. Les mesures d'edge de `oos_tracker.json` (177 slots avec une edge
   disponible, dont **24 seulement à `ci_low > 0`**) sont obsolètes ; or ce sont
   elles qui pilotent les poids d'enveloppe via `slot_weights`. Un redémarrage
   allouerait le capital sur des mesures d'il y a une semaine.
2. `data/oos_tracker.json` montre **0 slot avec le moindre trade live**
   (`live.n_trades == 0` partout, `verdict == "pas_assez_de_trades_reels"` sur
   les 254). Le contrat « le live confirme-t-il la simulation ? » n'a jamais
   été évalué une seule fois. C'est le point aveugle central : **tout ce
   dispositif de validation n'a encore rien validé**.

---

## Ce qui est solide

- **Parquet par `(symbol, tf)`** avec schéma explicite (`_OHLCV_SCHEMA`, temps
  en `Datetime("ms")` pour coller à ccxt) : format colonnaire adapté, typage
  fixé, pas de dérive de schéma.
- **Verrous par fichier** (`_get_file_lock`) plutôt qu'un verrou global : deux
  symboles se rafraîchissent en parallèle sans se bloquer.
- **Mémo « plus d'historique disponible »** (`_no_history`, TTL 6 h) : le
  raisonnement — « les 98 titres × 5 TF du SBF 120 rejouaient la même requête
  perdante à chaque cycle » — est exactement le bon diagnostic, et
  l'invalidation dès que la borne basse du cache bouge est la bonne condition.
- **`_valid_bars` paramétré par le provider** : une bougie à volume nul est un
  signal de données cassées en crypto et parfaitement normale sur une action
  peu liquide. Le provider tranche (`drop_zero_volume`) au lieu d'appliquer une
  règle crypto à tout. Même logique pour `min_since_ms` (plancher 2017 d'OKX
  contre une action cotée depuis les années 1990) et `bars_span_ms` (temps
  calendaire continu contre séances).
- **`ProviderRouter`** : le commentaire sur le `__getattr__` qui renvoyait tout
  à l'exchange crypto par défaut — donc « le contrat que YFinanceProvider
  expose au store était invisible » — décrit un bug de conception subtil,
  correctement identifié et corrigé.
- **`_drop_forming_candle`** côté live : la bonne idée, bien implémentée, avec
  le piège de fuseau horaire documenté. Il ne manque que de l'appliquer aussi
  au store (D-01).
- **`watchdog.write_heartbeat`** : écriture atomique via
  `tmp` + `os.replace`. Le motif à généraliser (D-05).
- **`market_calendar.py`** (499 lignes) : calendrier XPAR avec fériés, séances
  et clôture avant fin de séance. Un vrai travail, rarement fait dans un bot
  crypto qui s'ouvre aux actions.
