# 📝 Changelog

Historique des versions du Crypto Bot.

---

## [Non publié]

### ⚡ Performance backtest/optimisation : suppression d'un O(n²) et du « get_column storm » des stratégies ML

**Symptôme.** Backtests ML très lents et **ralentissant avec la taille** (279 → 185
→ 147 bars/s en cours de route) — catastrophique sur 50 000 barres.

**Causes (profil cProfile).**
1. **`_detect_timeframe(df)` appelé à chaque barre** sur la fenêtre **croissante**
   `df[:i+1]` : `times.diff().median()` est O(i) → backtest O(n²). Or le
   timeframe est **constant** sur tout le backtest.
2. **`_predict` (stratégies polars v7/v10_retrained/v11/v11_followsetup,
   opus_stat_retrained_v4) accédait aux ~440 colonnes de features une par une**
   (`row[c][0]`), soit ~880 `get_column` polars par barre (~58 % du temps de
   backtest).

**Correctif.**
- `_detect_timeframe` ne lit plus que les **derniers deltas** (`tail(64)`) → O(1)
  par appel, résultat identique (espacement uniforme). Appliqué à toutes les
  variantes (ML + `_no_ml`).
- `_predict` extrait la dernière ligne en **un seul appel** (`row(0, named=True)`
  → dict) puis lit les features depuis le dict → fini le `get_column` par feature.
- Résultats **numériquement identiques** (336 tests OK, dont parité scoring/ML).

**Gain mesuré** (v11, 1h) : **~5× plus rapide** (≈380 → ≈1840 bars/s à 4 000
barres) et **mise à l'échelle linéaire** restaurée (plus de ralentissement ; le
résidu provient des seuls ré-entraînements walk-forward périodiques).

**Audit O(n²) de TOUTES les stratégies** (40 fichiers) — 5 autres bugs corrigés,
même cause racine (indicateur pleine fenêtre recalculé par barre, sans cache ni
tail, pour ne lire que la dernière valeur) :
- `composite_score` : FFT+polyfit sur toute la fenêtre/barre (bornée à 1024
  barres) **et** stochastique recalculé sur tout le df (borné à k+d barres).
- `harmonic_regime` : `.to_numpy()` sur la colonne entière (ATR/close) pour
  n'utiliser que la queue → matérialisation bornée (`tail`), `_cycle` reçoit la
  queue + l'index absolu (cache stride préservé).
- `fear_momentum` : `volume.rolling_mean(20)[-1]` sur tout le df → `tail(20)`.
- `ml_dynamic_threshold` : `_detect_tf` (deltas pleine fenêtre) et `_adx(df,14)`
  recalculé par barre → bornés (`tail(64)` / `tail(300)`).

Mise à l'échelle vérifiée **constante** après correctif (ex. composite_score :
~1050 bars/s à 4 000 comme à 12 000 barres). 35 stratégies déjà saines (cache
`prepare_for_backtest` ou lectures `pre_val`/tails bornées). 336 tests OK.

### 🎯 Optimiseur : score monotone, données auto-dimensionnées, recherche TPE, parallélisme par défaut, réglage ML two-phase

Cinq améliorations ciblées de l'optimiseur et de ses performances :

- **#1 — Score composite monotone avec le PnL** (`opt_scoring.py`). Avant, la
  pénalité d'un résultat perdant était *multiplicative et jamais négative*
  (`ret_sign ∈ {1.0, 0.3}`) : une stratégie **nette perdante** au win-rate/Sharpe
  corrects obtenait un score **positif**, était sélectionnée par l'optimiseur et
  passait le gate live (`MIN_VIABLE_SCORE`). Désormais : `PnL > 0` → bundle
  qualité (échelle **inchangée**, rétro-compatible avec les scores déjà
  persistés) ; `PnL ≤ 0` → score = rendement normalisé (**négatif** et monotone
  avec la perte). Impact live : les paramétrages nets perdants sont correctement
  exclus de `get_active_strategies_per_tf` (effet uniquement après ré-optimisation
  — les scores déjà sauvegardés restent figés).
