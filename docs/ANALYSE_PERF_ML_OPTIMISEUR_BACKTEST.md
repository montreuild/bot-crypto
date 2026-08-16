# Analyse — performance du backtest, de l'optimiseur et de l'entraînement ML

> Analyse conduite le 2026-08-15 sur `main` @ `d62c487`. Deux questions :
> **(1)** où va réellement le temps, et que faire pour en gagner ; **(2)** quelles
> optimisations déjà présentes dans le dépôt ne sont appliquées qu'à une partie
> des stratégies.
>
> **Statut** : dix des douze recommandations sont **appliquées et mesurées** dans
> la branche `claude/ml-optimizer-backtest-perf-0x32so` (cf. §6 pour l'état de
> chacune, et §7 pour ce que l'application a fait apparaître). Restent proposés
> les items 9, 11 et 12. Deux prédictions de cette analyse ont été **démenties
> par la mesure** — c'est noté à l'endroit où elles figuraient.

---

## 0. Comment lire les chiffres

Toutes les mesures viennent de séries OHLCV **synthétiques** (marche géométrique,
graine fixée) sur une machine à **4 cœurs** : le dépôt ne contient aucun
historique (`data/ohlcv/` est vide dans cet environnement). Conséquences à garder
en tête :

- les **rapports de temps** (×10, ×120) et les **profils** sont fiables : ils
  dépendent de la structure du code, pas de la nature des prix ;
- les **temps absolus** ne le sont pas, et les **nombres de trades** encore moins
  — une marche aléatoire ne déclenche pas les mêmes signaux qu'un vrai marché ;
- les mesures d'entraînement LightGBM sur bruit s'arrêtent tôt (early stopping à
  6 arbres) ; les chiffres cités ci-dessous forcent 300 arbres pour rester
  représentatifs.

Deux échecs de tests préexistent dans cet environnement
(`test_config_split::test_comments_survive_a_write`,
`test_param_search_optim::…partial_fixed_sampler…`) : ils échouent aussi sur
l'arbre propre, versions de dépendances non épinglées ici. Le reste passe
(1 872 tests).

---

## 1. Ce que le dépôt fait déjà bien

Il faut le dire avant de proposer quoi que ce soit, parce que cela change les
recommandations : **la plupart des optimisations évidentes sont déjà là**, et
souvent mieux faites que ce qu'on écrirait de mémoire.

| Mécanisme | Où | Ce qu'il évite |
|---|---|---|
| Colonnes `_pre_*` mémoïsées par empreinte de plage | `indicators_precompute.py` | recalcul ATR/ADX/EMA/RSI à chaque trial |
| Réutilisation causale fenêtre↔df complet | `indicators_causal.py` (`ema_window`, `supertrend_last`, `macd_hist_last3`) | O(n²) sur SuperTrend/MACD/EMA |
| Hook `prepare_for_backtest` | `Backtester.run` + 25 stratégies | reconstruction des features à chaque barre |
| Pool de workers **persistant** + snapshot des features | `optimizer_search._open_pool`, `opt_workers._worker_init` | respawn et rebuild par vague d'essais |
| Cache d'entraînement process-wide + fenêtres alignées | `core/train_cache.py` | réentraînements identiques entre essais |
| Prédiction **par lot** au lieu de ligne à ligne | `MLBackend._batch_at`, `smc_ml_edge._precalcule_predictions` | ~190 s → ~15 s sur 12 000 barres (mesure du dépôt) |
| Gel des paramètres à faible impact, **en budget** | `_freeze_from_results`, `_optuna_apply_freeze` | essais gaspillés sur des paramètres inertes |
| Deflated Sharpe au gate de naissance | `opt_scoring.beats_baseline` | biais de test multiple |
| Gate walk-forward avant auto-apply | `auto_optimizer._wf_consistent` | promotion sur un split unique |
| Portillons CPU et mémoire inter-jobs | `auto_optimizer`, `opt_workers.mem_aware_max_workers` | OOM / thrashing |

**Le vrai gisement n'est donc pas d'inventer des mécanismes : c'est de finir
d'appliquer ceux qui existent.** C'est le sujet du §5, et c'est aussi ce qui a
produit le gain le plus important de cette analyse.

---

## 2. Backtest

### 2.1 Profil de départ

Backtests sur 2 500 barres synthétiques, une stratégie par run :

```
breakout                353 bars/s     momentum_blitz         9 723 bars/s
trend                   425 bars/s     volatility_squeeze     3 664 bars/s
pullback_trend          448 bars/s     harmonic_regime        6 076 bars/s
multi_tf_sr             429 bars/s     fft_spectral           4 873 bars/s
supertrend_macd         450 bars/s     liquidity_sweep_vol   29 985 bars/s
fear_momentum           531 bars/s     smart_money           15 084 bars/s
gemini_trend_follow     473 bars/s     trend_rider           45 386 bars/s
```

Le dépôt se sépare en deux populations nettes : sept stratégies entre 350 et
530 barres/s, tout le reste au-dessus de 2 000. **Les sept lentes sont exactement
les sept qui appellent `htf_trend(df_ltf=df)`.**

### 2.2 ✅ `htf_trend` : un O(n²) sur 8 stratégies — corrigé

Profil de `breakout` sur 4 000 barres, avant correction :

```
22,19 s  total
18,28 s  (82 %)  indicators_market.htf_trend
17,62 s           └─ smc_sessions._htf_buckets
 7,88 s              └─ 5 944 108 appels à numpy.ufunc.reduce
```

`htf_trend(df_htf=None, df_ltf=fenêtre)` reconstruit **toute** l'agrégation HTF à
chaque barre : buckets d'horloge, trois compréhensions Python sur les buckets
(`max`/`min`/`sum`), EMA. O(n) par barre, donc O(n²) sur le backtest.

Ce repli n'est pas une erreur — il a été ajouté délibérément (L5, parité
backtest↔live : sans lui le filtre HTF de neuf stratégies était *inerte en
simulation et actif en production*). Mais il a été branché sans le traitement que
le dépôt applique déjà à SuperTrend, MACD et EMA : **calculer la série une fois
sur `full_df`, indexer en O(1)**.

