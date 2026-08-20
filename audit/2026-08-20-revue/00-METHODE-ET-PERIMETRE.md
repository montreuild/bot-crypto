# Audit du 2026-08-20 — méthode et périmètre

## Périmètre temporel

Le dernier audit a été livré le **2026-08-18** (rapports `audit/00-SYNTHESE.md` …
`audit/12-ARCHITECTURE-DETTE.md`, commits `d03ddc2` → `177d71d`, intégrés par le
merge `a6659e1` à 19:48:56 +0200).

Le delta audité ici est donc **`a6659e1..HEAD`** (HEAD = `bb94993`).

| Mesure | Valeur |
|---|---|
| Commits | 49 |
| Fichiers touchés | 268 |
| Lignes | +10 914 / −6 308 |
| Fichiers dans le périmètre | 232 (`app/`, `frontend/`, `tests/`, `config/`, CI, Docker) |

Taille du dépôt au moment de l'audit : 227 modules Python (63 125 lignes),
172 fichiers TS/TSX (34 115 lignes), 183 fichiers de test.

## Périmètre de code

**Dans le périmètre** — `app/` (api, core, engine, live, ml, strategies),
`frontend/`, `tests/`, `cli.py`, `config/`, Docker, CI.

**Hors périmètre** — `scripts/`, `research/`, `optimize_runner.py`, `docs/`,
`audit/`, `CHANGELOG.md`, `README.md`, `ARCHITECTURE.md`.

### Exception vérifiée : dépendance de `app/` vers `scripts/` ou `research/`

Vérification exécutée :

```
grep -rnE "^\s*(from|import)\s+(scripts|research)\b" app/ cli.py
```

**Résultat : aucun import.** Les 20 occurrences textuelles de `scripts/` et
`research/` dans `app/` sont toutes des références en docstring (pointeurs vers
des scripts de mesure). Le graphe de code montre des arêtes `engine-job →
scripts-cfg` (69) et `engine-job → research-section` (47) : elles sont
**orientées scripts → app**, pas l'inverse. Le sens de dépendance est correct.

## Règle de preuve appliquée

Aucun constat ne repose sur un commentaire de code ni sur la documentation
existante. Chaque constat porte :

- un identifiant stable (`FIN-xx`, `OPT-xx`, `DAT-xx`, …) ;
- une sévérité : **P0** bloquant / **P1** majeur / **P2** mineur / **P3** cosmétique ;
- une référence `fichier:ligne` ;
- un scénario d'échec concret (entrées → sortie fausse) ;
- la vérification faite, et son statut :
  - **CONFIRMÉ** — reproduit par exécution (script de repro, mesure, ou rejeu
    sur les données réelles) ;
  - **PLAUSIBLE** — établi par lecture du code seule, non reproduit.

Les scripts de reproduction sont cités dans chaque constat et sont rejouables ;
ils vivent hors du dépôt (répertoire de travail de session), conformément à la
contrainte de lecture seule.

## Méthode

### 1. Graphe de code

`code-review-graph`, reconstruction complète : **714 fichiers, 6 818 nœuds,
67 162 arêtes, 15 communautés, 505 flux**. Utilisé pour l'architecture, les
hubs, les points de passage et l'impact du delta (rapport `02-ARCHITECTURE.md`).

### 2. Revue du delta fichier par fichier

Chemins critiques d'abord : exécution, coûts, risque, PnL, optimiseur, données.
Rapport `03-REVUE-DELTA.md`.

### 3. Audit transversal par domaine

Un rapport par domaine (04 à 16).

### 4. Vérification exécutable

Toutes les commandes ont été lancées depuis l'environnement du projet
(`.venv`, Python 3.14.6) sur le worktree à `bb94993`.

| Outil | Commande | Résultat |
|---|---|---|
| ruff 0.15.8 | `ruff check .` | **ÉCHEC — 3 erreurs** (I001) |
| mypy | `mypy app/core app/engine app/api/ws_tickets.py app/live/protocols.py app/ml/overfitting_gate.py` (périmètre CI exact) | **ÉCHEC — 1 erreur** |
| mypy | `mypy app/` (périmètre complet) | 347 erreurs / 56 fichiers |
| pytest | `pytest -q` | **2 142 passés, 30 ignorés** (241 s) |
| tsc | `tsc --noEmit` | **OK — 0 erreur** |
| eslint | `eslint .` | **OK — 0 erreur** |
| vitest | `vitest run` | **190 passés / 20 fichiers** |

Les deux échecs ruff/mypy sont des **gates CI rouges sur HEAD** : voir
`15-TESTS-CI.md` (CI-01, CI-02).

### 5. Rejeu sur les données réelles

Les données de production vivent dans le dépôt principal
(`bot-crypto/data/`, **456 Mo** : 413 Mo de features, 44 Mo d'OHLCV), pas dans
le worktree. Tout ce qui touche au financier ou aux données a été rejoué
dessus :

- **645 fichiers parquet**, 128 symboles ;
- **1 848 671 barres actions** (SBF 120, suffixes `.PA`/`.AS`/`.F`) ;
- **610 249 barres crypto** (BTC_USDC, 1m → 1d).

Constats issus d'un rejeu réel : `DAT-01` (trous masqués sur BTC_USDC 1h et 4h),
`PERF-01` (coût du chemin calendaire, A/B avant/après sur 140 575 barres).

### 6. Mesure de performance chiffrée

Aucune estimation : chaque constat de performance porte un temps mesuré, un
volume de barres, et le cas échéant un A/B contre la version d'avant le delta
(extraite par `git show a6659e1:<fichier>`).

## Livrables

| Fichier | Domaine |
|---|---|
| `00-METHODE-ET-PERIMETRE.md` | Ce document |
| `01-SYNTHESE-EXECUTIVE.md` | Synthèse |
| `02-ARCHITECTURE.md` | Architecture, hubs, couplage |
| `03-REVUE-DELTA.md` | Revue du delta 2 jours |
| `04-MOTEUR-FINANCIER.md` | Coûts, PnL, risque |
| `05-BACKTEST.md` | Backtest, walk-forward |
| `06-OPTIMISEUR.md` | Optimiseur, gates d'application |
| `07-LIVE-EXECUTION.md` | Live, exécution, ordres |
| `08-ML.md` | ML, splits, calibration |
| `09-DONNEES.md` | Données, trous, complétude |
| `10-BACKEND-API.md` | API, schémas, WebSocket |
| `11-FRONTEND.md` | Frontend, types, hooks |
| `12-UI-UX-ACCESSIBILITE.md` | UI/UX et accessibilité |
| `13-PERFORMANCE.md` | Performance |
| `14-SECURITE.md` | Sécurité |
| `15-TESTS-CI.md` | Tests et CI |
| `16-DETTE-TECHNIQUE.md` | Dette technique |
| `99-REGISTRE.md` | Registre global des constats |

## Contrainte de lecture seule

Aucune modification n'a été faite sur `app/`, `frontend/src` ni `tests/`.
Seuls les fichiers de `audit/2026-08-20-revue/` sont écrits.
