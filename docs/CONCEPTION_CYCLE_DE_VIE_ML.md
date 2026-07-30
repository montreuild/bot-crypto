# Conception — Cycle de vie des modèles ML (recette, registre, politique)

> Statut : **proposition à valider** (2026-07-24). Concrétise **ML-02** du
> plan directeur (et recoupe **ML-01** — gating de promotion — et **STRAT-02**
> — `models/index.json`). Issue de l'investigation « légitimité du pkl figé »
> et de la discussion produit sur deux architectures candidates (« sortir
> l'entraînement de l'optimiseur » vs « fenêtre d'entraînement pilotée par le
> backtest »).
>
> Les chiffres mesurés cités (AUC, PF) proviennent d'**une seule campagne
> d'essais sans phase d'optimisation** : ils fixent des ordres de grandeur et
> des directions, pas des vérités. L'architecture proposée sert précisément à
> rendre ces mesures reproductibles et comparables avant d'en tirer des règles.
>
> À lire avec : `docs/PLAN_DIRECTEUR_MULTI_ACTIFS.md` (§ML-01/ML-02) et
> `docs/CONCEPTION_PROMOTION_PAR_EDGE.md` (promotion des *stratégies* — le
> présent document traite de la promotion des *modèles*, problème jumeau).
>
> **Suite** : `docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md` reprend §3.1 (la
> recette comme objet de premier ordre) et §5.5 (même recette ⇒ mêmes
> artefacts), restés non construits, et en tire les conséquences mesurées sur
> `app/strategies/`.

---

## 1. Constats de départ (vérifiés dans le code)

Le système actuel conflate trois objets distincts — *comment* on entraîne,
*quel* artefact on obtient, *quand* on le remplace — dans un unique fichier
mutable `models/{stratégie}_{tf}.*` :

1. **Un seul slot par (stratégie, TF), écrasé sans comparaison.** Trois
   écrivains partagent le même chemin : le trainer live
   (`app/ml/trainer.py:156-158`), le train final post-optimisation
   (`app/engine/auto_optimizer.py:88-91`), et tout backtest en mode inline qui
   sauvegarde. Seul garde-fou : un fit non concluant laisse l'état inchangé
   (`app/ml/trainer.py:159-169`). Un modèle *pire mais entraînable* écrase
   silencieusement un modèle meilleur.
2. **Aucune dimension symbole.** Le chemin est `{name}_{tf}`
   (`app/engine/backtest.py:648`, `app/ml/trainer.py:189`) et le trainer live
   n'entraîne que sur BTC (`app/ml/trainer.py:130`) — le même modèle sert
   ensuite ETH/XRP. Ni versionné, ni daté, ni rattaché à un symbole.
3. **Aucune dimension temps → fuites structurelles.** `Backtester` en mode
   pré-entraîné charge le modèle *courant* quel que soit l'intervalle
   backtesté (`backtest.py:645-651`) : un modèle entraîné hier peut être
   évalué sur les données du mois dernier qu'il a vues à l'entraînement.
   `WalkForwardAnalyzer` a le même défaut dans chaque fold
   (`walk_forward.py:93-96`, `use_pretrained_ml=True` implicite).
4. **Repli silencieux.** Si le chargement échoue, le backtest bascule en
   entraînement inline avec un simple log info (`backtest.py:652-656`) —
   l'écart mesuré entre les deux modes (ex. V11 : PF 5.73 figé vs 0.55
   inline, à prendre avec recul) rend ce switch invisible dangereux pour
   toute comparaison. Rien dans `BacktestResult` ne dit quel mode a tourné
   ni quel modèle a servi.
5. **Fenêtres et cadences incohérentes avec les mesures.** Live :
   `fetch_n ≈ 1560` barres, cadence 6 h (`trainer.py:137`,
   `engine.py:77`) ; backtest inline : fenêtre `warmup_bars×2 = 1500`,
   cadence 800 barres (`opus_omnibus_v11.py:516-529`). Or la campagne a
   mesuré que ce régime « fréquent + petit » perd, et que l'edge du V4 vient
   d'une fenêtre ~40 000 barres. Les seuls modèles rentables sont figés à
   jamais (`retrain_interval_h = 24×365`) — l'intermédiaire « rare + grand »
   n'existe pas.
6. **Provenance quasi nulle.** `meta.json` ne stocke que
   features/medians/AUC/train_meta (`persistence.py:119-128`) — pas de dates
   d'entraînement, pas de symbole, pas de commit, pas de seed. Le V4 vient
   d'un script hors dépôt, irrégénérable. Dates et AUC ont dû être
   reconstitués a posteriori.
7. **Deux modèles entraînés, un seul a un signal.** AUC direction mesurée
   0.53-0.54 partout (hasard) ; amplitude 0.67-0.76. On paie pourtant le
   double d'entraînement et ~⅓ des dimensions de `PARAM_SPACES` (seuils
   `dir_min`/`dir_max`) balaient un signal inexistant.