**Appliqué** : `indicators_causal.htf_trend_ema_series` + paramètres
`full_df=`/`cache=` sur `htf_trend`, branchés sur les 8 stratégies concernées
(`gemini_trend_follow` reçoit au passage le hook `prepare_for_backtest` qui lui
manquait). Sur grille temporelle irrégulière — bougie manquante, séance
boursière — la série se refuse (`None`) et l'appelant retombe sur le calcul barre
à barre : correct d'abord, rapide quand c'est démontrable.

Mesures sur 6 000 barres, **courbes d'équité comparées point par point** :

| Stratégie | Avant | Après | Gain | Équité identique |
|---|---|---|---|---|
| `gemini_trend_follow` | 24,98 s | 0,21 s | **×120** | ✅ |
| `supertrend_macd` | 26,06 s | 0,30 s | **×87** | ✅ |
| `trend` | 25,47 s | 0,51 s | **×50** | ✅ |
| `pullback_trend` | 24,40 s | 0,50 s | **×49** | ✅ |
| `multi_tf_sr` | 26,65 s | 0,81 s | **×33** | ✅ |
| `fear_momentum` | 22,66 s | 0,95 s | **×24** | ✅ |
| `breakout` | 29,03 s | 2,91 s | **×10** | ✅ |
| `breakout_filtreHor` | 6,68 s | 0,75 s | **×8,8** | ✅ |

Le facteur **croît avec la longueur de série** : c'est un terme quadratique qui
disparaît, pas une constante qu'on rabote. Sur les 50 000 bougies d'un job
d'optimisation × 40 essais × 2 backtests (IS + OOS), l'ordre de grandeur du job
change.

L'égalité est démontrée, pas constatée (`tests/test_htf_trend_causal.py`, 9 cas,
parcours barre à barre des 700 barres pour quatre couples `(ema_period, mult)`,
zones de garde comprises) :

1. les buckets sont des bornes d'horloge — ceux d'un préfixe sont ceux du df
   complet, tronqués ;
2. `htf_trend` ne lit que les buckets **entièrement clôturés**, donc jamais le
   bucket partiel de fin, le seul qui diffère entre préfixe et df complet ;
3. `ewm_mean(adjust=False)` est une récurrence : l'EMA d'un préfixe **est** le
   préfixe de l'EMA.

### 2.3 Reliquat : `bb_squeeze` domine maintenant `breakout`

Après correction, `breakout` reste le plus lent des huit. Nouveau profil
(6 000 barres) :

```
5,40 s  total
3,49 s  (65 %)  indicators_core.bb_squeeze   — 5 774 appels
1,37 s           └─ 23 168 PyLazyFrame.collect()
```

`bb_squeeze` est déjà O(1) par barre (il tronque à `bb_period + lookback + 2`
barres, correction documentée dans son propre code). Ce qui coûte, c'est le
**surcoût d'appel polars** : quatre opérations paresseuses `collect()` sur une
série de 37 éléments, soit ~600 µs par appel pour quelques dizaines d'additions.

**Proposition (P2, effort S)** — deux options, la seconde préférable :

- *a.* réécrire le corps en numpy (`np.lib.stride_tricks.sliding_window_view` sur
  la queue) : même sémantique, sans passer par polars ;
- *b.* ajouter `bb_squeeze_series(full_df, lookback, bb_period, quantile)` dans
  `indicators_causal.py` et le brancher par `full_df=`/`cache=`, **exactement
  comme on vient de le faire pour `htf_trend`** — la largeur de bande est causale,
  donc le même argument d'exactitude s'applique.

