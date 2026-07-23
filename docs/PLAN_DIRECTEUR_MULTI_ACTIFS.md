# Plan Directeur — État des lieux & Feuille de route

> **Document de référence unique** du bot (améliorations + généralisation
> multi-actifs). Il remplace et fusionne les plans historiques dispersés
> (`AUDIT.md`, `docs/audit/00-INDEX.md` + 7 fichiers domaine, les audits
> externes successifs). Ces fichiers restent consultables comme **archive des
> directives détaillées**, mais **c'est ici la source de vérité** sur ce qui est
> fait et ce qui reste à faire.
>
> **Dernière mise à jour : 2026-07-23** — branche `claude/apply-patch-sn96hj`
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
| **Stratégies actives** | 15 (`manual_active`) | Sur ~45 fichiers `app/strategies/` — dont **8 morts** à supprimer (DEAD-01). |

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
| Audit Vague 6 | UX/a11y/docs, tests LiveTrader/API/lifecycle, découpe CHANGELOG | ✅ (sauf TEST-11, bloqué par DEAD-01) |
| Sprint 7 | Nettoyage code mort partiel (`scoring_v3`, pyflakes, imports morts), **CI + ruff.toml + mypy.ini + pytest.ini** | ✅ (hors DEAD-01) |
| Sprint 8 | Quick wins financiers/sécu : benchmark B&H, compteur de frais, slippage paper, rate-limit par endpoint, backup auto, `audit_param_space.py` | ✅ (hors FIN-01) |
| Post-8 | Parallélisme réel de l'optimiseur (`n_jobs` câblé, refonte `param_search_optim`), résorption dette lint pré-existante (773 → 0) | ✅ |

---

## 3. Ce qui reste à faire — backlog priorisé

Grille : 🟢 quick win · 🔵 bet structurant · 🟡 itératif.

### 3.1 🟢 DEAD-01 — Supprimer les 8 stratégies Opus/stat mortes

**Le chantier de nettoyage le plus rentable, et il débloque plusieurs autres.**
Analyse comparative (fonctionnelle + empirique sur 5 TF) déjà livrée ;
**décision de suppression utilisateur en attente**. Aucun fichier supprimé à ce
jour.

Candidates (aucune dans `manual_active`, ~7 569 L au total) :

| Fichier | Lignes | Verdict analyse |
|---|---:|---|
| `opus_omnibus_v7.py` | 1266 | suppression nette |
| `opus_omnibus_v7_pretrained.py` | 640 | suppression nette |
| `opus_omnibus_v9.py` | 738 | suppression nette |
| `opus_omnibus_v10_retrained.py` | 1323 | suppression nette |
| `opus_omnibus_v11_no_ml.py` | 545 | suppression nette |
| `opus_omnibus_v11_followsetup.py` | 1444 | discutable (seule paire positive sur 1j, échantillon faible) |
| `opus_omnibus_v11_followsetup_no_ml.py` | 492 | discutable |
| `opus_stat_retrained_v4.py` | 1121 | suppression nette |

⚠ **Ne pas toucher** `opus_omnibus_v8`, `opus_omnibus_v10`,
`opus_stat_pretrained_v4` (dépendances réelles, dont le scanner V8).

> **Note** : ces stratégies ont été migrées hors de `joblib` vers le format
> natif LGB+JSON (§2.3, 2026-07-23), donc **SEC-020 est déjà clos** et elles ne
> crashent plus à la sauvegarde. Leur suppression est désormais un nettoyage de
> **code mort « pur »**, sans enjeu sécurité résiduel.
>
> **Débloque** : TEST-11 (smoke tests stratégies) et ARCH-01 (OpusBase — moins
> de variantes à factoriser).

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
| **ARCH-01** (OpusBase) | Factoriser le routing V10 partagé (setups, `_select_setup`, `_check_early_exit`) des variantes **survivantes** (v8/v10/v11/v12/pretrained_v4). `MLBackend` a déjà extrait le ML ; ~600 L de routing restent dupliquées | DEAD-01 |
| **STRAT-01** | Champ `status: experimental\|validated\|production\|archived` dans chaque YAML (distinct du lifecycle runtime) | — |
| **STRAT-02** | Versioning modèles ML (hash features + date, `models/index.json`) | — |
| ARCH-05 | Réduire `smart_money.py` (838 L) et `smart_money_signals.py` (891 L) — dette de lisibilité, pas d'urgence | — |