- **#2 — Limite de bougies auto-dimensionnée** (`optimizer.auto_fetch_limit`,
  utilisée par `optimize_runner.py` et la route web). Corrige le décalage
  `RECOMMENDED_LIMIT[1h]=1500 < 2229` requis par les omnibus ML, qui faisait
  **ignorer silencieusement** ces stratégies. La limite par défaut (`--limit 0`,
  `limit=0` côté UI) dérive désormais du besoin réel (`ceil(min_bars/0.35)`). Les
  jobs ignorés sont en plus **remontés visiblement** (`⊘ … ignoré : …`) au lieu
  d'être noyés dans les logs.
- **#4 — Recherche bayésienne TPE via Optuna** (`optimizer.bayesian_search`).
  Remplace l'heuristique « random + perturbation locale » par une vraie recherche
  informée (TPE). Parallèle sur un **ProcessPool persistant** (cache de features /
  d'entraînement réutilisé entre lots) ou séquentielle (cache in-process chaud).
  **Repli automatique** sur l'ancienne heuristique si Optuna est absent (dépendance
  optionnelle ajoutée à `requirements.txt`).
- **#5 — Parallélisme par défaut** (`optimize_runner.py --jobs 0 = auto cpu-1`).
  Les threads BLAS/LightGBM restent épinglés à 1 et le portillon mémoire borne la
  concurrence → discret mais bien plus rapide.
- **#6 — Réglage ML two-phase** (`optimizer.optimize_two_phase`, opt-in via
  `--ml-tune` / case UI). Grille externe sur les hyperparamètres d'entraînement
  (`learning_rate`, `n_estimators`) × recherche interne sur les seuils de décision.
  Chaque combo segmente le cache d'entraînement (coût ~linéaire). Les HP retenus
  sont persistés dans `best_params` et réutilisés au ré-entraînement du modèle final.

### 🛡️ Optimiseur ML : portillon mémoire anti-OOM (corrige l'arrêt silencieux du bot pendant une optimisation multi-jobs)

**Problème.** Lancer une optimisation ML sur plusieurs stratégies × timeframes
(ex. 9 jobs) depuis l'UI faisait **s'arrêter le bot sans aucune traceback**
(retour sec au prompt). Cause : `start_async` ouvre un thread par job et en
laisse tourner jusqu'à `cpu-1` **en parallèle** ; avec le défaut `n_jobs=1`,
chaque job évalue ses trials **dans le process** (pas de ProcessPool) — soit
`cpu-1` backtests ML walk-forward simultanés, chacun **réentraînant LightGBM en
boucle** sur de larges matrices de features. Sur de gros jeux de données (50k
bougies), le pic mémoire cumulé épuise la RAM → `std::bad_alloc` LightGBM / OOM →
le process entier est tué (sur Windows : pas d'OOM-killer noyau, pas de
traceback). Le garde-fou `mem_aware_max_workers` existant ne couvrait que le
ProcessPool d'un job (chemin `n_jobs>1`), **jamais** la concurrence inter-jobs,
et était de surcroît **désactivé sur Windows** (`available_memory_bytes()`
dépendait de `psutil` — non installé — ou de `/proc/meminfo` — Linux only).

**Correctif.**
- **Portillon d'admission mémoire** (`auto_optimizer.py`) : un job n'entre en
  exécution que si son empreinte estimée tient dans le budget restant (70 % de
  la RAM dispo, mesurée par lot). La mémoire **cumulée** des jobs actifs est
  bornée → sur machine contrainte, les jobs ML se sérialisent au lieu d'OOM.
  Règle anti-blocage : un job seul est toujours admis (au pire, un par un).
  L'estimation est échelonnée sur la taille réelle des données (la variable qui
  provoque l'OOM) et le type de stratégie (ML vs non-ML).
- **`available_memory_bytes()` multi-plateforme** (`opt_workers.py`) : ajout du
  fallback Windows `GlobalMemoryStatusEx` (ctypes) → le cap mémoire fonctionne
  enfin sur Windows même sans `psutil`.
