# Audit — Architecture et dette technique

> Périmètre : organisation des modules, couplage, duplication, code mort,
> cohérence des conventions. Vue transverse ; les constats fonctionnels sont
> dans les rapports par domaine.

---

## Vue d'ensemble

```
app/          58 059 lignes   190 fichiers Python
frontend/     46 413 lignes   172 fichiers (122 .tsx)
tests/        26 541 lignes   159 fichiers
docs/         35 332 lignes    33 fichiers .md   (+ CHANGELOG.md : 4 435 lignes)
strategies/    3 657 lignes    41 YAML
recipes/         787 lignes    10 YAML
```

Découpage de `app/` :

| Paquet | Fichiers | Rôle |
|---|---|---|
| `app/core/` | 59 | primitives partagées (risque, exécution, indicateurs, SMC, données) |
| `app/strategies/` | 45 | stratégies |
| `app/api/` | 27 | FastAPI (20 routers + middleware/helpers/state/schemas) |
| `app/engine/` | 20 | backtest + result + lifecycle, optimiseur, scanner, walk-forward |
| `app/live/` | 15 | trader live et ses mixins |
| `app/ml/` | 22 | apprentissage, registre, politique de promotion |

Le découpage est **cohérent et lisible**. Les responsabilités sont réelles, pas
nominales : `app/core` ne dépend jamais de `app/engine` (vérifié), les mixins
du live sont documentés avec leurs prérequis d'instance, et la circularité
`backtest ↔ walk_forward` est gérée par un import en fin de module avec la
raison écrite.

---

## Tableau de bord

| # | Sévérité | Titre |
|---|----------|-------|
| X-01 | 🟠 Majeur | Six modules écrits, testés… et jamais appelés en production | ✅ résolu — câblés ou `core/deflated_sharpe.py` supprimé |
| X-02 | 🟠 Majeur | 80 % de duplication entre `scoring_statistique_opus_v4` et `_v5` |
| X-03 | 🟡 Moyen | Trois fichiers de plus de 1 200 lignes portent le cœur du système | ✅ backtest découpé ; restent `optimizer_search.py` et `smart_money_signals.py` |
| X-04 | 🟡 Moyen | Deux implémentations du Deflated Sharpe, deux du Monte-Carlo | ✅ `_sf` unifié ; DSR = Bailey ; MC double usage légitime |
| X-05 | 🟡 Moyen | Constantes dupliquées malgré des modules de source unique |
| X-06 | 🟡 Moyen | 45 stratégies dont 12 sont des variantes de 4 familles |
| X-07 | 🔵 Mineur | Le changelog fait 244 Ko et l'audit ne peut pas s'y fier | ✅ bandeau → `audit/15` |

---

## X-01 🟠 Du code écrit, testé, et jamais appelé

Le dépôt contient plusieurs modules complets, soignés, couverts par des tests…
et inertes sur tous les chemins de production. C'est la forme de dette la plus
coûteuse : elle donne l'illusion qu'une garantie existe.

| Module | Taille | État | Détail |
|---|---|---|---|
| `frontend/src/lib/i18n.tsx` | — | ✅ branché | U-01 — nav + sélecteur FR/EN |
| `engine/backtest_risk_gate.py` | — | ✅ câblé | `realistic_risk=True` sur opt / WF / FT (B-07) |
| `opt_scoring.deflated_sharpe_ratio` | — | ✅ câblé | Bailey & LdP ; `core/deflated_sharpe.py` (heuristique) **supprimé** |
| `Backtester.run_dual_pass` | — | ✅ câblé | `compute_jobs` / `/api/backtest` |
| `Envelope.venue_envelope` | — | ✅ lu | F-05 + R-02 (`RiskLedger`) |
| `ml/overfitting_gate.py` | — | ✅ diagnostic | `auc_floor=AUC_WEAK` (M-05) ; overlap invalide le frozen (M-06) |

À quoi s'ajoute une catégorie voisine : **du code qui s'exécute mais ne
conclut rien**.

