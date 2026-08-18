# 06 — Optimiseur : recherche, scoring, gates d'application

Périmètre : `app/engine/opt_scoring.py`, `app/engine/optimizer_search.py`,
`app/engine/auto_optimizer.py`, `app/engine/opt_bayesian.py`, `app/engine/opt_budget.py`,
`app/engine/opt_workers.py`, `app/core/is_oos.py`, `config/lifecycle.yaml`.

**Jugement d'ensemble.** C'est le sous-système le plus travaillé du dépôt et celui qui
porte le plus de risque résiduel. Les protections en place sont réelles et bien pensées :
holdout découpé en fin d'historique et retiré du pipeline de recherche, purge/embargo
calculés, seuil de trades unifié entre sélection et promotion, `overfitting_ratio` qui
refuse de rendre un nombre quand le ratio n'a pas de sens, pénalité de surapprentissage
qui ne s'applique plus aux scores négatifs. Ce sont de bonnes corrections, récentes et
justes.

Le problème n'est pas là. Il est que **la chaîne de décision qui mène à l'auto-apply
comporte quatre points où un garde-fou se neutralise silencieusement**, et un cinquième
où il se prononce sur un nombre qui ne signifie pas ce que son nom annonce. Chacun pris
isolément est défendable ; leur conjonction fait qu'un paramétrage peut être appliqué en
production sur des preuves beaucoup plus minces que la lecture du code ne le laisse croire.

---

## OPT-01 — Le gate Deflated Sharpe se prononce sur un nombre gouverné par une constante non mesurée

**Sévérité P1 · CONFIRMÉ (calcul reproduit)**

`opt_scoring.py:236-240` appelle le DSR **sans** `trial_sharpes_std` :

```python
dsr = deflated_sharpe_ratio(
    float(oos_sharpe),
    n_observations=int(oos_trades),
    n_trials=int(n_trials),
)
```

Le paramètre retombe donc sur son défaut (`opt_scoring.py:312`) :

```python
std_sr = trial_sharpes_std if trial_sharpes_std is not None else 1.0
```

Recherche exhaustive des appelants : **aucun des deux ne le fournit**
(`app/api/routes/optimizer.py:356`, `app/engine/auto_optimizer.py:655`). La valeur 1.0
est toujours celle utilisée.

### Ce que cette constante impose

`_expected_max_sharpe` est **linéaire** en `sharpe_std`. Le seuil implicite `sr0` que le
candidat doit dépasser :

| `n_trials` | `E[max SR]` avec std = 1.0 |
|---:|---:|
| 10 | 1,575 |
| 40 | 2,189 |
| 100 | 2,531 |
| **400** | **2,985** |
| 1 000 | 3,255 |

`config/lifecycle.yaml:45` fixe `max_trials: 400`. Le gate exige donc, en pratique, un
Sharpe OOS annualisé d'environ **3,0** avant que le DSR n'atteigne 0,5 — la valeur du
seuil par défaut (`auto_optimizer.py:645` : `deflated_sharpe_min: 0.5`, gate activé par
défaut ligne 644, et **absent de `config/lifecycle.yaml`** donc jamais surchargé).

DSR obtenu, `n_trials = 400`, 20 trades OOS :

| Sharpe OOS | DSR | Verdict au seuil 0,5 |
|---:|---:|---|
| 0,5 | 0,000000 | refusé |
| 1,0 | 0,000000 | refusé |
| 1,5 | 0,000005 | refusé |
| 2,0 | 0,006599 | refusé |
| 3,0 | 0,511268 | accepté de justesse |
| 5,0 | 0,991592 | accepté |

Un Sharpe OOS de 1,5 — un résultat parfaitement respectable — donne une « probabilité
que le Sharpe soit réellement positif » de **cinq millionièmes**.

### Ce que change le seul choix de la constante

Même candidat (Sharpe 1,5 · 20 trades · 400 essais), en faisant varier `trial_sharpes_std`
et rien d'autre :