- `psutil` ajouté aux dépendances (chemin préféré ; fallbacks `/proc/meminfo` et
  `GlobalMemoryStatusEx` conservés s'il est absent).
- `optimize_runner.py --ml-only` : pendant symétrique de `--no-ml-only`, optimise
  toutes les stratégies ML (détection structurelle `BaseStrategyML`). Ex. :
  `python optimize_runner.py --ml-only --limit 50000 --apply` (séquentiel, sûr).
- Couverture : `tests/test_opt_mem_gate.py` (admission, mise en attente,
  anti-blocage, annulation, libération, estimation).

### ⚡ Optimiseur ML : hyperparamètres d'entraînement figés + fenêtres alignées (≈ heures → minutes sur 50k bougies)

**Problème.** Impossible de mener à terme les optimisations des stratégies ML
sur 50 000 bougies (plusieurs heures par job, souvent interrompu) :

1. Les `param_space` des stratégies ML « retrained » échantillonnaient les
   hyperparamètres **d'entraînement** (`retrain_every`, `warmup_bars`,
   `n_estimators`, `num_leaves`, `learning_rate`, `amp_top_pct`, et pour V11
   `label_horizons`/`calibrate`/`prune_features`). Chaque trial avait donc une
   clé de cache d'entraînement différente → le cache process-wide
   (`train_cache.py`) ne servait à rien et **chaque trial repayait l'intégralité
   des retrains LightGBM walk-forward** (~30-100 entraînements × 2 modèles ×
   ~462 features par trial).
2. Même à hyperparamètres identiques, le déclenchement des retrains dépend du
   compteur d'appels `score()` (fonction des trades du trial) : les fenêtres
   d'entraînement divergeaient de quelques barres entre trials → fingerprints
   différents → cache contourné.

**Correctif.**
- Les hyperparamètres d'entraînement sortent de `param_space` (déplacés en
  `fixed_params`, valeurs = `_DEFAULTS`, surchargables via le YAML stratégie)
  pour : `opus_omnibus_v7`, `opus_stat_retrained_v4`, `opus_omnibus_v10_retrained`,
  `opus_omnibus_v11`, `opus_omnibus_v11_followsetup`, `opus_omnibus_v12`
  (`mldyn_lookahead`/`mldyn_vol_multiplier`), `scoring_statistique_opus_v3/v4/v5`.
  L'optimiseur se concentre sur les paramètres de **décision** (seuils, SL/TP…),
  ceux qui déterminent réellement le ratio gain/risque par timeframe.
- Nouveau `aligned_train_window()` (`app/core/train_cache.py`) : la fin de la
  fenêtre d'entraînement est alignée sur la grille `retrain_every` → fenêtres
  identiques entre trials → hits de cache déterministes. Staleness bornée à
  `retrain_every` barres, identique à la cadence de déclenchement existante.
- **Bugfix walk-forward** : dans `_train_impl`, le fast-path features prenait
  `self._bt_features.head(len(train_df))` — soit les **premières** lignes des
  features pré-calculées alors que `train_df` est une tranche de **fin** de
  fenêtre. Les modèles inline s'entraînaient donc sur les plus *vieilles*
  données au lieu des plus récentes. Corrigé via un offset (`_bt_train_offset`)
  posé par `score()` avant `_train`.

Vérifié sur données synthétiques (3 000 barres 1h, `opus_omnibus_v7`) : 2ᵉ run
= 100 % de hits du cache d'entraînement, 0 réentraînement, trades identiques.

### 🗑️ Suppression des stratégies ML `_1`

`opus_omnibus_v7_1`, `v8_1`, `v9_1`, `v10_1` (variantes « score additif » des
omnibus ML) sont supprimées (`.py` + `strategies/*.yaml`). Aucune référence
restante dans `config.yaml`, les tests ou l'UI.

### 🛠️ Optimiseur : score réaligné sur le PnL et garde-fou d'application durci

**Problème.** L'optimiseur pouvait retenir/appliquer un paramétrage au PnL OOS
nettement inférieur (ex. `+33` sur 3 trades) face au paramétrage courant
(`+96` sur 15 trades). Deux causes :

