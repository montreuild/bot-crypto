# Audit — Optimiseur et sélection de paramètres

> Périmètre : `app/engine/optimizer_search.py` (1 312 lignes), `opt_scoring.py`,
> `opt_workers.py`, `opt_persistence.py`, `auto_optimizer.py` (822 lignes),
> `app/core/is_oos.py`, `stats_thresholds.py`, `deflated_sharpe.py`,
> `app/api/routes/optimizer.py`.

---

## Tableau de bord

> ⚠️ **Révisé le 17 puis le 18 août 2026.** La PR #222 a introduit une
> **troisième tranche (holdout, 20 %)** qui résout O-02, O-03 et O-07, et
> requalifie O-01. N-01 / N-02 / N-03, O-04, O-05 (sans sklearn), O-06,
> O-08, O-10, O-11 : [`14-REVISION-2026-08-18.md`](14-REVISION-2026-08-18.md).
> Les sections ci-dessous décrivent l'état au 14 août, conservé comme trace du
> raisonnement ; la colonne « État » donne la situation courante.

| # | Sévérité | Titre | Fichier | État au 17/08 |
|---|----------|-------|---------|---------------|
| O-01 | 🔴→🟡 | L'objectif d'optimisation **est** le score OOS : il n'y a pas d'out-of-sample | `optimizer_search.py:305-313` | **requalifié** — alias `val_*` / `best_val_*` à côté de `oos_*` |
| O-02 | 🔴 Critique | Le gate de promotion est évalué sur la fenêtre qui a servi à sélectionner | `auto_optimizer.py:534-541` | ✅ **résolu** — gate sur holdout, variables réaffectées (`auto_optimizer.py:587-590`) |
| O-03 | 🔴 Critique | Le gate walk-forward tourne sur les mêmes données que la sélection | `auto_optimizer.py:543-585` | ✅ **résolu** — reçoit `df_recherche` (cf. N-03 sur le nom) |
| O-04 | 🟠 Majeur | L'optimiseur mesure sur 1 000 € quand le live dimensionne sur l'enveloppe du slot | `optimizer_search.py:274` | ✅ résolu — enveloppe du slot |
| O-05 | 🟠 Majeur | Gel de paramètres décidé sur un dépistage sous-dimensionné | `optimizer_search.py:401-529` | ✅ atténué — gel seulement si assez d'essais **et** impact &lt; bruit ; **pas de sklearn** |
| O-06 | 🟠 Majeur | `seed=0` figé : deux campagnes explorent exactement le même chemin | `optimizer_search.py:650` | ✅ résolu — `optimizer.seed` (défaut `None`) |
| O-07 | 🟠 Majeur | `n_trials` du Deflated Sharpe ≠ nombre d'essais réellement joués | `auto_optimizer.py:531` | ✅ **résolu** — `result.get("n_trials")` (`auto_optimizer.py:626`) |
| O-08 | 🟡 Moyen | Early stop sur le score OOS : arrêt d'autant plus précoce que le bruit est fort | `optimizer_search.py:786-805` | ✅ résolu — jamais avant la moitié du budget |
| O-09 | 🟡 Moyen | `overfitting_ratio` inopérant sur les scores négatifs | `opt_scoring.py:141-147` | ✅ déjà en place — `NaN` si IS ou OOS ≤ 0 |
| O-10 | 🟡 Moyen | Modèle ML final entraîné sur IS+OOS par défaut | `auto_optimizer.py:642-644` | ✅ résolu — `is_only` par défaut |
| O-11 | 🟡 Moyen | Un trial en timeout est silencieusement ignoré, pas rejoué | `optimizer_search.py:591-593` | ✅ résolu — rejoué in-process |
| O-12 | 🔵 Mineur | `_perturb` peut ne rien perturber (`offsets` contient 0) | `optimizer_search.py:1104-1110` | ✅ résolu — toujours un voisin distinct |
| O-13 | 🔵 Mineur | `_penalized_score` ne pénalise jamais un sur-apprentissage à score OOS négatif | `optimizer_search.py:311-312` | ✅ déjà en place — pas de bonus si `oos ≤ 0` |

