# Conception — Architecture unifiée : entraînement, gestion et exploitation des modèles

> **Statut** : analyse et proposition. Aucun code applicatif modifié par ce
> document. **Révision 2** — intègre deux informations qui changent les
> conclusions : (a) le bot n'est **pas en production**, donc aucune contrainte
> de rétrocompatibilité ; (b) le pack V4 figé n'a de valeur que s'il bat un
> ré-entraînement — hypothèse désormais **mesurée** (§1.5), et fausse.
>
> **Cadre** : fait suite à `CONCEPTION_CYCLE_DE_VIE_ML.md` (ML-02). Le socle de
> ML-02 est livré et n'est pas remis en cause : registre daté, gate de
> promotion, politique unique, `ml_mode`, page Modèles. Ce document traite ce
> que ML-02 avait **posé en §3.1 et §5.5 sans jamais le construire** — la
> recette comme objet de premier ordre — et en tire les conséquences sur
> l'ensemble de `app/strategies/`.
>
> **Périmètre** : les besoins ML d'abord (§1 à §7). Les stratégies sans modèle
> sont traitées en §8 comme sous-objectif explicitement non prioritaire.

---

## 0. Méthode

Tout chiffre de ce document est mesuré sur l'arbre à `f6fcc2a`, pas estimé.
Les mesures structurelles (identité de fonctions, lignes par responsabilité)
sont faites par comparaison d'AST — deux fonctions sont dites identiques si
leur AST sérialisé est égal, ce qui ignore commentaires et mise en forme mais
**pas** les renommages de variables. Quand une comparaison d'AST suggère une
divergence, le diff textuel est lu avant d'en conclure quoi que ce soit :
c'est précisément le piège qui, lors de la factorisation des helpers V4, avait
fait passer six copies identiques pour six variantes divergentes.

La mesure de §1.5 est reproductible :
`PYTHONPATH=. python scripts/compare_legacy_vs_retrained.py`.

Les seules valeurs **estimées** du document sont les projections de volume de
§7 ; elles sont dérivées de lignes mesurées et l'arithmétique est montrée.

---

## 1. État des lieux mesuré

### 1.1 Le décompte

| | |
|---|---|
| Fichiers de stratégie | 46 (`app/strategies/*.py`) |
| Fichiers de paramètres | 45 (`strategies/*.yaml`) |
| Stratégies ML | **14** |
| Fichiers portant les familles ML et leurs variantes | **19**, soit **12 963 lignes = 58 %** du répertoire |
| Modules ML | 13, 4 187 lignes (`app/ml/`) |
| **Artefacts présents dans le registre** | **3** — `BTC_USDC/{15m,30m,1h}/opus_stat_pretrained_v4/legacy` |
| Fichiers modèle sur disque | 6 `.lgb` + 3 `.meta.json` |

**13 000 lignes de stratégies ML tournent aujourd'hui autour de trois
artefacts, tous `legacy`, tous issus du même pack V4 de mai 2026.** Le poids
est dans le code de liaison, pas dans les modèles.

### 1.2 Trois axes de duplication superposés

Les 19 fichiers ne sont pas 19 idées. Ils sont **10 générations de routing**
croisées avec **la manière dont le modèle est obtenu** :

| Génération de routing | modèle figé (pack V4) | modèle ré-entraîné | proxy d'indicateurs |
|---|---|---|---|
| `opus_stat_v4` | `opus_stat_pretrained_v4` | `opus_stat_retrained_v4` | — |
| `omnibus_v7` | `opus_omnibus_v7_pretrained` | `opus_omnibus_v7` | — |
| `omnibus_v8` | `opus_omnibus_v8` | — | `opus_omnibus_v8_no_ml` |
| `omnibus_v9` | `opus_omnibus_v9` | — | — |
| `omnibus_v10` | `opus_omnibus_v10` | `opus_omnibus_v10_retrained` | `opus_omnibus_v10_no_ml` |
| `omnibus_v11` | — | `opus_omnibus_v11` | `opus_omnibus_v11_no_ml` |
| `omnibus_v11_followsetup` | — | `opus_omnibus_v11_followsetup` | `opus_omnibus_v11_followsetup_no_ml` |
| `omnibus_v12` | — | `opus_omnibus_v12` | — |
| `dyn_threshold` | — | `ml_dynamic_threshold` | `dynamic_threshold_no_ml` |
| `scoring_stat` | — | `scoring_statistique_opus_v4`, `_v5` | — |

**La colonne n'est pas une idée de stratégie : c'est une source de
prédiction.** Aujourd'hui elle coûte un fichier complet.

Trois observations valident cette lecture :

1. **Cinq stratégies, un seul artefact.** `opus_stat_pretrained_v4`,
   `opus_omnibus_v7_pretrained`, `v8`, `v9` et `v10` appellent le même
   `_load_pretrained()` (`opus_stat_pretrained_v4.py:130`, importé par les
   quatre autres) → `registry.resolve(pin="legacy")`. Un modèle, cinq
   routings, cinq fichiers. `app/api/services/scanner_service.py:189` s'y
   branche aussi directement.

