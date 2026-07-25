# Conception — Architecture unifiée : entraînement, gestion et exploitation des modèles

> **Statut** : analyse et proposition. Aucun code modifié par ce document.
>
> **Cadre** : fait suite à `CONCEPTION_CYCLE_DE_VIE_ML.md` (ML-02). Le socle de
> ML-02 est livré et n'est pas remis en cause : registre daté, gate de
> promotion, politique unique, `ml_mode`, page Modèles. Ce document traite ce
> que ML-02 avait **posé en §3.1 et §5.5 et qui n'a jamais été construit** — la
> recette comme objet de premier ordre — et en tire les conséquences sur
> l'ensemble du répertoire `app/strategies/`.
>
> **Périmètre** : les besoins ML d'abord (§1 à §6). Les stratégies sans modèle
> sont traitées en §7 comme sous-objectif explicitement non prioritaire.

---

## 0. Méthode

Tout chiffre de ce document est mesuré sur l'arbre à `f6fcc2a`, pas estimé.
Les mesures structurelles (identité de fonctions, comptages de lignes par
responsabilité) sont faites par comparaison d'AST — deux fonctions sont dites
identiques si leur AST sérialisé est égal, ce qui ignore commentaires et mise
en forme mais **pas** les renommages de variables. Quand une comparaison d'AST
suggère une divergence, le diff textuel est lu avant d'en conclure quoi que ce
soit : c'est précisément le piège qui, lors de la factorisation des helpers V4,
avait fait passer six copies identiques pour six variantes divergentes.

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
| **Artefacts réellement présents dans le registre** | **3** — `BTC_USDC/{15m,30m,1h}/opus_stat_pretrained_v4/legacy` |
| Fichiers modèle sur disque | 6 `.lgb` + 3 `.meta.json` |

Le rapport de ces deux dernières lignes aux précédentes est le fait central de
ce document : **13 000 lignes de stratégies ML tournent aujourd'hui autour de
trois artefacts, tous `legacy`, tous issus du même pack V4.**

Ce n'est pas un reproche à ML-02 : le registre est neuf et se remplira au
premier entraînement publié. Mais cela dit où est le poids — dans le code de
liaison, pas dans les modèles.

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

Trois observations mesurées valident cette lecture :

1. **Cinq stratégies, un seul artefact.** `opus_stat_pretrained_v4`,
   `opus_omnibus_v7_pretrained`, `v8`, `v9` et `v10` appellent toutes le même
   `_load_pretrained()` (défini dans `opus_stat_pretrained_v4.py:130`, importé
   par les quatre autres) → `registry.resolve(pin="legacy")`. Un modèle, cinq
   routings, cinq fichiers.

2. **Les setups sont déjà identiques.** Les huit setups
   (`SIGNAL_UP`, `SHORT_TD_HIGH`, `LONG_CHOPPY`, `SHORT_CHOPPY`,
   `LONG_RANGE_STRICT`, `LONG_RANGE_LIGHT`, `LONG_TU`, `LONG_EXIT_TD`) sont
   les mêmes, au nom près, dans `opus_omnibus_v10`, `v10_retrained`,
   `v10_no_ml`, `v11` et `v11_no_ml`.

3. **Ce qui diverge dans une paire, c'est l'accès au modèle, pas la décision.**
   Pour la paire v10, le cœur de décision est **byte-identique** :
   `_evaluate_setup`, `_select_setup`, `_apply_setup_overrides` et
   `_check_early_exit` ont exactement le même AST. `score()` diffère sur 113
   lignes, mais le diff ne contient que : le garde `_ensure_loaded()` (chemin
   figé) absent côté inline, `min_bars_required()` vs `min_bars_required(params)`,
   `_FEATURE_BUILDER.build(window)` vs `_build_features(_window_polars(...))`,
   et l'alignement des `=`. Aucune règle de trading ne diffère.

### 1.3 Où part le code

Mesure par AST, en classant chaque fonction en **plomberie ML** (cycle de vie
du modèle : `fit`, `_train_impl`, `save_model`, `load_model`, `reset_model`,
`_predict`, `predict_*`, `prepare_for_backtest`, `is_trained`,
`managed_externally`, `_tf_from_path`, construction de features) ou en
**routing** (le reste : setups, seuils, sizing, sorties).

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