### 3.5 🟡 Tests, docs, DX

| ID | Item |
|---|---|
| TEST-11 | Smoke tests paramétrés pour les stratégies survivantes (après DEAD-01) |
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

### 4.2 Les 3 points de couplage restants

| Point | À faire |
|---|---|
| **Calendrier de marché** (le plus structurant) | `app/core/market_calendar.py` (wrapper `exchange_calendars` XPAR), gating `_cycle()`/`CandleStore`/forward-test, seuil de gap par classe d'actif, `close_at_session_end`. La boucle live suppose encore un marché 24/7. |
| **Sizing & coûts par venue** | `compute_size` en fractions continues → sizing entier (`lot_size`, `tick_size`) ; frais par venue (fixe + % + **TTF française 0,4 %**) partagés backtest/live. |
| **Provider actions** | `YFinanceProvider` (data-only, tickers `.PA`) → CandleStore ; `data/universe/sbf120.yaml` ; scanner mode equity ; ML par instrument (retirer le `"BTC" in s` codé en dur). |

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
| 6 | **G2** | Actions SBF120 en paper (calendrier, sizing, frais, provider) | G1 (fait) |
| 7 | **G3** | Exécution réelle actions (IBKR/Saxo) | G2 validé |
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
- **ML-02** *(nouveau, 2026-07-20)* — **Gestion du cycle de vie des modèles pkl** (entraînement, provenance, fraîcheur, reproductibilité). Chantier structurant issu de l'investigation légitimité du pkl figé (§DEAD-01, rapport HTML §06). Effort M/L. Cinq volets, tous étayés par des constats de code/mesure :
  - **Dimensionnement de la fenêtre d'entraînement.** Le trainer auto (`app/ml/trainer.py`) ne fetche que ~1560 barres (`fetch_n = max(need+200, 2·need, 1000)`, `need = min_bars_required ≈ 780`) — alors que ce qui fait l'edge du V4 figé, c'est son entraînement sur ~40 000 barres. Mesuré : AUC amplitude 0.76 (V4 figé, 40k) contre 0.62-0.70 (recette ré-entraînée/V11 sur fenêtre courte). `fit()` garde déjà tout ce qu'on lui passe (`n_keep = max(2200, len(df))`) : il suffit de rendre `fetch_n` configurable et de viser ≥40k là où l'historique le permet (15m/30m/1h ; 4h/1j restent bornés par les données locales).
  - **Provenance / métadonnées.** Le pkl ne stocke aujourd'hui que `split_idx`/`features`/`config` — la fenêtre d'entraînement a dû être *inférée* pour le test de fuite. Stocker : dates début/fin d'entraînement, `n_bars`, symbole, AUC de validation, version de la liste de features, commit git. (Recoupe **STRAT-02** — versioning modèles ML, `models/index.json` ; à fusionner.)
  - **Reproductibilité.** Le pkl V4 actuel a été généré par un script **hors dépôt**, impossible à régénérer à l'identique. Committer un script d'entraînement paramétré (fenêtre + hyperparamètres + seed) dans le dépôt.
  - **Ré-entraînement périodique sur GRANDE fenêtre.** Un modèle figé se dégrade (constaté : direction 15m passe **sous 0.5** — anti-prédictive — sur les données récentes). Ni « figé pour toujours » ni « ré-entraîné tous les 800 barres sur peu de données » (les deux perdent, mesuré) : viser un ré-entraînement **rare** (mensuel/trimestriel) sur **≥40k barres glissantes**. L'infra existe (`MLStrategyTrainer`, `retrain_interval_h`, `save_model`/`load_model`, `managed_externally`) — il manque le dimensionnement et la planification.
  - **Optimiser contre un modèle figé.** L'optimiseur est câblé en dur sur `use_pretrained_ml=False` (`optimizer.py:247`, `opt_workers.py:284` — ré-entraîne à chaque essai). Ajouter un flag pour geler un modèle puis optimiser ses seuils de setup **contre** lui : plus rapide (pas de ré-entraînement par essai), méthodologiquement correct (cible fixe), et cela débloque proprement l'expérimentation « recette figée + seuils re-tunés » (tentée manuellement en §06, réfutée pour V11, mais l'outillage manque pour l'industrialiser).
=======


## 6. Décisions produit en attente

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