8. **Des briques saines existent déjà** et sont à réutiliser, pas à
   réinventer : `MLBackend` (façade features/train/predict/persist),
   `train_cache.cached_train` + `aligned_train_window` (mutualisation des
   entraînements entre trials), `FeatureStore`, `split_is_oos`,
   `_freeze_from_results` (gel des params à faible impact), format natif LGB
   sans pickle, `managed_externally`.

## 2. Critique des deux solutions proposées

### Solution 1 — « sortir l'entraînement, page dédiée, modèles versionnés »

**À garder :**
- Séparer l'entraînement de l'optimiseur et des stratégies : oui — c'est la
  séparation des responsabilités qui manque (constat 1).
- Versionner/dater les modèles, rangés par symbole et TF : oui — c'est le
  prérequis de tout le reste (constats 2-3-6).
- Déclencheur de ré-entraînement basé sur la *date du dernier entraînement*
  (métadonnée persistée) plutôt qu'un timer en mémoire : oui — survit aux
  redémarrages, et c'est la seule façon d'alerter sur la fraîcheur.
- Page dédiée avec validation sur bougies fraîches (esprit IS/OOS) : oui,
  comme *banc d'essai de recettes* (cf. §3.1).

**À corriger :**
- « Enregistrer le modèle après un meilleur résultat d'optimisation ou de
  backtest » : **non**. Un backtest gagnant n'est pas un critère de promotion
  d'un modèle — c'est sélectionner sur le bruit (exactement la divergence
  ML-01 déjà constatée sur les stratégies). La promotion d'un modèle se juge
  sur une validation *hold-out antérieure aux données tradées* (§3.4), le
  backtest économique servant d'arbitre final en walk-forward, pas de
  critère au fil de l'eau.
- « Comparer l'AUC pour différents TF » : les AUC ne sont **pas comparables
  entre TF** (bases de labels, équilibre de classes et horizons différents).
  On compare des recettes *au sein d'un TF* ; entre TF on compare des
  métriques économiques (walk-forward) ou au minimum des métriques à
  définition de label constante.
- Un labo UI sans script committé recrée le problème V4 (artefact
  irréproductible fabriqué à la main). La page doit être un *wrapper* d'un
  runner versionné dans le dépôt, jamais un chemin d'entraînement parallèle.
- « Plus d'entraînement dans les stratégies » : vrai pour le live (déjà le
  cas via `managed_externally`), mais le mode inline doit survivre comme
  outil de recherche explicite — et le backtest long ne peut PAS se contenter
  d'un modèle unique figé s'il prétend refléter un live qui, lui, se
  ré-entraîne (cf. §5.1 — c'est la question ouverte de la Solution 1, et la
  Solution 2 y répond).

### Solution 2 — « fenêtre/cadence d'entraînement pilotées par le backtest »

**À garder :**
- L'idée centrale — *le backtest rejoue le ré-entraînement comme si on était
  en live* — est la bonne réponse à « backtests fiables sur 2-3 ans ». C'est
  du walk-forward ancré de modèles, et l'infra (train_cache, fenêtres
  alignées) en fait déjà 80 %.
- « On garde le nouveau modèle seulement s'il est meilleur » : oui — c'est le
  même gate de promotion qu'en live, et c'est précisément ce qui permet au
  backtest de simuler la politique complète, gate inclus.

**À corriger :**
- « Meilleur » jugé sur la tranche qu'on s'apprête à trader = **lookahead**.
  Le gate doit se juger sur un hold-out qui *précède* la tranche (§3.4).
  Sinon le backtest simulé sur-estime systématiquement.
- « Entraîner 4 modèles au début » : eager et redondant — les artefacts
  doivent être produits *paresseusement* aux frontières et **persistés dans
  le registre** (clé déterministe recette×fenêtre) : le 2ᵉ backtest ou les
  trials de l'optimiseur les réutilisent gratuitement, entre process et
  entre jours (le train_cache actuel meurt avec le process).
- Mettre la fenêtre d'entraînement dans l'espace de recherche de
  l'optimiseur : **non**. La fenêtre/cadence sont des paramètres de
  *recette* (rares, coûteux, à balayer explicitement au banc — « window
  sweep »), pas des hyperparamètres à mélanger aux seuils de décision
  (explosion combinatoire + surapprentissage de fenêtre garanti).
