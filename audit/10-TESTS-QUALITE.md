# Audit — Tests et qualité de code

> Périmètre : `tests/` (159 fichiers, 26 541 lignes, **1 723 tests**),
> `frontend/src/**/__tests__` + `frontend/e2e/`, `pytest.ini`, `ruff.toml`,
> `mypy.ini`, `.github/workflows/ci.yml`.

---

## Chiffres

| Mesure | Valeur |
|---|---|
| Tests Python (`def test_`) | 1 723 |
| Lignes de test / lignes de `app/` | 26 541 / 58 059 = **0,46** |
| Assertions par test (moyenne) | 1,9 |
| Usages de mock (`MagicMock`/`patch`/`monkeypatch`) | 315 |
| `skip` / `xfail` | 21 |
| Modules `app/` ≥ 40 lignes sans aucun test | **34** |
| Tests frontend (Vitest) | 6 fichiers |
| Suites e2e (Playwright) | 4 (`a11y`, `pages`, `visual`, `qw-backtest`) |
| Seuil de couverture frontend déclaré | 60 % (statements/branches/functions/lines) |
| Seuil de couverture Python déclaré | **aucun** |

---

## Tableau de bord

| # | Sévérité | Titre |
|---|----------|-------|
| T-01 | 🟠 Majeur | Les tests vérifient la présence des champs, pas leur signification |
| T-02 | 🟠 Majeur | 34 modules sans aucun test, dont plusieurs sur le chemin critique |
| T-03 | 🟠 Majeur | Aucun seuil de couverture Python, aucune mesure en CI |
| T-04 | 🟡 Moyen | Aucun test de propriété/invariant sur la comptabilité monétaire |
| T-05 | 🟡 Moyen | Les tests `slow` sont exclus de la CI sans être joués ailleurs |
| T-06 | 🟡 Moyen | `mypy` absent de la CI et configuré pour ne rien signaler (cf. S-06) |
| T-07 | 🔵 Mineur | Linters ciblés Python 3.12, runtime en 3.14 |

---

## T-01 🟠 Des tests qui constatent la forme, pas le fond

Cas d'école : `tests/test_monte_carlo.py:30-33`.

```python
def test_drawdown_stats_still_present():
    res = MonteCarlo(n_runs=100).run(_trades([10, -5, 8, -4]), 1000.0)
    assert res["max_dd_p95"] >= 0.0
    assert 0.0 <= res["prob_ruin_10pct"] <= 100.0
```

`max_dd_p95` est calculé comme `abs(np.percentile(max_dds, 95))` : la valeur
absolue rend l'assertion `>= 0.0` **vraie par construction**, quelle que soit
l'implémentation. Le test ne peut pas échouer.

C'est ainsi que **F-03** (le p95 de drawdown renvoie le meilleur cas au lieu du
pire) a survécu : 145 enregistrements sur 155 dans `data/oos_tracker.json`
portent un `max_dd_p95_pct` **inférieur** au drawdown effectivement observé,
ce qui est mathématiquement impossible pour un vrai p95 — et aucun test ne
regarde cette relation.

L'assertion qui aurait attrapé le bug tient en une ligne :

```python
def test_dd_p95_is_at_least_the_realised_drawdown():
    pnls = [10, -5, 8, -40, 8]
    res = MonteCarlo(n_runs=500).run(_trades(pnls), 1000.0)
    realised = abs(_max_dd(pnls, 1000.0))
    assert res["max_dd_p95"] >= realised     # un p95 majore l'échantillon
```

Le motif se répète ailleurs. Les tests du dépôt sont majoritairement des tests
de **contrat de forme** (le champ existe, le type est bon, la valeur est dans
un intervalle large). Ils protègent bien contre les régressions d'API et mal
contre les erreurs de sens.

**Correction** : pour chaque grandeur financière, écrire l'assertion qui
exprime sa **définition**, pas son domaine :

| Grandeur | Assertion de définition |
|---|---|
| `max_dd_p95` | `>= |max_drawdown|` de la série d'origine |
| `total_pnl` | `Σ trade["pnl"] == final_equity − initial_capital` (cf. F-01) |
| `sharpe` | `None` si moins de N observations (cf. F-02) |
| `by_strategy[s]["final_equity"]` | `== result.final_equity` en mono-stratégie (cf. F-08) |
| `RiskLedger` | `Σ release == Σ reserve` après un cycle complet (cf. L-05) |
| `borrow_cost` | `== 0` quand `notional <= own_funds` (cf. F-04) |

