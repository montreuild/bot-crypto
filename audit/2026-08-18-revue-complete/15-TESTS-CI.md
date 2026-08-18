# 15 — Tests et intégration continue

Périmètre : `tests/` (~29 100 lignes, 200+ fichiers), `frontend/e2e`,
`frontend/src/**/__tests__`, `.github/workflows/ci.yml`, `.gitlab-ci.yml`, `pytest.ini`,
`mypy.ini`, `ruff.toml`.

---

## Résultats d'exécution

Tout ce qui suit a été lancé sur le worktree audité.

| Outil | Résultat |
|---|---|
| `pytest -m "not slow"` | **2 075 passés · 27 skipped · 18 deselected · 0 échec** — 153,87 s |
| Couverture `app/` | **66 %** (28 364 instructions, 9 620 non couvertes) |
| `ruff check .` | **propre** |
| `mypy app` | **1 084 erreurs / 120 fichiers** (206 analysés) |
| `tsc --noEmit` | **0 erreur** |
| `eslint .` | **0 erreur, 5 avertissements** |
| `vitest run` | **126 passés · 10 fichiers · 0 échec** |
| Couverture `frontend/src` | **4,84 %** |

2 075 tests qui passent tous, en 2 min 34 s, sans flaky observé : la suite backend est
réelle et rapide. C'est le socle sur lequel les correctifs de ce rapport peuvent être
appliqués en confiance. Les problèmes sont ailleurs — dans **ce que la CI accepte de
laisser passer**.

---

## TEST-01 — La CI impose un plancher de couverture 41 points sous la réalité

**Sévérité P1 · CONFIRMÉ (mesure)**

`.github/workflows/ci.yml` et `.gitlab-ci.yml` :

```
--cov=app --cov-report=term-missing:skip-covered --cov-fail-under=25
```

Couverture réelle : **66 %**.

Un plancher à 25 % quand on est à 66 % n'est pas un garde-fou, c'est un décor : il autorise
la suppression de **41 points** de couverture — soit environ 11 600 instructions couvertes
— sans faire échouer la CI. Concrètement, on peut supprimer les deux tiers des tests
existants et la CI reste verte.

**Correction** : porter le seuil à `--cov-fail-under=64` (deux points de marge sous le
réel), puis le relever avec la couverture. C'est un changement d'une ligne qui transforme
un chiffre décoratif en contrainte.

---

## TEST-02 — Aucun plancher de couverture côté frontend, et 4,84 % de réel

**Sévérité P1 · CONFIRMÉ (mesure)**

Le job frontend, dans les deux pipelines :

```yaml
- npm ci
- npm run lint
- npm run type-check
- npm test          # ← aucun seuil
- npm run build
```

Couverture mesurée : **4,84 %**. Détail en `10-FRONTEND.md` (FE-01) — `use-api.ts`
(745 lignes) et `lib/api.ts` (650 lignes), qui transforment toutes les données avant
affichage, sont à **0 %**.

L'asymétrie est institutionnalisée : le backend a un seuil (même trop bas), le frontend n'en
a aucun. `@vitest/coverage-v8` est pourtant déjà en dépendance et le script
`test:coverage` existe dans `package.json` — il n'est simplement pas appelé par la CI.

**Correction** : remplacer `npm test` par `npm run test:coverage` avec
`--coverage.thresholds.lines=30`, en visant d'abord `lib/` et `hooks/`.

---

## TEST-03 — Mypy sur 3 fichiers sur 206, en `continue-on-error`

**Sévérité P2 · CONFIRMÉ (lecture + mesure)**

```yaml
mypy:
  continue-on-error: true          # (allow_failure: true côté GitLab)
  run: python -m mypy app/core/sanitize.py app/core/ohlcv_gaps.py app/ml/overfitting_gate.py
```

Trois fichiers, et l'échec est toléré. `mypy app` complet rapporte 1 084 erreurs.

Le chemin de sortie est établi dans `03-ARCHITECTURE.md` (ARCH-04) : un `Protocol` par
famille de mixins supprime 344 erreurs, `RUF013` en autofix en supprime ~266. Après ces
deux passes, étendre la CI à `app/core` + `app/engine` avec `continue-on-error: false`
devient réaliste.