Concerne `breakout`, `breakout_filtreHor`, `composite_score`. Gain attendu :
×2,5 à ×3 sur ces trois stratégies. À vérifier par comparaison d'équité comme
en §2.2.

### 2.4 Le plancher de la boucle est sain

Les stratégies sans indicateur lourd tournent à 30 000–45 000 barres/s, soit
~25 µs par barre pour `ctx.window = df[:i+1]` + `best_signal` + diagnostics.
Sur 50 000 barres cela fait ~1,5 s de surcoût de boucle : **il n'y a rien à
gagner à réécrire la boucle du `Backtester`**, et les idées habituelles
(vectoriser la boucle, éviter le slice polars) viseraient 3 % du temps. Ce
constat mérite d'être écrit pour éviter qu'un chantier s'y engage.

---

## 3. Optimiseur

### 3.1 Vitesse

**Le parallélisme est déjà bon.** Mesure de la dispersion des durées d'essai
(18 essais `breakout`, 8 000 barres) : moyenne 3,55 s, coefficient de variation
**0,04**. Avec des durées aussi homogènes, la barrière de fin de vague de
`_submit_wave` coûte 1 % à 13 % selon `k` :

```
k=2 : utilisation 99 %      k=3 : 97 %      k=4 : 87 %
```

Une soumission continue (garder `safe_jobs` futures en vol au lieu d'attendre la
fin de vague) reste souhaitable pour les stratégies ML — dont les essais sont
beaucoup moins homogènes, le premier essai d'un worker payant tous les
entraînements et les suivants tapant le cache — mais **ce n'est pas le levier
principal**, contrairement à ce qu'on pourrait supposer. P3.

De même, chaque soumission d'essai renvoie les DataFrames IS+OOS sérialisés
(**2,48 Mo par essai**, 99 Mo pour 40 essais) alors que `_worker_init` les a déjà
chargés dans `_W` et que `_eval_worker` les ignore. Coût CPU réel mesuré :
**0,06 s pour 40 essais**. C'est de l'hygiène (`None` en sentinelle quand le pool
est initialisé), pas une optimisation. P3.

### 3.2 ⚠ Le vrai levier : `train_cache` n'est pas branché sur 3 stratégies ML

`app/core/train_cache.py` existe précisément pour cela : *« sur une optimisation
(40 trials × 50 000 bougies), les mêmes entraînements sont relancés à l'identique
dans chaque trial qui ne fait varier que des paramètres de décision — des heures
de calcul redondant »*.

Il est branché sur les stratégies `MLBackendMixin` (`opus_omnibus_v11`,
`v11_followsetup`, `opus_stat_retrained_v4`, `v7`, `v10_retrained`) et sur
`ml_dynamic_threshold`. Il ne l'est **pas** sur `scoring_statistique_opus_v4`,
`scoring_statistique_opus_v5` et `smc_ml_edge`, qui portent leur propre `_train`.

Mesure directe (4 essais consécutifs, 5 000 barres, même série, même config) :

| Stratégie | Essai 1 | Essai 2 | Essai 3 | Essai 4 | Cache |
|---|---|---|---|---|---|
| `opus_stat_retrained_v4` *(câblé)* | 1,8 s | **0,5 s** | **0,5 s** | **0,5 s** | 3 hits / 1 miss |
| `scoring_statistique_opus_v4` *(non câblé)* | 1,3 s | 1,2 s | 1,1 s | 1,1 s | **0 hit, 0 miss** |

La stratégie câblée devient 3,6× plus rapide dès le deuxième essai ; l'autre reste
plate — le cache n'est même jamais consulté. Sur un job réel
(`retrain_every: 800` × 50 000 bougies ≈ 60 réentraînements par backtest au lieu
des ~6 ici, × 40 essais), l'écart n'est plus un facteur 3,6 mais l'essentiel du
coût du job.

**Proposition (P1, effort M)** — porter les deux moitiés du mécanisme :

1. `aligned_train_window(df, retrain_every, n_train)` à la place de
   `df.slice(len(df) - n_train - 1, n_train)`. Sans cet alignement, deux essais
   aux seuils différents retrainent à des barres décalées → fenêtres distinctes →
   aucun hit possible. C'est la moitié qu'on oublie.
2. `cached_train(self, df, tf, params, self._train_impl, STATE_ATTRS, PARAM_KEYS)`
   en enveloppe de `_train`.

⚠ **Piège à ne pas reproduire** : `TrainState.PARAM_KEYS` ne contient pas
`adx_threshold`. Pour `scoring_statistique_opus_v4/v5`, `adx_threshold` change le
**calcul des features** (détection de régime) : le réutiliser tel quel ferait
partager un modèle entre deux essais entraînés sur des features différentes —
un faux résultat, pas seulement une perte de vitesse. Ces stratégies doivent
déclarer leurs propres `PARAM_KEYS` incluant `adx_threshold` et `amp_top_pct`.