---

## T-02 🟠 34 modules sans test

Modules d'au moins 40 lignes dont le nom n'apparaît dans **aucun** fichier de
`tests/` (vérifié individuellement, y compris sous leur forme de classe) :

| Lignes | Module | Rôle |
|---|---|---|
| 779 | `app/api/services/scanner_service.py` | scan de marché — plus gros service non testé |
| 584 | `app/engine/recommendations.py` | recommandations affichées à l'utilisateur |
| 492 | `app/strategies/fear_momentum.py` | stratégie |
| 441 | `app/core/smc_structure.py` | structure de marché SMC |
| 384 | `app/live/health_mixin.py` | heartbeat, dead-man, reprise réseau |
| 365 | `app/strategies/scoring_statistique_opus_v2.py` | stratégie |
| 312 | `app/strategies/multi_tf_sr.py` | stratégie |
| 278 | `app/core/risk_notifier.py` | notifications de risque, persistance d'état |
| 275 | `app/core/smc_geometry.py` | géométrie SMC |
| 251 | `app/strategies/smart_trend_adx.py` | stratégie |
| 247 | `app/strategies/smart_money_plans.py` | plans de trade SMC |
| 246 | `app/engine/smc_signals_scan.py` | job de fond démarré par l'API |
| 245 | `app/strategies/smart_money_aux.py` | auxiliaires SMC |
| 232 | `app/ml/model_versioning.py` | versionnage des modèles |
| 231 | `app/live/signal_pipeline.py` | **collecte et ranking des signaux live** |
| 190 | `app/ml/overfitting_gate.py` | gate de qualité des modèles |
| 184 | `app/core/correlation_matrix.py` | corrélations entre symboles |
| 182 | `app/core/audit_log.py` | journal d'audit |
| 170 | `app/ml/backend/isotonic.py` | **calibration des probabilités** |
| 167 | `app/live/market_hours_mixin.py` | calendrier de marché, clôture de séance |
| 163 | `app/core/oos_tracker.py` | contrats Monte-Carlo, cône d'edge, verdicts |
| 133 | `app/core/smc_primitives.py` | primitives SMC |
| 111 | `app/core/smc_volume.py` | volume SMC |
| 101 | `app/core/sanitize.py` | nettoyage JSON de l'API |
| 79 | `app/core/backtest_history.py` | persistance de l'historique |
| … | + 9 autres | |

Trois entrées méritent une attention particulière :

- **`app/live/signal_pipeline.py`** : c'est le composant qui décide **quels
  signaux le bot exécute**, et dans quel ordre. Aucun test.
- **`app/ml/backend/isotonic.py`** : réimplémentation maison de PAV qui remplace
  scikit-learn et calibre les probabilités sur lesquelles les stratégies ML
  décident d'entrer. Aucun test. (La docstring dit « le code est testé » — il ne
  l'est pas.)
- **`app/core/oos_tracker.py`** : porte `_mc_contract`, `_edge_contract` et
  `_verdict`, c'est-à-dire les grandeurs qui **pilotent l'allocation des
  enveloppes** via la promotion par edge. Aucun test.

À l'inverse, la couverture est excellente sur `backtest`, `risk_*`, `execution`,
`indicators`, `smc`, `ml/*` et les routes API.

---

## T-03 🟠 Pas de seuil de couverture Python

Le frontend déclare des seuils explicites (`vitest.config.ts`) :

```js
thresholds: { statements: 60, branches: 60, functions: 60, lines: 60 }
```

Côté Python, `pytest.ini` ne déclare ni `--cov` ni de seuil, et
`.github/workflows/ci.yml` lance :

```yaml
run: python -m pytest tests/ -q --tb=short -m "not slow"
```

Aucune mesure de couverture n'est produite, donc l'évolution de T-02 n'est pas
suivie : un module ajouté sans test ne déclenche rien.

**Correction** : `pytest-cov` avec un seuil **non régressif** — mesurer la
valeur actuelle, la figer comme plancher, et l'augmenter par paliers. Un seuil
absolu trop haut d'emblée serait contourné.

---

## T-04 🟡 Aucun test de propriété sur la comptabilité

Les invariants monétaires du dépôt sont vérifiables mécaniquement sur des
entrées aléatoires — c'est le terrain naturel d'un `hypothesis` :

```python
from hypothesis import given, strategies as st

@given(entry=st.floats(1, 1e5), stop=st.floats(1, 1e5),
       size=st.floats(1e-6, 100), fee=st.floats(0, 0.01))
def test_close_pnl_est_monotone_en_prix_de_sortie(entry, stop, size, fee):
    ...
```