Le job tel qu'il est aujourd'hui ne peut rien détecter : trois fichiers déjà propres, et
un échec sans conséquence.

---

## TEST-04 — Cinq modules à 0 % de couverture, dont deux sur le chemin de décision

**Sévérité P2 · CONFIRMÉ (mesure)**

| Module | Instructions | Couverture |
|---|---:|---:|
| `app/engine/recommendations.py` | 198 | **0 %** |
| `app/engine/smc_signals_scan.py` | 144 | **0 %** |
| `app/engine/compute_pool.py` | 69 | **0 %** |
| `app/core/correlation_matrix.py` | 67 | **0 %** |
| `app/core/backtest_history.py` | 45 | **0 %** |
| `app/api/services/scanner_service.py` | 334 | **4 %** |
| `app/ml/features_smc.py` | 165 | **11 %** |
| `app/strategies/breakout_filtreHor.py` | 184 | **14 %** |
| `app/engine/forward_test.py` | 102 | **18 %** |
| `app/engine/walk_forward.py` | 65 | **18 %** |
| `app/engine/auto_optimizer.py` | 427 | **22 %** |
| `app/engine/scanner.py` | 235 | **20 %** |
| `app/api/routes/optimizer.py` | 315 | **25 %** |

Trois lignes méritent une attention particulière :

- **`recommendations.py` (198 instructions, 0 %)** — le module qui produit les
  recommandations affichées à l'utilisateur. Zéro test.
- **`walk_forward.py` (18 %)** — porte **BT-01, BT-02, BT-03** de ce rapport. La faible
  couverture n'est pas une coïncidence : les trois défauts sont dans les branches non
  couvertes (le calcul du warmup par fold, le drapeau `realistic_risk`, le `except`
  qui avale un fold).
- **`auto_optimizer.py` (22 %)** — porte **OPT-04 et OPT-05**. Même observation : les
  chemins de repli des gates (`df_gate = df_oos`, les trois `return True`) sont
  exactement ceux que les tests ne parcourent pas.

**La couverture prédit les défauts de ce rapport avec une précision remarquable.** Cinq des
neuf P1 sont dans des modules sous 25 %.

---

## TEST-05 — Les tests de performance sont construits sur des grilles synthétiques régulières

**Sévérité P1 · CONFIRMÉ (démontré par PERF-01)**

C'est le constat le plus instructif du rapport sur la méthode de test.

`13-PERFORMANCE.md` (PERF-01) démontre que `htf_trend_ema_series` rend `None` sur les
données réelles du dépôt (5 pas irréguliers sur 15 768, dont un trou de 164 jours) et un
tableau valide sur une grille parfaitement régulière — celle que construisent les tests.

Conséquence : l'optimisation ×120 du commit `bfc330e` est **validée par les tests et inerte
en production**, depuis son introduction. Le facteur 45 mesuré entre `trend` (58 barres/s)
et `volatility_squeeze` (2 637 barres/s) n'a jamais été vu parce qu'aucun test ne mesure un
débit, et parce que ceux qui exercent le chemin le font sur des données qui n'existent pas.

Cinq commits de la seule fenêtre auditée annoncent des gains de performance (`bfc330e`,
`4830ef9`, `229cb4c`, `a68e364`, `b791e40`). **Aucun n'est protégé par un test.**

**Corrections, les deux ensemble** :
1. Un test `@pytest.mark.slow` de débit sur une série **réelle tronquée** (`data/ohlcv`),
   avec seuil. ~30 lignes, et il aurait attrapé PERF-01 le jour de son introduction.
2. Une fixture de données « réalistes » — grille avec trous — à utiliser partout où un test
   exerce un chemin causal vectorisé. `pytest-benchmark` est déjà en dépendance.

---

## TEST-06 — `eslint` analyse les artefacts générés

**Sévérité P3 · CONFIRMÉ (exécution)**

```
$ npx eslint .
frontend/coverage/prettify.js   1:1  warning  Unused eslint-disable directive
frontend/coverage/sorter.js     1:1  warning  Unused eslint-disable directive
frontend/eslint.config.mjs     12:1  warning  import/no-anonymous-default-export
frontend/src/lib/api.ts       146:3  warning  Unused eslint-disable directive
✖ 5 problems (0 errors, 5 warnings)
```