- Réponse à « est-ce que la partie dédiée vaut le coup si le backtest
  ré-entraîne de toute façon ? » : oui, **à condition que l'unité de travail
  soit la recette, pas l'artefact** — cf. §5.2. Sans registre unifiant les
  deux, elle ne vaudrait pas le coup (les modèles peaufinés divergeraient de
  ceux des backtests — l'« arbitraire » pressenti dans la question).

**Verdict : les deux solutions sont complémentaires, pas concurrentes.**
La Solution 1 décrit la *gestion des artefacts* (registre, provenance,
promotion, UI), la Solution 2 décrit la *simulation de la politique dans le
temps* (backtest fidèle). Ce qui manque aux deux, c'est le concept qui les
unifie : séparer **recette / artefact / politique**.

## 3. Architecture cible

Trois objets, trois responsabilités :

```
RECETTE (git, YAML)      ──produit──►  ARTEFACT (registre, immuable, daté)
  features, labels, HP,                  modèles + meta complète
  fenêtre, cadence, gate                 par (TF, recette, date)
        │                                        ▲
        └──────── POLITIQUE (un seul code) ──────┘
                  « faut-il rafraîchir ? entraîne, compare au sortant,
                    promeut ou garde » — exécutée par :
                  • le live (calendrier, barres réelles)
                  • le backtest simulated_live (frontières de barres)
                  • le runner CLI / la page Modèles (à la demande)
```

### 3.1 La recette — bloc `model:` du YAML de stratégie, versionné git

L'unité qu'on « peaufine » (au banc, via la page dédiée) est la **recette**,
jamais un artefact individuel. Exemple :

```yaml
# strategies/opus_omnibus_v11.yaml
model:
  recipe_version: 1                 # bump manuel à chaque changement
  heads: [amp]                      # défaut : amplitude seule (cf. §5.4)
  features_catalog: v4_polars@1     # catalogue FeatureStore existant
  feature_list: recipes/v11_feats_60.json   # liste EXPLICITE figée (cf. §4.2)
  labels: {horizons: [1, 3, 6], amp_top_pct: 0.30}
  hp: {n_estimators: 500, num_leaves: 31, learning_rate: 0.03, seed: 42}
  window_bars: {"15m": 40000, "30m": 40000, "1h": 25000, default: max}
  min_window_bars: 20000            # en-dessous : warning + refus de publier
  cadence_bars: 3000                # politique de rafraîchissement
  gate:                             # cf. §3.4
    holdout_bars: 1500
    metric: auc_amp                 # + cal_err en garde-fou secondaire
    epsilon: 0.01
    auc_floor: 0.55
```

- Le bloc peut être **inline** (recette propre à la stratégie) ou référencer
  une **recette nommée partagée** — `model: {recipe: recipes/omnibus_amp_v1.yaml,
  pin: …}` : plusieurs stratégies qui consomment le même signal (famille
  omnibus) partagent alors entraînements, artefacts, gate et monitoring
  (cf. §5.5). Une stratégie peut référencer *plusieurs* recettes (V12 :
  l'omnibus partagée + celle de son filtre `ml_dynamic_threshold`).
- Le hash canonique de ce bloc (`recipe_hash`) identifie la recette dans le
  registre. Tout artefact porte le hash de la recette qui l'a produit.
- Remplace à terme `retrain_interval_h` (heures) par `cadence_bars`
  (barres) : une même politique s'exprime naturellement par TF.
- Les valeurs ci-dessus sont des **hypothèses issues d'une seule campagne**
  (fenêtre ~40k > fenêtres courtes ; « rare + grand » > « fréquent + petit »),
  à calibrer au banc (§5.1) avant d'être considérées comme réglées.

### 3.2 L'artefact et le registre — `app/ml/registry.py`

```
models/
  BTC_USDC/
    15m/
      omnibus_amp_v1/                   # RECETTE, pas stratégie (cf. §5.5)
        2026-07-18T00-00Z_a3f9c1d2/     # {train_end}_{recipe_hash8}
          model.amp.lgb
          model.meta.json               # schéma v2, cf. ci-dessous
        decisions.jsonl                 # journal append-only des gates
```

- **`meta.json` v2** = payload actuel (`persistence.py:119-128`) **plus** :
  `symbol`, `train_start`, `train_end`, `n_bars`, `window_bars`,
  `recipe` (résolue, inline), `recipe_hash`, `features_hash`, `git_commit`,
  `seed`, `data_fingerprint` (réutiliser `train_cache._df_fingerprint`),
  `source` (`live|runner|backtest_sim|optimizer`), `gate` (décision et
  métriques vs sortant), `created_at`.
- **API** : `publish(...)`, `resolve(tf, recipe, as_of=None, pin=None)`,
  `latest_promoted(...)`, `list_versions(...)`.

  > ⚠️ **La dimension symbole a été RETIRÉE de la clé** (2026-07-26, décision
  > 11 — `docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md` §8bis). Ce document la
  > décrivait comme un axe de rangement ; la mesure a montré qu'elle nommait
  > une partition inexistante — le trainer live n'entraînait que sur BTC et le
  > pipeline servait CE modèle à tous les symboles. 17 des 18 cellules de la
  > matrice de transfert sont indiscernables du bruit. Le symbole subsiste en
  > **provenance** (`ArtifactRef.train_symbol`, `publish(train_symbol=…)`) :
  > savoir sur quoi un artefact a été construit est de la traçabilité, pas une
  > clé. À rejouer si un actif d'une autre classe devient entraînable.
 `resolve` commence
  par traduire la stratégie vers sa (ses) recette(s) via le YAML : deux
  stratégies référençant la même recette obtiennent le même artefact. **La vérité est
  le système de fichiers** (scan des meta, cache mtime) ; `index.json`
  (STRAT-02) devient un simple cache reconstruisible — pas de fichier d'index
  à verrouiller entre le thread live et les process de l'optimiseur.
- **`resolve(as_of=T)`** retourne la dernière version promue dont
  `train_end ≤ T`. C'est la primitive qui supprime les fuites 3 et rend le
  walk-forward correct par construction.
- ~~**Compat** : `resolve` sait lire l'ancien layout plat~~ — **supprimé**
  (2026-07-26, décision 4) : le bot n'étant pas en production, un chemin de
  repli était une dette contractée sans contrepartie. `resolve` ne connaît
  plus qu'un seul layout.
- ~~**V4**~~ — **le pack V4 figé a été retiré** (2026-07-26, décision 1) après
  mesure : il ne bat plus un ré-entraînement sur 3/3 timeframes, le résultat
  tient sur 5 fenêtres d'entraînement, et **aucun régime** ne le sauve (IC
  bootstrap appariés). Ses AUC auto-déclarées surestimaient jusqu'à 0.15. Il
  survit sous `models/_archive/`, jamais énuméré ni résolu.
  `opus_stat_pretrained_v4.py` a été supprimé avec 4 autres stratégies figées.
- **Rétention** : garder toutes les versions promues + N candidates
  récentes ; les artefacts de simulation partagent le même cache (clé
  recette×fenêtre) et sont GC-ables. `models/` runtime reste hors git (état
  actuel) ; seuls d'éventuels artefacts « release » épinglés se committent
  (décision produit, `.gitattributes *.lgb binary` déjà en place).

### 3.3 La politique — `app/ml/policy.py`, une seule implémentation

```python
def maybe_refresh(strategy, train_symbol, tf, df, ...) -> Decision:
    incumbent = registry.latest_promoted(tf, recipe)   # train_symbol = provenance
    if incumbent and bars_since(incumbent.train_end, T) < recipe.cadence_bars:
        return Keep(incumbent)                      # pas dû
    candidate = runner.train(recipe, window_ending=T - holdout)   # cf. §3.4
    decision  = gate(candidate, incumbent, holdout=]T-holdout, T])
    registry.publish(candidate, decision)           # publié même si refusé
    return decision                                 # Promote | Keep + alerte
```

Trois exécutants, zéro duplication :
- **Live** : `MLStrategyTrainer` devient un client de `maybe_refresh`. Le
  « dû » se calcule sur `meta.train_end` (persisté) et non plus sur un timer
  mémoire — survit aux restarts, et l'âge du modèle devient observable
  (alerte fraîcheur au chargement, impossible aujourd'hui).
- **Backtest `simulated_live`** : appelle `maybe_refresh` aux frontières de
  cadence (grille alignée, même esprit qu'`aligned_train_window`) en ne
  passant que les données antérieures à la frontière. Les artefacts produits
  sont publiés dans le registre (`source=backtest_sim`) → le run suivant, ou
  l'optimiseur, les récupère sans ré-entraîner.
- **Runner CLI committé** (reproductibilité, ML-02 volet 3) :
  `python -m app.ml.train --strategy opus_omnibus_v11 --symbol BTC/USDC
  --tf 15m [--as-of 2026-06-01] [--windows 10000,20000,40000] [--publish]`.
  Le « window sweep » (`--windows`) couvre le besoin « entraîner sur
  plusieurs plages et comparer » de la Solution 1 — avec hold-out commun,
  donc comparaison légitime.

### 3.4 Le gate de promotion (répond au point de vigilance n°3 — modèle, pas stratégie)

> Précision (implémentation, 2026-07-24) : ce gate porte sur la promotion
> d'un **artefact modèle** dans le registre — il ne couvre PAS ML-01, qui
> gate la promotion d'une **stratégie** vers `manual_active`
> (`app/live/slot_lifecycle.py`, walk-forward multi-fenêtres sur le PnL).
> Même philosophie (comparer sur une fenêtre tenue à l'écart plutôt qu'un
> score unique), deux mécanismes distincts — ML-01 reste un chantier séparé.

À l'instant de rafraîchissement `T`, avec `h = holdout_bars` :

1. Le candidat s'entraîne sur la fenêtre se terminant à `T − h` (il ne voit
   **jamais** le hold-out).
2. Sortant et candidat sont évalués sur `]T − h, T]` — aveugle pour les deux
   (le sortant a `train_end ≤ T − cadence ≤ T − h`).
3. Promotion ssi `auc_cand ≥ max(auc_floor, auc_inc − ε)` et
   `cal_err_cand ≤ k × cal_err_inc`. Sinon on garde le sortant **et on
   alerte** (échecs répétés = décroissance d'edge, le signal qui manquait
   pour détecter « direction 15m passe sous 0.5 »).
4. La décision (métriques des deux camps, fenêtres, verdict) est journalisée
   dans `decisions.jsonl` — c'est la matière première de l'évaluation « du
   comportement réel de la ML dans le temps ».
5. On déploie **l'artefact mesuré** (entraîné jusqu'à `T − h`), pas une
   version ré-entraînée jusqu'à `T` : le chiffre du gate reste honnête pour
   le modèle réellement en production (h ≪ fenêtre, coût négligeable).
   Corollaire : ce même mécanisme remplace le « train final IS+OOS » de
   l'auto-optimiseur (`auto_optimizer.py` §S4-03, fuite assumée par design —
   elle n'a plus de raison d'être).