Propriétés candidates, toutes cassées ou fragiles d'après cet audit :

1. `Σ trade["pnl"] == final_equity − initial_capital` (F-01) ;
2. `RiskLedger` : après N `reserve` et N `release` dans un ordre quelconque,
   tous les agrégats reviennent à 0 (L-05) ;
3. `quantize_size(x, venue) <= x` toujours ;
4. `borrow_cost` croissante en `hours_held`, nulle en 0 ;
5. `compute_size(...)[1] <= env.max_notional` toujours ;
6. `plan_partial_targets` : `Σ fraction <= 1` et toutes les cibles du bon côté.

`hypothesis` n'est pas dans `requirements-dev.txt`.

---

## T-05 🟡 Les tests `slow` ne tournent nulle part

`pytest.ini` déclare le marqueur :

```ini
slow: tests longs (walk-forward, optimiseur, benchmarks) exclus par défaut en CI
```

et la CI les exclut (`-m "not slow"`). Aucun autre job ne les exécute — ni
nocturne, ni hebdomadaire, ni sur `main`.

Or ce sont précisément les tests qui couvrent **walk-forward et optimiseur**,
c'est-à-dire les zones où cet audit relève ses constats les plus lourds
(O-01 à O-03, B-03, B-04).

**Correction** : un job `schedule: cron` hebdomadaire qui lance
`pytest -m slow`. Le coût est nul en temps de développement.

---

## T-06/T-07

- **T-06** : voir [`09-SECURITE.md`](09-SECURITE.md) S-06. `mypy.ini` désactive
  toutes les vérifications utiles et le job n'existe pas en CI, alors que le
  frontend fait tourner `tsc --noEmit`.
- **T-07** : `ruff.toml` cible `py312`, `mypy.ini` `python_version = 3.12`,
  mais `requirements.txt` et le `Dockerfile` exigent **Python 3.14** (et la CI
  installe 3.14). Les linters analysent donc pour une version antérieure à
  l'exécution — les constructions propres à 3.13/3.14 ne sont pas comprises et
  les dépréciations ne sont pas signalées.

---

## Ce qui est solide

- **1 723 tests pour 58 000 lignes** est un rapport élevé pour un projet de ce
  type. La discipline de test est réelle : presque chaque correctif du
  changelog a son fichier de test dédié (`test_sprint0_critical_fixes.py`,
  `test_backtest_qw_fixes.py`, `test_legacy_slot_keys.py`…).
- **Tests de parité explicites** : `test_backtest_live_parity.py`,
  `test_execution_parity.py`, `test_generic_parity.py`. La préoccupation
  « backtest et live doivent calculer pareil » est instrumentée, pas seulement
  documentée. C'est rare et précieux — même si, comme le montre L-01, la parité
  des *formules* ne garantit pas la parité des *cadences d'échantillonnage*.
- **Tests de sécurité dédiés** : `test_api_auth_invariant.py`,
  `test_auth_xff.py`, `test_config_security.py`, `test_sec_hardening.py`,
  `test_config_strict_env.py`. L'anti-spoofing `X-Forwarded-For` et le refus de
  démarrage sans clé API sont verrouillés par des tests.
- **Tests de concurrence** : `test_risk_thread_safety.py`,
  `test_order_idempotency.py`. La bonne préoccupation, au bon endroit.
- **`test_legacy_redirects.py`** : verrouille la cohérence entre la table de
  redirections FastAPI et celle de `next.config.mjs`. C'est la bonne réponse à
  une duplication qu'on ne peut pas supprimer — un test plutôt qu'un
  commentaire.
- **CI à quatre jobs** (lint, test, security, frontend) avec `pip-audit` sur
  les dépendances **de production et de développement**, et
  `npm run lint` + `tsc --noEmit` + `vitest` côté frontend. Le commentaire
  expliquant que la suite Vitest « échouait à 7/9 sans que rien ne l'attrape »
  avant son ajout en CI montre que la leçon a été tirée.
- **Suite Playwright complète** (`a11y` avec axe-core, `pages`, `visual` avec
  instantanés, `qw-backtest`) : l'outillage e2e existe et couvre
  l'accessibilité. Il ne reste qu'à le faire tourner en CI.
- **Seuils de couverture frontend effectivement déclarés** à 60 % — un
  engagement chiffré, pas une intention.