Validation attendue : mêmes `best_params` et mêmes scores qu'avant sur un job de
référence, avec `TRAIN_CACHE_MAX=0` comme bras témoin.

### 3.3 Budget d'essais indépendant de la taille de l'espace

`scripts/audit_param_space.py` (déjà dans le dépôt) le dit sans détour :

```
smart_money                     58 params   4,5×10¹⁹ combos   40 essais   0,00 %
opus_omnibus_v11_followsetup_no_ml  22      1,8×10¹¹          40 essais   0,00 %
opus_omnibus_v11_followsetup    19          6,5×10⁹           40 essais   0,00 %
…
15 stratégies sur 41 sous le seuil de couverture
```

`n_trials: 40` est un **littéral de config**, identique pour `signal_consensus`
(27 combinaisons, couverture 148 %) et pour `smart_money` (4,5×10¹⁹). Le gel des
paramètres à faible impact (`_should_reduce_space`) atténue, il ne compense pas.

**Propositions**, par ordre de rentabilité :

- **P1, effort S** — faire dépendre `n_trials` du nombre de paramètres **libres**
  après gel : `n_trials = clamp(15 × n_params_libres, 40, 400)`. Un espace à
  5 paramètres n'a pas besoin de 40 essais, un espace à 22 en a besoin de bien
  plus. Le coût total du parc baisse tout en améliorant les gros espaces.
- **P1, effort M** — réduire `smart_money.param_space` : **58 paramètres n'est pas
  un espace de recherche, c'est un panneau de configuration**. Basculer en
  `fixed_params` tout ce dont l'importance mesurée par `_optuna_param_importances`
  est indiscernable du bruit sur trois jobs, et documenter le choix.
- **P2, effort S** — court-circuiter le backtest OOS quand l'IS est dégénéré
  (`is_trades < min_trades`). Un paramétrage qui ne produit pas 10 trades sur 65 %
  de l'historique n'en produira pas davantage sur les 35 % restants ; on économise
  ~50 % du coût de ces essais. **Ce n'est pas neutre** : aujourd'hui un essai à IS
  dégénéré et OOS flatteur reste sélectionnable. C'est précisément le
  bruit qu'on veut refuser — mais c'est un changement de comportement, donc sous
  clé de config avec mesure avant/après.

### 3.4 Qualité de sélection : l'OOS n'est plus out-of-sample

Point structurel, indépendant de la vitesse, et à mon avis **le plus important du
document du point de vue du résultat en argent**.

Le déroulé actuel :

```
split_is_oos(df, oos_fraction=0.35)  →  df_is (65 %)   df_oos (35 %)
                                            │              │
                       40 essais évalués sur les deux ─────┤
                       sélection : max(_penalized_score)   │  ← sur df_oos
                       gate beats_baseline                 │  ← sur df_oos
                       baseline de comparaison             │  ← sur df_oos
                       gate walk-forward                   │  ← sur df_full ⊇ df_oos
```

`df_oos` sert **quatre fois** : à classer les 40 essais, à mesurer le gagnant, à
mesurer la référence, et (via `df_full`) à vérifier la consistance walk-forward.
Après 40 sélections, un score sur `df_oos` n'est plus une estimation
hors-échantillon : c'est un maximum d'ordre 40, biaisé vers le haut par
construction.

Le dépôt a déjà posé le bon garde-fou partiel — le **Deflated Sharpe** avec
`n_trials`, qui corrige exactement ce biais sur le Sharpe. Mais il ne corrige ni
le PnL, ni le win-rate, ni le score composite, qui sont les trois autres
conditions de `beats_baseline`.

**Proposition (P1, effort M)** — passer à trois tranches :

```
df_train (55 %)   df_select (25 %)   df_holdout (20 %, jamais vu)
      │                  │                    │
   essais           classement          UNE mesure du gagnant
                                        → beats_baseline ici
```

`df_holdout` n'est touché qu'une fois par job, sur un seul paramétrage. Le gate
d'apply devient une vraie estimation hors-échantillon, et le Deflated Sharpe
redevient une ceinture plutôt que la seule bretelle. Coût en calcul : un backtest
de plus par job (~1 % du budget). Coût en données : il faut ~1,5× l'historique
actuel pour garder des tranches significatives — à arbitrer par TF, et à refuser
explicitement (avec la raison journalisée) quand l'historique ne suffit pas
plutôt qu'à dégrader en silence.

Un module `app/core/is_oos.py` existe déjà et centralise la convention : c'est
le bon endroit, `split_is_oos` devenant un cas particulier de
`split_train_select_holdout`.