La lecture est nette : **les stratégies qui composent `MLBackend` paient 7–11 %
de plomberie ; celles qui ne le font pas en paient 28–38 %.** `MLBackend`
résout déjà le problème — il n'est utilisé que par 5 stratégies sur 14, et
même là par une couche de propriétés de transfert (`opus_omnibus_v11.py:352-395`
expose 20 propriétés qui ne font que relayer `self.ml.state.*`).

### 1.4 Les fractures actuelles

Cinq conséquences concrètes, toutes vérifiées :

**(a) Des métriques fabriquées, dupliquées cinq fois.** Les cinq stratégies
figées écrivent dans leur `train_meta` des littéraux :

```python
self._best_auc_per_tf = {"15m": 0.626, "30m": 0.597, "1h": 0.603}
"auc_amp": {"15m": 0.749, "30m": 0.690, "1h": 0.676}.get(tf, 0.0),
"auc_dir": {"15m": 0.503, "30m": 0.504, "1h": 0.530}.get(tf, 0.0),
```

Copié-collé identique dans `opus_stat_pretrained_v4.py:413`,
`opus_omnibus_v7_pretrained.py:346`, `v8.py:372`, `v9.py:415`, `v10.py:406`.
Ces mêmes nombres vivent désormais aussi dans les `model.meta.json` du
registre. **Deux sources de vérité pour la même mesure, dont une en dur dans
cinq fichiers** — c'est ce qui remonte dans l'UI.

**(b) La clé de registre est le nom de la stratégie, pas la recette.**

```
app/ml/policy.py:243        recipe = recipe or getattr(strategy, "name", "strategy")
app/engine/backtest.py:972  params=sle["params"], recipe=sle["strat"].name,
app/ml/trainer.py:209       ..., params=sp, recipe=name, ...
app/ml/train_runner.py:111  ..., recipe=strategy_name, ...
```

Quatre sites, aucun paramétrable. Conséquence directe : la règle « même
recette ⇒ mêmes artefacts » (ML-02 §5.5) est inapplicable. Cinq consommateurs
du pack V4 ne peuvent pas partager une entrée de registre ; `v12` réentraîne
et republie une recette strictement identique à celle de `v11`.

**(c) La recette n'existe pas comme objet.** ML-02 §3.1 spécifiait un bloc
`model:` dans le YAML de stratégie (catalogue de features, labels,
hyperparamètres, fenêtre, cadence, gate). Vérification : **45 YAML de
stratégie, aucun bloc `model:` ; pas de répertoire `recipes/` ; ni
`recipe_version`, ni `features_catalog`, ni `feature_list`, ni `cadence_bars`
n'existent dans le code.** Ce qui en tient lieu est un mélange, dans le même
espace de noms `params:`, des seuils de décision et des hyperparamètres
d'entraînement — `strategies/opus_omnibus_v11.yaml` fait cohabiter
`setup_signal_up_amp_min` et `n_estimators`.

**(d) L'optimiseur peut changer la recette sans que le registre le sache.**
`param_space` reste globalement propre, mais trois stratégies exposent à
l'optimiseur des clés qui entrent dans l'entraînement :
`adx_threshold` (`scoring_statistique_opus_v4`, `_v5` — c'est un argument de
`_build_features(df, adx_threshold)`, donc les features changent) et
`lookahead` (`ml_dynamic_threshold` — c'est l'horizon de labellisation).
Or `_RECIPE_PARAM_KEYS` (`app/ml/policy.py:53`) ne contient ni l'un ni l'autre.
Deux essais d'optimisation produisent donc **des modèles différents, sous la
même clé de registre et avec le même `recipe_hash`.**