2. **Les setups sont déjà identiques.** Les huit setups (`SIGNAL_UP`,
   `SHORT_TD_HIGH`, `LONG_CHOPPY`, `SHORT_CHOPPY`, `LONG_RANGE_STRICT`,
   `LONG_RANGE_LIGHT`, `LONG_TU`, `LONG_EXIT_TD`) sont les mêmes dans
   `opus_omnibus_v10`, `v10_retrained`, `v10_no_ml`, `v11` et `v11_no_ml`.

3. **Ce qui diverge dans une paire, c'est l'accès au modèle, pas la décision.**
   Pour la paire v10, le cœur de décision est **byte-identique** :
   `_evaluate_setup`, `_select_setup`, `_apply_setup_overrides`,
   `_check_early_exit`. `score()` diffère sur 113 lignes, mais le diff ne
   contient que le garde `_ensure_loaded()` (chemin figé), `min_bars_required()`
   vs `min_bars_required(params)`, `_FEATURE_BUILDER.build(window)` vs
   `_build_features(_window_polars(...))`, et l'alignement des `=`. **Aucune
   règle de trading ne diffère.**

### 1.3 Où part le code

Classement par AST de chaque fonction en **plomberie ML** (cycle de vie du
modèle) ou en **routing** (setups, seuils, sizing, sorties) :

| stratégie | total | plomberie | routing | % plomberie |
|---|---:|---:|---:|---:|
| `opus_stat_pretrained_v4` | 754 | 84 | 670 | 11 % |
| `opus_stat_retrained_v4` | 773 | **295** | 478 | **38 %** |
| `opus_omnibus_v7` | 883 | **276** | 607 | **31 %** |
| `opus_omnibus_v7_pretrained` | 640 | 64 | 576 | 10 % |
| `opus_omnibus_v8` | 710 | 58 | 652 | 8 % |
| `opus_omnibus_v9` | 738 | 59 | 679 | 8 % |
| `opus_omnibus_v10` | 743 | 59 | 684 | 8 % |
| `opus_omnibus_v10_retrained` | 952 | **265** | 687 | **28 %** |
| `opus_omnibus_v11` | 756 | 71 | 685 | 9 % |
| `opus_omnibus_v11_followsetup` | 1099 | **303** | 796 | **28 %** |
| `opus_omnibus_v12` | 228 | 16 | 212 | 7 % |
| `ml_dynamic_threshold` | 972 | **316** | 656 | **33 %** |
| `scoring_statistique_opus_v4` | 784 | **287** | 497 | **37 %** |
| `scoring_statistique_opus_v5` | 710 | **243** | 467 | **34 %** |
| **TOTAL** | **10 742** | **2 396** | **8 346** | **22 %** |

**Les stratégies qui composent `MLBackend` paient 7–11 % de plomberie ; celles
qui ne le font pas en paient 28–38 %.** `MLBackend` résout déjà le problème —
il n'est utilisé que par 5 stratégies sur 14, et même là via une couche de
propriétés de transfert (`opus_omnibus_v11.py:352-395` expose 20 propriétés qui
ne font que relayer `self.ml.state.*`).

### 1.4 Les fractures actuelles

**(a) Des métriques fabriquées, dupliquées cinq fois.** Les cinq stratégies
figées écrivent des littéraux dans leur `train_meta` :

```python
self._best_auc_per_tf = {"15m": 0.626, "30m": 0.597, "1h": 0.603}
"auc_amp": {"15m": 0.749, "30m": 0.690, "1h": 0.676}.get(tf, 0.0),
"auc_dir": {"15m": 0.503, "30m": 0.504, "1h": 0.530}.get(tf, 0.0),
```

Copié-collé identique dans `opus_stat_pretrained_v4.py:413`,
`opus_omnibus_v7_pretrained.py:346`, `v8.py:372`, `v9.py:415`, `v10.py:406`.
Ces nombres vivent aussi dans les `model.meta.json`. Deux sources de vérité,
dont une en dur dans cinq fichiers — et c'est celle-là qui remonte dans l'UI.
**§1.5 montre qu'elle est en plus fausse aujourd'hui.**

**(b) La clé de registre est le nom de la stratégie, pas la recette.**

```
app/ml/policy.py:243        recipe = recipe or getattr(strategy, "name", "strategy")
app/engine/backtest.py:972  params=sle["params"], recipe=sle["strat"].name,
app/ml/trainer.py:209       ..., params=sp, recipe=name, ...
app/ml/train_runner.py:111  ..., recipe=strategy_name, ...
```

Quatre sites, aucun paramétrable. « Même recette ⇒ mêmes artefacts »
(ML-02 §5.5) est donc inapplicable : cinq consommateurs du pack V4 ne peuvent
pas partager une entrée de registre, et `v12` réentraîne une recette
strictement identique à celle de `v11`.