**Proposition complémentaire (P2, effort S)** — le gate walk-forward tourne sur
`df_full`, qui **contient** les données de sélection. C'est un test de
robustesse, pas une validation hors-échantillon, et le nom `wf_gate` laisse
croire l'inverse. Soit le restreindre au holdout, soit renommer et documenter ce
qu'il mesure vraiment.

---

## 4. Entraînement ML

### 4.1 LightGBM tourne sur un seul cœur, partout

`n_jobs=1` est codé en dur dans les deux trainers
(`app/ml/backend/trainer.py:397`, `app/ml/recipe_trainer.py:64`).

C'est **juste dans les workers d'optimisation** — `_worker_init` force déjà
`OMP_NUM_THREADS=1` pour éviter les `std::bad_alloc` de LightGBM quand N workers
se battent pour les cœurs. C'est **inutilement coûteux pour l'entraînement
autonome** (`train_runner`, `/api/ml/train`, `scripts/retrain_all_models.py`), qui
tourne seul :

```
300 arbres, 12 000 barres × 437 features
  n_jobs=1 : 5,52 s        n_jobs=2 : 2,82 s        n_jobs=4 : 1,80 s   (×3,1)
```

**Proposition (P1, effort S)** — un seul point de décision, par exemple
`app/ml/threads.py::lgb_threads()` : retourne `1` si `OMP_NUM_THREADS=1` est déjà
posé (donc dans un worker) ou si `optimizer.*` est l'appelant, sinon
`min(4, cpu_count-1)`. `n_jobs` est **déjà** dans `_LGB_KEYS` de
`recipe_trainer` : une recette peut le surcharger, il ne manque que le défaut
contextuel.

⚠ **Réserve à mesurer avant de généraliser** : LightGBM ne garantit pas
l'invariance bit-à-bit au nombre de threads (ordre de sommation dans la
construction des histogrammes). Un modèle réentraîné à `n_jobs=4` peut différer
marginalement de son homologue mono-thread. Pour un artefact **publié**, cela
touche à la reproductibilité : mesurer l'écart d'AUC sur trois recettes avant
d'activer, et consigner `n_jobs` dans le `meta.json` du modèle.

### 4.2 Aucun embargo entre entraînement et validation

Les trois chemins d'entraînement découpent en 80/20 **contigu, sans intervalle** :

```
app/ml/backend/trainer.py:368     split = max(int(n * 0.8), 100)
app/ml/recipe_trainer.py:258      split = max(int(lab.n * 0.8), 100)
ml_dynamic_threshold:287          train_idx = arange(0, train_end) ; test_idx = arange(train_end, …)
```

`grep -rn "embargo" app/` ne retourne **rien** ; les seules occurrences de
« purge » du dépôt concernent le nettoyage des jobs d'optimisation
(`api/routes/optimizer.py`), pas les labels.

Or les labels regardent devant : `multi_horizon_labels(close, [1,3,6,12], …)`
construit `y[i]` à partir de `close[i+12]`. Les 12 dernières lignes
d'entraînement portent donc une information tirée des premières lignes de
validation. Idem au gate (`policy.maybe_refresh` : `train_df` finit exactement où
`holdout_df` commence).

L'ampleur est modeste — une douzaine de lignes sur plusieurs milliers — et je ne
prétends pas qu'elle explique les AUC observées. Mais c'est **le défaut par
construction** que la littérature (López de Prado, *purging & embargo*) demande
d'éliminer d'abord, et il coûte trois lignes :

```python
embargo = max(cfg.label_horizons or [1])
X_train, y_train = X_full[:split - embargo], y[:split - embargo]
```

**Proposition (P2, effort S)**, aux trois endroits, avec la même constante.

### 4.3 Le seuil d'amplitude est calculé sur train + validation

```python
amp_thr = float(np.quantile(abs_max, 1.0 - amp_top_pct))   # sur TOUT n
y_amp   = (abs_max >= amp_thr)
```

Le quantile qui **définit** les labels résume la période entière, validation
comprise. Le taux de positifs de la validation est donc épinglé à `amp_top_pct`
par construction, au lieu d'être ce qu'un seuil appris sur le passé y produirait.

Ce n'est **pas** une fuite d'exécution — `amp_thr` ne sert qu'à fabriquer les
labels d'entraînement, jamais à décider en live. C'est un **biais d'estimation de
la métrique du gate** : l'AUC de validation, qui arbitre la promotion des
modèles, est mesurée sur des labels partiellement définis par les données
qu'elle prétend n'avoir jamais vues.

