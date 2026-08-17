# Audit — Performance

> Périmètre : points chauds CPU/mémoire du backtest, de l'optimiseur, du cycle
> live, de l'API et du frontend. Les constats redondants avec les autres
> rapports sont référencés, pas répétés.

---

## Tableau de bord

> ⚠️ **Révisé le 17 août 2026.** **P-01 est fortement atténué** par les PR #222
> et #227 : `htf_trend` passe de O(n²) à O(n) sur 8 stratégies (**jusqu'à ×120**),
> `bb_squeeze` en série causale (**×80** sur `breakout`), prédiction par lot sur
> v4/v5/`ml_dynamic_threshold`, threads LightGBM (**×3,1**), énumération des
> composés SMC. Nouveau module `app/core/indicators_causal.py`. Le constat de
> fond subsiste (36 stratégies sans `prepare_for_backtest`) mais les pires cas
> sont traités. Détail dans
> [`13-REVISION-2026-08-17.md`](13-REVISION-2026-08-17.md) §3.

| # | Sévérité | Titre | Fichier |
|---|----------|-------|---------|
| P-01 | 🟠 Majeur → atténué | Boucle de backtest en Python pur : ~3 000 barres/s au mieux | `engine/backtest.py:1541-1635` |
| P-02 | 🟠 Majeur | Un appel exchange par position et par cycle, multiplié par 5 chemins | `live/*`, `A-04` |
| P-03 | 🟠 Majeur | `_find_strategy` en O(k) appelé 2× par barre | `engine/backtest.py:655-663` |
| P-04 | 🟡 Moyen | `alpha_vs_buy_hold` en O(n²) | `core/performance_metrics.py:164-165` |
| P-05 | 🟡 Moyen | `ctx.window = df[:i+1]` reconstruit à chaque barre | `engine/backtest.py:1582` |
| P-06 | 🟡 Moyen | Sérialisation IPC des DataFrames répétée dans `_safe_worker_count` | `engine/optimizer_search.py` |
| P-07 | 🟡 Moyen | `_reconcile_close_costs` demande tout l'historique depuis l'ouverture | `live/position_close_mixin.py:68-76` |
| P-08 | 🟡 Moyen | Bundle frontend monolithique : 98 composants clients, aucun `next/dynamic` | `frontend/src` |
| P-09 | 🔵 Mineur | `_module_defines_strategy` relit 45 fichiers toutes les 60 s | `api/helpers.py:125-163` |
| P-10 | 🔵 Mineur | `oos_tracker._save_record` relit et réécrit tout le JSON à chaque slot | `core/oos_tracker.py` |

---

## P-01 🟠 Le cœur du backtest est une boucle Python

`engine/backtest.py:1541-1635` :

```python
for i in range(warmup, len(df) - 1):
    ...
    ctx.window = df[:i + 1]
    signal = self.engine.best_signal(ctx.window, strat_params, ...)
```

Chaque itération appelle `Strategy.score()` sur une tranche de DataFrame. Pour
une stratégie non instrumentée, `score()` recalcule ses indicateurs sur toute
la fenêtre — soit un coût O(i) par barre, donc **O(n²) sur le run**.

Le dépôt a identifié le problème et posé la bonne réponse : le hook
`prepare_for_backtest(df)` construit les features en une passe, puis `score()`
lit la dernière ligne du cache. Neuf stratégies sont instrumentées
(`backtest.py:1382-1390`). Les **trente-six autres** ne le sont pas.

Ordre de grandeur mesurable dans les logs de progression
(`backtest.py:1572-1581`, champ `bars/s`) : c'est la métrique à surveiller. Un
backtest de 20 000 barres × 40 essais d'optimisation × 45 stratégies est
dominé par ce coût.

**Corrections, par ordre de rendement** :

1. Instrumenter les stratégies les plus lourdes avec `prepare_for_backtest` —
   le mécanisme existe, il suffit de l'appliquer. `scripts/` contient déjà des
   outils de mesure comparative.
2. Éviter `df[:i+1]` (P-05) et passer plutôt un index `i` avec les tableaux
   numpy déjà extraits dans `ctx` (`close_arr`, `high_arr`, `low_arr`,
   `atr_arr`).