**(e) Les variantes `_no_ml` ont dérivé en silence.** Sur les 9 fonctions
communes à `opus_omnibus_v11` et `opus_omnibus_v11_no_ml`, **8 divergent**.
Une partie est cosmétique (`setup` renommé `s`, `exit_td_active` en
`exit_td`), mais pas tout : la variante `_no_ml` a perdu les conditions
`needs_bearish_excess` et `needs_rsi_below`, et a fusionné le test
`regime == REGIME_TREND_DN` dans la garde `exit_td`. Les huit setups sont
pourtant déclarés identiques. **Ces fichiers sont des forks figés d'un routing
qui a continué d'évoluer** — ce n'est pas de la duplication inerte, c'est un
écart de comportement non documenté.

---

## 2. Diagnostic : une seule cause

Les cinq fractures ont la même racine :

> **La classe de stratégie est propriétaire de son modèle.**

Elle possède aujourd'hui, dans un seul fichier et une seule classe : le
routing, la construction des features, la boucle d'entraînement, le format de
persistance, l'état ML en mémoire, la clé de registre, l'espace de recherche de
l'optimiseur et le slot de cycle de vie. Toucher à **l'un** de ces axes impose
un nouveau fichier — d'où les trois axes de duplication de §1.2.

ML-02 a correctement extrait **l'artefact** (registre) et **la décision de
promotion** (gate). Il n'a pas extrait **la recette** ni **le prédicteur**.
C'est exactement ce qui manque, et c'est ce qui explique que le travail récent
ait dû se faire par contrats rapportés à la classe (`gate_spec`,
`score_holdout` surchargeable) : de bons palliatifs, mais qui confirment le
diagnostic — on décrit à la stratégie ce que la recette devrait porter.

---

## 3. Architecture cible

### 3.1 Quatre objets, quatre responsabilités

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

### 3.2 Le contrat de prédicteur

```python
# app/ml/predictor.py
class Predictor(Protocol):
    heads: tuple[str, ...]          # ex. ("amp", "dir") ; ("dir",) pour dyn_threshold
    recipe: str | None              # None = pas d'artefact (proxy)
    version_id: str | None          # traçabilité UI / ml_info

    def predict_last(self, features, tf: str) -> dict[str, float]: ...
    def predict_series(self, features, tf: str) -> dict[str, np.ndarray]: ...
```

Quatre implémentations, **toutes déjà présentes dans le code, à extraire** :

| implémentation | source actuelle | consommateurs |
|---|---|---|
| `LgbmBundlePredictor` | `persistence.load_amp_dir_bundle` | v7, v10_retrained, v11, v11_followsetup, stat_retrained_v4 |
| `LgbmScalerPredictor` | `persistence.load_lgb_with_scaler` | `scoring_statistique_opus_v4`, `_v5` |
| `LgbmSinglePredictor` | `.lgb` + `.meta.json` de `ml_dynamic_threshold` | `ml_dynamic_threshold` |
| `ProxyPredictor` | `_proxy_p_up` / `_proxy_p_event` | les 5 variantes `_no_ml` (§7) |

La quatrième ligne est le point qui unifie tout : `_proxy_p_up(...) -> float`
a **exactement la signature de sortie de `predict_direction`**. Une variante
« sans ML » n'est pas une autre stratégie — c'est le même routing branché sur
un prédicteur qui ne consulte aucun artefact.

Ce contrat remplace, par stratégie : `_predict`, `predict_amplitude`,
`predict_direction`, `_predict_series`, `load_model`, `save_model`,
`reset_model`, `is_trained`, `managed_externally`, `_tf_from_path`. Soit
l'essentiel des **2 396 lignes** mesurées en §1.3.

### 3.3 La recette comme fichier

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

- **`recipe_hash` couvre tout ce qui change le modèle** — y compris
  `features.params`, ce qui referme la fracture (d). Si l'optimiseur veut
  balayer `adx_threshold`, il balaie des **recettes**, chacune avec sa propre
  lignée d'artefacts, au lieu d'écraser une clé unique.
- **La recette est indépendante de la stratégie.** Cinq consommateurs du pack
  V4 pointent une recette `v4_legacy_pack` : un entraînement, un gate, une
  alerte de fraîcheur, une ligne d'UI. Les littéraux AUC de la fracture (a)
  disparaissent — la seule source devient `model.meta.json`.
- **`persistence:` désigne le prédicteur**, ce qui supprime le besoin de
  deviner le format (`unsupported_format` dans `app/ml/scoring.py` devient
  sans objet).