- `ml_registry.overlaps` détecte une fuite train/test et se contente d'un
  WARNING — le backtest continue et son résultat est utilisé (M-06) ;
- `test_risk_diagnostics.test_two_symbols_at_full_exposure_overflow_the_venue_envelope`
  **teste le débordement** de l'enveloppe venue au lieu de le prévenir (F-05) ;
- `score_factor` est calculé et journalisé comme « Sizing = X % » alors qu'il
  n'entre dans aucun calcul de taille (L-10).

**Recommandation** : pour chaque entrée, trancher explicitement — brancher ou
supprimer. Un module inerte qui reste six mois devient un module que personne
n'ose retirer parce que « il doit bien servir à quelque chose ».

---

## X-02 🟠 `scoring_statistique_opus_v5` est `_v4` à 80 %

Mesure : **478 des 600 lignes significatives** de
`scoring_statistique_opus_v5.py` sont présentes à l'identique dans
`scoring_statistique_opus_v4.py`.

Les deux fichiers font respectivement 780 et 740 lignes. Un correctif appliqué à
l'un ne l'est pas à l'autre — et rien ne le signale.

Le cas est différent des variantes `_no_ml`, qui sont de vraies petites classes
dérivées (16–19 % de recouvrement, 68 à 92 lignes) : celles-là sont un bon
motif.

**Correction** : extraire le tronc commun (`scoring_statistique_opus_base.py`)
et ne garder dans chaque version que ce qui diffère. Le dépôt sait faire — c'est
exactement ce que font `smart_money_aux` / `smart_money_params` /
`smart_money_plans` / `smart_money_signals`, extraits de `smart_money.py`.

---

## X-03 🟡 Trois fichiers concentrent le cœur

| Lignes (audit) | Fichier | État #244 |
|---|---|---|
| 1 809 → ~686 | `app/engine/backtest.py` | `run()` + diagnostics ; import `Backtester, BacktestResult` inchangé |
| — → ~410 | `app/engine/backtest_result.py` | métriques + sérialisation |
| — → ~646 | `app/engine/position_lifecycle.py` | mixin : `_close_at` / `_try_enter` / scale-in (`RiskLedger`) |
| 1 312 | `app/engine/optimizer_search.py` | **non découpé** |
| 1 212 | `app/strategies/smart_money_signals.py` | **non découpé** |
| 1 559 → ~1 015 | `optimizer-view.tsx` | `JobCard` / `LiveProgress` extraits |
| 1 488 → ~175 | `app/lab/page.tsx` | shell + `dynamic` ; onglet Backtest dans `backtest-view.tsx` |

Ce ne sont pas des fourre-tout : chacun a une cohérence interne réelle et une
documentation d'intention dense. Mais leur taille a un coût mesurable dans cet
audit : les constats **B-05** (`min_notional` vérifié avant `partial_fill`) et
**B-06** (le pyramidage échappe à quatre garde-fous) sont des divergences entre
deux endroits **du même fichier**, séparés par 200 lignes.