| `trial_sharpes_std` | `sr0` | DSR |
|---:|---:|---:|
| 0,10 | 0,30 | 0,999836 |
| 0,25 | 0,75 | 0,987902 |
| 0,50 | 1,49 | 0,509064 |
| **1,00 (défaut)** | **2,98** | **0,000005** |
| 2,00 | 5,97 | 0,000000 |

**Cinq ordres de grandeur** sur une décision de production, pilotés par un paramètre que
personne ne mesure. Et l'optimiseur **a la mesure sous la main** : il stocke `oos_sharpe`
pour chaque essai (`optimizer_search.py:337`) — l'écart-type empirique des Sharpe d'essais
est à un `statistics.stdev` de distance.

La docstring du module l'admet à demi-mot (`opt_scoring.py:263-268`) : « *Fonction
volontairement AUTONOME (pas encore câblée dans composite_score) : une intégration
nécessiterait de faire remonter n_trials/trial_sharpes_std depuis la boucle de
l'optimiseur* ». `n_trials` a bien été câblé depuis. `trial_sharpes_std` ne l'a pas été,
et le gate a quand même été activé par défaut.

### OPT-02 — et le Sharpe fourni n'est pas sur la bonne échelle

**Sévérité P1 · PLAUSIBLE (analyse dimensionnelle)**

Aggravant, et indépendant. Le Sharpe passé au DSR est le Sharpe **annualisé**
(`backtest_result.py:75-77`) :

```python
ann_factor = np.sqrt(returns_per_year(len(returns), self._years(), _bars_per_year(...)))
raw_sharpe = float(returns.mean() / std * ann_factor)
```

Or la formule de Bailey & López de Prado est définie sur le Sharpe **par observation** :
c'est précisément le rôle du facteur `sqrt(t - 1)` dans

```python
z = (sr - sr0) * math.sqrt(t - 1) / denom      # opt_scoring.py:319
```

d'effectuer l'annualisation. En fournissant un Sharpe déjà annualisé, on l'annualise deux
fois. Symétriquement, `sr0` construit avec `std = 1.0` est sur l'échelle par observation.
`sr` et `sr0` ne sont donc pas comparables, et le terme `(kurtosis-1)/4 · sr²` du
dénominateur — calibré pour un Sharpe de l'ordre de l'unité — explose sur un Sharpe
annualisé.

Effet net : sur un petit échantillon OOS, l'annualisation gonfle `sr` (10 trades sur 6 mois
→ facteur ≈ 4,5), ce qui peut faire **passer** le gate à un candidat que la formule
correcte rejetterait. Les deux défauts ne se compensent pas — ils rendent le résultat
non interprétable dans les deux sens.

### Correction (OPT-01 + OPT-02)

1. Calculer l'écart-type empirique des Sharpe d'essais dans la boucle de recherche et le
   passer à `deflated_sharpe_ratio`.
2. Passer le Sharpe **non annualisé** (conserver `returns.mean() / std` à côté du Sharpe
   annualisé dans `BacktestResult`), ou retirer `sqrt(t-1)` — l'un ou l'autre, pas les deux.
3. Tant que ce n'est pas fait, **désactiver le gate** (`deflated_sharpe_gate: false`)
   plutôt que de le laisser filtrer sur une grandeur arbitraire : un gate qu'on ne sait pas
   interpréter est pire qu'un gate absent, parce qu'il donne l'illusion d'une protection.

**Effort** : ~20 lignes + tests. ½ journée. Le point 3 est immédiat.

---

## OPT-03 — Aucune contrainte de drawdown, nulle part dans la chaîne

**Sévérité P1 · CONFIRMÉ (recherche exhaustive)**

Le drawdown OOS **est mesuré** — `optimizer_search.py:340` : `"oos_dd": res_oos.max_drawdown`.
Il n'est ensuite utilisé **par aucun critère de décision** :

- `beats_baseline` (`opt_scoring.py:178-251`) : cinq critères — nombre de trades,
  PnL > 0, PnL > baseline, amélioration WR **ou** Sharpe, DSR. **Aucun sur le risque.**