**(c) La recette n'existe pas comme objet.** ML-02 §3.1 spécifiait un bloc
`model:` dans le YAML de stratégie. Vérification : **45 YAML, aucun bloc
`model:` ; pas de répertoire `recipes/` ; ni `recipe_version`, ni
`features_catalog`, ni `feature_list`, ni `cadence_bars` n'existent dans le
code.** Ce qui en tient lieu est un mélange, dans le même espace de noms
`params:`, des seuils de décision et des hyperparamètres d'entraînement —
`strategies/opus_omnibus_v11.yaml` fait cohabiter `setup_signal_up_amp_min` et
`n_estimators`.

**(d) L'optimiseur peut changer la recette sans que le registre le sache.**
Trois stratégies exposent à l'optimiseur des clés qui entrent dans
l'entraînement : `adx_threshold` (`scoring_statistique_opus_v4`, `_v5` — c'est
un argument de `_build_features(df, adx_threshold)`, donc les features
changent) et `lookahead` (`ml_dynamic_threshold` — horizon de labellisation).
Or `_RECIPE_PARAM_KEYS` (`app/ml/policy.py:53`) ne contient ni l'un ni l'autre.
Deux essais produisent donc **des modèles différents, sous la même clé de
registre et le même `recipe_hash`.**

**(e) Les variantes `_no_ml` ont dérivé en silence.** Sur les 9 fonctions
communes à `opus_omnibus_v11` et `opus_omnibus_v11_no_ml`, **8 divergent**.
Une partie est cosmétique (`setup` → `s`), mais pas tout : la variante `_no_ml`
a perdu les conditions `needs_bearish_excess` et `needs_rsi_below`, et a
fusionné le test `regime == REGIME_TREND_DN` dans la garde `exit_td`. Les huit
setups sont pourtant déclarés identiques. **Ce sont des forks figés d'un
routing qui a continué d'évoluer** — un écart de comportement non documenté.

**(f) Deux couches de cache de features en parallèle.** Le `FeatureStore`
(disque, partagé, indexé par catalogue) coexiste avec un cache par instance
`_bt_features` / `_bt_features_len` / `_bt_train_offset` — **86 occurrences
dans `app/strategies/`**, et `prepare_for_backtest` réimplémenté dans **13
stratégies ML** (31 stratégies au total). Les deux stockent les mêmes 462
colonnes.

### 1.5 Le pack V4 legacy ne gagne plus — mesuré

C'est le test décisif : ce pack ne mérite le code qui le maintient en vie que
s'il bat un ré-entraînement. Protocole — les deux artefacts scorés sur le
**même holdout** (1 500 dernières barres BTC/USDC), avec la **même convention
de labels** (`label_horizons=[1]`, `amp_top_pct=0.30`, celle déclarée par la
recette V4), candidat entraîné strictement avant le holdout. C'est exactement
ce que fait `policy.decide_gate` en production.

| TF | n_train | LEGACY `auc_amp` | RETRAIN `auc_amp` | LEGACY `auc_dir` | RETRAIN `auc_dir` |
|---|---:|---:|---:|---:|---:|
| 15m | 52 851 | 0.598 | **0.638** | 0.467 | **0.529** |
| 30m | 50 676 | 0.656 | **0.674** | 0.524 | 0.519 |
| 1h  | 49 738 | 0.600 | **0.663** | 0.482 | **0.530** |

**Le ré-entraînement bat le pack figé sur 3 TF / 3**, avec des marges de
+0.018 à +0.063 sur `auc_amp` — au-dessus de l'`epsilon` du gate (0.010) dans
les trois cas. Le gate promouvrait donc le candidat partout.

Deux corollaires que la mesure impose :

- **Les littéraux de la fracture (a) surestiment fortement la performance
  actuelle.** Le pack s'auto-déclare `auc_amp` = 0.749 / 0.690 / 0.676 (chiffres
  de son entraînement de mai) ; sur un holdout de juillet il obtient 0.598 /
  0.656 / 0.600. **Jusqu'à −0.15 d'écart**, affiché dans l'UI comme s'il
  s'agissait de sa performance courante. Ce n'est pas une erreur de code : c'est
  ce que devient un chiffre d'entraînement figé qu'on recopie à la main.
- **Sa tête directionnelle est sous le hasard.** `auc_dir` = 0.467 en 15m et
  0.482 en 1h : moins bien qu'une pièce. C'est cohérent avec la purge
  `dir_min`/`dir_max` décidée précédemment, et cela répond définitivement à la
  question du `p_dir` V4 — il n'y a rien à récupérer de ce côté-là.

**Réserve honnête** : un symbole, un holdout, une fenêtre. Ce n'est pas une
campagne. Mais 3/3 avec des marges au-dessus d'`epsilon`, plus une histoire de
décroissance cohérente (entraînement mai, holdout juillet), suffit pour une
décision d'architecture. Et si le résultat s'inversait un jour, le mécanisme
qui le dirait est précisément le gate — pas cinq fichiers de stratégie.