Découpages naturels de `backtest.py` — **faits** (#244) :

- `BacktestResult` + `_group_metrics` → `backtest_result.py` ;
- `_close_at` / `_close_partial_at` / `_manage_open_position` / `_try_enter` →
  `position_lifecycle.py` — le pendant des mixins du live ;
- `run()` + diagnostics → `backtest.py`.

`_try_enter` et le scale-in passent tous deux par `RiskLedger.reserve`
(R-02). Restent à découper : `optimizer_search.py`, `smart_money_signals.py`.

---

## X-04 🟡 Deux implémentations pour un même concept

| Concept | Implémentation A | Implémentation B | Câblée |
|---|---|---|---|
| Deflated Sharpe | `core/deflated_sharpe.py` (heuristique maison) | `engine/opt_scoring.deflated_sharpe_ratio` (Bailey & LdP) | **A** |
| Monte-Carlo | `engine/monte_carlo.MonteCarlo` (permutation + bootstrap) | `core/oos_tracker._mc_contract` / `_edge_contract` (bootstrap de moyenne) | les deux, pour des questions différentes |
| Warmup | `backtest._MIN_WARMUP=210` | `is_oos.WARMUP_BARS_DEFAULT=210`, `walk_forward.WARMUP=220`, `forward_test._WARMUP_BARS=250` | les quatre |
| Plancher d'AUC | `policy.auc_floor=0.55` | `overfitting_gate.AUC_GOOD=0.60` | les deux |
| `_sf` (safe float) | `backtest.py:39` | `monte_carlo.py:27`, `walk_forward.py:23` | dupliqué 3× avec le commentaire « pour éviter un import circulaire » |

Le cas Monte-Carlo est légitime (deux questions différentes, bien documentées).
Les autres sont de la divergence latente.

Pour `_sf`, la justification (cycle d'import) est réelle mais le remède ne l'est
pas : la fonction n'a aucune dépendance et appartient à `app/core/sanitize.py`,
qui existe déjà et ne crée aucun cycle.

---

## X-05 🟡 Constantes dupliquées malgré les modules de source unique

Le dépôt a créé plusieurs modules explicitement destinés à supprimer les
duplications : `core/is_oos.py`, `core/stats_thresholds.py`,
`core/timeframes.py`, `core/execution.py`, `core/risk_curve.py`. La démarche est
la bonne — et la docstring de `is_oos.py` raconte précisément le problème
qu'elle résout (« `auto_optimizer` dupliquait `WARMUP = 210` + `0.65` en dur à
TROIS endroits »).

Deux consommateurs lui ont pourtant échappé (`walk_forward.py:63`,
`forward_test.py:43`), et une table de conversion timeframe→minutes locale
subsiste dans `api/helpers.py:169` alors que `core/timeframes.TF_MINUTES` est la
source canonique — la même erreur exacte que celle corrigée dans `backtest.py`
(commentaire ligne 50-52 : « l'ancienne table locale (9 clés) renvoyait le
défaut pour 6h/8h/12h »).

**Correction** : un test qui recense les littéraux `210`, `220`, `250`, `0.35`,
`0.65` dans `app/` et échoue s'ils n'importent pas la constante. C'est plus
efficace qu'une relecture.

---

## X-06 🟡 45 stratégies, 4 familles

```
scoring_statistique_opus   ×4  (base, v2, v4, v5)
opus_omnibus               ×3  (v7, v11, v12)
opus_omnibus_v11           ×3  (followsetup, followsetup_no_ml, no_ml)
opus_omnibus_v10           ×2  (no_ml, retrained)
```

Soit **12 modules pour 4 idées**, plus `opus_omnibus_v8_no_ml`,
`opus_stat_retrained_v4`, `opus_omnibus_v10_retrained`… Les 41 YAML de
`strategies/` suivent, chacun avec ses `optimizer_results`.

Le coût est concret :

- l'optimiseur et le scanner itèrent sur toutes ; `backtest_history.json`
  contient 252 entrées pour 45 stratégies × TF ;
- une correction du moteur de features doit être vérifiée sur 12 variantes ;
- `_discover_strategies` lit 45 fichiers par minute (P-09) ;
- l'utilisateur choisit dans une liste de 45 noms dont il ne peut pas déduire la
  généalogie.

Cohérence vérifiée : **les 41 YAML ont tous leur module Python**, et les 4
modules sans YAML (`smart_money_aux`, `_params`, `_plans`, `_signals`) sont bien
des auxiliaires sans `class Strategy` — correctement exclus par
`_module_defines_strategy` (`api/helpers.py:122`). Ce point est propre.

**Recommandation** : marquer les variantes obsolètes (`deprecated: true` dans
le YAML), les exclure de l'optimisation automatique et de la liste de l'UI, et
les supprimer après une période de grâce. `git` conserve l'historique.

---

## X-07 🔵 Le changelog est illisible pour un audit

`CHANGELOG.md` : **244 Ko, 4 435 lignes**. `docs/` : 33 fichiers, 35 332 lignes.

Conformément à la demande, cet audit n'a lu ni l'un ni l'autre. La constatation
utile est structurelle : la documentation d'un dépôt de 58 000 lignes de code
Python en fait 35 000, soit un ratio de 0,6 — plus élevé que le ratio
tests/code (0,46).

Ce n'est pas mécaniquement un défaut. Mais cet audit a rencontré plusieurs cas
où **le commentaire décrit un état qui n'est plus vrai** :

- `isotonic.py` : « Le code est correct et testé » — aucun test ne le couvre ;
- `monte_carlo.py` : la docstring décrit le bootstrap comme corrigé ; les
  données persistées montrent 145 enregistrements produits par l'ancien calcul,
  sans marqueur de version ;
- `api.ts` : « le cookie HttpOnly `api_key` (posé par les pages web, cf.
  `app/api/main.py::_tpl`) » — `_tpl` a été supprimé avec Jinja2, c'est
  maintenant le proxy Next qui le pose ;
- `position_open_mixin.py:422` : le log « Sizing = X % » décrit un mécanisme
  retiré de `compute_size`.

La densité de commentaires est par ailleurs une **force réelle** de ce dépôt
(voir plus bas). Le risque n'est pas d'en avoir trop, c'est de ne pas avoir de
mécanisme qui les fasse échouer quand ils deviennent faux.

---

## Ce qui est solide

- **Séparation en couches respectée** : `app/core` ne dépend d'aucun module
  `app/engine` ni `app/live` (vérifié). C'est une contrainte réelle, tenue,
  et documentée à l'endroit où elle a coûté quelque chose (`oos_tracker.py` :
  « l'exécution du forward-test vit dans `app/engine/forward_test.py` ;
  `app/core` ne dépend d'aucun module `app/engine` »).
- **Découpage en mixins du `LiveTrader`** (ARCH-003) : chaque mixin déclare en
  docstring les attributs d'instance qu'il attend. C'est la bonne façon de
  rendre un mixin relisable sans son hôte.
- **Source unique des formules monétaires** (`core/execution.py`), consommée
  par le backtest **et** le live, avec un test de parité dédié. C'est
  l'architecture qui rend la question « pourquoi le live diverge-t-il ? »
  répondable.
- **Compatibilité ascendante gérée explicitement** : ré-exports en fin de
  module (WF / MC après la classe `Backtester`, pour casser le cycle), alias
  `RiskManager = RiskGate`, `StrategyOptimizer = OptimizerSearchEngine`. Les
  refactorings n'ont pas cassé les appelants.
- **Configuration découpée par responsabilité** (`config.yaml` → 5 fichiers
  alignés sur les briques du code), avec **refus de chargement si une section
  est déclarée deux fois** — « sinon l'ordre de lecture déciderait en
  silence ». Et l'écriture depuis l'UI est routée vers le fichier propriétaire
  (`yaml_io.update_config_yaml`). C'est du bon design de configuration.
- **Validation de configuration au démarrage** (`_validate_venues`,
  `_validate_risk_envelopes`) qui **refuse de démarrer** sur une incohérence
  (venue par défaut manquante, `symbol_risk_pct < profile`, `web.host: 0.0.0.0`
  sans clé). Échouer tôt plutôt que trader mal.
- **Qualité des commentaires d'intention** : le dépôt explique
  systématiquement *pourquoi*, avec le chiffre du bug corrigé (« Sharpe affiché
  9,5 », « 749 s vs 287 s », « ~30 %/an d'intérêt fictif », « geler 20/21
  paramètres à chaque run »). Cet audit s'appuie très largement sur cette
  qualité — sans elle, comprendre `optimizer_search.py` aurait pris des jours.
  C'est, de loin, la meilleure propriété du dépôt.
- **Absence de dépendance lourde inutile** : scikit-learn et scipy ont été
  retirés au profit d'implémentations numpy ciblées (AUC par rang, isotonie
  PAV). Le choix est assumé et documenté.