**Proposition (P2, effort S)** : calculer `amp_thr` sur `abs_max[:split]`
seulement, et le persister dans le `meta.json` (il devient un paramètre du
modèle, ce qu'il est déjà de fait). Attendre une légère baisse des AUC
rapportées — ce sera la correction d'un biais, pas une régression, et il faut le
dire dans le changelog sous peine que quelqu'un « répare » la baisse.

### 4.4 Prédiction ligne à ligne : le lot n'est pas généralisé

Le dépôt a déjà mesuré ce gain et l'a écrit noir sur blanc dans
`smc_ml_edge._precalcule_predictions` : *« ~190 s → ~15 s pour un backtest de
12 000 barres »*. `MLBackend._batch_at` fait la même chose pour la famille Opus,
avec une vérification par colonnes témoins qui **prouve** l'alignement du frame
plutôt que de le supposer.

Trois stratégies prédisent encore une ligne à la fois :

| Stratégie | Site | Appels par backtest |
|---|---|---|
| `scoring_statistique_opus_v4` | `:672-673` | 2 par barre (amp + dir) |
| `scoring_statistique_opus_v5` | `:618-619` | 2 par barre |
| `ml_dynamic_threshold` | `:801` | 1 par barre |

Mesure de l'écart pur (300 arbres, 437 features, 12 000 lignes) :

```
12 000 predict d'une ligne : 0,62 s        un seul predict de 12 000 lignes : 0,056 s   (×11)
```

**Proposition (P1, effort M)** — porter `_precalcule_predictions` : recalculer le
tableau à chaque réentraînement, lire `arr[i]` à la barre `i`. L'argument de
non-fuite est déjà écrit dans `smc_ml_edge` et s'applique mot pour mot (modèle
antérieur, features causales, la barre `i` ne lit que `arr[i]`) — le reprendre
en commentaire plutôt que de le réinventer.

### 4.5 Deux gaspillages secondaires

- `ml_dynamic_threshold:779` recalcule `_adx(df.tail(300), 14)` **à chaque
  barre** alors que la colonne `_pre_adx14` est pré-calculée et lisible en O(1)
  par `pre_val`. Correction d'une ligne (P2, effort S) — mais vérifier la
  convention de lissage : `_pre_adx14` est en Wilder depuis §8ter, `_adx` peut ne
  pas l'être. Si les deux diffèrent, c'est un changement de comportement à
  mesurer, pas un remplacement.
- `train_runner.window_sweep` entraîne les fenêtres **en série** alors que les
  candidats sont indépendants et partagent un holdout commun. Un
  `ProcessPoolExecutor` de 3–4 workers donnerait un facteur proche du nombre de
  fenêtres balayées (P3, effort M).

---

## 5. Améliorations déjà faites ailleurs, à reporter

C'est la réponse directe à la seconde question. Chaque ligne est une optimisation
**qui existe et fonctionne dans le dépôt**, appliquée à certaines stratégies
seulement.

| Mécanisme | Implémentation de référence | Manque à | Priorité |
|---|---|---|---|
| Série HTF causale mémoïsée | *(ajouté ici)* `indicators_causal.htf_trend_ema_series` | ~~8 stratégies~~ | ✅ **fait** |
| Cache d'entraînement + fenêtre alignée | `opus_stat_retrained_v4:268`, `MLBackendMixin._train` | `scoring_statistique_opus_v4`, `_v5`, `smc_ml_edge` | **P1** |
| Prédiction par lot | `smc_ml_edge._precalcule_predictions`, `MLBackend._batch_at` | `scoring_statistique_opus_v4`, `_v5`, `ml_dynamic_threshold` | **P1** |
| Réutilisation causale `full_df`+`cache` pour `bb_squeeze` | idiome de `ema_window` / `supertrend_last` | `breakout`, `breakout_filtreHor`, `composite_score` | P2 |
| Lecture des colonnes `_pre_*` au lieu de recalculer | convention générale du dépôt | `ml_dynamic_threshold` (ADX) | P2 |
| Hook `prepare_for_backtest` | 25 stratégies | ~~`gemini_trend_follow`~~ ; reste sans hook mais **déjà rapides** : `momentum_blitz`, `volatility_squeeze`, `harmonic_regime`, `funding_flow`, `derivatives_reversion`, `snowball_pyramid` | ✅ / sans objet |

**Point négatif utile** : les six stratégies sans `prepare_for_backtest` de la
dernière ligne tournent entre 3 600 et 41 000 barres/s. Leur ajouter le hook ne
gagnerait rien de mesurable. Le hook n'est pas une bonne pratique à généraliser :
c'est un remède à un symptôme précis, et l'appliquer par uniformité ajouterait de
l'état à maintenir sans contrepartie.

Un contrôle simple éviterait la prochaine régression de ce type : `breakout` était
40× plus lent que `smart_money` depuis l'introduction de L5, sans que rien ne le
signale. **Proposition (P2, effort S)** : un test de garde qui échoue si une
stratégie descend sous ~1 000 barres/s sur une série synthétique de référence —
un plancher, pas un chronomètre, donc stable en CI.

---

## 6. Plan proposé, par rentabilité