3. À terme, vectoriser la génération de signaux : la plupart des stratégies
   sont exprimables comme des masques booléens sur des colonnes polars.

---

## P-02 🟠 Cinq chemins appellent l'exchange par position

Détaillé en [`05-BACKEND-API.md`](05-BACKEND-API.md) A-04. Récapitulatif des
appels `fetch_ticker` par cycle de 60 s, avec 5 positions ouvertes :

| Chemin | Appels |
|---|---|
| `_manage_position` (1 par position) | 5 |
| `_sync_paper_balance` (1 par position) | 5 |
| `_open_positions_market_value` (1 par position) | 5 |
| Signaux du cycle (1 par signal candidat) | 0–N |
| `_serialize_position` via `/api/status` **toutes les 3 s** | 5 × 20 = **100** |

Soit ≈ **115 appels REST par minute** pour 5 positions, dont 87 % viennent du
sondage de l'interface. Chaque appel passe par `with_retry`, qui peut dormir
jusqu'à 30 s (`core/exchange.py`).

**Correction** : un `fetch_tickers([symbols])` groupé par cycle, mis en cache
avec un TTL de 5 s, consommé par les cinq chemins. Passe de 115 appels à 1.

---

## P-03 🟠 Recherche linéaire de stratégie à chaque barre

`engine/backtest.py:655-663` :

```python
def _find_strategy(self, name: str):
    for s in self.engine.strategies:
        if getattr(s, "name", None) == name:
            return s
    return None
```