C'est la version sans fuite du « on garde le meilleur modèle pour cette
tranche » de la Solution 2 — jugé sur l'avant-tranche, jamais sur la tranche.

## 4. Les trois consommateurs

### 4.1 Backtest — paramètre explicite `ml_mode`, fini le repli silencieux

| mode | comportement | usage |
|---|---|---|
| `frozen` (défaut) | `resolve(as_of = début du df)` + **garde anti-chevauchement** : erreur si `[train_start, train_end]` intersecte la fenêtre backtestée (override explicite possible) | comparaison rapide et déterministe, seuils vs modèle fixe |
| `simulated_live` | politique complète (cadence + gate) rejouée aux frontières | backtests longs « comme en live », validation finale |
| `inline` | comportement actuel `use_pretrained_ml=False` | recherche, tests unitaires (inchangés) |

- `BacktestResult` gagne un bloc **`ml_info`** obligatoire : mode effectif,
  version(s) utilisée(s) (avec dates/AUC/âge), nombre de rafraîchissements et
  décisions de gate en mode simulé. Si `frozen` est demandé et que `resolve`
  échoue → **erreur claire**, plus jamais de bascule inline silencieuse
  (`backtest.py:652-656`).
- `WalkForwardAnalyzer` transmet `ml_mode` aux Backtester de ses folds — la
  fuite actuelle (fold servi par un modèle du futur) disparaît par
  construction via `as_of`.