1. **Score composite dominé par un Sharpe brut non plafonné** (`_composite_score`,
   `optimizer.py`). Le terme `sharpe * 0.28` n'étant pas borné, un Sharpe absurde
   de petite fenêtre (>50, voire 120 sur 3 trades) écrasait tous les autres
   termes (échelle 0–1) — et le **montant** du PnL n'entrait pas du tout dans le
   score (seul son *signe* via `pnl_sign`). Le score gonflait jusqu'à ~34 alors
   que l'UI attend une échelle 0–1 (seuils verts `>0.4`, viabilité `-0.05`).
2. **Garde-fou d'application « 2 critères sur 3 »** (`_beats_baseline`,
   `auto_optimizer.py`) : un meilleur Win Rate **et** Sharpe suffisait à
   appliquer un PnL pourtant inférieur au baseline.

**Correctif.**
- Sharpe **normalisé** dans `[-1, 1]` (saturation à `|Sharpe| ≥ 10`) pour qu'il
  pèse comme les autres métriques ; ajout d'un terme de **montant du PnL**
  normalisé (poids 0.20) ; léger renfort du poids du nombre de trades
  (`0.08 → 0.10`). Le score retrouve l'échelle 0–1 attendue par l'UI.
- `_beats_baseline` rend l'**amélioration du PnL OOS obligatoire** (plus jamais
  outvotée par WR/Sharpe), en plus d'au moins un gain de qualité (WR ou Sharpe).

### 🛠️ Optimiseur : « non appliqué = non utilisé » + `params:` jamais écrasé

**Problème.** Un résultat d'optimisation **non appliqué** (refusé par le
garde-fou en auto-apply, ou simplement en attente du bouton « Appliquer »)
était quand même persisté dans `optimizer_results[tf]` via
`save_optimizer_results`. Or `resolve_strategy_params` donne **précédence** à
`optimizer_results` sur `params:`, et `load_config` recharge ce store : le
paramétrage « refusé » devenait donc **immédiatement actif** pour le
backtest/comparatif/live (et pouvait même activer une stratégie en live). C'est
pourquoi un comparatif après optimisation reproduisait les chiffres « Après
optimisation » alors que « rien n'était appliqué ». De plus, le panneau
« Avant optimisation » était calculé sans timeframe → sans l'overlay
`optimizer_results`, donc à partir du seul bloc `params:`, ce qui ne reflétait
pas le paramétrage réellement actif.

**Correctif.**
- **Non appliqué = non utilisé** : les chemins non appliqués tracent désormais
  le résultat dans le changelog (audit) via la nouvelle fonction
  `record_optimizer_audit`, **sans** écrire dans `optimizer_results`. Le
  paramétrage en place reste actif tant qu'il n'est pas explicitement appliqué.
- **`params:` jamais écrasé** : `apply_best_params` écrit désormais
  **uniquement** dans `optimizer_results[tf]` (le store actif, par précédence) et
  laisse intact le bloc `params:` = configuration par défaut réglée à la main.
  Un timeframe est requis (sans lui, aucun emplacement à activer).
- **Baseline réaligné** : `_run_baseline` reçoit le timeframe et applique
  l'overlay `optimizer_results` → le panneau « Avant optimisation » reflète le
  paramétrage réellement actif (comme le live et le comparatif).

### 🐛 Optimiseur : Alpha OOS manquant dans le panneau « Après optimisation »

Le chemin d'évaluation **non parallèle** (`Optimizer._eval`) n'incluait pas
`oos_alpha` dans son dict de résultat, contrairement au worker parallèle. En
mode `n_jobs=1`, `best_oos_alpha` revenait donc `None` et l'UI masquait la ligne
Alpha. Ajout de `oos_alpha` à `_eval` pour aligner les deux chemins.

## [12.7.0] - 2026-06-06

### ✨ Indicateurs du catalogue V4 ajoutés à `indicators.py` + runner d'optimisation

**Indicateurs repris du catalogue de features V4 (~462 colonnes).** Quatre
primitives génériques, réutilisables et jusque-là absentes de
`app/core/indicators.py`, y sont ajoutées (avec tests) :
- `roc(close, n)` — Rate of Change en % (momentum fondamental, étonnamment absent) ;
- `green_ratio(df, n)` — proportion de bougies haussières sur `n` (breadth locale) ;
- `rsi_divergence(df, period, lookback)` — divergence RSI/prix signée {−1, 0, +1}
  (fusion des features `bull_div`/`bear_div`) ;