Appelée deux fois par barre pendant qu'une position est ouverte
(`_manage_open_position` : `check_early_exit` ligne 888, `check_scale_in`
ligne 1009). Avec un seul `Engine` mono-stratégie (le cas de l'optimiseur et de
l'UI), le coût est négligeable — mais `getattr` + comparaison de chaîne restent
exécutés 2 × `bars_in_position` fois par run, soit typiquement 2 × 10 000
appels.

**Correction** : un `dict` construit une fois dans `run()`.

```python
self._strat_by_name = {s.name: s for s in self.engine.strategies}
```

---

## P-04 🟡 `alpha_vs_buy_hold` recalcule les moyennes dans la boucle

Détaillé en [`01-FINANCIER.md`](01-FINANCIER.md) F-13. `mean(strat)` et
`mean(bench)` sont appelés à l'intérieur des compréhensions
(`performance_metrics.py:164-165`), donc `n` fois chacun. Latent aujourd'hui
(la branche n'est atteinte que si les deux séries ont la même longueur, ce qui
n'arrive pas depuis le backtest), mais c'est une bombe à retardement pour
quiconque alignera les deux séries.

---

## P-05 🟡 `df[:i+1]` à chaque barre

`engine/backtest.py:1582` : `ctx.window = df[:i + 1]`.

Polars produit une vue (slice) plutôt qu'une copie pour cette opération, donc
le coût direct est faible. Mais :

- chaque `score()` qui fait `window["close"].to_numpy()` **matérialise** la
  tranche — O(i) par barre ;
- chaque `window.tail(n)` / `window[-n:]` dans une stratégie idem ;
- la tranche est reconstruite même quand aucune stratégie ne l'utilise (barres
  en position, où seul `check_early_exit` la consomme).

**Correction** : passer `(df, i)` aux stratégies plutôt qu'une tranche, et
laisser chacune décider de sa fenêtre. C'est un changement de contrat, donc à
planifier — mais c'est la vraie source du O(n²).

---

## P-06 🟡 Double sérialisation IPC

`OptimizerSearchEngine._safe_worker_count` sérialise `df_is` et `df_oos` en IPC
**uniquement pour en mesurer la taille** :

```python
_buf_is = io.BytesIO(); self.df_is.write_ipc(_buf_is)
_buf_oos = io.BytesIO(); self.df_oos.write_ipc(_buf_oos)
per_worker = int((_buf_is.tell() + _buf_oos.tell()) * 5) + 256 * 1024 * 1024
```

Puis `_serialize_pool_inputs()` recommence l'opération pour de bon. Sur des
DataFrames de 20 000 lignes × ~460 features, chaque sérialisation coûte du
temps et de la mémoire — payés deux fois, à chaque ouverture de pool.

**Correction** : sérialiser une fois, passer la taille à `_safe_worker_count`.
Ou estimer via `df.estimated_size()`, qui est O(1) en polars.

---

## P-07 🟡 `fetch_my_trades` depuis l'ouverture de la position

`live/position_close_mixin.py:68` :

```python
since = max(0, int(pos.get("open_time", time.time()) * 1000) - 60_000)
...
my_trades = self.exchange.fetch_my_trades(symbol, since=since) or []
```

Puis filtrage en Python sur `t["order"] == close_id`. Pour une position tenue
plusieurs jours sur un symbole actif, cela ramène l'intégralité des exécutions
de la période — potentiellement paginées par ccxt — pour n'en garder qu'une ou
deux.

**Correction** : `since = int(time.time() * 1000) - 300_000` (l'ordre de
clôture vient d'être passé), ou utiliser `fetch_order_trades(close_id)` quand
l'exchange le supporte.

---

## P-08 🟡 Bundle frontend monolithique

Détaillé en [`06-FRONTEND-UI-UX.md`](06-FRONTEND-UI-UX.md) U-04 et U-08.
Résumé chiffré :

- 98 composants `'use client'` sur 122 ;
- **aucun** `next/dynamic` dans tout `frontend/src` ;
- quatre vues de plus de 700 lignes, dont `optimizer-view.tsx` (1 558) chargée
  sur toutes les routes ;
- Recharts **et** lightweight-charts **et** framer-motion dans le même bundle.

---

## P-09/P-10 (mineurs)

- **P-09** : `_discover_strategies` (`api/helpers.py:141-163`) ouvre et lit les
  45 fichiers de `app/strategies/*.py` pour y chercher `class Strategy` par
  expression régulière, avec un cache de 60 s. Le choix de tester le **texte**
  plutôt que d'importer est judicieux (importer coûterait plusieurs secondes de
  LightGBM/polars) ; reste que 45 lectures de fichier par minute sont
  inutiles. Un cache invalidé par `mtime` du répertoire suffirait.
- **P-10** : `oos_tracker._save_record` recharge l'intégralité de
  `data/oos_tracker.json` (254 entrées, **264 Ko**), y insère un slot et
  réécrit tout — pour **chaque** slot du forward-test. Avec 250 slots actifs,
  c'est 250 lectures + 250 écritures de 264 Ko par passe. Un fichier par slot,
  ou une écriture unique en fin de boucle, réglerait le problème.

---

## Ce qui est solide

- **Pré-calculs vectorisés O(n)** en tête de `Backtester.run` : `_pre_atr14`,
  puis extraction en tableaux numpy (`atr_arr`, `low_arr`, `high_arr`,
  `close_arr`) pour un accès O(1) dans la boucle. C'est la bonne structure.
- **Cache LRU process-wide des colonnes pré-calculées**
  (`core/indicators_precompute.py`), dimensionné par
  `perf.precompute_cache_size` (128, configurable) : le raisonnement — « 4
  stratégies en parallèle sur le même df, N trials sur les mêmes df_is/df_oos »
  — est exact et le gain est réel.
- **Pool de process persistant** de l'optimiseur : ne pas repayer le spawn +
  le ré-import complet de l'application (LightGBM, polars) entre le dépistage
  et la recherche est le bon arbitrage, correctement mesuré (« 749 s vs 287 s
  sur opus_omnibus_v9 »).
- **`mem_aware_max_workers`** : plafond de workers dérivé de la mémoire
  disponible et de la taille réelle du payload, pas d'un nombre magique.
- **`_group_metrics` pré-groupé en une passe** (`backtest.py:255-260`) : le
  commentaire décrit précisément le O(n×k) supprimé. Bonne prise.
- **`log_throttled`** (`core/log_throttle.py`) : le modèle de coûts est
  annoncé une fois par contexte distinct puis passe en DEBUG — sans quoi
  l'optimiseur, qui crée un `Backtester` par essai, noierait les journaux.
- **Index SQLite composites** posés d'après les requêtes réelles, WAL activé,
  `busy_timeout` à 30 s.
- **`GZipMiddleware(minimum_size=500)`** et sondage frontend étagé (3 s / 10 s /
  60 s / 180 s selon la fraîcheur nécessaire) : le principe est bon, seule la
  cadence de `useBotStatus` est trop agressive (U-03).