- Répond à « traçabilité de la recette » : `ml_info` expose
  `recipe_hash`/`n_features`/`horizons`/`calibrated` sans script ad hoc.

### 4.2 Optimiseur — cible fixe par défaut, confirmation en politique simulée

- **Trials en `frozen`** : `resolve(as_of = début IS)`, le même artefact sert
  IS et OOS (cible fixe, méthodologiquement propre, zéro entraînement par
  trial → le poste de coût dominant des optimisations ML disparaît ;
  `optimizer_search.py:248-250` et `opt_workers.py:284` prennent le mode en
  paramètre au lieu du `False` câblé).
- **Passe de confirmation** : le best sort ensuite un run `simulated_live`
  sur l'OOS — on vérifie que les seuils tiennent face à un modèle qui se
  rafraîchit, avant d'écrire `optimizer_results`. Le YAML stocke la version
  de modèle utilisée (`model_version` pin) : la config promue est traçable
  jusqu'à l'artefact contre lequel elle a été optimisée.
- **`inline` reste disponible** pour la recherche explicite (comportement
  historique).
- **Espace de recherche purgé** : les dimensions `dir_min`/`dir_max`
  disparaissent des `PARAM_SPACES` par défaut (aucun signal mesuré — §5.4) ;
  `_freeze_from_results` reste le filet pour le reste.