- `trend_duration(df, n, adx_threshold)` — barres consécutives en tendance forte
  (persistance de régime).

Les autres features V4 utiles étaient déjà couvertes : `precompute_df` expose en
O(1) RSI/ATR/ADX/±DI/MACD/SMA/EMA, les ratios de volatilité normalisés 100b
(`_pre_atr_pct_r`…), `_pre_range_pos20`, `_pre_rsi_vel6`, structure de bougie, etc.
— c'est cette base que consomment les jumeaux `_no_ml`.

**Runner d'optimisation en ligne de commande — `optimize_runner.py`.** Lance
l'optimisation des stratégies **une à une** sans passer par l'interface web
(même moteur `AutoOptimizer` : baseline → recherche → sauvegarde
`strategies/<nom>.yaml`, ré-entraînement/persistance du modèle pour les ML) :
- **séquentiel** (un seul job à la fois) via `AutoOptimizer.optimize_sequential` ;
- **anti-veille** multi-plateforme (macOS `caffeinate` / Windows
  `SetThreadExecutionState` / Linux `systemd-inhibit`), best-effort ;
- **thread-safe** : verrou fichier exclusif (une seule instance) en plus des
  verrous internes de l'optimiseur (YAML, registre de jobs) ;
- **tâche de fond discrète** : priorité processus abaissée (`nice`/IDLE) et threads
  de calcul bornés (`--jobs`, défaut 1 ; env `OMP/MKL/…_NUM_THREADS` plafonnés).

Exemples : `python optimize_runner.py --no-ml-only --apply`,
`python optimize_runner.py --strategies opus_omnibus_v11_no_ml --tfs 1h --trials 30`.

### 🔧 Fichiers

| Fichier | Changement |
|---------|------------|
| `app/core/indicators.py` | + `roc`, `green_ratio`, `rsi_divergence`, `trend_duration` |
| `app/engine/auto_optimizer.py` | + `AutoOptimizer.optimize_sequential` (exécution une-à-une) |
| `optimize_runner.py` | Script CLI : optimisation séquentielle, anti-veille, verrou, priorité basse (nouveau) |
| `tests/test_indicators.py` | + tests des 4 nouveaux indicateurs |

---

## [12.6.0] - 2026-06-06

### ✨ Jumeaux « sans ML » des stratégies Opus Omnibus + seuil dynamique

Les stratégies ML (`opus_omnibus_v8/v10/v11/v11_followsetup`, `ml_dynamic_threshold`)
sont coûteuses à entraîner et à maintenir (modèles LightGBM/sklearn, pkl,
ré-entraînement périodique). Cette version ajoute pour chacune un **équivalent
purement à base d'indicateurs**, suffixé `_no_ml`, qui réplique le routing de la
stratégie d'origine et ne remplace **que** les deux sorties ML.

**Chaque jumeau est autonome.** Aucun module proxy partagé, aucun import croisé
entre stratégies : tout le routing (régime, setups, sélection, sorties
anticipées) est embarqué dans le fichier, et tous les indicateurs proviennent de
`app/core/indicators.py`.

**Performance.** Les proxys lisent les indicateurs en **O(1)** depuis les
colonnes `_pre_*` déjà calculées par `precompute_df` (appliqué une fois par le
backtest et le live ; repli idempotent sinon). Aucun DataFrame de features lourd
(~462 colonnes) n'est reconstruit par bougie → backtest ~0.5 s (contre plusieurs
dizaines de secondes auparavant), coût live négligeable.

**Proxys déterministes (inline dans chaque fichier) :**
- `p_up` (direction) — sigmoïde d'une moyenne pondérée de signaux directionnels :
  DI_diff, RSI, MACD/ATR, ROC, distance SMA50, vélocité RSI, position dans la
  range 20, direction du corps de bougie.
