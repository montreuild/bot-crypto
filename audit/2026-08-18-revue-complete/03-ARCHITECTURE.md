# 03 — Architecture, couplage et dette structurelle

Source : graphe de code reconstruit intégralement — **669 fichiers, 6 490 nœuds,
64 181 arêtes, 487 flux d'exécution, 15 communautés**.

**Jugement d'ensemble.** L'architecture est saine dans sa forme : couches nettes
(`core` → `engine` / `live` → `api`), aucune dépendance de `app/` vers `scripts/` ou
`research/`, une source unique pour les formules monétaires, et un découpage en mixins
qui a permis d'extraire `position_lifecycle`, `opt_bayesian`, `opt_budget`, `opt_freeze`,
`backtest_result` et `compute_pool` dans la fenêtre auditée. Le sens des dépendances est
respecté partout.

Trois tensions structurelles subsistent, et elles expliquent une bonne part des constats
des autres rapports.

---

## ARCH-01 — `Backtester` est le chokepoint du dépôt et son contrat n'est pas typé

**Sévérité P1 · CONFIRMÉ (graphe)**

| Nœud | Entrant | Sortant | Total |
|---|---:|---:|---:|
| `Backtester.run` | **86** | 161 | **247** |
| `Backtester` (classe) | **91** | 60 | 151 |

86 appelants pour une seule méthode de 405 lignes. Tout passe par elle : l'API
(`run_backtest`), l'optimiseur (`optimizer_search._eval`, deux appels par essai), le
walk-forward (deux `Backtester` par fold), le forward-test, le replay, le lifecycle.

Le contrat de sortie est un `BacktestResult` dont `to_dict()` produit **45 clés**, dont
neuf dictionnaires imbriqués (`by_strategy`, `by_setup`, `by_module`, `by_exit_reason`,
`by_exit_leg`, `by_structure_state`, `by_sequence_type`, `by_tier`, `by_target_class`),
plus `diagnostics`, `ml_info`, `cost_model`, `realistic_risk_diagnostics` et la liste
complète des trades. Aucun de ces champs n'est typé ni validé.

C'est la racine commune de plusieurs constats :

- **FIN-01** — un champ (`pnl`) dont la sémantique a changé sans que rien ne le détecte,
  parce qu'aucune signature ne le décrit.
- **API-01** — 99 routes sans `response_model` : ce dict traverse le réseau tel quel.
- **FE-03** — 1 462 lignes de types recopiées à la main côté client pour en décrire la
  forme.
- **BT-02** — `realistic_risk` est dans le dict mais `_fold_summary` ne le remonte pas :
  personne ne remarque que deux résultats comparés n'ont pas la même économie.

**Correction** : faire de `BacktestResult` une dataclass typée (ou un modèle Pydantic), et
dériver `to_dict()` de la structure au lieu de l'écrire à la main. Cela résout FIN-01 par
construction (le champ `entry_fees` ne peut plus être confondu avec `fees`), alimente
API-01 sans travail supplémentaire, et rend FE-03 générable.

**Effort** : 2 à 3 jours, mais c'est le changement qui a le meilleur rendement du rapport :
un seul chantier ferme quatre constats.

---

## ARCH-02 — Sept fonctions de plus de 400 lignes, dont trois sur des chemins de décision

**Sévérité P2 · CONFIRMÉ (graphe)**

| Lignes | Fonction | Fichier |
|---:|---|---|
| 656 | `SmartReplayView` | `frontend/src/components/views/smart-replay-view.tsx:79` |
| 609 | `useSmartGraphChart` | `frontend/src/hooks/use-smart-graph-chart.ts:21` |
| 551 | `SmartGraphView` | `frontend/src/components/views/smart-graph-view.tsx:34` |
| 521 | `BacktestResults` | `frontend/src/components/views/backtest-results.tsx:146` |
| 517 | `OptimizerConfigForm` | `frontend/src/components/optimizer/optimizer-config-form.tsx:22` |
| 516 | `BacktestView` | `frontend/src/components/views/backtest-view.tsx:41` |
| 483 | `CompareView` | `frontend/src/components/views/compare-view.tsx:121` |
| 419 | `Strategy.score` | `app/strategies/fear_momentum.py:73` |
| 411 | `analyze` | `app/core/smc_structure.py:30` |
| **405** | **`Backtester.run`** | `app/engine/backtest.py:241` |
| 353 | `Strategy.score` | `app/strategies/composite_score.py:255` |
| **317** | **`AutoOptimizer._run_one_job`** | `app/engine/auto_optimizer.py:490` |
| **262** | **`_manage_open_position`** | `app/engine/position_lifecycle.py:189` |