---

## 2. Diagnostic : une seule cause

Les six fractures ont la même racine :

> **La classe de stratégie est propriétaire de son modèle.**

Elle possède aujourd'hui, dans un seul fichier : le routing, la construction
des features, la boucle d'entraînement, le format de persistance, le cache de
features, l'état ML en mémoire, la clé de registre, l'espace de recherche de
l'optimiseur et le slot de cycle de vie. Toucher **un** de ces axes impose un
nouveau fichier — d'où les trois axes de duplication de §1.2.

ML-02 a correctement extrait **l'artefact** (registre) et **la décision de
promotion** (gate). Il n'a extrait ni **la recette** ni **le prédicteur**. Que
le travail récent ait dû procéder par contrats rapportés à la classe
(`gate_spec`, `score_holdout` surchargeable) confirme le diagnostic : on décrit
à la stratégie ce que la recette devrait porter.

---

## 3. Ce qui complique sans rien apporter — à supprimer

Sans contrainte de production, ces éléments n'ont pas à être migrés,
dépréciés ni maintenus. Ils sont à **retirer**.

### 3.1 Tout ce qui existe pour le pack V4 figé

§1.5 le rend obsolète. Ce qui disparaît avec lui :

| élément | lignes | rôle |
|---|---:|---|
| `opus_stat_pretrained_v4.py` | 754 | consomme le pack |
| `opus_omnibus_v7_pretrained.py` | 640 | consomme le pack |
| `opus_omnibus_v8.py` | 710 | consomme le pack |
| `opus_omnibus_v9.py` | 738 | consomme le pack |
| `opus_omnibus_v10.py` | 743 | consomme le pack |
| `app/ml/lgb_logging.py` | 86 | filtre `bagging_by_query` émis par les `.lgb` du pack |
| `registry._legacy_artifact` + `import_legacy` | 67 | résolution / import du pack |
| `_load_pretrained`, `_PRETRAINED_CACHE`, `_FeatureBuilder`, `_to_pandas_window` | ~24 réf. | accès et wrappers de compat |
| `ArtifactRef.legacy`, `pin="legacy"`, branche legacy de `freshness_warning`, `overlap_warning` | épars | sémantique « modèle sans provenance datée » |
| littéraux d'AUC ×5 (fracture a) | 15 | métriques recopiées à la main |
| `scripts/migrate_v4_to_registry.py` | — | migration achevée, à usage unique |

**≈ 3 760 lignes**, plus les branches éparses et les références dans 10
fichiers de test.

**Nuance importante — supprimer le pack ≠ supprimer les routings.** `v8`, `v9`
et `v10` n'existent qu'en version figée. Deux options, et c'est une **décision
produit, pas technique** :

- **retirer** ces générations (elles sont antérieures à v11/v12, qui les
  remplacent fonctionnellement) ; ou
- **les rebrancher** sur la recette ré-entraînée — mais elles deviennent alors
  des stratégies jamais backtestées sous cette forme, à re-valider.

`v7_pretrained` et `opus_stat_pretrained_v4` ont chacun un jumeau ré-entraîné
vivant : pour eux la suppression est sans perte.

Le pack lui-même (6 `.lgb` + 3 `.meta.json`) peut rester sur disque comme
archive morte, hors du chemin de résolution. Il ne coûte rien tant qu'aucun
code ne le cherche.

### 3.2 Le code de rétrocompatibilité pur

Écrit pour ne pas casser des appelants ; sans production, il n'y a rien à ne
pas casser.

- **`use_pretrained_ml`** (7 réf. dans `backtest.py`) — booléen historique
  traduit en `ml_mode`. `ml_mode` devient l'unique levier, **obligatoire** :
  un mode implicite est précisément ce qui a produit le « repli silencieux »
  que ML-02 §4.1 voulait supprimer.
- **Ré-exports et alias de `app/ml/policy.py`** — le bloc
  `from app.ml.scoring import (...)  # noqa: F401` et `recipe_gate_defaults`,
  documentés « conservés pour les appelants et tests existants ». Les appelants
  importent `app.ml.scoring`.
- **Arguments directs `label_horizons` / `amp_top_pct` de `score_holdout`** —
  doublon de `gate_cfg`, gardés pour « appelants et tests historiques ».
- **Repli sur l'ancien layout plat** dans `resolve()` — `models/{recipe}_{tf}.*`
  n'existe plus sur disque.
- **`unsupported_format`** (4 réf., `scoring.py` → `policy.py` → les deux UI) —
  une devinette de format. Avec `persistence:` déclaré dans la recette (§4.3),
  le format est **connu**, jamais reniflé.

### 3.3 Ce que l'architecture cible rend caduc

Pas à supprimer aujourd'hui, mais à ne pas reconstruire :

- **`managed_externally`** — drapeau qui dit à une stratégie « ne te
  ré-entraîne pas toi-même, quelqu'un d'autre te gère ». Il n'existe que parce
  que la stratégie décide de s'entraîner. Avec l'injection de prédicteur, elle
  ne décide plus rien : le drapeau n'a plus d'objet.