Deux des cinq avertissements viennent de `coverage/`, répertoire généré par Vitest et absent
des `ignores` d'eslint. Sans conséquence (0 erreur), mais le bruit finit par masquer le
signal — et il apparaîtra ou disparaîtra selon qu'un rapport de couverture a été généré
avant le lint, ce qui rend la sortie non déterministe.

Les deux directives `eslint-disable` inutilisées (dont `src/lib/api.ts:146`) sont un reste
de nettoyage : `--fix` les retire.

---

## Ce qui a été vérifié et tenu

- **2 075 tests, zéro échec, 154 s.** Une suite de cette taille qui tourne en deux minutes
  et demie est utilisable en boucle de développement — c'est la condition pour que les tests
  servent réellement.
- **Ruff propre** sur tout le dépôt, avec des `per-file-ignores` justifiés
  (`indicators.py` est une façade de ré-exports, les `F401` y sont intentionnels et
  marqués `# noqa`).
- **`pip-audit` bloquant** sur `requirements.txt` **et** `requirements-dev.txt` —
  `allow_failure` absent, donc `false`.
- **Deux pipelines maintenus en miroir** (GitHub Actions et GitLab CI), avec l'en-tête du
  fichier GitLab qui dit explicitement qu'il est un miroir. La divergence entre les deux
  est le risque habituel de ce montage ; ici les jobs sont identiques (vérifié
  job par job).
- **`CRYPTO_BOT_INLINE_COMPUTE: "1"`** en CI — les calculs restent en processus, ce qui
  évite les `ProcessPool` dans un conteneur contraint. Bon réflexe.
- **`interruptible: true`** côté GitLab : un push annule le pipeline précédent.
- **Cache `npm ci` clé sur `package-lock.json`.**
- **Séparation `-m "not slow"` / `slow.yml`** : la suite rapide reste rapide, la lente
  existe.
- **E2E réels** — 4 spécifications Playwright : `a11y.spec.ts` (axe-core, WCAG 2.1 AA),
  `pages.spec.ts`, `visual.spec.ts` (instantanés versionnés), `qw-backtest.spec.ts`. La
  couverture unitaire du frontend est faible, mais l'e2e compense partiellement — et il
  couvre précisément ce que l'unitaire couvre mal (le rendu réel).
- **Un test par correctif** — la fenêtre auditée ajoute **2 761 lignes de tests sur 52
  fichiers**, dont `test_exit_modes.py`, `test_partial_exits.py`, `test_risk_ledger.py`,
  `test_execution_parity.py`, `test_smc_patterns_*.py`, `test_train_cache_v4_v5.py`,
  `test_retrain_cadence.py`. La discipline « un correctif, un test » est réelle.

Et une remarque sur la qualité des tests eux-mêmes : `tests/test_partial_exits.py` est
exemplaire dans sa structure (docstring qui énonce les trois propriétés vérifiées —
conservation, neutralité, priorité — puis un test par propriété, sur des données
déterministes). C'est ce fichier que j'ai réutilisé pour reproduire FIN-01. Son seul défaut
est de vérifier `net_profit == final_equity - initial_capital`, une identité vraie par
définition, là où l'invariant utile était `total_pnl == net_profit`.

---

## Récapitulatif

| ID | Sévérité | Preuve | Constat | Effort |
|---|---|---|---|---|
| TEST-05 | **P1** | CONFIRMÉ | Perf testée sur grilles synthétiques ⇒ gain inerte non détecté | 2 h |
| TEST-01 | **P1** | CONFIRMÉ | `--cov-fail-under=25` pour 66 % réels | 1 ligne |
| TEST-02 | **P1** | CONFIRMÉ | Aucun seuil de couverture frontend, 4,84 % réels | 1 ligne + 3 j |
| TEST-04 | P2 | CONFIRMÉ | 5 modules à 0 %, et la couverture prédit les P1 | continu |
| TEST-03 | P2 | CONFIRMÉ | Mypy sur 3/206 fichiers en `continue-on-error` | avec ARCH-04 |
| TEST-06 | P3 | CONFIRMÉ | eslint analyse `coverage/` | 5 min |
