# 🔍 Audit Global du Bot — Architecture, Code, Web, Workflow

Audit réalisé le 2026-06-10 (analyse statique complète + profilage d'exécution
réel des 44 stratégies), **mis à jour le 2026-06-11 après la passe de
corrections** (les éléments traités sont marqués ✅). Complémentaire de
`PRODUCTION_READINESS.md` (axé livetrading) — ce document couvre
l'architecture, la cohérence du code, les redondances, le web et le flux de
travail.

**Synthèse** : l'architecture en couches est saine (core / engine / live / api /
strategies, registre auto-découvert, séparation config.yaml ↔ strategies/*.yaml,
stockage Parquet unifié). Les trois problèmes structurels identifiés étaient :
**(1) la prolifération de variantes de stratégies (45 fichiers, ~17 variantes
Opus largement copiées-collées) — reste le chantier principal ; (2) des
fichiers monolithiques — ✅ `Backtester.run()` a été découpé ; (3) la
duplication backtest ↔ live de la logique d'exécution — ✅ résolue via
`app/core/execution.py` + test de parité.**

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
| Constat | Détail | Statut |
|---|---|---|
| **Monolithes** | `backtest.py` (la méthode `run()` gérait ~700 L : signaux, trailing, TP, time-exit, early-exit, scale-in, fees, equity), `optimizer.py` 1033 L, `indicators.py` 1030 L, `live_trader.py` 951 L | ✅ `run()` découpé en `_close_at()` / `_manage_open_position()` / `_try_enter()` (vérifié iso-comportement sur baseline). Optimizer/indicators/live_trader restent à découper (moyen terme) |
| **Duplication backtest ↔ live** | Fees/slippage/borrow calculés dans `backtest.py` ET `position_mixin.py` avec des formules voisines mais pas identiques (ex. borrow simple en backtest vs composé en live) | ✅ Résolu : `app/core/execution.py` (trade_fees, borrow_cost composé, close_pnl, sizing) consommé par les deux chemins ; parité verrouillée par `tests/test_execution_parity.py` ; le backtest utilise la formule d'emprunt du live (impact ≤ 0,003 %) et le TF effectif du run (bugfix) |
| **Couche qui fuit** | `live_trader` charge directement les modèles ML (`MLStrategyTrainer`) ; `backtest.py` connaît les détails ML (`use_pretrained_ml`, reset_model) | ⏳ Responsabilités partagées, fragile — à traiter avec la factorisation Opus |

---

## 2. Redondances — stratégies (le plus gros chantier)

45 fichiers dans `app/strategies/` pour ~20 stratégies réellement distinctes.

### Code mort ou quasi mort (suppression candidate, gain immédiat)
- **Wrappers ultra-minces** : `opus_omnibus_v7_1.py` (30 L), `v8_1.py` (44 L),
  `v9_1.py` (22 L), `v10_1.py` (22 L) — simples surcharges de défauts.
  → À remplacer par des presets de params dans le YAML de la version mère.
- **Versions dépassées** : `scoring_statistique_opus.py`, `_v2`, `_v3`
  (la v5 est l'aboutissement) ; `opus_omnibus_v8/v9` si v11/v12 sont retenues.
- ✅ **YAML orphelins** (le `.py` n'existait plus) : `strategies/yoyo.yaml`,
  `opus_omnibus_v6.yaml`, `opus_omnibus_v6_pretrained.yaml`,
  `ml_dynamic_threshold_no_ml.yaml` — **supprimés** (ils étaient chargés dans
  `strategies.enabled` et provoquaient un échec d'import à chaque démarrage).
- ✅ `optimizer_changelog.json` : *(correction du constat initial : une
  rotation à 200 entrées existait déjà)* — écriture passée en JSON compact,
  166 Ko au lieu de 265 Ko.

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

- **`except Exception` : ~296 occurrences** dont 13 `except Exception: pass`.
  Beaucoup sont une dégradation gracieuse volontaire (réseau, notifications) —
  acceptable. ✅ Les trois cas qui masquaient de vrais bugs sont promus en
  `warning` : import de stratégie en échec dans `registry.py` (la stratégie
  disparaissait du registre/optimiseur/UI avec un simple `debug`), bootstrap
  YAML dans `config.py`, et `reset_model` avalé entre les snapshots IS/OOS
  de l'optimiseur (risque de fuite d'état IS→OOS). *(Le chargement des YAML
  stratégies loggait déjà en `warning` — vérifié.)*
- Fonctions trop longues : ✅ `Backtester.run` découpé ; restent
  `Optimizer.search` et v11 `_train` (acceptables à court terme).
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
  `backtest.html` 1092 L. ✅ Les utilitaires réellement dupliqués à
  l'identique ont été mutualisés dans `base.html` : CSS `.form-row`,
  `.form-lbl`, `.btn-outline`, `.empty-state`, `.stat-chip`/`.chip-*` ;
  JS `fmtSign`/`fmtPrice` (rejoignent `escHtml`/`apiFetch`/`toast` déjà
  partagés). Les pages ne gardent que leurs vraies variantes en surcharge
  (le bloc `page_styles` est injecté après les styles de base). *(Analyse :
  la duplication restante est essentiellement du CSS spécifique par page,
  pas du copier-coller — un framework front n'est pas justifié.)*
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
2. ✅ Renommage/suppression d'une stratégie : les 4 YAML orphelins constatés
   ont été supprimés ; l'import en échec est désormais loggé en `warning`
   (registry), ce qui rendra les prochains cas visibles.
3. ✅ `optimizer_changelog.json` : rotation existante confirmée (200 entrées),
   écriture compactée.
4. Modèles ML (`models/*.pkl`) sans versioning : un changement de features
   rend un pkl silencieusement incompatible (le FeatureStore a un champ
   version, les pkl non).

---

## 7. Tests & données

- 26 fichiers de tests, 282 tests, rapides (~15 s) — bonne base : backtest,
  risk, allocator, indicateurs, stratégies clés, e2e paper, ✅ cache
  d'entraînement (`test_train_cache.py`), ✅ parité backtest↔live et formules
  d'exécution (`test_execution_parity.py` : même trade ⇒ même PnL net via
  `Backtester._close_at` et `PositionMixin._close_position`).
- Manques restants : exécution d'ordres live (mockée, incl. stops exchange),
  optimiseur (1033 L non testées), trailing multi-phases.
- Données : stockage cohérent depuis cette session (`data/ohlcv`,
  `data/derivatives`, `data/features`). `trades.db` (SQLite) sans stratégie de
  backup automatisée → à brancher (cron + copie datée).

---

## 8. Plan d'action priorisé

### ✅ Fait (passe du 2026-06-11)
1. Suppression des 4 YAML orphelins + compactage `optimizer_changelog.json`.
2. Promotion en `warning` des `except` silencieux critiques (registry,
   bootstrap YAML, reset_model optimiseur).
3. Module commun `app/core/execution.py` + découpage de `Backtester.run()`
   (`_close_at` / `_manage_open_position` / `_try_enter`, vérifié
   iso-comportement) + test de parité backtest ↔ live.
4. Mutualisation des utilitaires CSS/JS dupliqués dans `base.html`.
5. Redaction `web.api_key` / credentials DB dans `GET /api/config`.
6. *(Passe précédente)* cache d'entraînement ML inter-trials, optimisation
   `ml_dynamic_threshold`, page Dérivés, stops exchange, etc. — voir
   `PRODUCTION_READINESS.md` et l'historique git.

### Quick wins restants (≤ 1 jour)
7. Décider la liste des stratégies « production » et passer `enabled: false`
   sur toutes les autres (réduit le bruit UI, le temps de chargement et les
   surprises de l'auto-optimiseur).

### Moyen terme (1–2 semaines)
8. Factoriser la famille Opus autour d'une `OpusBase` ; supprimer wrappers
   `*_1` et versions dépassées (−4000 L). C'est désormais LE chantier
   structurel principal.
9. ✅ fait — `optimizer.py` découpé en `opt_scoring` / `opt_persistence` /
   `opt_workers` (+ façade compatible) et `indicators.py` en
   `indicators_core` / `indicators_causal` / `indicators_market` /
   `indicators_precompute` (+ façade) ; chemin parallèle de l'optimiseur
   vérifié, 54 sites d'import inchangés.

### Long terme
10. Statut de cycle de vie des stratégies (YAML + UI) et archivage `research/`.
11. Versioning des modèles ML (hash features + date dans le nom du pkl,
    contrôle au chargement).
12. Couverture de tests : ordres live mockés (ccxt stub, incl. stops
    exchange), optimiseur, scénarios de crash/restauration.

---

*Audit complémentaire : voir `PRODUCTION_READINESS.md` pour le volet
livetrading/production (stops exchange, margin, sécurité API).*