> 18 août : N-01, N-02, N-03 traités. Voir [`14-REVISION-2026-08-18.md`](14-REVISION-2026-08-18.md).

---

## O-01 🔴 Il n'y a pas d'out-of-sample

### Constat

Le critère maximisé par toutes les méthodes de recherche est
`_penalized_score` (`optimizer_search.py:305-313`) :

```python
def _penalized_score(self, r: dict) -> float:
    oos = r["oos_score"]              # ← composite_score du backtest OOS
    ovf = r.get("overfit", 1.0)
    if np.isnan(ovf): return oos
    if ovf > 2.5:     return oos * (2.5 / ovf)
    return oos
```

et il est utilisé comme objectif dans :

- `_optuna_sequential:780` → `study.tell(trial, score)` ;
- `_optuna_parallel:856` → idem ;
- `_run_parallel._record:1020` → `best_so_far` ;
- `_bayesian_search_legacy:947` → `best = max(self.results, key=self._penalized_score)`.

Autrement dit : **l'optimiseur choisit ses paramètres pour maximiser la
performance sur la tranche OOS.** Une fenêtre sur laquelle on optimise n'est,
par définition, plus out-of-sample. Après 40 à 200 essais, le paramétrage retenu
est celui qui colle le mieux au bruit de la tranche OOS.

### Ce que la pénalité de sur-apprentissage ne corrige pas

`overfitting_ratio = is_score / oos_score` pénalise le cas « IS excellent, OOS
médiocre ». Mais le mécanisme décrit ici produit exactement le cas **inverse** :
IS moyen, OOS excellent — parce qu'on a cherché l'OOS excellent. Le ratio vaut
alors < 1 et **aucune pénalité ne s'applique**. La garde est aveugle au mode de
défaillance qu'elle est censée couvrir.

### Ordre de grandeur

Le dépôt est bien conscient du biais de sélection multiple : il a écrit deux
implémentations du Deflated Sharpe pour le corriger. Mais le DSR corrige le
Sharpe **rapporté** ; il ne rend pas une fenêtre optimisée à nouveau
out-of-sample. Avec `n_trials = 40` (défaut `auto_optimizer.py:302`) sur des
tranches OOS de quelques dizaines de trades, le maximum de 40 tirages bruités
est largement au-dessus de l'espérance.

### Correction proposée

Trois tranches, pas deux :

```
[--------- TRAIN (55 %) ---------][--- VALIDATION (25 %) ---][--- TEST (20 %) ---]
        optimisation                  sélection du best         gate d'apply,
                                                                jamais optimisé
```

- `_penalized_score` reste l'objectif, mais calculé sur **VALIDATION** ;
- `beats_baseline` (O-02) et `_wf_consistent` (O-03) sont évalués sur **TEST**,
  qu'aucune boucle de recherche n'a jamais vu ;
- `split_is_oos` devient `split_train_val_test`, avec purge et embargo (cf.
  B-08 dans [`02-BACKTEST.md`](02-BACKTEST.md)).