- **La double couche de cache de features** (fracture f) — `prepare_for_backtest`
  et le triplet `_bt_features` / `_bt_features_len` / `_bt_train_offset`
  disparaissent des stratégies ; le `FeatureStore` reste seul, indexé par
  catalogue de recette.
- **La clé de `train_cache`** (`type(strategy).__module__`,
  `app/core/train_cache.py:94`) — après fusion des routings, elle cesserait de
  discriminer. Elle doit devenir `recipe_hash` + fenêtre, sinon le cache
  produit des faux positifs.

### 3.4 Ce qui pose question mais n'est pas tranché ici

Signalé par honnêteté, sans recommandation faute de mesure :

- **Calibration isotone** (`app/ml/backend/isotonic.py`, 169 L) et **élagage de
  features** (`prune_features`) sont activés par défaut. Aucune mesure au dépôt
  ne montre leur apport. Ils sont peu coûteux à garder ; ils méritent **une**
  expérience (même protocole que §1.5, `calibrate=False` / `prune_features=False`
  en comparaison) avant d'être considérés comme acquis.
- **La dimension `symbole` du registre** est portée de bout en bout, mais le
  live l'écrase : `_resolve_symbol` « préfère BTC » (`app/ml/trainer.py:233`).
  Une dimension présente dans les chemins et jamais renseignée est un piège —
  elle donne l'illusion d'un modèle par symbole. **Soit l'exploiter, soit la
  retirer** ; la porter à moitié est le pire des trois.