Les quatre en gras portent des décisions financières. Ce n'est pas une remarque de style :

- `_manage_open_position` (262 lignes) enchaîne MAE/MFE, sortie temporelle, sortie
  anticipée, TP fixe, stop intrabar, jambes partielles, point mort, trailing et
  pyramidage. **Trois des constats du rapport financier** (FIN-02, FIN-06, FIN-08) sont
  dans cette seule fonction, à des dizaines de lignes les uns des autres. Une fonction de
  cette taille est un endroit où les invariants ne se voient plus.
- `_run_one_job` (317 lignes) contient toute la chaîne de gates de l'optimiseur —
  **OPT-04 et OPT-05** y cohabitent sans que leur interaction soit visible.

L'effort de découpage est engagé et visible côté frontend (`backtest-view` /
`backtest-results`, `smart-graph-view` / `-tables` / `-helpers` séparés dans la fenêtre
auditée) — mais les fonctions résultantes restent au-dessus de 500 lignes. Le découpage
a séparé les *fichiers*, pas les *fonctions*.

**Correction prioritaire** : extraire de `_manage_open_position` trois fonctions pures —
`_evaluer_sorties(position, barre) → RaisonDeSortie | None`, `_appliquer_jambes(…)`,
`_mettre_a_jour_trailing(…)`. Chacune devient testable isolément, ce qui est exactement
ce qui manque aujourd'hui aux trois constats FIN correspondants.

---

## ARCH-03 — La couche `core` est appelée de partout et ses 15 communautés convergent vers elle

**Sévérité P2 · Observation structurelle**

Couplage inter-communautés (hors tests) :

| Source → cible | Arêtes |
|---|---:|
| `routes-ml` → `core-fetch` | 104 |
| `core-fetch` → `live-position` | 105 |
| `core-fetch` → `strategies-strategy` | 82 |
| `core-fetch` → `engine-job` | 70 |
| `routes-ml` → `engine-job` | 55 |
| `engine-job` → `ml-predict` | 14 |

`app/core` compte **15 728 lignes réparties sur 57 modules**, dont onze pour le seul
domaine SMC (`smc.py`, `smc_geometry`, `smc_primitives`, `smc_quality`, `smc_sessions`,
`smc_state`, `smc_structure`, `smc_volume`) et neuf pour le risque (`risk_curve`,
`risk_diagnostics`, `risk_envelope`, `risk_gate`, `risk_ledger`, `risk_notifier`,
`risk_sizer`, `risk_state`).

Ces deux familles sont cohésives et mériteraient d'être des sous-paquets
(`app/core/smc/`, `app/core/risk/`) plutôt que 20 modules à plat parmi 57. Le bénéfice
n'est pas cosmétique : il rend visible qu'un module de risque ne devrait jamais importer
un module SMC, ce qu'aucune barrière n'empêche aujourd'hui.

Le graphe montre aussi que `core-fetch` a la **cohésion la plus faible** de toutes les
communautés Python (0,12 contre 0,15 pour `strategies` et 0,13 pour `live`) : c'est le
signe d'un paquet qui agrège des choses sans rapport, pas d'un noyau.

---

## ARCH-04 — 1 084 erreurs mypy dont la CI n'en regarde que trois fichiers

**Sévérité P2 · CONFIRMÉ (exécution)**

```
$ python -m mypy app --ignore-missing-imports
Found 1084 errors in 120 files (checked 206 source files)
```

La CI (`.gitlab-ci.yml`) exécute :

```yaml
mypy:
  allow_failure: true
  script:
    - python -m mypy app/core/sanitize.py app/core/ohlcv_gaps.py app/ml/overfitting_gate.py
```

**Trois fichiers sur 206**, et en `allow_failure: true`. Le typage n'est donc pas une
contrainte, c'est une intention.

Répartition des erreurs, par famille :

| Occurrences | Famille |
|---:|---|
| 96 | `"PositionManageMixin" has no attribute …` |
| 59 | `"PositionOpenMixin" has no attribute …` |
| 52 | `"PositionLifecycleMixin" has no attribute …` |
| 47 | `"PositionCloseMixin" has no attribute …` |
| 47 | `"OptimizerBayesianMixin" has no attribute …` |
| 43 | `"AutoOptMixin" has no attribute …` |
| ~96 | `Incompatible default for argument` (`params: dict = None`) |
| 170 | `implicit Optional` |