- `p_event` (amplitude) — sigmoïde recentrée de signaux d'amplitude déjà
  normalisés par leur moyenne 100 barres (TF-indépendants) : ATR%, range,
  écart-type des log-returns, ratio de volume, ADX, corps absolu.
- Coefficients (`p_up_gain`, `p_event_gain`, `p_event_center`) paramétrables/optimisables.

**Variantes ajoutées :** `opus_omnibus_v8_no_ml`, `opus_omnibus_v10_no_ml`,
`opus_omnibus_v11_no_ml` (régime enrichi DI/pente conservé),
`opus_omnibus_v11_followsetup_no_ml` (sortie sur flip de setup conservée),
`ml_dynamic_threshold_no_ml` (filtre ADX + seuils proba + porte de volatilité
reproduisant l'esprit « seuil dynamique »).

### 🔧 Fichiers

| Fichier | Changement |
|---------|------------|
| `app/strategies/opus_omnibus_v8_no_ml.py` | Jumeau autonome sans ML de V8 (nouveau) |
| `app/strategies/opus_omnibus_v10_no_ml.py` | Jumeau autonome sans ML de V10 (nouveau) |
| `app/strategies/opus_omnibus_v11_no_ml.py` | Jumeau autonome sans ML de V11 (nouveau) |
| `app/strategies/opus_omnibus_v11_followsetup_no_ml.py` | Jumeau autonome sans ML de V11-FollowSetup (nouveau) |
| `app/strategies/ml_dynamic_threshold_no_ml.py` | Jumeau autonome sans ML du seuil dynamique (nouveau) |
| `strategies/*_no_ml.yaml` | Configs (params + coefficients de proxy) des 5 jumeaux |

---

## [12.5.0] - 2026-06-03

### ✨ Dérivés « au fil de l'eau » + stratégie `funding_flow` (100 % dérivés)

Suite de l'intégration dérivés (V12.4) : accumulation automatique dans la boucle
live + stratégie directionnelle théorique exploitant funding/OI/LSR/taker.

**Accumulation au fil de l'eau (comme l'OHLCV) :**
- `DerivativesStore.refresh()` — fetch incrémental throttlé, merge dans
  `data/derivatives/*.parquet` (même logique que CandleStore pour l'OHLCV).
- Branché dans `OHLCVCache.get()` derrière le flag `derivatives.enabled`
  (opt-in, **comportement inchangé si désactivé**) : à chaque nouvelle bougie,
  accumulation + injection des colonnes `funding_z`/`oi_change_pct`/`lsr_z`/
  `taker_z` dans le df de scoring. **Gracieux** : réseau KO → df OHLCV inchangé.
- `research/accumulate_derivatives.py` — accumulation hors-bot (cron/backfill).
- Config : section `derivatives` (`enabled`, `period`, `refresh_interval`, `z_window`).

**Stratégie `funding_flow` (rule-based, théorique) :** fade des extrêmes de
positionnement — pression de foule = somme pondérée `funding_z`/`lsr_z`/`taker_z`
(contrarian), conviction renforcée par l'OI, garde-fou tendance. Pression positive
extrême (foule longue) → SHORT ; négative → LONG. Sans dérivés → abstention.
⚠️ Théorique (historique gratuit OI/LSR/taker ≈ 30 j) : à calibrer/valider en live.

### 🔧 Fichiers

| Fichier | Changement |
|---------|------------|
| `app/core/derivatives.py` | + `refresh()` throttlé (accumulation au fil de l'eau) |
| `app/live/ohlcv_cache.py` | + enrichissement dérivés dans `get()` (opt-in, gracieux) |
| `app/core/config.py`, `config.yaml` | + section `derivatives` |
| `app/strategies/funding_flow.py` | Stratégie directionnelle 100 % dérivés |
| `strategies/funding_flow.yaml` | Paramètres |
| `research/accumulate_derivatives.py` | Script d'accumulation/backfill autonome |
| `tests/test_funding_flow.py` | Tests stratégie + hook OHLCVCache (réseau mocké) |

---

## [12.4.0] - 2026-06-03

### ✨ Intégration de données de dérivés (gratuites) + edge directionnel

Réponse à la question « peut-on prédire la direction ? ». Démarche en deux temps.

**1. Chasse à l'edge directionnel** (`research/directional_hunt.py`) — mesure
honnête sur OHLCV (P(up|condition), z-scores binomiaux, AUC logistique OOS) :
- La direction non-conditionnelle est ~martingale (AUC OOS combiné ≈ **0.52**).
- **Seul edge robuste : la mean-reversion sur la position-dans-le-range** —
  bas de range → biais UP (P(up)≈**57 %**, **z=7.6**), haut → DOWN (z=-7.3),
  cohérent 1h/4h/1d. Renforcé par : reversal de streaks, fade d'euphorie, rebond
  de capitulation (volume), pullback en tendance.
- Saisonnalité (heures funding, jour de semaine) : **démentie** (z≈0).

**2. Pourquoi il faut les dérivés** — le cœur OHLCV mean-reversion est ≈ breakeven
(`derivatives_reversion` backtest 4h ≈ -1.8 %) : l'edge directionnel est réel
(win 59 %) mais le payoff est asymétrique (cassures de range → besoin de 71 % de
win). **Filtrer les fausses reversions exige funding/OI/sentiment** — là vit
l'alpha directionnel crypto.

**Module `app/core/derivatives.py` — `DerivativesStore` (gratuit, sans clé API) :**
- funding_rate (ccxt, historique long), open_interest (ccxt), long_short_ratio &
  taker_buy_sell_ratio (Binance futures-data REST). Cache Parquet, thread-safe,
  **dégradation gracieuse** (aucune exception si réseau KO).
- `align_to_ohlcv()` : enrichit l'OHLCV (join_asof causal) avec funding_z, oi_change,
  lsr_z, taker_z. Câblage live en 1 ligne (cf. research/DERIVATIVES_integration.md).

**Stratégie `derivatives_reversion` (rule-based, zéro ML) :** fade des extrêmes de
range, **veto/boost par funding & sentiment** quand les colonnes sont présentes ;
fallback OHLCV pur sinon.

### 🔧 Fichiers ajoutés

| Fichier | Rôle |
|---------|------|
| `app/core/derivatives.py` | DerivativesStore (funding/OI/LS/taker, cache, alignement) |
| `app/strategies/derivatives_reversion.py` | Stratégie directionnelle mean-reversion + dérivés |
| `strategies/derivatives_reversion.yaml` | Paramètres |
| `tests/test_derivatives.py` | Tests (réseau mocké) du store + de la stratégie |
| `research/directional_hunt.py` | Chasse à l'edge directionnel (P(up), AUC OOS) |
| `research/DERIVATIVES_integration.md` | Doc : sources gratuites, limites, câblage |
| `research/backtest_reversion.py` | Harnais backtest du cœur OHLCV |

---

## [12.3.0] - 2026-06-03

### ✨ Nouvelle stratégie — `volatility_squeeze` (RULE-BASED, antithèse de l'Omnibus)

Issue d'une **remise en question des hypothèses** de la lignée `opus_omnibus`
V7/V8/V10/V11 (cf. `research/CRITIQUE_omnibus_v7-v11.md`).

**Critique de l'Omnibus :**
- Hypothèse fondatrice fausse — la lignée prédit la DIRECTION par ML alors qu'elle
  **admet elle-même un AUC_dir ≈ 0.53** (quasi-aléatoire, docstring V10). Toute la
  machinerie de routing `p_up` filtre donc du bruit.
- Sous-exploite le seul edge réel — l'AMPLITUDE/volatilité (AUC ≈ 0.7, clustering
  ACF|r| 0.15-0.28).
- Sur-apprentissage — 17 à 23 paramètres + seuils par setup, **tunés sur des
  backtests in-sample de 12-122 trades**, `oos_score: null` (aucune validation OOS).
- Mauvais timeframes — 15m/30m/1h, **sous le mur des frais** (mesuré).
- Complexité fragile — LightGBM inline, path-dépendant, non déterministe.

**Réponse — `volatility_squeeze` :** trader la VOLATILITÉ (prévisible), pas la
direction (aléatoire). On attend une **compression** (squeeze = largeur Bollinger
dans son percentile bas) puis sa **détente alignée sur la tendance établie**
(jamais prédite) ; abstention en chop. Règle pure : **~8 paramètres, déterministe,
ZÉRO ML, zéro réentraînement**.

**Backtest 4h, 7.5 ans (frais/spread/borrow réalistes) :**
- long-only strict : **+68.7 %** · Sharpe 13.7 · maxDD **-5.8 %** · **PF 2.49** · win 51 %.
- Walk-forward OOS : **consistance 80 %** (meilleure des stratégies du repo).
- BEAR 2022 **-1.2 % vs B&H -53 %** ; BULL 2023-24 +12.0 % (PF 3.9) ; CHOP -1.2 %.
- ⚠️ 1h backtesté **-41.6 %** → confirme que l'orientation bas-TF de l'Omnibus est
  sous le mur des frais.

> 8 paramètres déterministes battent 23 paramètres + ML inline non-validé OOS :
> la discipline (ne trader que l'edge réel) bat la complexité.

### 🔧 Fichiers ajoutés

| Fichier | Rôle |
|---------|------|
| `app/strategies/volatility_squeeze.py` | Stratégie rule-based (`BaseStrategy`, zéro ML) |
| `strategies/volatility_squeeze.yaml` | Params + `optimizer_results` (4h, 1d) |
| `tests/test_volatility_squeeze.py` | Tests unitaires + intégration |
| `research/CRITIQUE_omnibus_v7-v11.md` | Critique structurée de la lignée Omnibus |
| `research/backtest_squeeze.py` | Harnais backtest/walk-forward/split |

---

## [12.2.0] - 2026-06-03

### ✨ Nouvelle stratégie — `momentum_blitz` (AGRESSIVE, plein capital)

Pendant **agressif** de `harmonic_regime` : vise le rendement absolu maximal en
assumant un drawdown élevé. Issue de `research/analysis_aggressive.py` +
`research/STRATEGIE_momentum_blitz.md` (nouveaux TF 15m/30m analysés).

**Edges (mesurés, nets de frais) :** ignition = breakout Donchian + surge de
volume + expansion d'ATR + alignement HTF (net-positif seulement ≥ 4h ;
15m/30m/1h perdent : frais > edge). Asymétrie MFE/MAE≈1.24, queue droite +6 %.

**Mécanique d'agression :** déploiement **plein capital** (`size_factor` 1.0→2.0
selon conviction), exits **asymétriques** (stop serré 1.3×ATR + trailing LARGE
3×ATR → laisse courir), seuil de qualité bas mais gate ignition. Long-biais
(shorts net-négatifs désactivés).