- **`window_sweep`** (module + API + UI, 31 réf.) est un vrai outil (choisir la
  fenêtre d'entraînement sur holdout commun). Il n'est pas en cause ; il est
  simplement à rebrancher sur la recette plutôt que sur le nom de stratégie.

### 3.5 Ce qui est validé et reste tel quel

- **Le registre et le gate.** L'expérience de §1.5 *est* `decide_gate`. Le fait
  qu'elle réponde à une question d'architecture en dix minutes est la
  démonstration de leur valeur.
- **`ml_mode` et ses trois valeurs**, `simulated_live` compris : c'est le seul
  moyen de backtester la politique de rafraîchissement.
- **`app/ml/backend/`** — features, trainer, predictor, persistance. Bon
  découpage ; il gagne des appelants, il n'en perd pas.
- **Le contenu des routings.** Rien ici ne propose de retoucher une règle de
  trading.

---

## 4. Architecture cible

### 4.1 Quatre objets, quatre responsabilités

```
        RECETTE  (fichier git, nommée, hachée)
        « quelles features, quels labels, quels HP, quelle fenêtre, quel gate »
             │
             │ entraînement (une seule implémentation)
             ▼
        ARTEFACT  (registre, immuable, daté)          ← existe déjà (ML-02)
        « ce modèle-là, entraîné le …, sur …, promu parce que … »
             │
             │ résolution : ml_mode (frozen | inline | simulated_live) + as_of + pin
             ▼
        PRÉDICTEUR  (objet runtime, contrat unique)
        « features → {amp: …, dir: …} »
             │
             │ injection
             ▼
        ROUTING  (app/strategies/*.py — et rien d'autre)
        « setups, seuils, sizing, sorties »
```

L'artefact et la politique de résolution existent. **Les deux objets à créer
sont la recette et le prédicteur** — et c'est le prédicteur qui fait
disparaître les colonnes du tableau §1.2.

### 4.2 Le contrat de prédicteur

```python
# app/ml/predictor.py
class Predictor(Protocol):
    heads: tuple[str, ...]          # ("amp", "dir") ; ("dir",) pour dyn_threshold
    recipe: str | None              # None = aucun artefact (proxy)
    version_id: str | None          # traçabilité UI / ml_info

    def predict_last(self, features, tf: str) -> dict[str, float]: ...
    def predict_series(self, features, tf: str) -> dict[str, np.ndarray]: ...
```

Trois implémentations, **toutes déjà présentes dans le code, à extraire** —
la quatrième (`ScalerLgbm`, pour le pack figé) meurt avec §3.1 :

| implémentation | source actuelle | consommateurs |
|---|---|---|
| `LgbmBundlePredictor` | `persistence.load_amp_dir_bundle` | v7, v10, v11, v11_followsetup, stat_v4 |
| `LgbmScalerPredictor` | `persistence.load_lgb_with_scaler` | `scoring_statistique_opus_v4`, `_v5` |
| `LgbmSinglePredictor` | `.lgb` + `.meta.json` de `ml_dynamic_threshold` | `ml_dynamic_threshold` |
| `ProxyPredictor` | `_proxy_p_up` / `_proxy_p_event` | les 5 variantes `_no_ml` (§8) |

La dernière ligne unifie tout : `_proxy_p_up(...) -> float` a **exactement la
signature de sortie de `predict_direction`**. Une variante « sans ML » n'est pas
une autre stratégie — c'est le même routing branché sur un prédicteur qui ne
consulte aucun artefact.

Ce contrat remplace, par stratégie : `_predict`, `predict_amplitude`,
`predict_direction`, `_predict_series`, `load_model`, `save_model`,
`reset_model`, `is_trained`, `managed_externally`, `_tf_from_path`,
`prepare_for_backtest` — l'essentiel des **2 396 lignes** de §1.3.

### 4.3 La recette comme fichier

`recipes/omnibus_amp_dir_v1.yaml` — l'objet de ML-02 §3.1, enfin construit :

```yaml
recipe: omnibus_amp_dir_v1
version: 1                          # bump manuel → nouveau hash → nouvelle lignée
features:
  catalog: v4_polars@1              # catalogue FeatureStore existant
  params: {}                        # tout ce qui change les features vit ICI
labels:
  horizons: [1, 3, 6]
  amp_top_pct: 0.30
heads: [amp, dir]
hp:
  n_estimators: 500
  num_leaves: 31
  learning_rate: 0.03
  seed: 42
window:
  bars: {15m: 40000, 30m: 40000, 1h: 25000, default: max}
  min_bars: 20000
  cadence_bars: 3000
gate:
  metric: auc_amp
  holdout_bars: 1500
  auc_floor: 0.55
  epsilon: 0.01
persistence: lgbm_amp_dir_bundle    # ← choisit le Predictor au chargement
```

Trois propriétés que le modèle actuel n'a pas :

- **`recipe_hash` couvre tout ce qui change le modèle**, `features.params`
  compris — ce qui referme la fracture (d). Balayer `adx_threshold` devient
  balayer des **recettes**, chacune avec sa lignée d'artefacts.
- **La recette est indépendante de la stratégie**, donc « même recette ⇒ mêmes
  artefacts » devient exécutable : un entraînement, un gate, une alerte de
  fraîcheur, une ligne d'UI par recette. Les littéraux de la fracture (a)
  disparaissent — seule source : `model.meta.json`.
- **`persistence:` désigne le prédicteur**, ce qui supprime `unsupported_format`.

### 4.4 La liaison, et ce que devient une stratégie

Dans `strategies/opus_omnibus_v10.yaml` :

```yaml
models:
  signal: omnibus_amp_dir_v1        # nom de recette, ou "proxy:indicators"
params:
  setup_signal_up_amp_min: 0.50     # seuils de DÉCISION — plus aucun HP ici
```

Et la stratégie :

```python
class Strategy(BaseStrategy):        # BaseStrategy, plus BaseStrategyML
    name = "opus_omnibus_v10"
    models = {"signal": "omnibus_amp_dir_v1"}    # défaut, surchargeable par YAML

    def score(self, df, params=None, df_htf=None, symbol=""):
        pred = self.predictors["signal"]          # injecté par le runtime
        out  = pred.predict_last(self.features(df), tf)
        p_event, p_up = out["amp"], out["dir"]
        ...                                       # routing pur, inchangé
```

Le tableau §1.2 devient de la configuration :

| aujourd'hui | demain |
|---|---|
| `opus_omnibus_v10_retrained` | `opus_omnibus_v10` + `models: {signal: omnibus_amp_dir_v1}` |
| `opus_omnibus_v10_no_ml` | `opus_omnibus_v10` + `models: {signal: proxy:indicators}` |
| `opus_omnibus_v10` (figé) | *supprimé* (§3.1) |

Une correction du routing v10 s'applique désormais aux deux — ce qui referme
la fracture (e) par construction.

`v12` illustre le cas multi-modèles, prévu par ML-02 §5.5 :

```yaml
models:
  signal: omnibus_amp_dir_v1        # partagé avec v11 — fini le double entraînement
  filter: dyn_threshold_v1
```

---

## 5. Ce que ça change pour les quatre consommateurs

**Backtest.** `ml_mode` garde ses trois valeurs et devient **obligatoire** (plus
de repli sur `use_pretrained_ml`). Il choisit comment le prédicteur est
fabriqué : `frozen` → `registry.resolve(as_of=…)` ; `inline` → entraînement de
la recette sur la fenêtre ; `simulated_live` → `policy.maybe_refresh` aux
frontières de cadence. `ml_info` gagne `recipe` et `version_id` par tête.

**Optimiseur.** Deux gains. La surface de recherche cesse d'être gonflée par
les variantes — de 19 fichiers découverts par `_discover_strategies()` à 7 ou 9
routings selon la décision de §3.1. Et la séparation `params:` / recette rend
explicite la question « optimise-t-on les seuils, ou la recette ? », aujourd'hui
tranchée par accident (fracture d).

**Live.** `MLStrategyTrainer` itère sur les **recettes distinctes** référencées
par les stratégies actives, pas sur les stratégies : un timer, un gate, une
alerte de fraîcheur par recette. À trancher au passage : la dimension symbole
(§3.4).

**UI Modèles.** Elle liste des recettes plutôt que des stratégies — ce que
`registry.list_recipes()` renvoie déjà, à ceci près que « recette » y vaut
aujourd'hui « nom de stratégie ». Chaque ligne gagne ses consommateurs. Les
métriques viennent d'une seule source, et cessent donc d'afficher 0.749 pour un
modèle qui vaut 0.598 (§1.5).

---

## 6. Migration — cible directe, pas paliers de compatibilité

La révision 1 proposait six paliers rétrocompatibles. **Sans production, c'est
du coût pur** : chaque palier compatible demande un chemin de repli qu'il
faudra retirer ensuite. La séquence ci-dessous vise directement la cible ;
chaque étape laisse le dépôt vert et cohérent, mais aucune ne préserve d'API
historique.

**A. Purge du legacy (§3.1 + §3.2).** À faire en premier : elle retire 5 des 19
fichiers, donc tout ce qui suit travaille sur une surface plus petite. Prérequis :
trancher la décision produit sur `v8`/`v9`/`v10` (retirer vs rebrancher).
Inclut la suppression de `use_pretrained_ml`, des ré-exports de `policy.py`, du
repli layout plat et d'`unsupported_format`. `ml_mode` devient obligatoire.

**B. Recette + prédicteur, en une seule passe.** Aucune raison de les séparer :
c'est le contrat `persistence:` de la recette qui choisit le prédicteur. Livre
`recipes/*.yaml`, le chargeur, `recipe_hash` canonique, `app/ml/predictor.py`
et ses trois implémentations, le bloc `models:` dans les YAML de stratégie, et
`recipe` en **paramètre requis** aux quatre sites de la fracture (b). Referme
(a), (b), (c), (d).

**C. Entraînement unifié.** Un seul chemin `train(recipe, df, tf)` ; les
4 `_train_impl` de ~150 lignes disparaissent. Étape la plus sensible : à valider
**recette par recette**, en comparant les artefacts produits avant/après sur la
même fenêtre — le protocole de §1.5 s'y applique tel quel.

**D. Fusion des routings**, par ordre de risque croissant :
1. `omnibus_v10` — cœur de décision byte-identique, divergence purement
   plomberie ;
2. `opus_stat_v4`, `omnibus_v7` — routings réellement divergents
   (`_evaluate_setup` et `_apply_setup_overrides` diffèrent pour v7) : la fusion
   **doit trancher un comportement**, donc change des backtests ;
3. `omnibus_v11` / `v11_followsetup` / `v12` — générations vivantes, en dernier.

**E. `ProxyPredictor`** (§8) — absorbe les 5 variantes `_no_ml` et corrige la
dérive (e).

**F. Nettoyage induit** : cache de features unique, clé de `train_cache` sur
`recipe_hash`, retrait de `managed_externally` (§3.3).

**Protocole de non-régression.** Le même qui a validé la factorisation des
helpers V4 : comparaison des signaux `score()` avant/après sur des fenêtres
réelles BTC/USDC, ancienne version chargée depuis une copie de sauvegarde via
`importlib.util.spec_from_file_location`, **abandon de l'étape si un seul signal
diverge**. Les fusions de D.2 et D.3 sont les seules où une divergence est
attendue : elle doit alors être énumérée avant, pas constatée après.

**Point de vigilance opérationnel.** `config.yaml` → `lifecycle.manual_active`
référence des stratégies par nom (`opus_omnibus_v10_no_ml::1h`,
`opus_omnibus_v8_no_ml::1h`, `opus_omnibus_v12::30m`, …). Toute étape A ou D
doit migrer ces entrées dans le même commit, sinon des bots actifs disparaissent
au redémarrage.

---

## 7. Ce que ça donne en volume

Arithmétique à partir des lignes mesurées de §1.3 (`routing` = total −
plomberie). Les 19 fichiers des familles ML pèsent **12 963 lignes**.

Après purge du legacy (v8/v9/v10 retirés), fusion des variantes et absorption
des `_no_ml`, il reste le routing d'un représentant par génération :

| génération | routing conservé |
|---|---:|
| `opus_stat_v4` | 478 |
| `omnibus_v7` | 607 |
| `omnibus_v10` | 687 |
| `omnibus_v11` | 685 |
| `omnibus_v11_followsetup` | 796 |
| `omnibus_v12` | 212 |
| `dyn_threshold` | 656 |
| `scoring_stat_v4` / `_v5` | 964 |
| **total routing** | **5 085** |

Plus le code neuf : `app/ml/predictor.py` et ses adaptateurs, le chargeur de
recettes, les `recipes/*.yaml` — de l'ordre de **400 à 600 lignes**, à comparer
aux 86 de `lgb_logging.py` et aux 67 du chemin legacy qu'il remplace.

**≈ 12 960 → ≈ 5 600 lignes, soit −57 %**, sans supprimer une seule règle de
trading. Si `v8`/`v9`/`v10` sont rebranchés au lieu d'être retirés, ajouter
leur routing (652 + 679 + 684 = 2 015) — mais ils devront être re-validés en
backtest, ce qui en fait un coût, pas une économie.

---

## 8. Sous-objectif — les stratégies sans modèle

Non prioritaire, traité ici parce que la mesure donne une réponse claire et
**asymétrique** : deux populations très différentes se cachent derrière
« stratégie sans modèle ».

**Population 1 — les variantes `_no_ml` (5 fichiers, 2 221 lignes).** Elles
entrent dans l'architecture **sans rien y ajouter**. `_proxy_p_up` et
`_proxy_p_event` retournent des `float` dans `[0,1]` sur exactement le contrat
de `predict_direction` / `predict_amplitude`, et leurs setups sont identiques à
ceux du jumeau ML. Un `ProxyPredictor` (`recipe = None`, aucun artefact, aucun
entraînement, aucun gate) les absorbe, et l'opération **corrige au passage la
dérive (e)** : le routing redevient unique, donc `needs_bearish_excess` et
`needs_rsi_below`, perdues côté `_no_ml`, reviennent. Meilleur rapport
valeur/risque du document après la purge du legacy.

**Population 2 — les ~27 stratégies classiques** (`breakout`, `tvr_trend`,
`smart_money`, `fft_spectral`, `harmonic_regime`, …). Elles n'ont **pas** de
prédicteur : leur décision n'est pas « un score continu passé à des seuils »,
c'est une logique d'indicateurs de bout en bout. Les faire entrer dans le
contrat `Predictor` reviendrait à leur inventer une frontière qui n'existe pas.

**Recommandation : ne pas les toucher.** L'architecture leur est neutre —
`models: {}` signifie « aucun prédicteur à injecter », et rien d'autre ne
change. Une architecture qui n'impose rien aux 27 stratégies non concernées
vaut mieux qu'une abstraction qui les enrôlerait de force. Une seule chose les
concerne, et elle est indépendante du ML : `prepare_for_backtest` est
réimplémenté dans 31 stratégies (fracture f), ce qui relève d'un nettoyage
propre au moteur.

---

## 9. Ce que je ne recommande pas

- **Unifier les familles de features.** Le catalogue V4 (462 colonnes) et le
  jeu `scoring_statistique` (48 colonnes, paramétré par `adx_threshold`) sont
  deux modèles différents, pas deux versions du même. La recette doit *nommer*
  le catalogue, jamais l'imposer. Le test
  `test_scoring_statistique_opus_v4_is_not_a_duplicate` verrouille cette
  frontière ; il faut la garder.
- **Réécrire les routings pendant la migration.** Déplacer et réécrire dans le
  même commit rend toute divergence indiagnosticable.
- **Faire de `ml_mode` un attribut de stratégie.** C'est une décision
  d'exploitation, pas une propriété du routing. Il reste un paramètre d'appel —
  désormais obligatoire.
- **Garder un chemin de repli « au cas où ».** C'est ce qui a produit le repli
  silencieux `use_pretrained_ml`, la double couche de cache et le layout plat.
  Sans production, un chemin de repli est une dette contractée sans
  contrepartie.

---

## 10. Décisions à prendre

| # | décision | recommandation | conséquence |
|---|---|---|---|
| 1 | **Retirer le pack V4 figé** et son code | **oui** — mesuré obsolète (§1.5) | −~3 760 lignes ; l'archive `.lgb` peut rester hors chemin |
| 2 | `v8` / `v9` / `v10` : **retirer** ou **rebrancher** | retirer — v11/v12 les remplacent | rebrancher = +2 015 lignes **et** re-backtests |
| 3 | Supprimer le code de rétrocompat (§3.2) | **oui** | `ml_mode` devient obligatoire |
| 4 | Recette + prédicteur en une passe (**B**) | **oui** | referme (a)(b)(c)(d) ; `recipe` requis, sans repli |
| 5 | Entraînement unifié (**C**) | oui, **recette par recette** | validation artefact par artefact |
| 6 | Fusionner **v10** (D.1) | **oui** | comportement inchangé (à prouver) |
| 7 | Fusionner **v7 / stat_v4** (D.2) | **à trancher** | routings divergents : **change des backtests** |
| 8 | `ProxyPredictor` (**E**) | **oui** | absorbe 5 fichiers, corrige la dérive (e) |
| 9 | Population 2 (27 stratégies) | **ne rien faire** | l'architecture leur est neutre |
| 10 | Dimension **symbole** des modèles | à trancher | l'exploiter ou la retirer — pas la porter à moitié |
| 11 | Mesurer calibration / élagage | oui, une expérience | protocole de §1.5, `calibrate=False` en comparaison |

Les décisions **2, 7 et 10** sont les seules qui ne sont pas techniques : les
deux premières changent ce que le bot trade, la troisième change ce qu'un
modèle représente. Les autres sont des refactorisations à comportement
constant, à prouver signal par signal.
