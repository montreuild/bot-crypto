# 🔍 Audit Global du Bot — Architecture, Code, Web, Workflow

Audit réalisé le 2026-06-10 (analyse statique complète + profilage d'exécution
réel des 44 stratégies). Complémentaire de `PRODUCTION_READINESS.md` (axé
livetrading) — ce document couvre l'architecture, la cohérence du code, les
redondances, le web et le flux de travail.

**Synthèse** : l'architecture en couches est saine (core / engine / live / api /
strategies, registre auto-découvert, séparation config.yaml ↔ strategies/*.yaml,
stockage Parquet unifié). Les trois vrais problèmes structurels sont :
**(1) la prolifération de variantes de stratégies (45 fichiers, ~17 variantes
Opus largement copiées-collées), (2) des fichiers monolithiques difficiles à
faire évoluer (backtest.run ≈ 700 lignes), (3) la duplication backtest ↔ live
de la logique d'exécution (fees, trailing, sizing implémentés deux fois).**

---

## 1. Architecture & cohérence

### Points forts (à conserver)
- Couches claires : `app/core` (utilitaires), `app/engine` (signaux/backtest/
  optimiseur), `app/live` (exécution), `app/api` (FastAPI), `app/strategies`.
- Registre de stratégies auto-découvert (`registry.py` via `param_space`) ;
  ajouter une stratégie = déposer un `.py`, le YAML est bootstrappé.
- `resolve_strategy_params` : source unique des paramètres pour le backtest ET
  le live (cohérence garantie).
- CandleStore/DerivativesStore : Parquet incrémental, thread-safe, dédupliqué.
- Pas d'import circulaire, logging structuré partout, pas de secrets en dur.

### Problèmes
| Constat | Détail | Impact |
|---|---|---|
| **Monolithes** | `backtest.py` 1058 L (la méthode `run()` ≈ 700 L gère signaux, trailing, TP, time-exit, early-exit, scale-in, fees, equity), `optimizer.py` 1033 L, `indicators.py` 1030 L, `live_trader.py` 951 L | Chaque évolution (ex. pyramidage) demande de toucher un bloc géant ; tests unitaires impossibles sur les sous-parties |
| **Duplication backtest ↔ live** | Fees/slippage/borrow calculés dans `backtest.py` ET `position_mixin.py` avec des formules voisines mais pas identiques (ex. borrow simple en backtest vs composé en live) ; trailing partagé (bien) mais initialisation/phases dupliquées | Écarts paper/backtest vs live difficiles à diagnostiquer |
| **Couche qui fuit** | `live_trader` charge directement les modèles ML (`MLStrategyTrainer`) ; `backtest.py` connaît les détails ML (`use_pretrained_ml`, reset_model) | Responsabilités partagées, fragile |

**Recommandation** : extraire un module commun `app/core/execution.py`
(FeesCalculator, sizing) consommé par les deux chemins, et découper
`Backtester.run()` en `_manage_open_position()` / `_try_enter()` /
`_close_at()`. Ajouter un test de parité backtest ↔ live (même signal, même
trade ⇒ même PnL net).

---

## 2. Redondances — stratégies (le plus gros chantier)

45 fichiers dans `app/strategies/` pour ~20 stratégies réellement distinctes.

### Code mort ou quasi mort (suppression candidate, gain immédiat)
- **Wrappers ultra-minces** : `opus_omnibus_v7_1.py` (30 L), `v8_1.py` (44 L),
  `v9_1.py` (22 L), `v10_1.py` (22 L) — simples surcharges de défauts.
  → À remplacer par des presets de params dans le YAML de la version mère.
- **Versions dépassées** : `scoring_statistique_opus.py`, `_v2`, `_v3`
  (la v5 est l'aboutissement) ; `opus_omnibus_v8/v9` si v11/v12 sont retenues.
- **YAML orphelins** (le `.py` n'existe plus) : `strategies/yoyo.yaml`,
  `opus_omnibus_v6.yaml`, `opus_omnibus_v6_pretrained.yaml`,
  `ml_dynamic_threshold_no_ml.yaml` → chargés/affichés pour rien.
- `optimizer_changelog.json` : ~~croissance infinie~~ *(correction : une
  rotation à 200 entrées existait déjà ; l'écriture est désormais compacte,
  166 Ko au lieu de 265 Ko)*.

### Copier-coller massif
Les variantes Opus (v7→v12, `_no_ml`, `_pretrained`, `_retrained`) partagent
80–90 % de leur code : calcul de régime, features V4 (~462 colonnes),
labellisation, `_train` LightGBM, sélection de features, `load_model`.
Le wrapper de cache d'entraînement ajouté lors de cette session a d'ailleurs dû
être appliqué 5 fois à du code identique — symptôme typique.

**Recommandation** : une classe `OpusBase` (features, régime, train, predict)
+ sous-classes ne portant que les setups/seuils. Cible : 45 → ~25 fichiers,
−4000 à −6000 lignes. Décider d'abord quelles versions sont *réellement
tradées* (cf. optimizer_results + slots actifs) et archiver le reste dans
`research/`.

### research/
8 scripts one-shot (`backtest_squeeze.py`, `analysis_btc.py`, …) jamais
importés par `app/` ; conservent de la valeur documentaire mais devraient être
clairement marqués comme archives (README dans `research/`).

---

## 3. Qualité du code

- **`except Exception` : 296 occurrences** dont 13 `except Exception: pass`.
  Beaucoup sont une dégradation gracieuse volontaire (réseau, notifications) —
  acceptable — mais certaines masquent de vrais bugs :
  `registry.py:21` (stratégie cassée silencieusement ignorée → seul un
  `logger.debug` la signale), `optimizer.py` (reset modèle), chargement YAML
  stratégie (`config.py` — un YAML corrompu disparaît sans alerte visible).
  → Promouvoir au minimum ces trois cas en `logger.warning/error`.
- Fonctions trop longues : `Backtester.run`, `Optimizer.search`,
  `LiveTrader._cycle` + `_try_open_from_signal` (acceptable), v11 `_train`.
- Bons réflexes présents : validation des noms de stratégies (anti-injection),
  whitelist d'exchanges, sanitization JSON (NaN/Inf), locks fichiers Parquet.

---

## 4. Performance (mesures réelles, pas d'estimation)

Profilage de toutes les stratégies (fenêtres croissantes, 6000 bougies 1h) :

| Catégorie | Coût mesuré | Verdict |
|---|---|---|
| Rule-based (trend, breakout, fear_momentum, tvr, snowball, …) | 0,03–0,7 ms/barre | OK — `precompute_df` + caches `prepare_for_backtest` font le travail |
| Opus pretrained / no_ml | 0,1–2 ms/barre | OK |
| **Opus retrained (v7, v11, v12, stat_v4, v10_retrained)** | 0,7–2,8 s **par réentraînement** walk-forward (toutes les `retrain_every` barres) | Corrigé : cache d'entraînement inter-trials (`app/core/train_cache.py`) — trial 2+ ≈ 30× plus rapide |
| **ml_dynamic_threshold** | ~125 s par refit × tous les 50 appels → **~35 h sur 50k bougies** | Corrigé : réutilisation des hyperparams entre refits (refit 1,3 s), fenêtre d'entraînement bornée |

Conclusion : la lenteur « certaines stratégies mettent des heures » venait à
~95 % du réentraînement ML inline, pas du moteur de backtest. Le slicing
`df[:i+1]` polars par barre est bon marché ; inutile de réécrire le moteur.

Reste à faire (optionnel) : cache 16 entrées de `_PRECOMPUTE_CACHE`
(indicators.py) trop petit pour 40 trials × 6 TF ; scoring_statistique_v4/v5
paient ~0,5 ms/barre de validation sklearn par appel (batch-prédiction
possible par segment entre deux retrains).

---

## 5. Web / API

- 13 templates, ~8900 lignes au total ; `config.html` 1421 L,
  `backtest.html` 1092 L. CSS/JS utilitaires redéfinis par page (tables, KPI
  cards, fetch boilerplate) malgré un `base.html` déjà riche. Sans framework
  front, viser au minimum : extraire les styles communs des pages vers
  `base.html` et un `static/common.js` (apiFetch+toast+format helpers).
- Routes : auth systématique via `verify_api_key`, semaphores backtest/
  optimiseur, SSE pour la progression — sain. *(Vérification : les handlers
  lèvent bien `HTTPException` partout ; les `{"error": …}` rencontrés sont
  des résultats partiels par stratégie dans les payloads multi-backtests,
  voulus — pas d'incohérence à corriger.)*
- `/api/config` redacte `exchange` et `notifications` ; ✅ ajouté : masquage
  de `web.api_key` et des credentials d'URL de base de données.

---

## 6. Workflow données → backtest → optimisation → live

Le flux est globalement clair et c'est un point fort du projet :

```
data/ohlcv (CandleStore, accumulation auto)  +  data/derivatives (idem)
   → Backtest / Replay (UI)
   → Optimiseur (IS/OOS, bayésien) → strategies/{nom}.yaml optimizer_results
   → get_active_strategies_per_tf → slots LiveTrader (top N par TF)
```

Frictions identifiées :
1. **Cycle de vie des stratégies flou** : pas de statut explicite
   (expérimentale / validée / production / archivée). C'est la cause racine de
   l'accumulation des 45 fichiers. → Ajouter un champ `status:` dans le YAML
   et le filtrer dans l'UI.
2. Renommage/suppression d'une stratégie : le YAML devient orphelin sans
   alerte (4 cas constatés).
3. `optimizer_changelog.json` sans rotation.
4. Modèles ML (`models/*.pkl`) sans versioning : un changement de features
   rend un pkl silencieusement incompatible (le FeatureStore a un champ
   version, les pkl non).

---

## 7. Tests & données

- 24 fichiers de tests, 276 tests, rapides (<10 s) — bonne base : backtest,
  risk, allocator, indicateurs, stratégies clés, e2e paper.
- Manques principaux : exécution d'ordres live (mockée), parité
  backtest↔live, optimiseur (1033 L non testées), trailing multi-phases.
- Données : stockage cohérent depuis cette session (`data/ohlcv`,
  `data/derivatives`, `data/features`). `trades.db` (SQLite) sans stratégie de
  backup automatisée → à brancher (cron + copie datée).

---

## 8. Plan d'action priorisé

### Quick wins (≤ 1 jour)
1. ~~Supprimer les 4 YAML orphelins + compacter `optimizer_changelog.json`~~ ✅ fait.
2. Promouvoir en `warning` les `except` silencieux de `registry.py` et du
   chargement des YAML stratégies.
3. Décider la liste des stratégies « production » et passer `enabled: false`
   sur toutes les autres (réduit le bruit UI, le temps de chargement et les
   surprises de l'auto-optimiseur).

### Moyen terme (1–2 semaines)
4. Factoriser la famille Opus autour d'une `OpusBase` ; supprimer wrappers
   `*_1` et versions dépassées (−4000 L).
5. Découper `Backtester.run()` + extraire le calcul fees/borrow commun
   backtest/live ; ajouter le test de parité.
6. Mutualiser le CSS/JS des templates dans `base.html`/static.

### Long terme
7. Statut de cycle de vie des stratégies (YAML + UI) et archivage `research/`.
8. Versioning des modèles ML (hash features + date dans le nom du pkl,
   contrôle au chargement).
9. Couverture de tests : ordres live mockés (ccxt stub), optimiseur,
   scénarios de crash/restauration.

---

*Audit complémentaire : voir `PRODUCTION_READINESS.md` pour le volet
livetrading/production (stops exchange, margin, sécurité API).*