**Les 344 premières ont toutes la même cause** : les mixins accèdent à des attributs
fournis par la classe hôte (`self.exchange`, `self.cfg`, `self.risk`, `self.ledger`…) que
rien ne déclare. `position_manage_mixin.py:15-22` liste ces attributs **en commentaire** :

```
Requiert que l'instance possède (fournis par LiveTrader.__init__) :
  self.exchange, self.cfg, self.risk, self.notif, self.scanner
  self.open_positions, self._positions_lock, ...
```

Un `Protocol` traduirait ce commentaire en contrat vérifiable et **supprimerait un tiers
des erreurs mypy d'un coup**, tout en donnant l'auto-complétion et la détection de fautes
de frappe sur ces attributs. C'est le meilleur rapport effort/effet du fichier.

Les ~266 suivantes (`implicit Optional`, `dict = None`) sont mécaniques : `ruff --fix`
avec `RUF013`, ou `no_implicit_optional`.

**Correction, par étapes** :
1. Un `Protocol` par famille de mixins (live, optimiseur) — ~60 lignes, −344 erreurs.
2. `RUF013` en autofix — −266 erreurs.
3. Passer la CI de 3 fichiers à `app/core` + `app/engine`, `allow_failure: false`.

---

## ARCH-05 — 89 blocs `except: pass` sur 125 `except Exception`

**Sévérité P2 · CONFIRMÉ (recensement)**

```
except Exception dans app/ : 125
suivis d'un pass           :  89   (71 %)
```

Beaucoup sont légitimes (publication WebSocket, métriques, diagnostics). Le sous-ensemble
préoccupant est identifié dans `07-LIVE-EXECUTION.md` (LIVE-06) : le rollback d'ouverture
de position, la restauration, la clôture.

Le motif à généraliser est celui déjà utilisé aux bons endroits :
`logger.error(contexte)` + notification opérateur quand l'échec laisse un état incohérent.

---

## Ce qui a été vérifié et tenu

- **Aucune dépendance de `app/` vers `scripts/` ou `research/`** :
  `grep -rE '^\s*(from|import)\s+(scripts|research)' app/ cli.py` → vide. L'inverse
  existe 41 fois, ce qui est le sens attendu. Le couplage `engine-job → scripts-cfg`
  (69 arêtes) signalé par le graphe est donc entièrement dirigé de `scripts` vers `app`.
- **Source unique des formules monétaires** — `app/core/execution.py`, consommée par les
  quatre sites de clôture (2 backtest, 2 live). La parité est un objectif tenu dans sa
  structure, même si deux écarts subsistent (FIN-04, FIN-07).
- **Découpage actif** — la fenêtre auditée a créé 16 modules : `position_lifecycle`,
  `backtest_result`, `opt_bayesian`, `opt_budget`, `opt_freeze`, `compute_pool`,
  `compute_jobs`, `smc_patterns/{composites,journal,stats}`, `ohlcv_gaps`, `splitting`,
  `threads`, `retrain`, `bt_predictions`, `smart_money_setups`. C'est une vraie dynamique
  de réduction de la dette, pas une intention.
- **Isolation du calcul lourd** — `compute_pool` (`ProcessPoolExecutor` spawn) sort les
  calculs du thread API, avec des workers `pickleables` séparés dans `compute_jobs`.
- **Convention de nommage et de langue** homogène (français pour les commentaires et les
  identifiants métier récents, anglais pour l'API technique). Cohérent d'un bout à l'autre.

---

## Récapitulatif

| ID | Sévérité | Preuve | Constat | Effort |
|---|---|---|---|---|
| ARCH-01 | **P1** | CONFIRMÉ | `Backtester.run` : 86 appelants, contrat de 45 clés non typé | 2-3 j |
| ARCH-02 | P2 | CONFIRMÉ | 13 fonctions > 260 lignes, 4 sur des chemins financiers | 2 j |
| ARCH-04 | P2 | CONFIRMÉ | 1 084 erreurs mypy, CI sur 3 fichiers en `allow_failure` | 1 j |
| ARCH-03 | P2 | CONFIRMÉ | `app/core` : 57 modules à plat, cohésion 0,12 | 1 j |
| ARCH-05 | P2 | CONFIRMÉ | 89 `except: pass` sur 125 | avec LIVE-06 |
