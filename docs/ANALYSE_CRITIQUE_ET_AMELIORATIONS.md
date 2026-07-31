# 🔬 Analyse critique — Crypto Bot V12

> Revue critique read-only du dépôt (moteur de signaux & backtest, exécution live,
> risque, allocation & cycle de vie des slots, données, exchange, API/web, ML,
> config). Date : 2026-06-29.

**Périmètre audité** : `app/engine` (engine, backtest, optimizer, opt_scoring),
`app/live` (live_trader, position_mixin, signal_pipeline, capital_allocator,
slot_lifecycle, balance_sync, ohlcv_cache, watchdog), `app/core` (risk, execution,
trailing, exchange, database, candle_store, oos_tracker, indicators_causal,
config, notifications), `app/api` (main, helpers, state, routes), 15 templates web,
`app/ml/trainer`, `config.yaml`.

---

## Synthèse exécutive

Le projet est **étonnamment mûr** pour un bot personnel : architecture modulaire
propre (mixins, pipeline, allocator, lifecycle), garde-fous live réels
(kill-switch persistant, OCO natif OKX, ordres idempotents par `clOrdId`,
réconciliation des coûts réels, reprise après crash), et un backtest **causal
correct** (signal sur `df[:i+1]`, entrée à `open[i+1]`). La documentation est
abondante et honnête.

**Mais trois risques majeurs minent la fiabilité réelle :**

1. **🔴 Sur-risque systématique en live** : le sizing live divise par l'ATR brut
   alors que le stop est posé à `mult×ATR` → une position « 1 % de risque »
   en risque en réalité ~2,5 %. Le backtest, lui, dimensionne correctement
   (par la distance au stop). Divergence non couverte par le test de « parité ».
2. **🟠 Parité backtest↔live incomplète** : la « parité » verrouillée ne couvre
   que les *formules* de frais/PnL — ni le *sizing* (point 1), ni le *timing*
   (le live score sur la bougie **en cours de formation**, le backtest sur
   bougies clôturées). Les résultats paper/backtest ne prédisent pas fidèlement
   le live.
3. **🔴 Bypass d'authentification via `X-Forwarded-For`** : config par défaut sans
   clé API + `host: 0.0.0.0`, et l'IP cliente est extraite d'un header spoofable
   → accès non authentifié possible (start/stop, config, reset halt). Rate-limiter
   configuré mais **non branché**.

---

## 1. Stratégies & alpha

| # | Constat (preuve) | Gravité |
|---|---|---|
| 1.1 | **Timing live ≠ backtest.** Backtest : entrée à `open[i+1]` après clôture de la barre `i` (`backtest.py:475`). Live : `OHLCVCache.get` ne retire **pas** la bougie en cours (`ohlcv_cache.py:148-161`) et `candle_store.fetch` ne l'élague qu'en `prefer_cache` backtest (`candle_store.py:61`) → les stratégies scorent sur une bougie non close (repaint) et exécutent immédiatement au ticker. | **Élevé** |
| 1.2 | **Optimisation/forward-test mono-symbole.** L'auto-opt et l'edge ne tournent que sur `BTC/USDC` (`live_trader.py:832`, `oos_tracker` `symbol="BTC/USDC"`), mais le scanner trade ETH et d'autres. Params calés sur le régime BTC, appliqués ailleurs → transfert d'edge non garanti. | **Élevé** |
| 1.3 | **Sémantique portefeuille divergente.** Le `Backtester` retient *le meilleur* signal parmi toutes les stratégies (`engine.best_signal`, une position à la fois), alors que le live ouvre **une position par slot** en parallèle (`SignalPipeline`). | **Moyen** |
| 1.4 | **Prolifération de stratégies = biais de sélection.** ~40 stratégies dont de nombreuses quasi-duplications (`opus_omnibus_v7…v12`, `scoring_statistique_opus_v1…v5`). Choisir « la meilleure de 40 variantes » sur le même historique BTC gonfle l'edge apparent. | **Moyen** |
| 1.5 | **ML : entraînement fragile.** Retrain sur BTC seul ; AUC≈0 toléré silencieusement (`trainer.py:157-165`) ; timeout annule le *future* mais le thread continue en tenant potentiellement `_ml_lock` ; warning de version sklearn supprimé. | **Moyen** |

**Bonus découvert pendant l'implémentation** : `SignalPipeline` construit un
`Signal` qui ne conserve que `side/score/reason` — les hints d'exécution de la
stratégie (`sl_atr_mult`, `tp_atr_mult`, `stop_hint`, `trail_override`,
`disable_trailing`, `size_factor`, `indicators`, `setup`) sont **perdus** dans le
chemin de production (pipeline), alors qu'ils sont préservés dans le chemin direct
`_scan_symbol_strategy`. À corriger séparément.