- `auto_optimizer.py` : `grep max_drawdown` ne trouve qu'une seule occurrence
  (ligne 340), un champ d'affichage `"dd"`.
- `composite_score` : le terme `dd_factor = max(0, 1.0 - dd / 30)` **sature à 30 %** —

  | `max_drawdown` | `dd_factor` |
  |---:|---:|
  | 5 % | 0,833 |
  | 15 % | 0,500 |
  | 30 % | 0,000 |
  | 50 % | 0,000 |
  | 80 % | 0,000 |
  | 95 % | 0,000 |

Au-delà de 30 %, le classement est **aveugle** au risque. Un paramétrage à 32 % de
drawdown et un paramétrage à 88 % obtiennent exactement la même contribution. Pour un bot
à levier sur crypto, c'est la différence entre une mauvaise passe et la ruine, et le score
ne peut pas la voir.

Conséquence concrète : un paramétrage qui **double** le PnL OOS en **triplant** le
drawdown passe le gate (PnL positif ✓, PnL > baseline ✓, WR ou Sharpe amélioré ✓) et est
appliqué automatiquement en production.

**Correction** :
1. Ajouter un sixième critère à `beats_baseline` : refuser si
   `oos_dd < baseline_dd × (1 + tolérance)`. Le champ existe déjà côté appelant.
2. Remplacer la saturation par une pénalité qui continue de mordre — par exemple
   `dd_factor = 1 / (1 + dd/15)`, strictement décroissante sur tout le domaine.

**Effort** : ~15 lignes + recalibration des seuils. 1 journée avec la revalidation.

---

## OPT-04 — Sans holdout, le gate d'apply se prononce sur la tranche qui a servi à sélectionner

**Sévérité P1 · CONFIRMÉ (lecture)**

`auto_optimizer.py:300` :

```python
df_gate = df_holdout if df_holdout is not None else df_oos
```

`split_with_holdout` renvoie `df_holdout = None` quand l'historique ne permet pas de
réserver une tranche exploitable (`is_oos.py:82-86`) — condition assez large :

```python
if (n_holdout <= 0 or n_holdout < besoin
        or reste < max(warmup + 100 + besoin, int(besoin / max(oos_fraction, 1e-9)))):
```

Le repli est journalisé (`auto_optimizer.py:293-295`) puis **le gate s'exécute quand
même**, sur `df_oos`.

Or `is_oos.py:27-33` explique précisément pourquoi c'est invalide :

> *`df_oos` sert quatre fois dans le pipeline actuel : à classer les N essais, à mesurer
> le gagnant, à mesurer la référence, et à vérifier la consistance walk-forward. Après N
> sélections, un score sur cette tranche n'est plus une estimation hors-échantillon :
> c'est un maximum d'ordre N, biaisé vers le haut par construction.*

Le module qui décrit le défaut fournit le repli qui le réintroduit. Sur un historique
court — le cas le plus fréquent en développement, et celui où le sur-apprentissage est le
plus probable — l'auto-apply décide sur du biaisé, et rien dans le résultat du job ne le
signale à l'utilisateur (le champ `holdout_bars` existe pourtant, `auto_optimizer.py:472`).

**Correction** : sans holdout, **refuser l'auto-apply** et exiger une validation manuelle.
Le repli sur `df_oos` reste acceptable pour *afficher* un chiffre, jamais pour *décider*.
~5 lignes.

---

## OPT-05 — Le gate walk-forward passe par défaut dans les trois cas où il ne peut pas conclure

**Sévérité P1 · CONFIRMÉ (lecture)**

`_wf_consistent` (`auto_optimizer.py:661-701`) retourne `True` — donc laisse passer — dans
trois situations distinctes :

