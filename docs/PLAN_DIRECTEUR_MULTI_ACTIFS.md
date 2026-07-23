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
| **Sécurité désérialisation (RCE)** | ✅ **Clos** | Plus aucun `joblib`/`pickle.load` non protégé dans le code (actif **comme mort**) : format natif LGB+JSON partout, `RestrictedUnpickler` (liste blanche) en repli legacy. |
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
(`app/ml/backend/persistence.py`), avec repli RCE-safe `RestrictedUnpickler`
pour les anciens `.pkl` (whitelist étendue à la calibration `IsotonicRegression`
native). Ces stratégies restent candidates à DEAD-01, mais ne dépendent plus de
joblib et ne crasheraient plus à la sauvegarde. **SEC-020 est désormais clos.**

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

---

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
- ⛔ **HMAC de signature des `.pkl`** (ancien SEC-001) — rendu **sans objet** par
  le format natif LGB+JSON (plus de pickle non protégé dans le code ; le legacy
  passe par `RestrictedUnpickler` à liste blanche).
- ⛔ **Encapsulation `AppState` complète** — optionnelle, aucune inversion de
  couche restante ; à ne faire que sur besoin concret (multi-instances).