**Backtest 4h, 7.5 ans (frais/spread/borrow réalistes) :**
- full1x (réaliste) : **+58.2 %** · Sharpe **5.73** · maxDD **-11.6 %** · PF 1.55.
- lev2x (agressif) : **+113.7 %** (×2.14) · Sharpe **6.95** · maxDD -12.3 % · PF 1.74.
- Positif dans tous les régimes : BEAR 2022 flat (vs B&H -53 %), BULL +31.6 %,
  CHOP +6.4 %. Walk-forward OOS : PnL moyen +87, consistance 60 %.
- ⚠️ TF = 4h uniquement (1h/30m/15m backtestés négatifs).

> Leçon : *agressif ≠ plus de trades* (plus de frais, edge dilué). La
> sélectivité (ignition-only) maximise l'edge par trade, que le plein capital amplifie.

### 🔧 Fichiers ajoutés

| Fichier | Rôle |
|---------|------|
| `app/strategies/momentum_blitz.py` | Stratégie agressive (`BaseStrategy`) |
| `strategies/momentum_blitz.yaml` | Params + `optimizer_results` (4h) |
| `tests/test_momentum_blitz.py` | Tests unitaires + intégration |
| `research/analysis_aggressive.py` | Analyse edges de gros mouvement nets de frais |
| `research/backtest_blitz.py` | Harnais backtest (déploiement/levier, Monte-Carlo) |
| `research/STRATEGIE_momentum_blitz.md` | Rapport analyse → conception → validation |

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