```python
if not bool(opt_cfg.get("wf_gate", True)) or df_recherche is None:
    return True                                    # 1. désactivé ou pas de données
...
if "error" in res_wf:
    logger.info(... "walk-forward indisponible ... gate neutre")
    return True                                    # 2. WF impossible
...
except Exception as e:
    logger.warning(... "walk-forward KO ... gate neutre")
    return True                                    # 3. WF planté
```

Le commentaire assume le choix (« *on ne durcit pas à l'aveugle* »), et c'est un
raisonnement défendable **en isolation**. Il cesse de l'être une fois croisé avec deux
constats du rapport backtest :

- **BT-01** : `WalkForwardAnalyzer.run` renvoie `{"error": ...}` dès que
  `fold_n < 260`, soit `n < 1 560` barres. Sur du 4 h, c'est 260 jours d'historique. En
  dessous, le gate est **systématiquement neutre** — pas occasionnellement.
- **BT-03** : un fold qui lève est retiré silencieusement, et `consistency` est calculée
  sur les folds survivants. Quatre folds sur cinq qui plantent donnent une `consistency`
  de 100 % sur le seul fold restant — le gate passe, avec les honneurs.

S'ajoute **BT-02** : le walk-forward force `realistic_risk=True` alors que les essais
d'optimisation tournent avec le défaut `False`. Le gate valide donc une économie
différente de celle qui a été optimisée.

**Correction** : distinguer « gate satisfait » de « gate non évaluable ». Un gate non
évaluable doit bloquer l'auto-apply (et autoriser l'apply manuel avec un avertissement
explicite), pas l'autoriser. ~10 lignes.

---

## OPT-06 — `beats_baseline` accepte une amélioration du win-rate seul

**Sévérité P2 · PLAUSIBLE (lecture)**

`opt_scoring.py:218` :

```python
if not (oos_wr > b_wr or _sharpe_ok):
```

Le win-rate seul suffit. Or il est trivial de l'améliorer en dégradant l'espérance :
resserrer les cibles produit plus de petits gagnants et des perdants inchangés — win-rate
en hausse, profit factor en baisse. Le critère 3 (`oos_pnl > b_pnl`) borne partiellement
le dégât, mais sur un échantillon de 10 trades — le minimum admis — un PnL supérieur de
quelques euros et un win-rate supérieur de 10 points suffisent à promouvoir un paramétrage
strictement plus risqué.

`expectancy` et `profit_factor` sont calculés et disponibles. Exiger l'amélioration d'un
critère **d'espérance** (profit factor ou expectancy) plutôt que de fréquence serait plus
fidèle à l'intention.

---

## OPT-07 — Le score composite hérite du biais FIN-01 sur quatre de ses huit termes

**Sévérité P2 · Dérivé**

`composite_score` prend soin d'utiliser `net_profit` en priorité (`opt_scoring.py:62`,
`:71`) — ce qui, par chance, **contourne** FIN-01 sur le terme de rendement. Mais quatre
autres termes dérivent de `t["pnl"]`, donc portent le biais :

| Terme | Poids | Source | Biaisé par FIN-01 ? |
|---|---:|---|---|
| `ret_norm` | 0,20 | `net_profit` | **non** |
| `sharpe_norm` | 0,22 | `equity_curve` (trade par trade) | non |
| `wr` | 0,15 | signe de `t["pnl"]` | **oui** (près de zéro) |
| `pf / 6` | 0,15 | somme des `t["pnl"]` | **oui** |
| `exp_norm` | 0,08 | `t["pnl"]` | **oui** |
| `dd_factor` | 0,10 | `equity_mtm` | non |
| `trade_factor` | 0,10 | comptage | non |
| `alpha_bonus` | 0,10 | `total_pnl` | **oui** |

38 % du poids du score repose sur une grandeur systématiquement sous-estimée pour les
trades fractionnés — et seulement pour eux. L'optimiseur pénalise donc structurellement
les paramétrages qui utilisent des sorties partielles, c'est-à-dire précisément le mode
`tp1_tp2_runner` qu'on cherche à évaluer. Se résout avec FIN-01.

---