### 3.4 La liaison, et ce que devient une stratégie

Dans `strategies/opus_omnibus_v10.yaml` :

```yaml
models:
  signal: omnibus_amp_dir_v1        # nom de recette, ou "proxy:indicators"
params:
  setup_signal_up_amp_min: 0.50     # seuils de DÉCISION — plus aucun HP ici
  ...
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
| `opus_omnibus_v10` | `opus_omnibus_v10` + `models: {signal: v4_legacy_pack}` |
| `opus_omnibus_v10_retrained` | `opus_omnibus_v10` + `models: {signal: omnibus_amp_dir_v1}` |
| `opus_omnibus_v10_no_ml` | `opus_omnibus_v10` + `models: {signal: proxy:indicators}` |

**Trois fichiers de 743, 952 et 511 lignes → un fichier de routing et trois
lignes de YAML.** Et surtout : une correction du routing v10 s'applique
désormais aux trois, ce qui referme la fracture (e) par construction.

`v12` illustre le cas multi-modèles, déjà prévu par ML-02 §5.5 :

```yaml
models:
  signal: omnibus_amp_dir_v1        # partagé avec v11 — plus de réentraînement en double
  filter: dyn_threshold_v1
```

---

## 4. Ce que ça change pour les quatre consommateurs

**Backtest.** `ml_mode` garde ses trois valeurs et son sens exact. Il choisit
désormais *comment le prédicteur est fabriqué* : `frozen` →
`registry.resolve(as_of=…)` ; `inline` → entraînement de la recette sur la
fenêtre ; `simulated_live` → `policy.maybe_refresh` aux frontières de cadence.
Le `ml_info` déjà produit gagne `recipe` et `version_id` par tête. Le repli
silencieux `use_pretrained_ml` reste supporté tel quel.

**Optimiseur.** Deux gains. D'abord, la surface de recherche cesse d'être
gonflée par les variantes : 19 fichiers découverts par `_discover_strategies()`
tombent à 10 routings. Ensuite, la séparation `params:` / recette rend la
question « veut-on optimiser les seuils, ou la recette ? » explicite au lieu
d'accidentelle — un balayage de recettes devient un sweep de recettes, gaté
comme les autres, et non une suite d'écrasements sous une clé unique.

**Live.** `MLStrategyTrainer` se simplifie : il itère sur les **recettes
distinctes** référencées par les stratégies actives, pas sur les stratégies.
Cinq consommateurs du pack V4 = un timer, un gate, une alerte de fraîcheur.
Aujourd'hui ce serait cinq. Point à trancher au passage (§9) : le live résout
le symbole d'entraînement par `_resolve_symbol` qui « préfère BTC »
(`app/ml/trainer.py:233`) — la dimension symbole existe dans le registre mais
est écrasée à l'exécution.

**UI Modèles.** Elle liste des recettes plutôt que des stratégies — ce qui est
déjà ce que `registry.list_recipes()` renvoie, à ceci près que « recette »
y vaut aujourd'hui « nom de stratégie ». Chaque ligne gagne la liste de ses
consommateurs. Les métriques affichées viennent d'une seule source
(`model.meta.json`), et non plus de littéraux recopiés.

---

## 5. Ce que l'architecture ne change pas

À conserver **tel quel** — c'est du travail déjà correct, et le redéfaire
serait une régression :

- `app/ml/model_registry.py` — layout, `resolve(as_of=…)`, pin persistant,
  `decisions.jsonl`, éligibilité par décision de gate. Seule la sémantique de
  l'argument `recipe` change : il cesse d'être un alias du nom de stratégie.
- `app/ml/policy.py` — `decide_gate`, `maybe_refresh`, la restauration du
  sortant en cas de `keep`, `freshness_warning`. Inchangés.
- `app/ml/scoring.py` — `rank_auc`, `score_amp_dir_bundle`. Le dispatch par
  `gate_spec` migre de la stratégie vers la recette ; l'implémentation reste.
- `app/ml/backend/` — features, trainer, predictor, persistance, isotonic.
  C'est déjà le bon découpage ; il gagne des appelants, il n'en perd pas.
- `ml_mode` et ses trois valeurs.
- Le contenu des routings — setups, seuils, sizing, sorties. **Rien de ce
  document ne propose de retoucher une règle de trading.**

---

## 6. Migration

Six paliers, chacun utile seul, chacun livrable sans le suivant.

**P0 — `recipe` devient un paramètre.** Les quatre sites de la fracture (b)
lisent `params["recipe"]` avec repli sur `strategy.name`. ~10 lignes,
rétrocompatible par construction : sans clé `recipe`, le comportement actuel
est identique. Débloque tout le reste. *À faire dans tous les cas.*

**P1 — La recette comme fichier.** `recipes/*.yaml`, chargeur, `recipe_hash`
canonique couvrant `features.params`, bloc `models:` dans les YAML de
stratégie. Referme (c) et (d). Referme (a) en supprimant les littéraux AUC des
cinq fichiers au profit du `meta.json`.

**P2 — Le contrat `Predictor` et ses quatre adaptateurs.** Aucune stratégie
n'est encore fusionnée ; chacune délègue son accès modèle au prédicteur
injecté. C'est le palier qui retire les ~2 400 lignes de plomberie et qui
supprime `unsupported_format`.

**P3 — L'entraînement derrière la recette.** Un seul chemin
`train(recipe, df, tf)`, plus de `_train_impl` par stratégie (4 copies
d'environ 150 lignes aujourd'hui). Palier le plus sensible : il touche
l'entraînement lui-même, donc à valider **recette par recette**, en comparant
les artefacts produits avant/après sur la même fenêtre.

**P4 — Fusion des variantes, génération par génération.** Ordre par risque
croissant, dicté par les mesures de §1.2 :
1. `omnibus_v10` — cœur de décision byte-identique, divergence purement
   plomberie. Fusion la moins risquée.
2. `opus_stat_v4`, `omnibus_v7` — routings réellement divergents
   (`_evaluate_setup` et `_apply_setup_overrides` diffèrent pour v7) : la
   fusion **doit trancher un comportement**, ce qui change des résultats de
   backtest. Décision produit, pas décision technique.
3. `omnibus_v11` / `v11_followsetup` / `v12` — dernier, parce que ce sont les
   générations vivantes.

**P5 — `ProxyPredictor`** (§7).

**Protocole de non-régression, à chaque palier.** Le même que celui qui a
validé la factorisation des helpers V4, et qui avait déjà démontré sa valeur :
comparaison des signaux `score()` avant/après sur des fenêtres réelles
BTC/USDC, l'ancienne version chargée depuis une copie de sauvegarde via
`importlib.util.spec_from_file_location`, avec **abandon du palier si un seul
signal diverge**. Les fusions de P4 sont les seules où une divergence est
attendue — elle doit alors être énumérée explicitement, pas constatée après
coup.

**Point de vigilance opérationnel.** `config.yaml` → `lifecycle.manual_active`
référence des stratégies par nom (`opus_omnibus_v10_no_ml::1h`,
`opus_omnibus_v8_no_ml::1h`, `opus_omnibus_v12::30m`, …). Toute fusion de P4
doit migrer ces entrées dans le même commit, sinon des bots actifs
disparaissent silencieusement au redémarrage.

---

## 7. Sous-objectif — les stratégies sans modèle

Non prioritaire, traité ici parce que la mesure donne une réponse claire et
qu'elle est **asymétrique** : il y a deux populations très différentes derrière
« stratégie sans modèle ».

**Population 1 — les variantes `_no_ml` (5 fichiers, 2 221 lignes).** Elles
entrent dans l'architecture **sans rien y ajouter**. `_proxy_p_up` et
`_proxy_p_event` retournent des `float` dans `[0,1]` sur exactement le contrat
de `predict_direction` / `predict_amplitude` ; les setups sont identiques à
ceux du jumeau ML. Un `ProxyPredictor` (`recipe = None`, aucun artefact, aucun
entraînement, aucun gate) les absorbe intégralement, et l'opération **corrige
au passage la dérive (e)** : le routing redevient unique, donc les conditions
`needs_bearish_excess` / `needs_rsi_below` perdues côté `_no_ml` reviennent.
C'est le meilleur rapport valeur/risque de tout le document après P0 — d'où sa
place en P5 et non en annexe.

**Population 2 — les ~27 stratégies classiques** (`breakout`, `tvr_trend`,
`smart_money`, `fft_spectral`, `harmonic_regime`, …). Elles n'ont **pas** de
prédicteur : leur décision n'est pas « un score continu passé à des seuils »,
c'est une logique d'indicateurs de bout en bout. Les faire entrer dans le
contrat `Predictor` demanderait de leur inventer une frontière qui n'existe
pas dans leur logique.

**Recommandation : ne pas les toucher.** L'architecture leur est neutre par
construction — `models: {}` (ou l'absence du bloc) veut dire « aucun
prédicteur à injecter », et rien d'autre ne change pour elles. Une
architecture qui n'impose rien aux 27 stratégies non concernées est un
meilleur résultat qu'une abstraction qui les enrôlerait de force. Le gain
d'unification réel se situe entièrement dans la population 1.

---

## 8. Ce que je ne recommande pas

- **Unifier les trois familles de features.** Le catalogue V4 (462 colonnes,
  partagé), le jeu `scoring_statistique` (48 colonnes, paramétré par
  `adx_threshold`) et celui de `ml_dynamic_threshold` sont trois modèles
  différents, pas trois versions du même. La recette doit *nommer* le
  catalogue, jamais l'imposer. Le test
  `test_scoring_statistique_opus_v4_is_not_a_duplicate` verrouille déjà cette
  frontière ; il faut la garder.
- **Réécrire les routings pendant la migration.** Déplacer du code et le
  réécrire dans le même commit rend toute divergence indiagnosticable. P4 doit
  déplacer, prouver l'équivalence, et s'arrêter là.
- **Supprimer le pack V4 legacy.** Il porte 40 features avec des listes
  amp/dir asymétriques, alors que les recettes vivantes en entraînent 437. Ce
  n'est pas du code mort : c'est un actif irreproductible. Il devient une
  recette figée (`v4_legacy_pack`, non ré-entraînable, `pin=legacy`), il ne
  disparaît pas.
- **Faire de `ml_mode` un attribut de stratégie.** C'est une décision
  d'exploitation (backtest / optimisation / live), pas une propriété du
  routing. Il reste un paramètre d'appel.

---

## 9. Décisions à prendre

| # | décision | recommandation | conséquence si oui |
|---|---|---|---|
| 1 | Lancer **P0** (`recipe` paramétrable) | **oui, sans réserve** | ~10 lignes, aucun changement de comportement, débloque P1–P5 |
| 2 | Construire **P1** (recette-fichier) | **oui** | referme (a), (c), (d) ; nouveau répertoire `recipes/` |
| 3 | Construire **P2** (`Predictor`) | **oui** | −~2 400 lignes ; `unsupported_format` disparaît |
| 4 | Faire **P3** (entraînement unifié) | oui, mais **recette par recette** | touche l'entraînement : validation artefact par artefact |
| 5 | Fusionner **v10** (P4.1) | **oui** | −2 fichiers ; comportement inchangé (à prouver) |
| 6 | Fusionner **v7 / stat_v4** (P4.2) | **à trancher** | routings divergents : impose de choisir un comportement, **change des backtests** |
| 7 | Fusionner **v11 / v12** (P4.3) | plus tard | générations vivantes, à stabiliser d'abord |
| 8 | **P5** — `ProxyPredictor` | **oui** | absorbe 5 fichiers et corrige la dérive (e) |
| 9 | Population 2 (27 stratégies) | **ne rien faire** | l'architecture leur est neutre |
| 10 | Dimension **symbole** des modèles | à trancher séparément | le registre la porte, le live l'écrase (`_resolve_symbol` préfère BTC) |

Les décisions 6 et 10 sont les seules qui ne sont pas techniques : la première
change des résultats de backtest, la seconde change ce qu'un modèle
représente. Les autres sont des refactorisations à comportement constant, à
prouver signal par signal.
