# 01 — Synthèse : trois causes, pas dix bugs

**10 constats, tous CONFIRMÉS par exécution** : 3 P1, 5 P2, 2 P3.
Détail et preuves dans [`02-CONSTATS.md`](02-CONSTATS.md).

Le sentiment de départ — « ce n'est pas ergonomique, il y a des erreurs ou des
ambiguïtés » — est fondé, et l'exemple donné était le bon fil. Mais les dix
constats ne sont pas dix accidents : ils sortent de **trois causes**, et c'est
à ce niveau qu'il faut agir.

---

## Cause A — Ces trois formulaires vivent hors du contrat typé

Le dépôt possède un contrat API→UI dérivé, pas recopié :
`scripts/gen_frontend_types.py` génère `frontend/src/types/generated.ts` depuis
les modèles Pydantic, et un test compare le fichier **entier** à la sortie du
générateur (`test_openapi_contracts.py:52`). Une dérive y est impossible.

**Les trois routes du Laboratoire sont hors de sa portée.** Elles prennent
leurs paramètres en query-string, sans corps Pydantic, et `/api/candles/stats`
n'a pas de `response_model`. Leurs types côté front sont donc **écrits à la
main** — et faux :

| Symptôme | Constat |
|---|---|
| 5 160 lignes de rebut dans « Cache bougies » | [`LAB-01`](02-CONSTATS.md#lab-01) |
| Entraînement poolé impossible — 422 puis 400 | [`LAB-02`](02-CONSTATS.md#lab-02) |
| `universe`, `compare_solo`, `max_symbols` absents du client | [`LAB-03`](02-CONSTATS.md#lab-03) |
| Complétude et trous reçus, jamais affichés | [`LAB-09`](02-CONSTATS.md#lab-09) |

`generated.ts` couvre 44 interfaces. `CandleStore`, `MlRecipe` et les
paramètres d'entraînement n'en font pas partie.

### Solution globale

Faire entrer ces routes dans le contrat existant, plutôt que corriger trois
types à la main :

1. Un corps Pydantic pour `POST /api/ml/train` (il existe déjà : `_TrainBody`
   — il suffit de l'exposer au générateur) et pour `POST /api/optimize/start`.
2. Un `response_model` sur `/api/candles/stats` — le modèle décrit déjà
   `symbol, tf, bars, from, to, size_kb, completeness, gaps`.
3. Étendre `_public_models` à ces modèles ; le test de dérive les couvre alors
   **sans une ligne de test supplémentaire**.

Le bénéfice n'est pas de corriger `LAB-01` : c'est que `LAB-01` **ne puisse
plus se produire**. Un type faux devient une erreur `tsc` en CI.

---

## Cause B — Le serveur décide, l'écran répète la demande

Le moteur ne fait pas ce que l'utilisateur a demandé — pour de bonnes raisons,
qu'il calcule et journalise. L'écran, lui, continue d'afficher la demande.

Le cas le plus net : **`format_budget`** (`opt_budget.py:84`) construit une
phrase destinée à l'opérateur, et sa propre docstring dit pourquoi —

> « le budget vaut ce qu'il vaut, sinon un opérateur qui a demandé 40 essais et
> en voit tourner 330 n'a aucun moyen de le comprendre. »

Cette phrase part dans le log. Le job reçoit `n_trials_budget`
(`auto_optimizer.py:382`). **Le front ne lit ni l'un ni l'autre.**

Résultat mesuré : le preset « Équilibré » annonce *60 trials — ~10 min*. Le
budget effectif médian est **135**, et **400 pour `smart_money`** — ×6,7.
**40 stratégies sur 41** tournent au-dessus de ce qui est annoncé
([`LAB-04`](02-CONSTATS.md#lab-04)).

Et trois mécanismes distincts peuvent faire s'arrêter le compteur avant le
nombre affiché — reproportionnement du budget, arrêt anticipé, essais en échec
— sans que l'UI en distingue **aucun** ([`LAB-05`](02-CONSTATS.md#lab-05)).
C'est l'explication complète du « 200 essais sur 400 » observé en usage : avec
`early_stop_patience: 15` posé par le preset, l'arrêt anticipé ne peut pas se
déclencher avant `n_trials // 2`, soit exactement **200 sur 400**.

Même forme ailleurs : `gate_source`, `wf_consistency`, `freshness_warning`,
`data_completeness` sont calculés pour l'opérateur et surfacés à moitié.

### Solution globale

**Un bandeau « ce que le serveur a décidé »**, un par exécution (job
d'optimisation, run de backtest, entraînement), alimenté par les champs que le
backend émet déjà. Pas un tooltip par champ : un endroit unique où lire l'écart
entre la demande et l'exécution.

Trois lignes suffisent au cas optimiseur : le budget effectif et sa raison, le
motif d'arrêt, le nombre d'évaluations qui soutiennent réellement le score.
Toutes existent côté serveur. Le SSE `/api/optimize/stream` est déjà branché
(`live-progress.tsx:36`) — il lui manque un champ, pas un mécanisme.

Corollaire : les presets doivent annoncer une fourchette calculée pour les
stratégies sélectionnées (`effective_n_trials` est une fonction pure,
appelable depuis une route), pas un nombre fixe que le moteur ignore.

---

## Cause C — Deux vocabulaires présentés comme un seul

Le domaine a deux unités, et elles sont **disjointes** — vérifié : aucun des
10 noms de recette n'est un nom de stratégie.

| | Unité | Clé de | Exemples |
|---|---|---|---|
| **Stratégie** | une classe `Strategy` | backtest, optimiseur | `opus_omnibus_v11`, `smart_money` |
| **Recette** | un contrat features + labels | registre de modèles | `omnibus_v4_multi`, `stat48_v5` |

L'onglet ML affiche les deux, l'un sous l'autre, tous deux étiquetés « ML »,
**sans dire ce qui les relie**. Puis le dialogue d'entraînement d'une recette
envoie le nom de la recette dans un champ nommé `strategy`
(`train-recipe-dialog.tsx:181`), que le backend re-résout en stratégie.

C'est exactement le point relevé en usage. Ce n'est pas un détail de nommage :
c'est ce qui rend le mode poolé inatteignable ([`LAB-02`](02-CONSTATS.md#lab-02)),
parce que le pooling n'existe **que** sur le chemin recette.

### Solution globale

Trancher l'unité par usage, et le dire à l'écran :

- **Entraîner** → une **recette**. C'est la clé du registre, et le seul chemin
  qui sache pooler. Le client envoie `recipe=`, jamais `strategy=`.
- **Backtester / optimiser** → une **stratégie**.
- **Le lien** est une donnée, pas un savoir implicite : `resolve_recipe_name`
  existe côté serveur. L'exposer permet à la table des stratégies d'afficher la
  recette qu'elle consomme, et à la liste des recettes d'afficher qui les
  utilise.

Une fois l'unité tranchée, [`LAB-06`](02-CONSTATS.md#lab-06) (« 1/14
entraînés » vs 3 entrées au registre) cesse d'être une contradiction : ce sont
deux comptages sur deux axes, qui doivent porter deux titres différents. Et
[`LAB-07`](02-CONSTATS.md#lab-07) — deux listes sans nom commun — devient une
seule table à deux colonnes.

---

## Ce que le backend offre déjà et que l'UI n'utilise pas

Recensé pendant l'audit — aucune de ces capacités ne demande de travail
serveur :

| Capacité | Route / champ | État côté UI |
|---|---|---|
| Entraînement sur un **univers** (`sbf120`) | `_TrainBody.universe` | absent — l'UI demande une liste manuelle de symboles |
| Comparaison **poolé vs solo** | `compare_solo` | contrôle présent, jamais transmis |
| **Complétude et trous** par dataset | `/api/candles/stats` | reçu, jamais affiché |
| **Justification du budget** d'essais | `n_trials_budget` | jamais lu |
| **Chevauchement** d'entraînement | `/api/ml/registry/overlaps` | utilisé sur `/models` seulement |

---

## Ordre proposé

1. **Cause A d'abord** — c'est l'unique changement qui empêche la récidive, et
   il rend `LAB-01`, `LAB-02` et `LAB-03` structurellement impossibles.
2. **Cause C ensuite** — elle conditionne l'ergonomie de l'onglet ML entier.
3. **Cause B enfin** — c'est de l'ajout, pas de la correction : sans risque,
   mais sans effet sur les erreurs.

Deux constats sont indépendants des trois causes et peuvent partir seuls :
[`LAB-08`](02-CONSTATS.md#lab-08) (durée négative, un formateur employé à
contre-emploi) et [`LAB-10`](02-CONSTATS.md#lab-10) (`filterMl` non transmis à
une table). Ce sont les deux seuls correctifs de ce rapport qui tiennent en
quelques lignes — les huit autres demandent la décision de structure.