| # | Action | § | Effort | Gain | Risque |
|---|---|---|---|---|---|
| ✅ | `htf_trend` causal mémoïsé (8 stratégies) | 2.2 | — | **×9 à ×120** mesuré | nul (équité identique, testé) |
| ✅ 1 | `train_cache` + `aligned_train_window` sur v4/v5/smc_ml_edge | 3.2 | M | ×1,4 à ×2,0 mesuré, croît avec le nombre de retrains | **a révélé un bug** (features désalignées) |
| ✅ 2 | Prédiction par lot sur v4/v5/ml_dynamic_threshold | 4.4 | M | ×14 à ×20 isolé ; invisible tant que l'entraînement domine | nul (bit-à-bit, testé) |
| ✅ 3 | `n_trials` fonction de l'espace | 3.3 | S | 11 stratégies sous le seuil au lieu de 15 | coût du parc en hausse, assumé |
| ✅ 4 | `n_jobs` LightGBM contextuel | 4.1 | S | ×3,1 en entraînement autonome | nul (modèle identique, mesuré) |
| ✅ 5 | Tranche holdout jamais vue avant `beats_baseline` | 3.4 | M | **qualité de décision** | refus franc si l'historique ne suffit pas |
| ✅ 6 | `bb_squeeze` causal mémoïsé | 2.3 | S | ×18 sur `breakout` (×80 cumulé) | nul (équité identique, testé) |
| ✅ 7 | Embargo = max(horizons) aux trois splits | 4.2 | S | justesse par construction | nul à embargo 0, testé |
| ✅ 8 | `amp_thr` sur le train seul | 4.3 | S | justesse de l'AUC du gate | nul (défaut inchangé) |
| 9 | Test de garde « plancher barres/s » | 5 | S | non-régression | nul |
| ✅ 10 | ADX pré-calculé dans `ml_dynamic_threshold` | 4.5 | S | marginal | nul (0 bascule de verdict sur 2 700 barres) |
| 11 | Soumission continue au pool ; payload IPC non renvoyé | 3.1 | S/M | 1–13 % ; 0,06 s/40 essais | faible |
| 12 | `window_sweep` parallèle | 4.5 | M | ≈ nb de fenêtres | faible |
| ✅ + | Cadence de réentraînement appliquée aussi aux échecs | 7 | S | 368 → 5 tentatives inutiles | nul si l'entraînement réussit |

Les items 1, 2, 4, 6 sont des **gains de vitesse à comportement constant** — ils se
valident par comparaison stricte (équité identique, mêmes `best_params`) et ne
demandent aucun arbitrage. Les items 5, 7, 8 **changent les chiffres rapportés**,
dans le sens d'une mesure plus honnête : ils demandent une décision explicite et
une entrée de changelog, sans quoi la baisse d'AUC ou de PnL rapporté sera prise
pour une régression.

---

## 7. Ce que l'application du plan a fait apparaître

Cette section n'existait pas dans l'analyse initiale : elle rapporte ce que la
mise en œuvre a appris, y compris contre l'analyse elle-même.

### 7.1 Deux bugs, trouvés en portant des optimisations

Aucun des deux ne se cherchait ; les deux sortent du fait qu'on est allé
regarder le code de près pour y brancher autre chose.

**Les features d'entraînement de v4/v5 ne correspondaient pas à leurs labels.**
`_features_for_training` servait `X[:len(df)]` — les premières barres du
backtest — quelle que soit la fenêtre reçue. Or `score()` entraîne sur une
fenêtre de QUEUE dès que le backtest dépasse `warmup_bars × 2 + 1` barres
(4 001 par défaut) : au-delà, chaque réentraînement apprenait des features du
DÉBUT de la série contre des labels de la FIN. Écart mesuré entre la matrice
servie et la bonne : 187 ; avec l'offset, 0. Tout job d'optimisation sur
50 000 bougies était concerné à partir de la 4 001ᵉ barre. `MLBackend` évite
cela depuis toujours avec `bt_train_offset` — c'est le même angle mort que le
cache d'entraînement : ce que la famille Opus reçoit par sa base, ces deux-là
ne l'avaient pas.

**Un entraînement qui échoue était relancé à chaque barre.** Le test de besoin
est `(tf non entraîné) OU (appels − dernier_succès ≥ retrain_every)` ; un échec
ne met pas à jour `dernier_succès`, donc la première clause reste vraie et la
boucle tourne sans délai. Mesuré : 368 échecs ré-essayés pour 7 succès à
`retrain_every=800`, 1 168 pour 3 succès à 1 600.

### 7.2 Trois prédictions démenties par la mesure