- **Gel des features, symétrique du gel des params** (point de vigilance
  n°1) : un job de banc « feature screening » (importance/corrélation sur la
  fenêtre d'entraînement uniquement) écrit une **liste explicite committée**
  (`feature_list` de la recette, budget ~40-60) ; `features_hash` en meta.
  Le `prune_features` actuel (élagage a posteriori d'un modèle déjà entraîné
  sur 437 features, `ml/backend/trainer.py:302-308`) devient inutile sur les
  recettes à liste figée. Mesuré à l'appui : V11 437 features fait *moins
  bien* que V4 40 features (AUC amp 0.67-0.70 vs 0.76).

### 4.3 Live — le trainer devient mince

- `load_models` → `resolve(latest_promoted)` + **warning de fraîcheur** si
  `âge > 2 × cadence_bars`.
- `retrain_due` → `policy.maybe_refresh` par (TF, recette) avec
  la fenêtre de la recette (fetch borné par l'historique local :
  `min_window_bars` non atteint ⇒ on n'entraîne pas, on alerte — plus de
  modèle « au rabais » entraîné sur 1560 barres faute de mieux,
  `trainer.py:137`).
- Nouveau signal de santé : **log de prédictions live** (p_amp émise → issue
  réalisée h barres plus tard) → AUC/calibration *réalisées* glissantes par
  modèle. C'est la troisième jambe de « évaluer le comportement réel de la
  ML dans le temps : optimiseur (trials tracés), backtest (`ml_info`),
  live (réalisé vs prédit) ».

## 5. Réponses aux questions ouvertes

### 5.1 « Backtests fiables sur 2-3 ans reflétant le ré-entraînement ? »

`ml_mode=simulated_live` **est** la réponse : la politique (fenêtre, cadence,
gate) est rejouée aux frontières avec uniquement les données antérieures,
artefacts cachés dans le registre (premier run coûteux — ~une dizaine
d'entraînements 40k par an simulé et par TF —, les suivants quasi gratuits).
Deux honnêtetés à garder en tête : (a) même simulé, cela reste optimiste vs
le vrai live (données propres, pas de pannes de fetch) — le forward-test
(`oos_tracker`) reste juge de la fidélité ; (b) la recette elle-même a été
choisie en regardant l'historique — d'où l'intérêt de figer la recette
*avant* la période d'évaluation finale et de ne plus y toucher.

C'est aussi le banc qui transforme les « constats à prendre avec recul » en
mesures : sweep de fenêtre (10/20/40k) et de cadence (800/3000/10000 barres)
à recette constante, hold-out commun — l'expérience bricolée manuellement
pour V11 devient un run natif.

### 5.2 « La partie entraînement dédiée vaut-elle le coup ? »

Oui, **si l'unité de travail est la recette** : on y compare des recettes
(fenêtres, features, labels) sur hold-out commun, on committe la gagnante,
et le backtest/l'optimiseur consomment des artefacts produits par *cette même
recette* via le registre — plus rien d'« arbitraire ». Non, si c'est un labo
à peaufiner des artefacts individuels à la main : c'est la genèse du V4
(irréproductible), et le backtest simulé les écraserait de toute façon à la
frontière suivante.

### 5.3 « Comparer les AUC entre TF, valider sans fuite »

Au sein d'un TF, à définition de label constante : AUC (amp), PR-AUC,
`cal_err`, lift au seuil opérationnel, taux de signaux — sur hold-out commun
postérieur à toutes les fenêtres comparées. Entre TF : uniquement l'arbitre
économique (walk-forward `simulated_live`). L'anti-fuite n'est pas une
vérification a posteriori mais une propriété du protocole : hold-out
chronologique jamais vu, embargo `h`, gate jugé avant la tranche (§3.4).

### 5.4 « Et le modèle de direction ? »

Par défaut, **ne plus l'entraîner ni l'optimiser** (`heads: [amp]`) : AUC
mesurée au hasard dans les deux recettes, y compris in-sample — le filtre
d'amplitude est la seule porte d'entrée ML justifiée aujourd'hui. Le head
`dir` reste disponible dans la recette pour la recherche (ex. hypothèse
« dir n'a de signal qu'en régime TD » — AUC 0.87 revendiquée par le rapport
V4 historique, jamais re-vérifiée : c'est un run de banc, pas un défaut de
prod). Le retirer par défaut divise le coût d'entraînement par deux et purge
~⅓ des dimensions d'optimisation.

### 5.5 « Un même modèle pour plusieurs stratégies, ou séparés ? »

**Partagé — le modèle appartient à la recette, pas à la stratégie.** Les
labels (`amp_top_pct`, `label_horizons`) décrivent des propriétés du marché ;
aucun paramètre de décision de la stratégie n'entre dans l'entraînement
(`_TRAIN_PARAM_KEYS` ne contient que des hyperparamètres de recette). Deux
stratégies à recette identique produiraient le même modèle au bruit près :
l'entraîner deux fois coûte double et fait diverger aléatoirement deux
consommateurs du même signal.

Le code actuel vit déjà dans les deux régimes, sans le gérer :

- **Partage de fait** : `opus_stat_pretrained_v4`, `v7_pretrained`, `v8`,
  `v9`, `v10` pointent tous `model_dir` sur les mêmes fichiers
  `opus_stat_pretrained_v4_data/*.lgb` — cinq stratégies, un artefact.
- **Duplication de fait** : `v12` hérite de `v11` avec une recette amp/dir
  strictement identique mais ré-entraîne et persiste son propre
  `models/opus_omnibus_v12_{tf}` ; le `train_cache` ne mutualise même pas
  entre les deux (clé par `type(strategy).__module__`,
  `train_cache.py:94`).

Règles retenues :

- **Même recette ⇒ mêmes artefacts** (répertoire de registre par *recette*,
  §3.2). Gate, alertes de fraîcheur et santé sont par modèle — détectés une
  fois, pas N fois. Les différences entre stratégies restent dans leurs
  seuils/setups/params, là où elles ont un sens.
- **Recette différente ⇒ modèle séparé, automatiquement**
  (`ml_dynamic_threshold`, `scoring_statistique_opus_v4/v5`). V12 consomme
  deux recettes (l'omnibus partagée + son filtre de confirmation).
- **Séparation à la demande, par fork de recette** (nouveau nom ou bump de
  `recipe_version`) quand une stratégie veut expérimenter d'autres
  features/labels — jamais par duplication silencieuse d'entraînements.
- **Déploiement progressif via `pin`** : un consommateur peut rester épinglé
  sur la version N pendant qu'un autre valide la N+1.

Risque assumé du partage : la **corrélation des consommateurs** (une
promotion touche toutes les stratégies liées ; une décroissance du modèle
les dégrade ensemble). Cette corrélation existe déjà implicitement — toutes
les variantes omnibus tradent la même famille de signal — le registre la
rend explicite : l'allocateur de capital peut la lire (deux stratégies de
même recette ne diversifient pas), et la passe de confirmation post-promotion
(§4.2) se joue par stratégie consommatrice.

## 6. UI — page « Modèles » (les deux fronts)

Étendre `app/api/routes/ml.py` (aujourd'hui : `is_trained`/`best_auc`/
`next_retrain`) et décliner dans `app/web/templates/ml.html` +
`frontend/src/app/ml/` :

- **Registre** : tableau par (TF, recette) — colonne « entraîné sur »
  (provenance), version active, âge
  (barres/jours), AUC/cal_err de validation, recette (hash + résumé),
  historique des versions et des décisions de gate (promu/refusé, pourquoi).
- **Actions** : « Entraîner maintenant » (wrapper du runner), window sweep,
  épingler/rollback une version (pin), promouvoir manuellement (bypass
  journalisé, esprit `manual_active`).
- **Santé** : fraîcheur (badge si âge > 2×cadence), AUC réalisée glissante
  live vs AUC de validation (décrochage = drift), distribution des p_amp
  émises (un glissement de distribution entre versions casse le sens des
  seuils optimisés — à surveiller après chaque promotion).
- Endpoints : `GET /api/ml/registry`, `GET /api/ml/health`,
  `POST /api/ml/train`, `POST /api/ml/promote|pin`.

## 7. Migration incrémentale (chaque étape utile seule)

> **Mise à jour 2026-07-24 (implémentation) :** E1-E5 livrés intégralement,
> E3 inclus. E6 livré **partiellement** (voir détail sous le tableau — le
> flip de défaut, la passe de confirmation et le feature freezing restent à
> faire). E7 (UI) non commencé. Suite complète : 812 tests passés / 2 skip,
> aucune régression sur la base préexistante (746 tests avant ce chantier).

| # | Étape | Effort | Statut | Contenu / risque |
|---|---|---|---|---|
| E1 | Meta v2 + `ml_info` + warning fraîcheur | S | ✅ fait | `persistence.py` (`provenance`/`gate` optionnels, rétro-compat v1), `BacktestResult.ml_info`, `MLStrategyTrainer._freshness_warning` |
| E2 | Registre + layout `(TF, recette)` + `resolve(as_of)` + garde anti-chevauchement | M | ✅ fait | `app/ml/model_registry.py`. **Révisé 2026-07-26** : la dimension symbole a quitté la clé (décision 11), le repli sur l'ancien layout plat a été supprimé (décision 4) et le pack V4 a été archivé après mesure (décision 1) |
| E3 | Runner CLI committé + window sweep | S | ✅ fait | `app/ml/train_runner.py` + `scripts/train_model.py` — dry-run par défaut (rien n'est écrit), `--publish` pour la publication gatée réelle, `--windows` pour le sweep sur holdout commun |
| E4 | Gate + `decisions.jsonl` + cadence en barres dans le trainer live | M | ✅ fait (cadence **partielle**) | `app/ml/policy.py` (`decide_gate`, `maybe_refresh`, AUC par rang sans sklearn/scipy) câblé dans le live trainer et le runner. Le live trainer garde une cadence **horloge murale** (`retrain_interval_h`, inchangé) plutôt que barres — voir note ci-dessous |
| E5 | `ml_mode=simulated_live` (Backtester + WalkForward) avec cache registre | M/L | ✅ fait | `Backtester.ml_mode` (`frozen`/`inline`/`simulated_live`) ; rafraîchissement périodique à même la boucle bar-par-bar, publication registre à chaque frontière de cadence ; `WalkForwardAnalyzer` transmet `ml_mode` par fold (la fuite `as_of` par fold est corrigée par construction, sans code spécifique aux folds) |
| E6 | Optimiseur `frozen` par défaut + passe de confirmation + purge dims dir + feature freezing | M | 🟡 partiel | Voir détail ci-dessous |
| E7 | UI Modèles + santé (les 2 fronts) | M | ⬜ non commencé | Pure surface, après E2/E4 — endpoints `app/api/routes/ml.py` à étendre (registre, décisions, fraîcheur), template `app/web/templates/ml.html` + page `frontend/src/app/ml/` |

### Détail E6 — ce qui est fait vs restant

Fait :
- `OptimizerSearchEngine.ml_mode` lu depuis `cfg["optimizer"]["ml_mode"]`
  (in-process **et** worker subprocess, même clé après désérialisation YAML) —
  remplace le `use_pretrained_ml=False` câblé en dur.
- Purge de `setup_*_dir_min`/`setup_*_dir_max` de `param_space` pour
  `opus_omnibus_v11` (mesuré directement) — `opus_omnibus_v12` en hérite
  automatiquement (`param_space = {**_V11Strategy.param_space, ...}`).
  Vérifié sans risque runtime : `_apply_setup_overrides` a son propre
  défaut câblé en dur (`_DEFAULT_SETUPS`), appliqué dès qu'une clé est
  absente des params résolus.

Restant, délibérément hors scope de cette passe (pas une conclusion
définitive — juste pas fait) :
- **Défaut `ml_mode` NON basculé sur `"frozen"`** pour l'optimiseur — reste
  `"inline"` (comportement historique). Le flip de défaut change la
  méthodologie de fond (cible fixe vs walk-forward réel par trial) pour
  potentiellement tous les runs existants ; laissé en opt-in explicite
  (`cfg["optimizer"]["ml_mode"] = "frozen"`) plutôt qu'imposé silencieusement.
- **Purge dir_min/dir_max non étendue** à `opus_omnibus_v7`/
  `opus_omnibus_v10_retrained`/`opus_omnibus_v11_followsetup` — même
  architecture partagée (label/feature builder identique par construction,
  cf. `app/ml/backend/features.py`), conclusion probablement transposable,
  mais leur `param_space`/routing interne n'a pas été relu ligne à ligne
  avant de toucher au comportement de l'optimiseur dessus.
- **Passe de confirmation post-optimisation** (§4.2 : re-jouer le best en
  `simulated_live` sur l'OOS avant d'écrire `optimizer_results`) — pas
  construite. Le train final leaky de `auto_optimizer.py` (S4-03, IS+OOS)
  n'a pas été retiré.
- **Feature freezing** (job de screening → liste figée committée dans la
  recette, symétrique du gel des params déjà fait par l'optimiseur) — pas
  construit ; `prune_features` (élagage a posteriori) reste le seul
  mécanisme en place.

### Note E4 — cadence live restée horloge murale, pas barres

Le trainer live (`MLStrategyTrainer`) continue de planifier ses cycles via
`retrain_interval_h` (heures, inchangé) plutôt que de compter les barres —
seul le **contenu** de chaque cycle a changé (gate via `maybe_refresh` au
lieu d'un `fit()+save_model()` aveugle), pas son **déclenchement**. Basculer
la cadence elle-même en barres aurait nécessité de faire correspondre le
scheduler live à la grille de bougies par TF, un chantier plus large
(recoupe le "chantier connexe" ci-dessous) volontairement laissé de côté ici.

~~Chantier connexe : **état ML par (symbole, TF)**~~ — **abandonné après
mesure** (2026-07-26, décision 11). Ce chantier était présenté comme le
prérequis de « modèles réellement par symbole ». La mesure a montré que ces
modèles n'apportent rien de mesurable sur le panier actuel : 17 des 18
cellules de la matrice de transfert sont indiscernables du bruit, ETH ne gagne
rien à son propre modèle, et XRP n'a pas assez d'historique pour en avoir un.
L'état ML keyé `tf` seul n'est donc plus une approximation à corriger — c'est
la bonne granularité, et le registre a été aligné dessus (le symbole a quitté
la clé pour la provenance).

À rejouer — et alors seulement à rouvrir — si un actif d'une **autre classe**
(action, ETF) et doté d'assez d'historique entre dans le bot :
`scripts/measure_symbol_transfer.py`.

## 8. Défauts proposés — hypothèses, pas des réglages

À calibrer au banc (E5) avant d'en faire des valeurs de référence, précisément
parce que la campagne d'origine n'avait pas de phase d'optimisation :
fenêtres `40k` (15m/30m) / `25k` (1h) / `max dispo` au-delà avec plancher
`20k` ; cadence `2000-4000` barres ; gate `ε=0.01`, `auc_floor=0.55`,
`holdout=1500` barres ; budget features `40-60`. Le coût d'un artefact
(LightGBM 40k×~60 features, 1-2 heads) se chiffre en minutes — c'est le
registre-cache, pas la puissance de calcul, qui rend l'ensemble praticable.