**Points forts** : causalité du backtest, indicateurs causaux mémoïsés
(`indicators_causal.py`), score composite monotone avec le PnL
(`opt_scoring.py:115-118`), cône d'edge + contrat Monte-Carlo budget-indépendants.

## 2. Gestion des slots & capital

| # | Constat (preuve) | Gravité |
|---|---|---|
| 2.1 | **Deux systèmes d'allocation concurrents, l'un mort.** `mode: performance` (rebalance hebdo, `capital_allocator.py:342`) est appliqué ; l'allocation continue pilotée par le score (`apply_continuous_allocation`) est calculée mais jamais appliquée (`continuous_allocation: false`). | **Moyen** |
| 2.2 | **Lifecycle largement court-circuité.** 15 slots forcés `ACTIF` via `lifecycle.manual_active` (`config.yaml:146-161`), alors que la machinerie candidat/essai/actif/retiré + promotion-par-edge est conçue pour décider ça. | **Moyen-Élevé** |
| 2.3 | **Incohérence lifecycle ↔ budgets.** `manual_active` liste 15 slots ; `capital_allocator.slot_budgets` n'en liste que 7 (`config.yaml:126-133`). | **Moyen** |
| 2.4 | **Stats hebdo de rebalance non persistées.** `weekly_pnl/wins/trades` vivent en mémoire (`register_close`) ; après crash elles repartent à 0 → rebalance faussé. | **Moyen** |
| 2.5 | **« Corrélation » trompeuse.** `check_correlation` rejette si ≥75 % des positions sont dans le même sens (`capital_allocator.py:270-307`) — garde de concentration *directionnelle*, pas une vraie corrélation. | **Faible-Moyen** |
| 2.6 | **Tolérance budget +5 %** par slot (`capital_allocator.py:244`) : la somme des dépassements peut sur-allouer l'agrégat. | **Faible** |

## 3. Risque & exécution

| # | Constat (preuve) | Gravité |
|---|---|---|
| 3.1 | **🔴 Sur-risque de sizing en live.** `risk.compute_size` : `size = capital×risk / ATR` (`risk.py:449`), mais le stop est à `mult×ATR` (`position_mixin.py:247-258`). Risque réel = `capital×risk×mult`. Avec `trail_wide=2.5`, un `risk_per_trade=1 %` **risque ~2,5 %**. Le backtest dimensionne par la distance au stop (`backtest.py:506-508`). | **Élevé** |
| 3.2 | **Sizing diverge aussi sur les facteurs.** Live applique `score_internal_factor` et `volatility_brake` au sizing (`risk.py:457-460`) ; le backtest non. | **Moyen** |
| 3.3 | **« Parité » sur-vendue.** `test_execution_parity` ne verrouille que les formules monétaires (`execution.py`) ; ni le sizing (3.1/3.2) ni le timing (1.1) ne sont testés. | **Moyen** |

**Points forts** : kill-switch persistant & sticky (`risk.py:179-203`), OCO natif
OKX (`position_mixin.py:552-611`), idempotence `create_order` par `clOrdId`
(`exchange.py:180-237`), alignement des fills partiels, réconciliation des coûts
réels + alerte >5 %, adoption du stop exchange à la reprise, recovery réseau,
watchdog dead-man fichier.

## 4. Corrélation & cohérence des pages

| # | Constat (preuve) | Gravité |
|---|---|---|
| 4.1 | **Templates potentiellement orphelins.** 15 templates pour 10 routers inclus (`api/main.py:275-284`) ; `compare.html`, `settings.html` sans route API dédiée évidente. | **Moyen (à vérifier)** |
| 4.2 | **Métriques à fenêtres mélangées, non étiquetées.** `/api/status` agrège `win_rate`/`profit_factor`/`by_strategy` sur **tous** les trades (`live_trader.py:1139`), le rebalance sur 7 j, le lifecycle sur 45 j — affichés côte à côte sans préciser la fenêtre. | **Moyen** |
| 4.3 | **`total_pnl_pct` au dénominateur dérivant.** `total_pnl / capital_display` (`live_trader.py:1104`). | **Faible** |

**Point fort** : source unique via `/api/status` + cache DB 10 s ; `_reserved`
filtré partout.

## 5. Intégrité & cohérence des données

