# Plan Directeur — État des lieux & Feuille de route

> **Document de référence unique** du bot (améliorations + généralisation
> multi-actifs). Il remplace et fusionne les plans historiques dispersés
> (`AUDIT.md`, `docs/audit/00-INDEX.md` + 7 fichiers domaine, les audits
> externes successifs). Ces fichiers restent consultables comme **archive des
> directives détaillées**, mais **c'est ici la source de vérité** sur ce qui est
> fait et ce qui reste à faire.
>
> **Dernière mise à jour : 2026-07-26** — branche
> `claude/plan-directeur-sprint-g2-n0ima2`, **sprint G2 : actions SBF 120 en
> paper**. Changements de statut apportés par cette passe :
>
> * **G2 ✅ FAIT** (§4.5bis) — les **3 points de couplage** de §4.2 sont levés :
>   calendrier de marché générique (`market_calendar.py` + `MarketHoursMixin`),
>   sizing entier et coûts par venue (`execution.py`, partagés backtest ↔ live),
>   provider actions data-only (`yfinance_provider.py`) routé par venue
>   (`provider_router.py`), univers statiques (`universe.py` +
>   `data/universe/sbf120.yaml`).
> * **Notification de trade** (demande explicite du sprint) — une venue
>   `can_execute: false` ne transmet **aucun** ordre et émet à la place un
>   ticket portant symbole / direction / ouverture / SL / TP. G3 se réduit
>   désormais à brancher un `ExecutionProvider` et basculer un booléen.
> * **Deux hypothèses crypto retirées du `CandleStore`** (plancher `since` à
>   2017, rejet des barres à volume nul) — elles auraient troué l'historique
>   actions en silence.
> * **Actions requises avant d'activer G2** : vérifier la composition de
>   `data/universe/sbf120.yaml` (`verified: false`, `scripts/check_universe.py`)
>   et le taux de TTF retenu — cf. §4.5bis et §6.
>
> **Mise à jour 2026-07-26 (matin)** — branche
> `claude/ml-training-optimization-ofqd0t`, chantier **architecture ML
> unifiée** (`docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md`, étapes A→G,
> décisions 1-13). Changements de statut apportés par cette passe :
>
> * **DEAD-01 ✅ RÉSOLU** (§3.1) — mais par **factorisation**, pas par
>   suppression : 5 fichiers supprimés, 6 devenus des presets de 84 à 129
>   lignes, aucun setup perdu (ceux de V9 sont portés dans V11, désactivés).
>   Le pack V4 figé a été retiré après mesure, ce qui a levé le « ne pas
>   toucher » qui pesait sur `v8`/`v10`/`opus_stat_pretrained_v4`.
> * **ARCH-01 ✅ largement absorbé** — `MLBackendMixin` est l'« OpusBase »
>   que cet item réclamait ; le routing V10 partagé ne vit plus qu'à un seul
>   endroit.
> * **Registre ML : la dimension symbole a été RETIRÉE de la clé** (décision
>   11, mesurée) — impact direct sur ce plan, cf. §4.2bis.
> * **Deux ADX incompatibles, corrigés** (décision 13) — `_pre_adx14` lissait
>   en `span=14` là où Wilder veut α = 1/14. **Action restante pour
>   l'utilisateur : réoptimiser les seuils ADX**, désaccordés par la
>   correction (§3.7).
> * **Trois bugs silencieux préexistants** trouvés et corrigés en chemin
>   (snapshot vide du cache d'entraînement, `defaults` ignoré par
>   `MLBackend.fit`, sorties anticipées mortes sur les variantes sans ML).
>
> **Mise à jour 2026-07-23** — branche `claude/apply-patch-sn96hj`
> (PR #155), après le **refactoring structurel Phases 1-6** (MLBackend, suppression
> de 5 façades, éclatement des fichiers-dieux, format ML natif RCE-safe,
> suppression de pandas/pyarrow/scikit-learn) et la **remise au vert de la CI**.
>
> **Mise à jour 2026-07-20** (branche `claude/sprint-7-8-planning-xb12m0`,
> commits `5022b69`…`3bbcd55`) : voir `CHANGELOG.md` § « Post-Sprint 8 » pour
> le détail technique. Résumé des changements de statut apportés par cette
> passe : **PERF-02** (Plan C, §4.5) marqué **FAIT** (parallélisme réel de
> l'optimiseur + refonte `param_search_optim`, détail §4.1bis) ; **TEST-01**
> nuancé — le job CI `lint` existait depuis Sprint 7 mais n'était **jamais
> passé** (773 erreurs pré-existantes, aucune liée à un sprint de ce
> document) jusqu'à cette passe, désormais vert ; **DEAD-01** toujours
> ouvert mais son **analyse est maintenant livrée et re-confirmée** (deux
> passes de comparatif fonctionnel + empirique des 15 variantes opus_omnibus/
> opus_stat sur 5 timeframes — la seconde avec un dimensionnement de fenêtre
> calqué sur la production et sans aucune stratégie sautée, rapports HTML
> remis à l'utilisateur) — la décision de suppression elle-même reste en
> attente, aucun fichier supprimé. Découverte annexe hors périmètre DEAD-01 :
> `v11`/`v12` (actives) sous-performent sur leur TF de production (1h) dans
> ce test, signalé pour examen séparé (§4.1). Garde-fou ajouté : `/api/
> backtest` et `/api/optimize/start` se refusent désormais mutuellement
> pendant que l'autre tourne (contention CPU/mémoire constatée en pratique).
> **Verdict DEAD-01 révisé après discussion** : `opus_omnibus_v9` retiré de
> la liste (classé à tort sur un motif de versioning — cf. §4.1) ; le pkl
> figé V4 (dépendance de `v7_pretrained`/`v8`/`v9`/`v10`) a été soumis à un
> test de fuite (aucune fuite trouvée) et à une expérience de gel de la
> recette V11 sur 40 000 barres (hypothèse réfutée — le V4 figé reste
> supérieur à volume de trades égal) ; nouveau backlog item **ML-01** (§4.5)
> sur le gating de promotion `manual_active`.
> Convention de statut : **✅ FAIT** (preuve : commit + vérif code/tests),
> **🟡 PARTIEL**, **❌ RESTE À FAIRE**, **⛔ DÉCLINÉ** (décision utilisateur ou
> prémisse obsolète).

---

## Table des matières

1. [État de santé actuel](#1-état-de-santé-actuel)
2. [Ce qui a été fait](#2-ce-qui-a-été-fait)
3. [Ce qui reste à faire — backlog priorisé](#3-ce-qui-reste-à-faire--backlog-priorisé)
4. [Généralisation multi-actifs (crypto → actions SBF120)](#4-généralisation-multi-actifs-crypto--actions-sbf120)
5. [Roadmap par sprints](#5-roadmap-par-sprints)
6. [Décisions produit en attente](#6-décisions-produit-en-attente)

---

## 1. État de santé actuel

| Indicateur | État | Détail |
|---|---|---|
| **CI** (`.github/workflows/ci.yml`) | ✅ **Verte** | Jobs `lint` (ruff) + `test` (pytest `-m "not slow"`). Run #19 = success sur `a601400`. |
| **Tests** | ✅ 746 verts, 2 skipped | Vérifié hors `sklearn`/`pandas`/`joblib` (conditions CI réelles). |
| **Lint** | ✅ `ruff check .` = 0 erreur | 25 erreurs introduites par le refactoring ont été résorbées. |
| **Sécurité désérialisation (RCE)** | ✅ **Clos** | Plus **aucun** `pickle`/`joblib` dans le code (actif comme mort) : format 100 % LightGBM natif (`.lgb` + `.meta.json`) partout. Le `RestrictedUnpickler` de compat legacy a été supprimé (plus de `.pkl` à lire). |
| **Dépendances** | ✅ Allégé | `pandas`, `pyarrow`, `scikit-learn` supprimés (~23 Mo + scipy/joblib transitifs). Polars + LightGBM natif partout. |
| **Couches** | ✅ Strictes | `core → engine → live → api → strategies`, 0 import circulaire (vérifié par grep). |
| **Stratégies actives** | 15 (`manual_active`) | Sur **42** fichiers `app/strategies/` (45 → 42 : 5 supprimés, 2 ajoutés). DEAD-01 clos par factorisation, plus de code mort Opus/stat. |
| **Tests (2026-07-26, après G2)** | ✅ 1 172 verts, 3 skipped | `pytest -m "not slow"` + `ruff check .` propres. 1 051 → 1 172 : +121 tests G2, **zéro régression**. |

### Note d'exécution — flakiness environnementale (rappel)

La suite complète en un seul process peut se bloquer de façon non-déterministe
en environnement sandboxé chargé (throttling CPU cgroup) — reproductible sur du
code antérieur, donc **non lié** aux évolutions récentes. La CI GitHub n'est pas
affectée. Mitigation recommandée si cela réapparaît : `pytest-timeout` pour
convertir un blocage en échec explicite.

---

## 2. Ce qui a été fait

### 2.1 Refactoring structurel — Phases 1-6 (2026-07-23, PR #155)

Objectif : réduire la dette (fichiers-dieux, duplication ML, façades),
supprimer le risque RCE des `.pkl`, et alléger les dépendances. **Tout vérifié
contre le code de la branche.**

| Thème | Livré | Preuve |
|---|---|---|
| **MLBackend générique** | `app/ml/backend/` : `features.py` (features V4 Polars ~462 col.), `trainer.py`, `predictor.py`, `persistence.py`, `isotonic.py` (IsotonicRegression native PAV), `__init__.py` (façade thread-safe) | 6 modules, V11 migrée |
| **Format ML natif RCE-safe** | `v4_models.pkl` (8,8 Mo) → 8 × `.lgb` + 8 × `.json` ; `save_lgb_with_scaler`/`load_model` natifs ; `RestrictedUnpickler` en défense-profondeur pour le legacy | 8 `.lgb` présents |
| **Suppression pandas/pyarrow/sklearn** | `ml_dynamic_threshold` refondu (LightGBM natif, plus de `StandardScaler`/`Pipeline`), `_FeatureBuilder` pandas supprimé, 5 stratégies Opus passées à Polars | `requirements.txt`, 0 import top-level résiduel |
| **Éclatement des god-classes** | `position_mixin.py` (1399 L) → 4 mixins (open/manage/close/restore) ; `risk.py` (667 L) → `risk_gate`+`risk_sizer`+`risk_notifier`+`risk_state` ; `optimizer.py` → `optimizer_search`+`optimizer_applier` ; `backtest.py` → +`walk_forward`+`monte_carlo` ; `_signal_at` SMC 542 → 24 L (dispatcher) | 4 façades supprimées |
| **Éclatement API** | `routes/config.py` (684 L) → 4 routers + `_config_helpers` ; middlewares FastAPI → `middleware.py` ; `main.py` 346 → 279 L | façade `config.py` supprimée |
| **Artefact mort** | `logs/ml_strategy.pkl` (549 Ko, dernier `.pkl` RCE committé) désindexé de git + `.gitignore` | supprimé |

**5 façades supprimées** : `risk.py`, `optimizer.py`, `position_mixin.py`,
`routes/config.py`, `_FeatureBuilder` pandas.

### 2.2 Remise au vert de la CI (2026-07-23)

Le refactoring avait rendu la CI rouge sur ses deux jobs. Corrigé :

- **Lint** : 25 erreurs ruff (imports non triés `I001`, imports inutilisés
  `F401`, `E701` multi-instructions, `F841` variable inutilisée). Les
  ré-exports de compat de `backtest.py` (`WalkForwardAnalyzer`, `MonteCarlo`)
  reçoivent un `# noqa: F401` explicite.
- **Test `test_freezes_via_partial_fixed_sampler`** : la suppression de
  scikit-learn a cassé le gel de paramètres du chemin **Optuna**, qui reposait
  sur l'estimateur d'importance **fANOVA** (RandomForestRegressor sklearn en
  interne). Correctif : ajout de **PedANOVA** (évaluateur d'importance Optuna
  *sklearn-free*) comme repli entre fANOVA et l'estimateur marginal
  (`_optuna_param_importances`). fANOVA reste prioritaire si sklearn est présent
  (dev). *Découvert par reproduction fidèle des conditions CI, hors sklearn.*

### 2.3 Persistance des stratégies « retrained » : joblib → natif (2026-07-23)

Les 4 stratégies retrained encore inactives (`opus_omnibus_v7`,
`_v10_retrained`, `_v11_followsetup`, `opus_stat_retrained_v4`) persistaient
leurs modèles via `joblib.dump`/`joblib.load` — dernier code dépendant de
`joblib` (supprimé avec sklearn) et de pickle (RCE). Migré vers le format natif
LGB+JSON via deux helpers factorisés `save_amp_dir_bundle`/`load_amp_dir_bundle`
(`app/ml/backend/persistence.py`, calibrators isotoniques sérialisés en JSON).
Dans la foulée, **toute la machinerie pickle a été supprimée** du backend
(`RestrictedUnpickler`, `restricted_pickle_load`, `import pickle`) et le suffixe
`.pkl` des chemins de modèle est remplacé par un préfixe natif — il n'existe
plus aucun `.pkl` ni pickle dans le code. Ces stratégies restent candidates à
DEAD-01, mais ne dépendent plus de joblib. **SEC-020 est désormais clos.**

### 2.4 Historique antérieur (résumé)

Travaux consolidés et vérifiés lors des passes précédentes (détail dans
`CHANGELOG.md` et les fichiers `docs/audit/*` d'archive) :

| Lot | Contenu | Statut |
|---|---|---|
| Audit Vagues 0-2 | Régressions per-symbole, sécurité/intégrité (auth, watchdog, notifs, parquet atomique), intégrité de la mesure (Monte-Carlo, IS/OOS, WF gate, garde-fous d'apply) | ✅ |
| Audit Vague 4 | Architecture : couches strictes, `param_resolution`, `timeframes`, `execution.py` (parité BT/live), découpage `live_trader`/scanner/smc | ✅ |
| Audit Vague 5 | Recherche d'edge SMC/ICT (inducement BTC 4h activé ; le reste `off`, sans preuve OOS) | ✅ |
| Audit Vague 6 | UX/a11y/docs, tests LiveTrader/API/lifecycle, découpe CHANGELOG | ✅ (TEST-11 débloqué depuis : DEAD-01 clos) |
| Sprint 7 | Nettoyage code mort partiel (`scoring_v3`, pyflakes, imports morts), **CI + ruff.toml + mypy.ini + pytest.ini** | ✅ (hors DEAD-01) |
| Sprint 8 | Quick wins financiers/sécu : benchmark B&H, compteur de frais, slippage paper, rate-limit par endpoint, backup auto, `audit_param_space.py` | ✅ (hors FIN-01) |
| Post-8 | Parallélisme réel de l'optimiseur (`n_jobs` câblé, refonte `param_search_optim`), résorption dette lint pré-existante (773 → 0) | ✅ |

---

## 3. Ce qui reste à faire — backlog priorisé

Grille : 🟢 quick win · 🔵 bet structurant · 🟡 itératif.

### 3.1 ✅ DEAD-01 — RÉSOLU, mais autrement que prévu (2026-07-26)

**Le plan prévoyait de supprimer 8 fichiers. Ce qui a été fait : en supprimer
5 et transformer les autres en presets** — la comparaison ayant montré que
leurs routings n'étaient pas 8 stratégies différentes, mais **une seule
déclinée en valeurs**. Supprimer aurait perdu des setups ; factoriser les
préserve et les rend de nouveau optimisables.

Détail dans `docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md` (étapes A à G,
décisions 1-13). Résultat fichier par fichier :

| Fichier | plan (L) | aujourd'hui | ce qui a été fait |
|---|---:|---|---|
| `opus_omnibus_v7.py` | 1266 | **129 L** | preset de V11 — équivalence de sélection prouvée sur 7 744 combinaisons |
| `opus_omnibus_v7_pretrained.py` | 640 | supprimé | pack V4 figé retiré après mesure (§1.5) |
| `opus_omnibus_v9.py` | 738 | supprimé | ses 2 setups exclusifs (`SHORT_TD`, `LONG_PULLBACK_TU`) **portés dans V11**, désactivés |
| `opus_omnibus_v10_retrained.py` | 1323 | **101 L** | preset de V11 — équivalence prouvée sur 3 plans |
| `opus_omnibus_v11_no_ml.py` | 545 | **90 L** | preset + `ProxyPredictor` |
| `opus_omnibus_v11_followsetup.py` | 1444 | 794 L | **conservé** — sa mécanique de sortie est du code, pas des valeurs (§7.3bis) |
| `opus_omnibus_v11_followsetup_no_ml.py` | 492 | **91 L** | preset |
| `opus_stat_retrained_v4.py` | 1121 | 445 L | plomberie ML extraite dans `MLBackendMixin` |

Les trois « ne pas toucher » (`opus_omnibus_v8`, `v10`,
`opus_stat_pretrained_v4`) **ont finalement été supprimés** : leur seule
dépendance réelle était le pack V4 figé, dont la mesure a montré qu'il ne bat
plus un ré-entraînement (3/3 TF, robuste sur 5 fenêtres, aucun régime sauvé).
Le scanner en a été débranché avant suppression.

Deux variantes ont rejoint la famille depuis, également en presets :
`opus_omnibus_v8_no_ml` (84 L) et `opus_omnibus_v10_no_ml` (87 L).
`dynamic_threshold_no_ml` (218 L) reste un fork **motivé** : il applique la
porte de volatilité en inférence là où son jumeau ML l'utilise pour
labelliser — en faire un preset ajouterait au parent une branche qu'il
n'emprunte jamais.

> **Débloque, comme prévu** : TEST-11 (smoke tests) et ARCH-01 — ce dernier est
> largement absorbé, `MLBackendMixin` étant l'« OpusBase » qu'il appelait.

### 3.2 🟢 Durcissement sécurité (audit externe SEC)

Non traités par le refactoring (hors périmètre). Effort faible, impact réel :

| ID | Item | Fichier |
|---|---|---|
| **SEC-002** | Path traversal via `tf` non validé → whitelist de timeframes dans `CandleStore._path` (+ `resolve().is_relative_to`) | `app/core/candle_store.py` |
| **SEC-003** | Retirer `web.allow_insecure: true` committé par défaut → opt-in par flag CLI/env, jamais via YAML | `config.yaml` |
| **SEC-004** | Retirer `"testclient"` de la whitelist localhost WebSocket | `app/api/routes/ws.py` |
| **SEC-005** | Retirer `auth_basic off;` sur `/api/optimize/stream` | `deploy/nginx.conf` |
| **SEC-006** | Docs OpenAPI (`/api/docs`, `/api/openapi.json`) non protégées → désactiver en prod (`ENV=prod`) | `app/api/main.py` |
| SEC-009/010 | Bumps CVE : `sqlalchemy>=2.0.32`, `jinja2>=3.1.5` | `requirements.txt` |
| SEC-007/008 | `notify-crash.py` (fuite de logs vers Telegram), `backup.sh` (chmod 600) | `deploy/` |

### 3.3 🔵 Observabilité (score le plus faible, ~4/10)

Aucun équivalent existant (fichiers/dépendances absents, vérifié) :

| ID | Item | Effort |
|---|---|---|
| OBS-01 | Métriques Prometheus (`prometheus-client`, `app/core/metrics.py`) | M |
| OBS-02 | Logs JSON structurés + correlation IDs (`contextvars`) — remplace les f-strings | M |
| OBS-06/07 | `/health` enrichi + alerting sur seuils critiques | M |
| OBS-03/04/05 | Grafana, AlertManager, tracing OpenTelemetry (dépendent d'OBS-01) | M/L |

### 3.4 🔵 Refactor stratégies avancé

| ID | Item | Dépend de |
|---|---|---|
| **ARCH-01** (OpusBase) | ✅ **absorbé (2026-07-26)** — le routing V10 partagé ne vit plus qu'à un endroit : `opus_omnibus_v11`. Les variantes sont des presets qui ne déclarent que des valeurs. `MLBackendMixin` joue le rôle d'« OpusBase » côté plomberie ML. **Reste** : `opus_omnibus_v11_followsetup` (794 L), dont la mécanique de sortie est du code et non des valeurs — cf. §3.7 | ✅ |
| **STRAT-01** | Champ `status: experimental\|validated\|production\|archived` dans chaque YAML (distinct du lifecycle runtime) | — |
| **STRAT-02** | ✅ **fait** — registre daté et versionné (`app/ml/model_registry.py`, clé `(TF, recette)`), provenance complète (hash de recette, commit git, dates, symbole d'entraînement). `models/index.json` n'a pas été créé : le système de fichiers EST l'index | ✅ |
| ARCH-05 | Réduire `smart_money.py` (838 L) et `smart_money_signals.py` (891 L) — dette de lisibilité, pas d'urgence | — |

### 3.5 🟡 Tests, docs, DX

| ID | Item |
|---|---|
| TEST-11 | Smoke tests paramétrés pour les stratégies survivantes — **débloqué** (DEAD-01 clos) |
| TEST-05 | Tests d'ordres live mockés complets (idempotence clientOrderId, partial fills, réconciliation frais, restauration crash) |
| TEST-04 | `pytest-cov` + seuil de couverture en CI ; ajouter mypy/security-scan à la CI |
| DOC-005/006 | 4 guides référencés au README mais inexistants ; 0 ADR |
| DOC-mAj | `ARCHITECTURE.md` : refléter `MLBackend` et la suppression des façades |
| DX-01 | `Dockerfile` + `docker-compose` |
| WKFLOW | pre-commit hooks, Dependabot/Renovate, release workflow |

### 3.6 🟡 Financier & recherche (itératif)

FIN-01 (frais VIP OKX dynamiques), FIN-02 (borrow dynamique), FIN-05
(Sharpe/Sortino/Calmar/VaR temps réel), FIN-08 (réconciliation PnL quotidienne),
RES-01 (regime detection HMM), RES-02 (backtest portefeuille multi-actifs). Au
fil de l'eau.

### 3.7 🔴 Suites du chantier ML unifié (2026-07-26)

Le chantier a livré A→G et tranché 13 décisions ; ce qu'il laisse ouvert, par
valeur décroissante. Détail et chiffres :
`docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md` §12.

| ID | Item | Effort | Pourquoi c'est là |
|---|---|---|---|
| **ML-10** 🔴 | **Réoptimiser les seuils ADX** des YAML (`adx_min`, `adx_threshold`, `needs_adx_above`, `adx_len`) | M | Suite obligée de la décision 13 : ces seuils ont été choisis face à un ADX qui valait 35 en moyenne, ils s'appliquent à un ADX qui vaut 28. Rien n'est cassé, rien n'est réglé. **Seul item qui bloque la confiance dans les stratégies actives.** Rien à purger : `optimizer_results` est vide |
| **ML-11** 🔴 | **Désactiver la calibration isotone en 1h** | S (débloqué) | Mesuré (ECE **+461 %** en 1h contre −47 %/−67 % en 15m/30m), conclusion écrite… et jamais appliquée : `omnibus_v4_multi.yaml` porte toujours `calibrate: true` sans dérogation par TF. **Écart entre ce que le doc affirme et ce que le code fait.** Sous-décision ouverte : une recette par TF, ou un bloc `hp` par TF. **Débloqué par ML-14** : tous les réglages LightGBM (`calibrate`, `max_bin`, `early_stopping_rounds`…) se déclarent maintenant dans le bloc `hp:` d'une recette, donc désactiver la calibration en 1h ne demande plus de toucher au code |
| **ML-12** 🟠 | Instruire les scores négatifs sur la fenêtre de validation | M | 3 des 4 cibles mesurées perdent sur le dernier tiers de l'historique, **sous les deux conventions d'ADX**. Protocole réduit (40 essais, une paire symbole/TF) donc pas un verdict — mais leurs bons scores viennent d'IS/OOS |
| **ML-13** 🔵 | Mode `follow_setup` dans V11 | M | Dernier morceau de la fusion omnibus. Sa machine anti-whipsaw (confirmation K bougies, cooldown, hystérésis) est du **code**, pas des valeurs : mérite son propre tour de mesure, d'où le report délibéré |
| ~~**ML-14**~~ ✅ | ~~Entraînement unifié des 3 dernières stratégies ML~~ | — | **FAIT** (2026-07-27) — voir §3.7bis. `app/ml/features_catalog.py` rend le catalogue déclaratif, `app/ml/labelling.py` le schéma de labels, `app/ml/recipe_trainer.py` entraîne depuis la seule recette. Les `_train` autonomes de `stat48_v4/v5` (257 lignes) sont supprimés, équivalence 0.000000 mesurée. Au passage : `stat48` a **56 colonnes, pas 48** |
| **ML-15** 🔵 | Rejouer la mesure de la dimension symbole | S | Automatique dès qu'un actif d'une autre classe (action, ETF) avec ≥ 8 000 barres entre dans le store — cf. §4.2bis |

### 3.7bis ✅ ML-14 livré — et trois défauts trouvés en le livrant (2026-07-27)

L'entraînement est **piloté par la recette** : `train(recipe, df, tf)` n'importe
aucune stratégie (verrouillé par un test qui fait échouer tout import de
`app.strategies.*` pendant l'entraînement). `features.catalog` était déclaré par
les recettes depuis l'étape B mais n'était **dispatché nulle part** — il n'entrait
que dans `Recipe.hash()`. C'était la moitié écriture de l'asymétrie de §2 :
lecture pilotée par la recette (`build_predictor`), écriture par la stratégie.

Équivalences mesurées avant toute bascule, conformément au protocole :

| Recette | Face à | Écart max | Corrélation |
|---|---|---:|---:|
| `omnibus_v4_multi` | `MLBackend` | **0.00000000** | 1.000000 |
| `stat48_v5` (`fit`) | `_train` autonome | **0.000000** | 1.0000 |
| `stat48_v5` (`score`) | idem, 10 fenêtres | **0 divergence de signal** | — |

**Trois défauts trouvés en cherchant l'origine d'une divergence**, aucun n'étant
la cause annoncée au premier diagnostic (`n_estimators` 300 vs 500 — l'early
stopping tranche bien avant, cet écart n'avait aucun effet) :

1. **`scoring_statistique_opus_v4/v5` s'entraînaient sur 250 barres.**
   `_get_or_build_features` retombe, hors backtest, sur les 250 dernières barres
   — bon pour `score()`, qui ne lit que la dernière ligne, mais `_train`
   l'empruntait aussi. Quelle que soit la fenêtre passée à `fit()`, le modèle
   n'apprenait que sur 200 lignes plus 50 de validation, alors que la recette
   annonce `min_bars: 2000`. Aucun message ne le signalait. **Corrigé — mais cela
   change le modèle produit en exploitation : réoptimiser avant de s'y fier,
   même logique que ML-10.**
2. **`stat48` produit 56 features, pas 48.** Le bloc BB (largeur + rang centile
   × 4 lags) a été ajouté sans que le décompte ni la docstring suivent. Le nom
   des recettes `stat48_*` est conservé : c'est une clé de registre.
3. **`Series.to_numpy()` sur une colonne `Datetime` segfaute en polars 1.0.0**
   (la version épinglée) — pas d'exception, le process meurt. Trouvé en écrivant
   le découpage temporel du pooling. Aucun autre site du dépôt n'utilise ce
   motif ; un test de garde le verrouille.

**Gain de côté :** `save_lgb_with_scaler` ne sérialisait ni features ni médianes,
donc le scorer générique rapportait `unsupported_format` et le gate concluait
« keep » quoi qu'il arrive — `stat48_v4/v5` n'étaient **jamais promouvables**.
Les artefacts produits par le chemin recette portent leurs noms de colonnes.

**Pooling multi-symboles** (`train_multi`) : une recette entraînée sur plusieurs
symboles mis en commun, features construites par symbole et découpage temporel
commun. Sur Euronext, `min_bars` + holdout demandent ~13,7 ans par titre en
journalier — le pooling est la condition d'existence du modèle actions, pas un
raffinement. Vérifié sur 8 titres inentraînables seuls.

Deux points attendent un arbitrage utilisateur, pas du travail :

* **Supertrend** — son ATR interne a été inclus dans la correction Wilder, ce
  qui dépasse la lettre de la demande (« ADX »). Même défaut, et `ta.supertrend`
  en Pine utilise `ta.atr` ; une ligne à annuler si non souhaité.
### 3.7ter 🟠 Ce que ML-14 laisse ouvert

| ID | Item | Effort | Pourquoi |
|---|---|---|---|
| **ML-16** 🟠 | Câbler `train_multi` à l'API et à l'UI | M | Le pooling est une brique **testée mais appelée par personne** : ni `/api/ml/train`, ni la page « Modèles », ni le backfill actions ne l'utilisent. Sans ce câblage, G2 ne peut pas produire son premier modèle actions |
| **ML-17** 🟠 | Valider le pooling sur des données actions RÉELLES | S | Mesuré uniquement sur séries synthétiques : les hôtes Yahoo sont refusés par la politique d'egress de l'environnement d'agent. À rejouer depuis une machine ayant accès, après `scripts/backfill_equities.py` |
| **ML-18** 🔵 | Variante recette de `window_sweep` | S | Le sweep reste piloté par la stratégie ; seul `train` a sa variante recette |
| **ML-19** 🔵 | Basculer les stratégies bespoke sur le chemin recette | M | `_train` délègue déjà, mais `fit()`/`score()` passent encore par la classe. Décision d'exploitation, recette par recette — l'équivalence est acquise, plus le modèle qui change |

* **`dynamic_threshold_no_ml`** — reste un fork, motivé (porte de volatilité en
  inférence là où le jumeau ML labellise). Le convertir dégraderait le parent.

---

## 4. Généralisation multi-actifs (crypto → actions SBF120)

*Objectif produit distinct de la dette technique : faire tourner le bot sur des
actions (SBF120) en plus de la crypto. Les abstractions sont posées, l'exécution
reste à brancher.*

### 4.1 Déjà générique — G1 ✅ FAIT

- `app/core/providers.py` : protocoles `MarketDataProvider`/`ExecutionProvider`.
- `Instrument` + `VenueRegistry` (extension `venues.defs`), devise de cotation neutralisée.
- `BaseStrategy.asset_classes` + marquage des stratégies crypto-only + filtre câblé.
- Golden test de parité (`tests/test_generic_parity.py`).
- Couplage ccxt confiné à ~5 fichiers (`exchange`, `candle_store`, `derivatives`,
  `position_*`, `routes/derivatives`).

### 4.2 Les 3 points de couplage — ✅ LEVÉS (G2, 2026-07-26)

| Point | État |
|---|---|
| **Calendrier de marché** (le plus structurant) | ✅ `app/core/market_calendar.py` — protocole `MarketCalendar`, `AlwaysOpenCalendar` (défaut = comportement 24/7 inchangé), moteur `SessionCalendar` déclaratif (fuseau, séances multiples, fériés fixes **et mobiles** via Pâques, demi-séances), `XPAR` livré en dur, adaptateur `exchange_calendars` si installé. Gating câblé dans `_cycle()` (`MarketHoursMixin`) + `close_at_session_end`. |
| **Sizing & coûts par venue** | ✅ `execution.quantize_size` / `quantize_price` / `venue_trade_cost` — sizing entier (`lot_size`, `fractional`), grille de cotation (`tick_size`), coûts fixe + % + plancher + **TTF** (assiette à l'achat). Partagés backtest ↔ live, appliqués sur les 3 chemins (ouverture, scale-in, clôture). |
| **Provider actions** | ✅ `app/core/yfinance_provider.py` (data-only) + `app/core/provider_router.py` (routage par venue) + `app/core/universe.py` + `data/universe/sbf120.yaml` + mode univers du scanner. ⚠ « ML par instrument » : **prémisse revue**, cf. §4.2bis. |

Détail d'exécution : §4.5bis.

### 4.2bis Le ML par instrument n'est plus un prérequis — c'est une question ouverte

Ce plan supposait qu'aller vers les actions imposerait un **modèle par
instrument**, et listait « retirer le `"BTC" in s` codé en dur » comme travail
à faire. La décision 11 du chantier ML a mesuré cette prémisse au lieu de la
suivre, et le résultat la renverse — pour la crypto au moins.

**Ce qui a été mesuré** (`scripts/measure_symbol_transfer.py`, §8bis de la
conception ML) : matrice de transfert modèle-BTC contre modèle-ETH, évalués
sur les holdouts BTC/ETH/XRP, 3 timeframes, coupure temporelle commune, IC 95 %
bootstrap apparié. **17 des 18 cellules indiscernables du bruit** ; ETH ne
gagne rien à son propre modèle (0.634 contre 0.638 en 1h).

**Ce qui en a été tiré** : le symbole est sorti de la clé du registre
(`{base_dir}/{tf}/{recette}/`) et ne subsiste qu'en **provenance**
(`ArtifactRef.train_symbol`). `_resolve_symbol` reste, mais documenté pour ce
qu'il est — le choix du **jeu d'entraînement**, pas l'identité du modèle. Ce
n'est donc plus « un codage en dur à retirer », c'est un choix assumé et
mesuré.

**Ce que ça ne dit pas, et qui concerne directement ce plan.** La mesure porte
sur **une seule paire entraînable** (BTC ~ ETH, corrélation de rendements
**0.76**) plus un indice non testable (XRP, corrélé à **0.31**, mais sans assez
d'historique pour avoir un modèle propre — il manque le contrefactuel). Trois
cryptos ne disent rien d'un panier actions/ETF.

> **Déclencheur explicite** : dès qu'un actif d'une autre classe et doté d'au
> moins 8 000 barres entre dans le store (SBF120, Nasdaq, ETF), **rejouer
> `scripts/measure_symbol_transfer.py`**. Il découvre les symboles depuis le
> store, mesure les corrélations, sépare paires *entraînables* et *évaluables
> seulement*, et annonce lui-même la portée de son verdict — aucune édition
> nécessaire. **C'est là seulement que la conclusion peut s'inverser**, et si
> elle s'inverse, réintroduire la dimension symbole dans la clé du registre
> devient un chantier de ce plan (item ML-15, §3.7).

### 4.3 Exécution réelle actions — G3 (Phase 3)

`IBKRExecutionProvider` (`ib_insync`) ou Saxo OpenAPI (idempotence `orderRef`),
`BalanceSyncMixin` multi-venue (EUR+USDC), exposition par classe d'actif.
SRD/short : chantier séparé, hors périmètre.

### 4.4 Fournisseurs recommandés

| Rôle | Recommandé | Alternative |
|---|---|---|
| Données 1h/1d | `yfinance` (`.PA`, gratuit) | EOD Historical Data |
| Exécution | Interactive Brokers (`ib_insync`) | Saxo OpenAPI |
| Calendrier | `exchange_calendars` (XPAR) | `pandas_market_calendars` |
| Univers | `data/universe/sbf120.yaml` statique | Scraping Euronext (à éviter) |

---

## 5. Roadmap par sprints

### Terminés

| Sprint | Contenu |
|---|---|
| 0-2, 4 | Correctifs P0/P1 sécurité-config, abstractions multi-actifs (G1), qualité métriques |
| Audit V0-6 | Régressions, sécurité, mesure, architecture, edge SMC, UX/docs |
| 7 | Nettoyage code mort partiel + **CI/lint/mypy/pytest configs** |
| 8 | Quick wins financiers & sécurité |
| Post-8 | Parallélisme optimiseur + dette lint |
| **Refactoring 1-6** | **MLBackend, 5 façades supprimées, format ML natif, suppression pandas/sklearn, CI remise au vert** (2026-07-23, PR #155) |

### À venir (ordre suggéré)

| Priorité | Sprint | Contenu | Dépend de |
|---|---|---|---| 
| 1 | **DEAD-01** | Supprimer les 8 stratégies mortes (décision utilisateur) → clôt SEC-020, débloque TEST-11 + ARCH-01 | décision |
| 2 | **Sécurité** | SEC-002/003/004/005/006 + bumps CVE | — |
| 3 | **ARCH-01** | OpusBase (routing partagé) sur les variantes survivantes | DEAD-01 |
| 4 | **Observabilité** | Prometheus, logs JSON, `/health` enrichi | — |
| 5 | **Tests** | TEST-11 (smoke), TEST-05 (ordres mockés), coverage CI | DEAD-01 |
| ~~6~~ | **G2** | ✅ **FAIT (2026-07-26)** — Actions SBF120 en paper (calendrier, sizing, frais, provider, **notification de trade**), cf. §4.5bis | G1 (fait) |
| 7 | **G3** | Exécution réelle actions (IBKR/Saxo) — la venue passe `can_execute: true`, le reste est déjà en place | G2 **validé en paper** (cf. §4.5bis, « ce qu'il reste à faire ») |
| — | Itératif | Financier, DX, recherche (§3.5-3.6) | au fil de l'eau |


claude/sprint-7-8-planning-xb12m0
| **DEAD-01** (audit) | Supprimer 8 générations Opus/stat jamais promues — liste **révisée** (7 restants, cf. note) — **8005 lignes** mortes au total avec DEAD-02. ⚠ NE PAS toucher `v8`, `v10`, `opus_stat_pretrained_v4` (dépendances réelles) | M | 🟡 **Analyse livrée (2026-07-19/20), re-confirmée le 2026-07-20 avec méthodologie de production, verdict révisé le 2026-07-20 après discussion utilisateur** — comparatif fonctionnel + empirique des 15 variantes sur 5 TF (15m/30m/1h/4h/1j), **re-run intégral** avec dimensionnement de fenêtre calqué sur la production (`auto_fetch_limit`/`split_is_oos`, plus de cap arbitraire) et **sans aucune stratégie sautée** (les 4 précédemment en timeout — v9/v10/v11_followsetup/v12 — ont maintenant un résultat optimisé complet), rapport HTML mis à jour remis à l'utilisateur (hors dépôt). **`opus_omnibus_v9` retiré de la liste DEAD-01** : classé « supprimer » à tort sur le seul motif que `v10` n'en hérite pas (versioning, pas qualité) — les chiffres montrent au contraire le signal le plus fort de toute la lignée v7-v10 (1h : PnL +561.5, Sharpe 49.4, supérieur à `v8` ET `v10`), avec deux setups Trend-Up réels (`LONG_TU`/`LONG_PULLBACK_TU`) que `v10` a explicitement écartés (jugement délibéré, mais non confirmé sur cette fenêtre de données). Verdict final : suppression nette pour **5/7** restants (`v7`, `v7_pretrained`, `v10_retrained`, `v11_no_ml`, `opus_stat_retrained_v4`), 2 « discutables » (`v11_followsetup[_no_ml]`) — le dossier a changé de forme lors du re-run : la variante *no_ml* se renforce (1h optimisé passe positif), la variante ML s'affaiblit (score OOS de recherche positif partout mais aucun gain traduit sur le backtest complet, signal probable de surapprentissage à 10 essais). **Découvertes hors périmètre DEAD-01** : (1) `v11`/`v12` (actives en production) ressortent négatives sur leur TF de production (1h) dans ce test, positives seulement sur 4h — signalé pour examen séparé, non tranché ; (2) l'écart ML vs no_ml (`v8`/`v8_no_ml`, `v11`/`v11_no_ml`) s'explique par des seuils de setup identiques mais des probabilités non calibrées côté proxy (sigmoïde à gain fixe non entraînée) contre un modèle ML calibré sur historique réel — 6-9× plus de trades, WR proche du hasard côté proxy. **Aucun fichier supprimé.** |
| **DEAD-02** (audit) | Supprimer `scoring_statistique_opus_v3.py` (579 L, aucun appelant) | S | ✅ FAIT — fichier + yaml supprimés, 0 référence |
| **DEAD-05** (audit) | Corriger le bug pyflakes `del ds_tr, ds_va` dans `opus_omnibus_v11.py:1065,1093` — **stratégie ACTIVE** (`manual_active: opus_omnibus_v11::30m`) | S | ✅ FAIT — try/finally englobant remplace les deux `del` dupliqués |
| **DEAD-06** (audit) | Supprimer 5 fonctions publiques jamais appelées : `config.strategy_file_path`, `execution.cap_notional`, `database.get_lifecycle_events`, `feature_store.get_provider`/`list_providers` | S | ✅ FAIT — les 5 supprimées |
| **DEAD-07** (audit) | 67 imports inutilisés (pyflakes, hors façade `indicators.py`) | M | ✅ FAIT — `ruff --select F` : 73 imports + 15 f-strings auto-fixés, 17 variables locales mortes retirées à la main |
| **DEAD-09** (audit) | Nettoyer `scripts/__pycache__` orphelin | S | ✅ Déjà propre — vérifié, rien à faire |
| **TEST-01** (audit + Plan C, doublon exact) | CI GitHub Actions (`pytest -m "not slow"`, lint) | S/M | ✅ FAIT — `.github/workflows/ci.yml` (lint ruff + pytest). **Nuance (2026-07-20)** : le job `lint` existait depuis Sprint 7 mais n'était **jamais passé** (773 erreurs `ruff check .` pré-existantes sur 163 fichiers, aucune liée à un sprint de ce document, jamais résorbées) — désormais **vert pour la première fois**, cf. §4.1bis |
| **TEST-04/05** (audit) = **TEST-02** (Plan C) | Config ruff (remplace flake8/mypy dispersés) : `ruff.toml`, `mypy.ini` | S/M | ✅ FAIT — `ruff.toml` + `mypy.ini`, `flake8` remplacé par `ruff` dans `requirements.txt` |
| **TEST-06** (audit) = **TEST-03** (Plan C) | Markers `pytest.ini` (`slow`, `strategy_smoke`), isoler les tests dépendant de `data/ohlcv` versionné | S/M | ✅ FAIT — `pytest.ini` avec les 2 markers ; aucun test ne dépend en fait de données versionnées (vérifié, tout est synthétique/`tmp_path`) |
| **TEST-11** (audit) = **TEST-04** (Plan C) | Tests smoke paramétrés pour les stratégies survivantes (~13/53 testées) | L | ❌ **Exclu explicitement de Sprint 7** — toujours bloqué par DEAD-01 |
| **DEAD-03** (audit) | Sort de `XRP_USDC` (données présentes, absent de `scanner.symbols`) | S | ✅ FAIT — **décision : ajouté à `scanner.symbols`** (`config.yaml`) ; pas de données `data/derivatives` pour XRP (gap connu, non bloquant) |

689/689 tests verts (649 avant Sprint 7 + 40 nouveaux Sprints 7/8).

### 4.1bis 🟢 PERF-02 (Plan C) — parallélisme réel de l'optimiseur — ✅ FAIT (2026-07-20)

Travail réalisé hors sprint numéroté, en marge de la préparation de la
décision DEAD-01 (nécessitait de faire tourner l'optimiseur sur les 15
variantes candidates × 5 timeframes en un temps raisonnable). Détail
technique complet dans `CHANGELOG.md` § « Post-Sprint 8 ». Résumé :

- `_SUPPORTED_TFS`/`_detect_timeframe` étendus à 4h/1j (14 fichiers,
  n'autorisaient que 15m/30m/1h en dur — exécution silencieuse sans signal
  au-delà).
- Cache d'entraînement process-wide branché sur `ml_dynamic_threshold`
  (sous-modèle de `opus_omnibus_v12`) — évitait jusqu'à ~40-60 % de coût par
  trial en retrains redondants. `random_search(n_trials=10)` sur 1h/8000
  barres : de « n'aboutit jamais en 300 s » à 67 s.
- **`random_search` : `n_jobs` réellement câblé** sur l'infra
  `ProcessPoolExecutor` déjà utilisée par `bayesian_search` (contexte
  `spawn`, cap mémoire anti-OOM, repli séquentiel) — la boucle restait
  séquentielle malgré le paramètre accepté. Mesuré ×3.0 sur 3 workers.
- `rolling_slope`/`rolling_hurst` (`app/core/indicators_market.py`)
  vectorisés (boucle Python O(n·window) → noyau/forme fermée), bit-exacts
  contre l'original. Mesuré ×233 / ×57.
- **Refonte de `param_search_optim`** (option de gel des paramètres à
  faible impact sur `random_search`/`bayesian_search`/`grid_search`, PAS un
  4e mode) : dépistage désormais **en budget** (les premiers essais de la
  recherche elle-même, jamais un essai en plus) et **pool de process
  partagé** entre dépistage et recherche (`_open_pool`/`_submit_wave`,
  remplace 3 blocs de création de pool quasi identiques). Mesuré sur
  `opus_omnibus_v12` : 137-140 s → ~63 s. Garde-fou `_MIN_SCREEN_PER_PARAM`
  ajouté après avoir mesuré que trop peu d'essais de dépistage vs nombre de
  paramètres gelait des paramètres sur un signal non fiable (mode
  facultatif uniquement — le mode grid, réduction obligatoire, n'est pas
  concerné).
- **Revue de code approfondie post-refonte** (pas seulement les tests
  unitaires) : 3 défauts trouvés et corrigés avant tout autre travail —
  `_run_parallel` comptait les succès au lieu des tentatives (ré-
  échantillonnage au-delà du budget, risque de `StopIteration` en mode
  grid) ; comptabilité fragile à la réutilisation d'instance de
  l'optimiseur ; `optimize_two_phase` ne propageait pas `param_search_optim`
  à `_dispatch` (toggle utilisateur silencieusement ignoré pour les jobs
  `ml_tune_hp`).
- 743/743 tests verts (+54 nouveaux/réécrits au total sur cet ensemble de
  travaux).

### 4.2 🟢 Quick wins financiers & sécurité — ✅ FAIT (Sprint 8), sauf FIN-01

Sprint 8 exécuté le 2026-07-18, **hors FIN-01** (exclu explicitement).

| ID (Plan C) | Item | Effort | Impact | État |
|---|---|---|---|---|
| **FIN-01** | Frais dynamiques par palier VIP OKX (`exchange.fee_schedule`, opt-in) | S | 5 | ❌ **Exclu explicitement de Sprint 8** — reste à faire |
| **FIN-04** | Benchmark vs Buy & Hold BTC (`app/core/performance.py`) | S | 4 | ✅ FAIT — déjà largement implémenté (`Backtester._add_buy_and_hold`) ; correctif du warmup figé à 210 (désynchronisé du warmup dynamique réel) |
| **FIN-06** | Compteur de frais par catégorie (taker/maker/borrow/stop) | S | 4 | ✅ FAIT — `Trade.fee_taker`/`fee_maker`/`exit_reason` (migration auto), `get_fee_breakdown()`, `GET /api/stats/fees` ; `exit_reason` distingue enfin clôture ≠ ouverture sur les 9 chemins de fermeture live |
| **FIN-07** | Slippage paper proportionnel à la taille | S | 3 | ✅ FAIT — `trading.paper_slippage_model: size` (défaut `static`), formule d'impact partagée avec le backtest (`app.core.execution.size_impact_cost`), volume lu depuis `OHLCVCache` |
| **STRAT-06** (Plan C) = **BT-13** (audit, jamais fait) | Compteur diagnostique `tp_sl_ambiguous_bars` (mesure, ne change pas la décision) | S | 3 | ✅ FAIT — `diagnostics.tp_sl_ambiguous_bars`, résolution stop-prioritaire inchangée |
| **SEC-04** | Rate-limiting granulaire par endpoint (au lieu du seul `default_limits` global `slowapi`) | S | 3 | ✅ FAIT — `Limiter` déplacé dans `app/api/state.py`, ~25 endpoints décorés `@state.limiter.limit(...)` |
| **SEC-05** | Backup automatique `trades.db` + `config.yaml` + `strategies/*.yaml` | S | 4 | ✅ FAIT — `deploy/backup.sh` (sqlite3.backup() Python, cohérent WAL), rétention automatique |
| **ARCH-07** (Plan C, partiel — §1.3.1) | Finir la migration des 16 littéraux `"BTC/USDC"` résiduels vers `DEFAULT_CONFIG_SYMBOL` | S | 2 | ✅ FAIT — les 3 sites de CODE Python migrés (`ohlcv_cache.py`, `config.py`) ; le reste (~28 occurrences) sont des docstrings/commentaires/valeurs par défaut UI HTML, hors scope Python |
| **BT-05** (audit, jamais fait) = **STRAT-03** (Plan C) | `scripts/audit_param_space.py` : lister chaque stratégie avec taille du param_space vs `n_trials`, warning si couverture < 1e-4 | M | 4 | ✅ FAIT — script + tests, `--strict` pour CI |
| **PERF-01** (Plan C) | Cache précompute indicateurs : 16 → 128 entrées, configurable | S | 3 | ✅ FAIT — `config.yaml:perf.precompute_cache_size` (défaut 128) |

### 4.3 🟡 Nettoyage résiduel architecture (effort réduit vs Plan C original)

| ID | Item | Effort réel | Note |
|---|---|---|---|
| **ARCH-05** (Plan C, partiel — §1.3.1) | Réduire `smart_money.py` (836 L) et `smart_money_signals.py` (702 L) sous 450 L chacun | M (pas L — la séparation en modules existe déjà, il s'agit d'extraire encore, pas de créer l'architecture) | Aucune urgence fonctionnelle, dette de lisibilité pure |
| **ARCH-03** (Plan C) = **ARCH-12** (audit, jamais fait, jugé optionnel) | `app/core/state.py::AppState` dataclass pour remplacer `app/api/state.py` | L | L'audit avait déjà conclu « l'encapsulation AppState complète reste optionnelle (aucune inversion restante) » après ARCH-04 audit — à ne faire que si un besoin concret apparaît (ex. plusieurs instances de bot par process) |
| **ARCH-01** (Plan C) | Extraire une stratégie maître unique **`setup_router`** (renommé depuis « OpusBase » 2026-07-20 — trop spécifique au nommage de la lignée existante ; le composant réel route le régime vers une table de setups configurable, avec source de signal — pkl figé / ré-entraîné / proxy / ML V11 — et mode de sortie, tous enfichables) pour les variantes Opus **survivantes** (v8, v9, v10, v11, v12, `opus_stat_pretrained_v4`) | L | À faire **après** DEAD-01 (réduit le nombre de fichiers à traiter). Chaque variante actuelle devient un *preset* YAML du moteur unique (10 setups, 3 axes de conception : routing/source-signal/sortie — cf. carte structurelle remise à l'utilisateur, hors dépôt) |
| **STRAT-01** (Plan C) | Champ `status: experimental\|validated\|production\|archived` dans chaque YAML stratégie | M | Concept **distinct** du `SlotLifecycleManager` runtime existant (candidat/essai/actif/retiré, calculé) — ceci est une déclaration statique de maturité, filtrable dans l'UI `/config` |
| **STRAT-02** (Plan C) | Versioning modèles ML (hash features + date, `models/index.json`) | M | Aucun équivalent existant |
| **SEC-06** (Plan C) | Migrations SQLite via Alembic | M | **Nuance** : `_migrate_schema` idempotent (`ALTER TABLE` auto) existe déjà depuis OPS-08 (audit, Vague 4) — Alembic serait un upgrade d'outillage, pas un correctif de bug. Impact réel réduit vs la description du Plan C |
| **TEST-05** (Plan C) | Tests d'ordres live mockés (`MockExchange` complet : idempotence clientOrderId, partial fills, réconciliation frais, restauration crash) | L | Aucun équivalent — les tests actuels (`test_live_trader.py`, `test_position_lifecycle.py`) couvrent des scénarios plus étroits |
| **TEST-06** (Plan C) | Fixtures de non-régression backtest byte-identique (3 configs de référence, JSON snapshot) | M | Partiellement couvert par `test_execution_parity.py`/`test_generic_parity.py` mais pas de snapshot global multi-métriques |

### 4.4 🔵 Bets structurants — observabilité, sécurité avancée, DX

Aucun équivalent dans l'audit initial ni dans ce plan directeur — intégralement
issus du Plan C, tous confirmés **non démarrés** (fichiers/dépendances absents,
vérifié) :

| ID | Item | Effort | Impact |
|---|---|---|---|
| **OBS-01** | Métriques Prometheus (`prometheus-client` absent de `requirements.txt`, `app/core/metrics.py` inexistant) | M | 5 |
| **OBS-02** | Logs JSON + correlation IDs (`contextvars`) | M | 4 |
| **OBS-03** | Dashboard Grafana (dépend OBS-01) | M | 4 |
| **OBS-04** | Alertes Prometheus AlertManager (dépend OBS-01) | M | 4 |
| **OBS-05** | Tracing OpenTelemetry | L | 2 (backlog) |
| **DX-01** | Docker + docker-compose (`Dockerfile` absent) | M | 5 |
| **SEC-01** | Rotation secrets via vault (`SecretProvider` Protocol) | M | 4 |
| **SEC-03** | Validation Pydantic des payloads API (`app/api/schemas.py` inexistant) | M | 4 |
| **UI-02** | Refonte navigation 3 sections + `/onboarding` (`onboarding.html` inexistant — seul le regroupement sidebar a été fait par le rewrite frontend, confirmé par la note du Plan C lui-même §0.4) | M | 4 |

### 4.5 🟡 Itératif — reste du backlog Plan C (non re-vérifié en détail)

Ces items n'ont pas été vérifiés ligne à ligne contre le code dans cette
session (faible risque d'être déjà faits vu qu'ils créent tous des fichiers
absents du repo — `app/core/regime.py`, `sentiment.py`, `tax_report.py`,
`app/ml/trainer.py` versioning, etc. — confirmé par `ls` négatif sur les
cibles principales). Conservés tels quels, priorité inchangée par rapport au
Plan C :

- **FIN-02** (borrow rate dynamique), **FIN-03** (reporting fiscal FIFO, dépend SEC-06), **FIN-05** (Sharpe/Sortino/Calmar/VaR/CVaR temps réel), **FIN-08** (réconciliation PnL quotidienne), **FIN-09** (multi-devises, backlog).
- **DX-02** (setup interactif), **DX-03** (hot-reload dev), **DX-04** (ADR), **DX-05** (profiling intégré), **DX-06** (OpenAPI enrichi).
- ~~**PERF-02** (parallélisation optimiseur)~~ — ✅ **FAIT** (2026-07-20, cf. §4.1bis), retiré de ce backlog. **PERF-03** (PostgreSQL, backlog), **PERF-04** (streaming SSE backtests).
- **LIFE-01** (tests transitions cycle de vie), **LIFE-02** (timeline UI), **LIFE-03** (auto-re-opt, backlog), **LIFE-04** (allocation graduelle, backlog).
- **WKFLOW-01/02/03** (conventional commits, pre-commit, templates issues/PR).
- **RES-01** (regime detection HMM), **RES-02** (backtest portfolio multi-actifs), **RES-03** (sentiment F&G, backlog), **RES-04** (extension usage dérivés).
- **ML-01** *(nouveau, découvert 2026-07-20 lors de l'investigation légitimité pkl §DEAD-01)* — Gating de promotion `manual_active` par walk-forward multi-fenêtres plutôt qu'un `oos_score` sur un seul split. Constaté sur `opus_omnibus_v8_no_ml`/`v10_no_ml` (déjà actives) : `oos_score` de production positif (0.76-0.77) mais backtest fenêtre complète nettement négatif (PnL −95/−110) — même divergence surapprentissage que `v11_followsetup`. Pas de suppression ni de retrait de `manual_active` proposé ici, juste un chantier de fiabilisation du critère de promotion.
- **ML-02** *(nouveau 2026-07-20, mis à jour 2026-07-24 — 🟢 majoritairement implémenté)* — **Gestion du cycle de vie des modèles ML** (entraînement, provenance, fraîcheur, reproductibilité). Chantier structurant issu de l'investigation légitimité du modèle V4 figé (§DEAD-01, rapport HTML §06). Effort M/L.
  > Note terminologique : les constats ci-dessous datent d'avant la bascule vers le format natif LightGBM (`.lgb` + `.meta.json`, sans pickle) — remplacer mentalement « pkl » par « modèle » partout où le mot apparaît encore ; il n'y a plus aucun `.pkl` dans le dépôt (cf. « Déclinés/obsolètes » ci-dessous, HMAC .pkl rendu sans objet).
  - **Dimensionnement de la fenêtre d'entraînement.** ✅ Configurable — `MLStrategyTrainer._retrain_thread` couvre désormais explicitement le plancher du gate (`holdout_bars+min_window_bars`) et accepte un knob `live_fetch_bars` par stratégie pour viser une fenêtre bien plus large (l'edge mesuré du V4 figé vient d'un entraînement sur ~40k barres) — aucune stratégie n'y est forcée par défaut (valeur restant à calibrer au banc, cf. conception §8).
  - **Provenance / métadonnées.** ✅ Fait — `meta.json` v2 (`provenance` : symbole, dates début/fin, n_bars, hash de recette, commit git ; `gate` : décision de promotion). Absorbe **STRAT-02** (`models/index.json` devient un cache reconstruisible du registre, pas un fichier à maintenir).
  - **Reproductibilité.** ✅ Fait — `app/ml/train_runner.py` + `scripts/train_model.py`, committé, paramétré (fenêtre/as_of/hyperparamètres), dry-run par défaut.
  - **Ré-entraînement périodique sur GRANDE fenêtre + gate de promotion.** ✅ Fait — `app/ml/policy.py` (`maybe_refresh`, `decide_gate`) : candidat entraîné puis comparé au sortant sur un holdout partagé, promu seulement s'il ne régresse pas ; audit trail dans `decisions.jsonl`. Cadence encore en heures (`retrain_interval_h`), pas en barres, côté live — cf. détail dans la conception §7.
    ⚠️ Ne couvre PAS **ML-01** : ML-01 gate la promotion d'une **stratégie** vers `manual_active` (machine à états `slot_lifecycle.py`, walk-forward multi-fenêtres sur le PnL) ; ce qui précède gate la promotion d'un **modèle** (artefact ML au sein du registre, AUC sur holdout). Deux mécanismes distincts à la même philosophie (comparer sur une fenêtre tenue à l'écart plutôt qu'accepter un score unique) — ML-01 reste un chantier séparé, non traité ici. La mention « absorbe ML-01 » de la spec détaillée (`CONCEPTION_CYCLE_DE_VIE_ML.md`, écrite avant implémentation) était erronée sur ce point précis — corrigée dans le document.
  - **Optimiser contre un modèle figé.** ✅ Fait (opt-in, pas par défaut) — `OptimizerSearchEngine.ml_mode` lu depuis `cfg["optimizer"]["ml_mode"]` (in-process et workers). Reste `"inline"` par défaut (flip de méthodologie non imposé silencieusement).
  - ➕ **Spec détaillée** : `docs/CONCEPTION_CYCLE_DE_VIE_ML.md` — architecture **recette / registre / politique de rafraîchissement**, §7 tient à jour le statut précis de chaque étape (E1-E7) et ce qui reste (passe de confirmation optimiseur, feature freezing, purge direction étendue aux stratégies sœurs, UI Modèles — E7 non commencé).


### 4.5bis 🟢 G2 — actions SBF 120 en paper — ✅ FAIT (2026-07-26)

Lève les **3 points de couplage** de §4.2. Principe directeur : rien de
spécifique aux actions dans le moteur — tout passe par la **venue**, et les
défauts de venue reproduisent exactement le comportement crypto historique.
Une configuration crypto existante n'emprunte aucun code nouveau (le routeur
n'est même pas instancié tant qu'aucune venue ne déclare de provider).

**Ce qui a été livré**

| Brique | Fichier | Rôle |
|---|---|---|
| Calendrier de marché | `app/core/market_calendar.py` | Protocole + `AlwaysOpenCalendar` (défaut) + moteur `SessionCalendar` déclaratif + `XPAR` + adaptateur `exchange_calendars` optionnel |
| Gating de la boucle live | `app/live/market_hours_mixin.py` | Filtre les entrées hors séance (log throttlé), garde-fou par signal, clôture avant fin de séance. **Les positions ouvertes restent gérées marché fermé** (trailing, stop au gap) |
| Contraintes & coûts venue | `app/core/execution.py` | `quantize_size`, `quantize_price`, `venue_trade_cost` — partagés backtest ↔ live |
| Modèle de venue étendu | `app/core/bot_identity.py` | `calendar`, `data_provider`, `can_execute`, `close_at_session_end`, `fee_pct/fixed/min`, `transaction_tax_pct`, `min_notional` |
| Provider actions | `app/core/yfinance_provider.py` | Data-only, deux backends (paquet `yfinance` ou API chart via `requests`) |
| Routage multi-provider | `app/core/provider_router.py` | Aiguille par venue ; **inerte** si aucune venue ne déclare de provider |
| Univers statiques | `app/core/universe.py`, `data/universe/sbf120.yaml` | Liste versionnée d'instruments, cumulée avec `scanner.symbols` |
| Vérification d'univers | `scripts/check_universe.py` | Interroge le provider ticker par ticker (radiés/renommés) |
| Notification de trade | `app/core/notifications.py` | `notify_trade_signal` — **le livrable central** ci-dessous |

**Notification de trade (exigence explicite du sprint).** Tant que l'exécution
réelle n'est pas branchée, une venue `can_execute: false` ne transmet **aucun**
ordre : le bot calcule le trade, le suit comme une position paper, et émet un
*ticket* portant **symbole, direction, prix d'ouverture, stop-loss,
take-profit**, plus quantité, notionnel, R:R, stratégie et venue. Envoyé en
**synchrone** et jamais throttlé (c'est le seul chemin vers l'exécution, il ne
peut pas être perdu dans une queue). Le message « position ouverte » habituel
est volontairement **supprimé** dans ce cas : il laisserait croire à un fill
réel. Symétrique à la sortie (« TRADE À SOLDER »). Décision prise dans
`_open_position`/`_close_position` — donc valable même sans routeur.

**Deux hypothèses crypto retirées du `CandleStore`** (elles auraient troué
l'historique actions en silence) : le plancher `since` à 2017 (fondation
d'OKX) et le rejet des barres à volume nul — désormais pilotés par le provider
(`min_since_ms`, `drop_zero_volume`).

**Limitations de l'API Yahoo, traitées explicitement** (elles se manifestent
autrement par des réponses vides inexpliquées) : profondeur plafonnée par
granularité (1 m → 7 j, intraday → 60 j, 1 h → 730 j, 1 j → illimité) avec
troncature **avertie une fois** par symbole/TF ; intervalles inexistants
(3 m, 2 h, 4 h, 6 h, 8 h, 12 h) ré-agrégés depuis l'intervalle de base, ancrés
sur l'epoch pour que le cache Parquet incrémental déduplique ; throttling
process-wide + backoff exponentiel sur 429 + cache TTL ; dégradation gracieuse
(liste vide, jamais d'exception qui tue un cycle).

> ⚠️ **Conséquence méthodologique** : un backtest actions en 15 m ne peut pas
> dépasser ~60 jours d'historique, et ~2 ans en 1 h. Les fenêtres
> d'entraînement ML calibrées sur la crypto (~40 k barres) ne sont donc **pas**
> atteignables en intraday actions. À arbitrer avant d'entraîner quoi que ce
> soit sur SBF 120 : soit du journalier, soit un fournisseur payant (EOD
> Historical Data, cf. §4.4).

**Ce qu'il reste à faire avant de faire tourner G2 pour de vrai**

1. **Vérifier l'univers** : `data/universe/sbf120.yaml` est un instantané
   constitué **hors ligne** (`verified: false`) — la composition de l'indice est
   révisée trimestriellement. Lancer `python scripts/check_universe.py sbf120`
   puis recouper avec la publication Euronext. Le fichier est délibérément
   marqué non vérifié plutôt que présenté comme faisant autorité.
2. **Vérifier le taux de TTF** (`transaction_tax_pct`, posé à 0,4 % dans
   l'exemple de `config.yaml` d'après ce plan) et les frais réels du courtier
   (`fee_pct`, `fee_fixed`, `fee_min`) — ils pilotent directement le seuil de
   rentabilité et le `min_notional`.
3. **Décommenter** la venue `euronext-paper`, `scanner.universe` et
   `providers.yfinance` dans `config.yaml`, puis assigner les instruments
   (`venues.assign`). Rien n'est activé par défaut.
4. **Rejouer `scripts/measure_symbol_transfer.py`** dès qu'un titre atteint
   8 000 barres dans le store — c'est le déclencheur explicite posé en §4.2bis,
   et le seul moment où la décision « symbole hors de la clé du registre »
   peut s'inverser.

**Non traité (hors périmètre, assumé)** : `bars_per_year` reste calé sur
365 j × 24 h, donc le Sharpe annualisé d'un backtest actions est sous-estimé
(une séance Euronext fait 8,5 h, 252 jours par an) — les comparaisons
inter-stratégies restent valides à classe d'actif constante, pas entre crypto
et actions. Correction à porter en même temps que G3.

**Tests** : 6 nouveaux fichiers, 121 tests — `test_market_calendar.py`,
`test_venue_costs.py`, `test_universe.py`, `test_yfinance_provider.py`,
`test_provider_router.py`, `test_equity_paper_flow.py` (parcours bout à bout
sur un vrai `LiveTrader`). Chaque comportement actions a son pendant
« non-régression crypto ».

---

## 6. Décisions produit en attente

- **G2 / univers SBF 120** : valider la composition de
  `data/universe/sbf120.yaml` (`verified: false`) et le taux de TTF retenu —
  cf. §4.5bis, « ce qu'il reste à faire ».
- **DEAD-01** : feu vert pour supprimer les 8 stratégies mortes (analyse
  livrée). Bloque plusieurs chantiers en aval.
- **FIN-01** : implémenter les frais VIP OKX dynamiques (exclu des sprints
  précédents).

### Déclinés / obsolètes (ne pas rouvrir sans confirmation)

- ⛔ **BT-11 / plafond d'exposition BTC+ETH corrélés** — décliné par
  l'utilisateur (multi-crypto corrélé assumé).
- ⛔ **DEAD-03 / dé-versionner `data/`** — les parquets sont poussés
  volontairement depuis la machine de l'utilisateur ; ne pas exécuter sans
  accord explicite. (Sort de `XRP_USDC` déjà tranché : ajouté à `scanner.symbols`.)
- ⛔ **HMAC de signature des `.pkl`** (ancien SEC-001) — rendu **sans objet** :
  il n'y a plus aucun `.pkl` ni pickle dans le code (format 100 % LightGBM natif).
- ⛔ **Encapsulation `AppState` complète** — optionnelle, aucune inversion de
  couche restante ; à ne faire que sur besoin concret (multi-instances).