C'est le changement de plus grande valeur de tout cet audit : sans lui, tous les
garde-fous en aval (Deflated Sharpe, gate walk-forward, `MIN_SIGNIFICANT_TRADES`,
cône d'edge) mesurent une quantité déjà contaminée.

---

## O-02 🔴 Le gate de promotion regarde la fenêtre de sélection

`auto_optimizer.py:534-541` :

```python
def _beats_baseline() -> bool:
    ok, reason = _bb(oos_trades, best_oos_pnl, best_oos_wr,
                     best_oos_sharpe, _baseline, ...)
```

Les quatre grandeurs proviennent de `result` (`optimizer_search._best_result()`),
c'est-à-dire du **meilleur essai sur la tranche OOS** — la tranche qui vient
d'être optimisée (O-01).

Les cinq critères de `beats_baseline` (`opt_scoring.py:155-218`) sont donc tous
évalués sur des données in-sample de fait :

| Critère | Fenêtre | Problème additionnel |
|---|---|---|
| `oos_trades >= 10` | OOS optimisée | — |
| `oos_pnl > 0` | OOS optimisée | biaisé par F-01 (frais d'entrée) |
| `oos_pnl > baseline.pnl` | OOS optimisée | idem |
| `oos_wr > b_wr` **ou** `oos_sharpe > b_sharpe` | OOS optimisée | un Sharpe de 230 sur 8 trades (F-02) satisfait seul ce critère |
| Deflated Sharpe | OOS optimisée | mal étalonné (F-07) et mal alimenté (O-07) |

Le « garde-fou UNIQUE d'application » est donc unique, partagé, bien factorisé —
et appliqué à la mauvaise fenêtre.

---

## O-03 🔴 Le gate walk-forward n'est pas hors échantillon non plus

`_wf_consistent()` (`auto_optimizer.py:543-585`) exécute un `WalkForwardAnalyzer`
sur `df_full`. Or `df_full` est la série **entière**, celle qui a été coupée en
`df_is` / `df_oos` par `split_is_oos` (`auto_optimizer.py:359`) et sur laquelle
la recherche a tourné.

Les folds du walk-forward recouvrent donc intégralement la zone d'optimisation.
La « consistency ≥ 60 % » qu'il vérifie mesure la stabilité d'un paramétrage
**sur ses propres données d'entraînement**.

S'y ajoutent les défauts propres du module (cf.
[`02-BACKTEST.md`](02-BACKTEST.md) B-03 et B-04) :

- `wf.run(df_full, symbol)` — `timeframe` non transmis ⇒ annualisation et coût
  d'emprunt calculés sur le TF de config ;
- aucune réoptimisation par fold, donc l'exercice ne teste pas la procédure
  d'optimisation, seulement la persistance d'un jeu figé ;
- `consistency` compte les folds à `total_pnl > 0` — biaisé par F-01.

Note : la clause `except Exception → return True` (ligne 583) et le
`if "error" in res_wf → return True` (ligne 572) rendent le gate **neutre en cas
d'échec**. Un historique trop court fait donc passer l'auto-apply sans aucune
vérification walk-forward, silencieusement (log en INFO).

---

## O-04 🟠 L'optimiseur mesure sur une autre échelle que le live

`optimizer_search.py:274` :

```python
bt = Backtester(eng, cfg, cancel_event=self._cancel_event, ml_mode=self.ml_mode)
```

Aucun `envelope=`. `Backtester.initial_capital` retombe donc sur
`_default_venue_capital(cfg)` = **1 000 €** (`config/risk.yaml`,
`envelopes.margin-isolated.capital`).

Le live, lui, dimensionne sur `env.slot_envelope` =
`venue_capital × max_symbol_exposure_pct × poids` = 1 000 × 0,5 × poids. Pour un
slot seul sur son symbole : **500 €**. Pour deux slots à poids égal : **250 €**.

Conséquences :

1. **Les contraintes absolues ne mordent pas au même endroit.** `min_notional`,
   la quantification par lot et les frais fixes s'expriment en devise ; à
   1 000 € ils sont deux à quatre fois moins contraignants qu'en live. Sur
   `euronext-paper` (`min_notional: 200`, `fee_min: 2.0`, quantité entière),
   l'écart est décisif.
2. **La garde de dérive de base est court-circuitée.**
   `risk.base_drift_tolerance: 0.20` refuse une promotion si l'enveloppe qui a
   mesuré l'edge diffère de plus de 20 % de l'enveloppe courante
   (`risk_envelope.base_drift`). Mais l'optimiseur n'enregistre aucune
   `envelope_base` — seul `forward_test` le fait (`forward_test.py:196-198`).
   La dérive 1 000 → 500 (soit 100 %) échappe donc au contrôle.
3. Le mécanisme de double passe `run_dual_pass` (`backtest.py:134-161`), conçu
   précisément pour séparer « ce bot est-il promouvable » de « cette stratégie
   vaut-elle quelque chose », n'est **appelé par aucun chemin d'optimisation**
   (vérifié : seuls des tests l'appellent).

**Correction** : passer l'enveloppe de référence
(`backtest.reference_envelope`) explicitement à l'optimiseur, et enregistrer
`envelope_base` dans le résultat pour que `base_drift` puisse fonctionner.

---

## O-05 🟠 Le gel de paramètres décide sur trop peu

`_freeze_from_results` (`optimizer_search.py:498-529`) gèle les paramètres à
faible impact, mesuré par `_impact_scores` : pour chaque paramètre, l'écart entre
la meilleure et la pire moyenne de score groupée par valeur.

La garde `_MIN_SCREEN_PER_PARAM = 2` (ligne 496) exige `2 × n_params` essais de
dépistage. Elle a été ajoutée après avoir observé « geler 20/21 paramètres à
chaque run » sur `opus_omnibus_v9` — le raisonnement est juste et bien documenté.

Elle reste toutefois très en dessous du nécessaire :

- avec 21 paramètres, 42 essais de dépistage donnent en moyenne **2 essais par
  valeur** pour un paramètre à 3 modalités ;
- l'« impact marginal » est une moyenne conditionnelle, c'est-à-dire une
  estimation **marginale** : tous les autres paramètres varient simultanément.
  Elle ne sépare pas l'effet propre de l'interaction ;
- `_should_reduce_space` ne déclenche que si `card > n_trials × 200`
  (ligne 385) — donc précisément quand la couverture est la plus faible, donc
  quand l'estimation d'impact est la moins fiable. La condition d'activation
  est corrélée négativement à la validité de la mesure.

Le chemin Optuna est meilleur : il tente fANOVA / PedANOVA
(`_optuna_param_importances:669-714`), qui traitent les interactions, avec repli
sur l'estimateur marginal. Mais fANOVA nécessite scikit-learn, retiré du dépôt
(commentaire ligne 678), donc **PedANOVA est le seul chemin réel** — et il
retombe sur `_impact_scores` s'il ne rend pas de signal.

**Correction** : porter le seuil à `_MIN_SCREEN_PER_PARAM × max(modalités)` et
n'autoriser le gel que si l'impact du paramètre est inférieur au bruit estimé
(écart-type des scores intra-groupe), pas simplement à 10 % de la somme des
impacts.

---

## O-06 🟠 Graine figée : deux campagnes explorent le même chemin

`optimizer_search.py:650` :

```python
sampler = optuna.samplers.TPESampler(n_startup_trials=n_startup, seed=0)
```

Idem pour `MonteCarlo` (`monte_carlo.py:79` `default_rng(42)`) et pour les
bootstrap de `oos_tracker` (`_mc_contract:118`, `_edge_contract:157`,
`default_rng(42)`).

La reproductibilité est une bonne propriété pour les tests. En production elle
a un coût : **relancer une optimisation sur les mêmes données donne exactement
le même résultat**, donc on ne peut pas distinguer un optimum robuste d'un
artefact du chemin d'exploration. Et l'intervalle de confiance produit par les
bootstrap à graine fixe est **le même intervalle** à chaque appel, ce qui lui
retire sa fonction de mesure d'incertitude.

**Correction** : `seed=None` (ou une graine dérivée de l'horodatage) sur les
chemins de production, `seed=0` sur les chemins de test via un paramètre. Pour
les bootstrap, tirer une graine différente à chaque enregistrement et la
persister à côté du résultat.

---

## O-07 🟠 `n_trials` du Deflated Sharpe est le budget, pas le réalisé

`auto_optimizer.py:531` :

```python
_ds_n_trials = int(self.n_trials) if _ds_gate_enabled else 1
```

`self.n_trials` est le **budget demandé**. Le nombre d'essais réellement joués
diffère systématiquement :

- early stop (`early_stop_patience`) interrompt avant la fin ;
- les workers en timeout ou en erreur sont ignorés (O-11) ;
- la phase de dépistage `param_search_optim` consomme une partie du budget puis
  **réduit l'espace**, ce qui change la nature statistique des essais suivants ;
- `len(self.results)` est disponible et n'est pas utilisé.

Le DSR étant très sensible à `N` (`E[max SR] ∝ √(2 ln N)`), passer 40 quand 12
essais ont tourné durcit le gate sans raison, et l'inverse le relâche.

**Correction** : `_ds_n_trials = len(opt.results)` — la valeur est retournable
par `_best_result()`.

---

## O-08 🟡 Early stop piloté par le bruit

`_optuna_sequential:786-805` (et équivalents) : `no_improve` s'incrémente à
chaque essai qui ne bat pas `best_score`, et la recherche s'arrête à
`early_stop_patience`.

Comme `best_score` est le maximum d'une série bruitée (O-01), plus le bruit est
fort, plus tôt un maximum élevé apparaît, et plus tôt la recherche s'arrête. Le
budget effectivement consommé est donc **inversement corrélé à la fiabilité du
résultat** : les stratégies les plus instables sont celles qu'on explore le
moins.

Ce n'est pas dramatique en soi, mais cela invalide toute comparaison de budget
entre stratégies, et alimente O-07.

---

## O-09 🟡 `overfitting_ratio` inopérant sur les scores négatifs

`opt_scoring.py:141-147` :

```python
if is_score <= 0: return 0.0
return round(min(is_score / max(oos_score, 0.01), 10.0), 2)
```

Deux angles morts :

- `is_score <= 0` → ratio `0.0`, donc `_penalized_score` ne pénalise rien.
  Or `composite_score` renvoie `ret_norm` (négatif) dès que `pnl <= 0`
  (`opt_scoring.py:136`) : un IS perdant et un OOS légèrement gagnant — cas
  typique de sur-apprentissage sur la fenêtre de sélection — passe sans pénalité.
- `max(oos_score, 0.01)` transforme un OOS négatif en dénominateur 0,01, donc un
  ratio plafonné à 10 quel que soit l'IS. Le plafond `min(..., 10.0)` écrase
  ensuite toute information.

---

## O-10 🟡 Le modèle ML livré n'est pas celui qui a été évalué

`auto_optimizer.py:642-644` :

```python
_ml_train_mode = self.cfg.get("optimizer", {}).get("ml_final_train_mode", "full")
```

Défaut `"full"` = réentraînement sur **IS + OOS**. Le score OOS rapporté (et sur
lequel le gate d'apply s'est prononcé) provient d'un modèle entraîné sur IS
seul. Le modèle publié au registre, celui qui tradera, a vu la fenêtre OOS.

C'est un choix défendable (plus de données = meilleur modèle) mais il a deux
effets non signalés :

1. le chiffre affiché à l'utilisateur ne décrit pas l'artefact livré ;
2. tout backtest ultérieur en `ml_mode="frozen"` sur une fenêtre incluse dans
   IS+OOS produit une fuite. Elle est **détectée** (`ml_registry.overlaps`,
   `backtest.py:110`) et reportée dans `ml_info.overlap_warning`, mais
   uniquement en WARNING — le backtest se poursuit et son résultat est utilisé.

L'alternative `is_only` existe (`ml_final_train_mode: is_only`) et n'est pas le
défaut.

**Correction** : soit basculer le défaut sur `is_only`, soit — mieux — retirer
`overlap_warning` du domaine du log et le faire remonter comme une **invalidation
du résultat** dans l'UI (bandeau rouge, résultat non promouvable).

---

## O-11 🟡 Trials perdus silencieusement

`_submit_wave:591-593` :

```python
except concurrent.futures.TimeoutError:
    logger.warning("[Optimizer] worker timeout (>%ds), ignoré", timeout)
    continue
```

Le timeout est de 300 s en dur (`optimizer_search.py:563`, `827`). Un trial ML
lourd (`opus_omnibus_v12`, entraînement inline sur 20 000 barres) le dépasse
sans difficulté. Les paramètres correspondants ne sont **jamais rejoués** : ils
sont retirés de l'exploration.

Le biais est systématique et orienté : ce sont les paramétrages **les plus
coûteux** (plus d'estimateurs, fenêtres plus longues, plus de features) qui
disparaissent. L'optimiseur explore donc préférentiellement les configurations
rapides, sans que rien ne le signale dans le résultat.

**Correction** : compter les timeouts dans le résultat
(`result["n_timeouts"]`), rendre le délai configurable, et rejouer une fois en
séquentiel avant d'abandonner.

---

## O-12 🔵 `_perturb` peut ne rien perturber

`optimizer_search.py:1104-1110` :

```python
offsets = [-1, 0, 1]
new_idx = curr_idx + random.choice(offsets)
new_idx = max(0, min(len(options) - 1, new_idx))
```

`0` est dans les offsets, et le clamp ramène `-1`/`+1` sur la même valeur aux
bornes. Sur un paramètre binaire (`[True, False]`) placé à l'indice 0,
`random.choice` renvoie la même valeur dans 2 cas sur 3. Avec `n_perturb =
len(keys) // 3`, la probabilité qu'un essai d'exploitation soit un **doublon
exact** du meilleur est loin d'être négligeable — et ces doublons consomment du
budget en étant comptés comme des essais.

Ce chemin n'est emprunté qu'en l'absence d'Optuna, mais c'est justement le cas
en production si la dépendance n'est pas installée.

---

## O-13 🔵 Pénalité de sur-apprentissage inversée sur les scores négatifs

`_penalized_score:311-312` : `if ovf > 2.5: return oos * (2.5 / ovf)`.

Multiplier par `2.5/ovf < 1` **augmente** un score négatif (le rapproche de 0).
Un paramétrage à `oos_score = −0,8` et `ovf = 10` obtient `−0,2`, donc un
meilleur classement qu'un paramétrage à `−0,5` sans sur-apprentissage. La
pénalité, correcte pour les scores positifs, est un bonus pour les négatifs.

**Correction** : `return oos * (2.5 / ovf) if oos > 0 else oos * (ovf / 2.5)`,
ou plus simplement soustraire une pénalité au lieu de multiplier.

---

## Ce qui est solide

- **Le pool de process persistant** (`_open_pool`, contexte `spawn`, workers
  initialisés une fois avec IS/OOS sérialisés en IPC Arrow) est un vrai travail
  d'ingénierie : le raisonnement sur le coût fixe du spawn face au nombre
  d'essais est juste, et le repli séquentiel sur `BrokenProcessPool` est
  correctement implémenté (les params non traités sont rejoués, pas perdus).
- **Le décompte du budget en tentatives et non en succès** (`_run_parallel`
  retourne `attempted`) évite un dépassement silencieux du budget — le
  commentaire explique précisément le bug qu'il corrige.
- **`_safe_worker_count`** avec cap mémoire estimé à partir de la taille réelle
  du payload IPC : garde anti-OOM proportionnée, pas un nombre magique.
- **Unification de `MIN_SIGNIFICANT_TRADES`** (`stats_thresholds.py`) : le
  raisonnement sur la marge binomiale est correct, et la suppression du double
  seuil (2 pour classer / 10 pour décider) est le bon arbitrage, bien argumenté.
- **`_eval` supprime l'entrée `optimizer_results` de la config du trial**
  (`optimizer_search.py:272-273`) : sans cela, les paramètres échantillonnés
  auraient été silencieusement écrasés par la résolution de précédence. Piège
  subtil, correctement désamorcé.
- **« Non appliqué = non utilisé »** (`auto_optimizer.py:617-625`) : un
  paramétrage refusé par le gate est tracé dans l'audit sans être écrit dans
  `optimizer_results`. La discipline est la bonne.