| # | Constat (preuve) | Gravité |
|---|---|---|
| 5.1 | **Écritures multi-tables non atomiques.** `save_trade` puis `update_daily_stats` font deux commits (`position_mixin.py:981-987`). | **Moyen** |
| 5.2 | **Allocator non verrouillé.** Threads de fond lisent/mutent `allocator._slots` sans lock pendant le cycle. | **Moyen** |
| 5.3 | **`entry_time` jamais renseigné** (`database.py:263-272`). | **Faible** |
| 5.4 | **`config.yaml` écrit par un thread daemon** (allocator), mais protégé (`_config_write_lock` + round-trip ruamel préservant `${VAR}` — **secrets non fuités**, vérifié `routes/config.py:14-22`). | **Faible** |

**Points forts** : SQLite WAL + `busy_timeout=30s`, hygiène candle store, UTC
partout, vérification des positions fantômes vs exchange à la reprise.

## 6. Architecture & qualité

- **Forces** : séparation des responsabilités nette ; tests utiles (parité,
  OCO, allocator, consensus, fft, dérivés, e2e) ; docs détaillées et honnêtes.
- **Dette / risques (Moyen)** :
  - **Code dormant labellisé « Phase »** (allocation continue, veto shadow,
    lifecycle bypassé) → charge cognitive.
  - **~40 stratégies versionnées** non archivées.
  - **Lacunes de tests** sur les 3 risques majeurs (timing live, sizing live vs
    backtest, auth `X-Forwarded-For`, concurrence allocator/lifecycle).
  - **`edge_lookback_days: 365`** tronqué silencieusement par `_MAX_EDGE_BARS=12000`
    sur petits TF.

## 7. Sécurité & exploitation

| # | Constat (preuve) | Gravité |
|---|---|---|
| 7.1 | **🔴 Bypass auth via `X-Forwarded-For`.** Sans clé (`web.api_key: ''` par défaut) et `host: 0.0.0.0`, l'IP vient de `_extract_client_ip` qui fait confiance au premier `X-Forwarded-For` sans validation (`helpers.py:21-28`) → `X-Forwarded-For: 127.0.0.1` = accès complet. | **Critique** |
| 7.2 | **Rate-limiter inerte.** `Limiter(default_limits=["60/minute"])` défini (`api/main.py:35`) mais `SlowAPIMiddleware` jamais ajouté et aucun `@limiter.limit`. | **Moyen** |
| 7.3 | **Surface par défaut large.** `0.0.0.0` + pas de HTTPS sauf opt-in. | **Moyen** |

**Points forts** : secrets exclusivement via `${ENV}` et non réécrits lors de la
persistance, whitelist d'exchanges, validation des noms de stratégie
(anti-injection), handler d'exception global, `/health` minimal.

---

## Tableau de priorisation

### ⚡ Quick wins (effort faible, impact élevé)

| Action | Axe | Pourquoi |
|---|---|---|
| Corriger le sizing live (distance au stop, pas ATR brut) dans `risk.compute_size`. | 3.1 | Élimine un sur-risque ~2,5× et aligne live/backtest. |
| Valider `X-Forwarded-For` (TRUSTED_PROXIES ; sinon `request.client.host`). | 7.1 | Ferme un bypass d'auth critique. |
| Brancher le rate-limit (`SlowAPIMiddleware`). | 7.2 | Active une protection déjà payée. |
| Élaguer la bougie en cours côté live avant scoring. | 1.1 | Supprime le repaint, rapproche le timing du backtest. |
| Défaut sûr : refuser le live si `host=0.0.0.0` et `api_key` vide. | 7.3 | Empêche l'exposition accidentelle. |
| Persister les stats hebdo de l'allocator. | 2.4 | Rebalance correct après redémarrage. |
| Étiqueter les fenêtres des métriques dans l'UI (lifetime / 7 j / 45 j). | 4.2 | Lève les incohérences perçues entre pages. |

### 🏗️ Chantiers de fond (effort élevé, impact structurel)

| Action | Axe |
|---|---|
| Optimiser/forward-tester par symbole (BTC *et* ETH) ou figer l'univers de trading. | 1.2 |
| Test de reproductibilité backtest↔live couvrant sizing + timing. | 3.3 / 6 |
| Trancher l'allocation (une seule voie), clarifier lifecycle ↔ budgets. | 2.1 / 2.2 / 2.3 |
| Verrou sur l'allocator + transaction atomique `save_trade`+`daily_stats`. | 5.1 / 5.2 |
| Rationaliser les ~40 stratégies. | 1.4 / 6 |
| Vraie mesure de corrélation (matrice des rendements par symbole). | 2.5 |
| Auditer les pages orphelines. | 4.1 |

---

## Zones non vérifiées

- Comportement réel du retour OKX/ccxt sur la dernière bougie (close ou en cours).
- Routes `compare`/`settings` (backing API).
- Audit de leakage *intra-stratégie* (features utilisant `close[i]` pour décider en `i`).
- `auto_optimizer`/`opt_workers` (concurrence, OOM sous charge).