| Ce que l'analyse annonçait | Ce que la mesure a dit |
|---|---|
| « attendre une légère baisse des AUC » après l'embargo (§4.2-4.3) | Sur 5 graines, le delta vaut +0,009 / +0,036 / +0,037 / −0,008 / +0,007 : **le signe n'est même pas constant**. Sur des prix aléatoires il n'y a aucun edge à faire fuir. La correction se justifie par construction, pas par ce chiffre. |
| « ×11 sur l'inférence » pour la prédiction par lot (§4.4) | ×14 à ×20 **isolément**, mais ×0,99 à ×1,14 sur un backtest réel : l'entraînement domine tellement que le gain est invisible tant que le cache d'entraînement n'est pas branché. Les deux corrections se composent ; aucune ne se mesure seule. |
| « reproductibilité à mesurer » avant de toucher `n_jobs` (§4.1) | Entre 1 et 4 threads, la sérialisation du booster diffère de **deux lignes sur 4 002** (`num_threads`), prédictions bit-à-bit identiques. La réserve ne tenait pas. |

Une quatrième mesure a été **corrigée en cours de route** : un premier A/B
donnait ×64 pour la prédiction par lot sur `ml_dynamic_threshold`. Le profil l'a
démenti — les 100 s étaient de l'entraînement, et le second bras profitait du
`train_cache` peuplé par le premier. Avec le cache vidé entre chaque bras et les
deux ordres joués, il ne reste rien. Un banc A/B sur du code qui mémoïse doit
vider ses caches entre les bras ; sinon il mesure l'ordre d'exécution.

### 7.3 `retrain_every` : peut-on passer de 800 à 2 000 ?

La question ne pouvait pas recevoir de réponse avant §7.1 : la première mesure
donnait 375 entraînements à 800, 373 à 1 200, **1 171 à 1 600**, 3 à 2 000. Un
paramètre de cadence qui triple le travail quand on le double ne mesure pas ce
qu'il annonce. Une fois la boucle d'échecs bornée :

| `retrain_every` | réussis | échecs | total | temps |
|---|---|---|---|---|
| 800 | 7 | 5 | 12 | 2,66 s |
| 1 200 | 5 | 4 | 9 | 1,81 s |
| 1 600 | 3 | 8 | 11 | 1,26 s |
| 2 000 | 3 | 0 | 3 | 1,21 s |

Trois choses à savoir avant de trancher :

1. **Le compteur compte des appels à `score()`, pas des barres.** `score()`
   n'est appelé que hors position : à 40 % de temps en position, 800 appels
   valent ~1 300 barres. « 800 » n'est donc pas une durée, et deux stratégies
   au même réglage ne se réentraînent pas au même rythme.
2. **L'argument de coût a beaucoup faibli.** C'était le principal motif pour
   augmenter la valeur ; avec le `train_cache` désormais branché sur v4/v5/
   smc_ml_edge, les essais d'un même job se partagent leurs entraînements —
   seul le premier paie. Ce qui reste à payer, c'est le live et le premier
   essai.
3. **Le coût de la staleness ne se mesure pas ici.** Sur une marche aléatoire,
   un modèle vieux de 800 ou de 2 000 barres est également inutile : il n'y a
   pas de régime à rater. Toute mesure de qualité sur ces données serait du
   bruit présenté comme un résultat.

**Recommandation.** Ne pas trancher à la main : `retrain_every` n'est dans
AUCUN `param_space` du dépôt — il n'a donc jamais été mesuré, seulement choisi.
Maintenant que le gate d'apply se prononce sur une tranche jamais vue (§3.4),
c'est exactement le genre de paramètre qu'on peut confier à l'optimiseur sans
craindre de sélectionner du bruit. Ajouter `retrain_every: [800, 1200, 1600,
2000]` au `param_space` de v4/v5/smc_ml_edge, sur données réelles, répondra
mieux que n'importe quel raisonnement — et la réponse sera probablement
différente selon le timeframe, ce qu'une valeur unique en dur ne peut pas
exprimer.

En attendant cette mesure : **garder 800**. C'est la valeur sous laquelle tous
les modèles publiés ont été produits, le coût qu'elle représente vient d'être
divisé, et rien de mesuré ne justifie d'en changer.

---

## Annexe — reproduire les mesures

```bash
pip install polars numpy lightgbm pyyaml python-dotenv pytest
export ALLOW_INSECURE_WEB=1          # config de dev, cf. app/core/config.py

python -m pytest tests/test_htf_trend_causal.py tests/test_bb_squeeze_causal.py -q
python -m pytest tests/test_bt_predictions.py tests/test_ml_splitting.py -q
python -m pytest tests/test_train_cache_v4_v5.py tests/test_holdout_split.py -q
python -m pytest tests/test_opt_budget.py tests/test_ml_threads.py -q
python -m pytest tests/test_retrain_cadence.py -q
python scripts/audit_param_space.py                    # couverture des espaces
```

Les bancs de mesure (séries synthétiques, profils cProfile, A/B d'équité,
dispersion des durées d'essai, effet du `train_cache`) sont des scripts jetables
volontairement non versionnés : ils dépendent d'un jeu de données absent du
dépôt. Les commandes exactes figurent dans le corps de l'analyse, section par
section.