## OPT-08 — Le budget d'essais alimente le seuil DSR, ce qui pénalise la recherche approfondie

**Sévérité P2 · Observation de conception**

Le budget est désormais proportionné à l'espace (`opt_budget.py`, `trials_per_param: 15`,
`max_trials: 400`) — bonne idée, et le commit `2722e77` porte `smart_money` de 40 à 400
essais. Mais `n_trials` alimente directement `sr0` (OPT-01) : passer de 40 à 400 essais
fait monter le seuil implicite de Sharpe de **2,19 à 2,99**.

C'est méthodologiquement **correct** — chercher plus expose davantage au biais de sélection
— mais l'effet doit être compris : améliorer la couverture de l'espace durcit
mécaniquement le gate de promotion. Avec la constante `std = 1.0` d'OPT-01, la combinaison
rend l'auto-apply pratiquement inatteignable sur les stratégies à grand espace. À corriger
avec OPT-01, pas séparément.

---

## Ce qui a été vérifié et tenu

- **`overfitting_ratio` refuse de rendre un nombre quand il n'a pas de sens** — `NaN` pour
  les trois cas dégénérés (`opt_scoring.py:166-169`), au lieu de `0.0` (la meilleure valeur
  de l'échelle) ou d'une saturation à 10. La correction est juste et le raisonnement
  documenté.
- **Le `NaN` est correctement absorbé en aval** — `_penalized_score` teste `np.isnan(ovf)`
  avant toute comparaison (`optimizer_search.py:369`). Pas de propagation silencieuse.
- **La pénalité de surapprentissage ne s'applique plus aux scores négatifs**
  (`optimizer_search.py:370`) : multiplier un score négatif par `2.5/ovf < 1` le
  *rapprochait* de zéro, donc récompensait le surapprentissage. Corrigé.
- **Le score est monotone avec le PnL** (`opt_scoring.py:135-138`) : une stratégie nette
  perdante ne peut plus obtenir un score positif via un bon win-rate.
- **Un seul seuil de trades** entre sélection et promotion (`MIN_SIGNIFICANT_TRADES`) —
  l'écart par lequel passaient les optima hyper-sélectifs est fermé.
- **Le Sharpe est borné dans le score** (`sharpe_norm ∈ [-1, 1]`, saturation à 10) : il ne
  peut plus écraser les autres termes sur de petites fenêtres.
- **Le holdout est réellement retiré du pipeline** — le walk-forward du gate tourne sur
  `df_recherche`, pas sur `df` (`auto_optimizer.py:689`).
- **Purge et embargo sont calculés et passés** (`auto_optimizer.py:288-289`, `:921-923`).
- **Le DSR est monotone croissant en Sharpe** — vérifié numériquement sur [0,5 ; 20] :
  aucune inversion. Le défaut d'OPT-01/02 est un défaut d'échelle et de calibration,
  pas de forme.

---

## Récapitulatif

| ID | Sévérité | Preuve | Constat | Effort |
|---|---|---|---|---|
| OPT-01 | **P1** | CONFIRMÉ | DSR piloté par `trial_sharpes_std = 1.0` jamais mesuré | ½ j |
| OPT-02 | **P1** | PLAUSIBLE | Sharpe annualisé fourni à une formule par observation | ½ j |
| OPT-03 | **P1** | CONFIRMÉ | Aucune contrainte de drawdown, et saturation à 30 % | 1 j |
| OPT-04 | **P1** | CONFIRMÉ | Sans holdout, le gate décide sur la tranche de sélection | 1 h |
| OPT-05 | **P1** | CONFIRMÉ | Gate walk-forward neutre dans ses 3 cas d'échec | 2 h |
| OPT-06 | P2 | PLAUSIBLE | Le win-rate seul suffit à promouvoir | 1 h |
| OPT-07 | P2 | Dérivé | 38 % du poids du score hérite du biais FIN-01 | — |
| OPT-08 | P2 | — | Budget élargi ⇒ gate DSR durci mécaniquement | avec OPT-01 |
