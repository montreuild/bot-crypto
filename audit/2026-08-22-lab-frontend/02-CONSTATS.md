# 02 — Constats

10 constats, **tous CONFIRMÉS par exécution**. Chacun porte la vérification
qui l'établit ; aucun ne repose sur la seule lecture.

Cause à laquelle il se rattache : [`A`](01-SYNTHESE.md#cause-a--ces-trois-formulaires-vivent-hors-du-contrat-typé) contrat typé ·
[`B`](01-SYNTHESE.md#cause-b--le-serveur-décide-lécran-répète-la-demande) décision serveur non remontée ·
[`C`](01-SYNTHESE.md#cause-c--deux-vocabulaires-présentés-comme-un-seul) vocabulaire.

---

## LAB-01 — Le cache de bougies affiche 5 160 lignes de rebut (P1, CONFIRMÉ, cause A)

**Fichiers** : `frontend/src/types/index.ts:242`,
`frontend/src/lib/api.ts:494`, `frontend/src/components/views/ml-view.tsx:99`,
`app/api/routes/ml.py:51`.

Le backend rend une **liste plate** :

```json
{"store": [{"symbol": "AC.PA", "tf": "15m", "bars": 2039, "from": null,
            "to": null, "size_kb": 30.1, "completeness": 1.0, "gaps": 0}, …]}
```

Le front la type en **objet imbriqué** :

```typescript
export type CandleStore = Record<string, Record<string, CandleStoreEntry>>;
```

`CandlesStatsTable` fait alors `Object.entries(store)` sur un tableau : les
**indices** deviennent des symboles, et les **clés** de chaque dict deviennent
des timeframes.

**Scénario d'échec** — 645 datasets en cache, 8 clés par entrée. L'utilisateur
ouvre `/lab?tab=ml` et voit un tableau de `645 × 8 = 5 160` lignes intitulées
`0 | bars | 0 | — | — | —`, `0 | completeness | 0 | — | — | —`, … Aucune ne
porte de donnée. Le tableau réel — 645 lignes utiles — n'apparaît nulle part.

**Vérification** — appel HTTP réel : `.store` est de type `list`, longueur
645. Puis comptage dans le navigateur sur le backend Docker :

```
{"lignes": 5160, "entete": "Symbole|TF|Bougies|Première|Dernière|Taille"}
```

`5160 = 645 × 8`. Le rendu n'est pas dégradé, il est faux de bout en bout.

---

## LAB-02 — L'entraînement poolé est inatteignable depuis l'UI (P1, CONFIRMÉ, causes A + C)

**Fichiers** : `frontend/src/components/cards/train-recipe-dialog.tsx:181` et
`:190`, `app/api/routes/ml.py:353`.

Le dialogue propose un mode « pool » (multi-symboles, ML-16) avec ses
contrôles : liste de symboles, `max_symbols`, `compare_solo`. Il échoue de
**deux** façons indépendantes.

**Premier échec — le type.** L'UI envoie `symbols` en chaîne :

```typescript
params.symbols = poolSymbols.split(',').map((s) => s.trim()).filter(Boolean).join(',');
```

`_TrainBody.symbols` est un `Optional[List[str]]`.

**Second échec — le vocabulaire.** L'UI envoie le nom de la recette dans le
champ `strategy` (`:181`), jamais dans `recipe`. Or le pooling n'existe **que**
sur le chemin recette, et le backend le refuse explicitement (`ml.py:353`).

**Scénario d'échec** — l'utilisateur choisit `stat48_v5`, bascule en mode pool,
saisit `BTC/USDC,ETH/USDC`, clique « Entraîner ». Il reçoit une erreur de
validation opaque. Corrigerait-il le type qu'il recevrait un refus métier.

**Vérification** — les trois payloads rejoués sur le conteneur :

| Payload | Réponse |
|---|---|
| tel que l'UI l'envoie (`symbols` chaîne) | **422** `Input should be a valid list` |
| `symbols` en liste, mais `strategy=` | **400** « l'entraînement poolé exige une recette » |
| `recipe=` + `symbols` en liste | **200** `{"job_id": "e87b1113b170"}` |

Le chemin correct fonctionne. L'UI ne l'emprunte pas.

---

## LAB-03 — Le client d'entraînement ignore la moitié du contrat (P2, CONFIRMÉ, cause A)

**Fichier** : `frontend/src/lib/api.ts:527`.

`startMLTrain` déclare 7 paramètres. `_TrainBody` en accepte 12. Manquent :
`recipe`, `symbols`, `universe`, `max_symbols`, `compare_solo`.

Le dialogue contourne le type en construisant `const params: any = {…}` — ce
qui désactive la seule vérification qui aurait signalé `LAB-02` à la
compilation.

**Scénario d'échec** — `universe: "sbf120"` permet d'entraîner sur 120 titres
sans en saisir un seul. Le commentaire du modèle le dit : « la seule
[alternative] praticable depuis l'UI : personne ne saisit 120 mnémoniques à la
main ». Le champ n'existe pas dans l'UI, qui propose exactement cette saisie
manuelle.

**Vérification** — lecture croisée du modèle Pydantic et du client, plus le
`422` de `LAB-02` qui démontre que le `any` laisse passer un type faux.

---

## LAB-04 — Les presets d'optimisation annoncent un budget que le moteur ne respecte pas (P1, CONFIRMÉ, cause B)

**Fichiers** : `frontend/src/components/optimizer/status.ts:6-8`,
`app/engine/opt_budget.py:49` et `:84`, `app/engine/auto_optimizer.py:382`.

Les trois presets annoncent un nombre d'essais et une durée :

| Preset | Annonce |
|---|---|
| Rapide | 20 trials, 1 worker — ~2 min |
| Équilibré | 60 trials, 2 workers — ~10 min |
| Approfondi | 150 trials, 2 workers, ML HP — ~45 min |

`effective_n_trials` reproportionne ensuite le budget à
`trials_per_param × n_params`, plafonné à `max_trials`.

**Scénario d'échec** — l'utilisateur choisit « Équilibré » sur `smart_money`,
lit « 60 trials — ~10 min », et le moteur en lance **400**.

**Vérification** — calcul exécuté dans le conteneur, avec la config réelle
(`trials_per_param: 15`, `max_trials: 400`), preset « Équilibré » :

```
smart_money                      58 params    60 demandés → 400  (plafonné)
opus_omnibus_v11_followsetup_no_ml  22 params  60 demandés → 330  (proportionné)
opus_omnibus_v11_followsetup     19 params    60 demandés → 285  (proportionné)
…
effectif médian : 135          facteur max : ×6,7
stratégies au-dessus du demandé : 40 / 41
```

Le moteur produit la justification (`format_budget`) et la range dans le job
(`n_trials_budget`) — le front ne lit ni l'une ni l'autre. Le nom du champ ne
figure nulle part dans `frontend/src`.

---

## LAB-05 — Trois causes d'arrêt, aucune distinguée à l'écran (P2, CONFIRMÉ, cause B)

**Fichiers** : `app/engine/optimizer_search.py:653`,
`app/engine/opt_bayesian.py`, `app/api/routes/optimizer.py` (SSE).

Le compteur d'essais peut s'arrêter avant le nombre affiché pour **trois**
raisons sans rapport entre elles :

1. le budget a été reproportionné (`LAB-04`) ;
2. l'arrêt anticipé s'est déclenché — `_should_early_stop` autorise l'arrêt dès
   `done >= max(patience, n_trials // 2)` ;
3. des essais ont échoué (workers KO), et le score ne repose alors que sur les
   survivants.

**Scénario d'échec** — preset « Équilibré » (`early_stop_patience: 15`) sur
`smart_money`. Budget effectif 400. L'arrêt anticipé devient possible à
`max(15, 200)` = **exactement 200**. L'utilisateur voit « 200 / 400 » après
avoir demandé 60, et rien ne lui dit laquelle des trois raisons s'applique.

**Vérification** — arithmétique du seuil confrontée au preset, et flux SSE
inspecté : il émet `n_trials`, `trials_done`, `progress`, `best_score`,
`trials`. Ni le motif d'arrêt, ni le nombre d'essais en échec, ni
`n_trials_budget`. Le mécanisme de diffusion est là (`live-progress.tsx:36`) ;
il lui manque des champs, pas une architecture.

---

## LAB-06 — « 1/14 entraînés » ne parle pas du même objet que le registre (P3, CONFIRMÉ, cause C)

**Fichier** : `frontend/src/components/views/ml-view.tsx`.

L'onglet ML affiche un badge « 1/14 entraînés », calculé sur `is_trained` par
**stratégie**. Le registre de modèles, lui, est indexé par couple **(timeframe,
recette)** et en compte 3.

**Scénario d'échec** — l'utilisateur lit « 1/14 entraînés », ouvre `/models`,
y trouve trois modèles actifs, et ne peut réconcilier les deux nombres :
ils comptent sur deux axes différents, sous des titres qui ne le disent pas.

**Vérification** — `/api/ml/strategy-info` : 14 stratégies, 1 avec
`is_trained: true`. `/api/ml/registry` : 3 couples `(tf, recipe)`. Les deux
réponses sont exactes ; c'est leur mise côte à côte qui induit en erreur.

---

## LAB-07 — Recettes et stratégies sont disjointes, présentées sans lien (P2, CONFIRMÉ, cause C)

**Fichiers** : `frontend/src/components/views/ml-view.tsx`,
`frontend/src/components/cards/ml-recipes-list.tsx`.

L'onglet ML empile « STRATÉGIES ML » (14 entrées) et « Recettes ML
disponibles » (10 entrées). **Aucun nom n'est commun aux deux listes.**

**Scénario d'échec** — l'utilisateur veut entraîner le modèle que consomme
`opus_omnibus_v11`. La liste des recettes ne contient pas ce nom. Rien à
l'écran ne dit que la recette correspondante est `omnibus_v4_multi`. Il choisit
au jugé.

**Vérification** — différence ensembliste sur les réponses réelles :

```
recettes qui ne sont pas des stratégies :
  dyn_threshold_v1, omnibus_full, omnibus_smc, omnibus_smc_tb,
  omnibus_v4_multi, omnibus_v4_multi_nopruning, omnibus_v4_single,
  proxy_indicators, stat48_v4, stat48_v5
```

Soit les 10 sur 10. La relation existe côté serveur
(`app.ml.scoring.resolve_recipe_name`) et n'est exposée par aucune route.

---

## LAB-08 — « Prochain retrain » affiche une durée négative (P2, CONFIRMÉ)

**Fichiers** : `frontend/src/components/views/ml-view.tsx:86`,
`frontend/src/lib/utils.ts:131`.

La colonne appelle `timeAgo(info.next_retrain_at)`. `timeAgo` calcule
`Date.now() - d.getTime()` — un **passé**. Appliqué à un horodatage **futur**,
`secs` est négatif, la première branche `secs < 60` s'applique, et la fonction
rend la valeur brute en secondes.

**Scénario d'échec** — `next_retrain_at: 1787568542` (futur). La colonne
« Prochain retrain » affiche **`-154352s`** sur les 14 lignes.

**Vérification** — mesuré dans le navigateur, 14 lignes sur 14. La valeur
serveur est correcte : c'est le formateur qui est employé à contre-emploi.
Aucune garde ne couvre le signe négatif.

---

## LAB-09 — Complétude et trous sont reçus, jamais affichés (P2, CONFIRMÉ, cause A)

**Fichiers** : `app/api/routes/ml.py:51`,
`frontend/src/components/views/ml-view.tsx:99`.

Chaque entrée de `/api/candles/stats` porte `completeness` et `gaps` — issus du
lot `DOWN-02`, précisément pour informer l'opérateur de la fiabilité d'une
série. Le tableau affiche à la place « Première », « Dernière » et « Taille ».

**Scénario d'échec** — deux datasets de 4 701 barres, l'un complet, l'autre à
82 % avec 12 trous : indiscernables à l'écran. Pire, `from` et `to` valent
`null` dans la réponse, donc les colonnes « Première » et « Dernière » sont
structurellement vides — deux colonnes sur six ne peuvent rien afficher.

**Vérification** — réponse réelle :
`{"symbol": "AC.PA", "tf": "15m", "bars": 2039, "from": null, "to": null,
"size_kb": 30.1, "completeness": 1.0, "gaps": 0}`.

---

## LAB-10 — « Optimiseur ML » liste les espaces des 41 stratégies (P3, CONFIRMÉ)

**Fichier** : `frontend/src/components/views/optimizer-view.tsx:55`.

`OptimizerView` reçoit `filterMl` et le transmet à `OptimizerConfigForm` et à
`OptimizerJobsPanel` — mais **pas** à `ParamSpaceTable`.

**Scénario d'échec** — sur `/lab?tab=ml`, le panneau titré « Optimiseur ML »
présente un sélecteur filtré sur les stratégies ML, puis, juste en dessous, une
table « ESPACES DE PARAMÈTRES — 41 STRATÉGIES » qui inclut les 25 stratégies
non-ML. Le titre promet un filtre que la table ignore.

**Vérification** — mesuré dans le navigateur sur `/lab?tab=ml` :

```
{"titre_optimiseur_ml": true, "espaces_annonces": "41"}
```
