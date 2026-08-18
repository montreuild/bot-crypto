# 05 — Moteur de backtest, métriques et biais méthodologiques

Périmètre : `app/engine/backtest.py`, `app/engine/backtest_result.py`,
`app/engine/walk_forward.py`, `app/engine/monte_carlo.py`, `app/core/is_oos.py`,
`app/core/performance_metrics.py`, `app/core/indicators_precompute.py`.

**Jugement d'ensemble.** La boucle de backtest est causale et le reste sur tous les
chemins vérifiés : la fenêtre servie aux stratégies s'arrête à la barre courante,
l'entrée se fait à l'ouverture de la barre suivante, le stop est celui fixé à la clôture
précédente. Le dépôt a manifestement déjà chassé les fuites évidentes. Les problèmes
restants sont d'un autre ordre : ils portent sur **ce que les chiffres mesurent
réellement**, pas sur leur causalité. Le plus lourd — BT-01 — fait que l'« analyse
walk-forward » n'évalue qu'une fraction de la fenêtre qu'elle affiche.

---

## BT-01 — Les folds OOS du walk-forward sont consommés à 80 % par leur propre warmup

**Sévérité P1 · CONFIRMÉ (arithmétique du code)**

`app/engine/walk_forward.py:69-72` :

```python
is_end  = fold_n * (k + 1)
oos_end = min(fold_n * (k + 2), n)
df_is   = df[:is_end]
df_oos  = df[is_end:oos_end]        # ← fenêtre BRUTE, sans historique amont
```

`df_oos` est passé tel quel à un `Backtester` neuf, qui applique son propre warmup
(`backtest.py:345-355`, minimum `WARMUP_BARS_DEFAULT = 210`) **à l'intérieur** de cette
fenêtre :

```python
for i in range(warmup, len(df) - 1):       # backtest.py:446
```

Le garde d'entrée n'exige que `fold_n >= MIN_IS = WARMUP + 50 = 260`
(`walk_forward.py:49,53`). Donc, dans le pire cas admis :

| Grandeur | Valeur |
|---|---|
| Longueur du fold OOS | 260 barres |
| Warmup consommé dans le fold | 210 barres |
| **Barres réellement tradées** | **49** |

**81 % de chaque fold OOS ne produit aucun trade** — et l'utilisateur, lui, lit « 5 folds
out-of-sample » en croyant à cinq fenêtres complètes. Sur un historique de 1 560 barres
(5 folds + 1), la mesure OOS totale porte sur ~245 barres, pas 1 300.

Deux conséquences qui se cumulent :

1. **`consistency`** (`walk_forward.py:129`) — « pourcentage de folds à PnL positif » — est
   calculée sur des échantillons de quelques trades chacun. Avec 49 barres tradables, un
   fold contient typiquement 0 à 3 trades. Un fold à zéro trade a `total_pnl = 0`, donc
   compte comme **non positif** et fait baisser la consistance : une stratégie sélective
   est pénalisée pour n'avoir pas eu le temps de trader.
2. **Les indicateurs redémarrent à froid** à chaque frontière de fold. Une EMA200 relancée
   sur 210 barres n'est pas l'EMA200 qu'aurait vue le bot en continu — les premières
   valeurs post-warmup portent encore le biais d'initialisation.

### Correction

Donner au fold OOS son historique de chauffe **en amont**, et ne trader qu'à partir de
`is_end` :

```python
df_oos = df[max(0, is_end - WARMUP):oos_end]
```

en portant le warmup effectif du `Backtester` à `WARMUP` pour ce run (le décalage est
alors exactement absorbé). L'IS n'a pas le problème : `df[:is_end]` est cumulatif et
contient déjà tout l'amont.

**Effort** : ~5 lignes, plus la revalidation de toute analyse walk-forward publiée.
⚠ Change les chiffres de tous les walk-forwards existants.

---

## BT-02 — Le walk-forward tourne avec les circuit breakers, le backtest normal sans

**Sévérité P1 · CONFIRMÉ (lecture)**

`walk_forward.py:89-92` force `realistic_risk=True` sur les deux backtesters de chaque
fold. Le `Backtester` par défaut, lui, est à `False` — et le dit explicitement
(`backtest.py:137`) :

> *Circuit breakers opt-in — off pour préserver la parité des backtests existants.*

L'utilisateur qui compare le PnL de son backtest à `avg_oos_pnl` compare donc deux
économies différentes : l'une subit les pauses après pertes consécutives, les plafonds de
drawdown journalier, le plafond de trades/jour et le frein de volatilité ; l'autre non.
Rien dans la sortie du walk-forward ne signale cet écart — `_fold_summary`
(`walk_forward.py:109-118`) ne remonte pas `realistic_risk`, alors que
`BacktestResult.to_dict()` l'expose (`backtest_result.py:448`).

**Correction** : soit aligner le défaut, soit — mieux — propager le drapeau et le faire
remonter dans la sortie du walk-forward pour que l'écart soit visible. ~4 lignes.

---

## BT-03 — Un fold qui plante disparaît de la moyenne sans que rien ne le dise

**Sévérité P2 · CONFIRMÉ (lecture)**

`walk_forward.py:97-98` :

```python
except Exception as e:
    logger.error(f"[WF] Fold {k} : {e}", exc_info=True)
```

Le fold est simplement absent des listes. Ensuite, `n_folds` renvoie
`len(out_sample_results)` (`walk_forward.py:121`) — **le nombre de folds réussis**, pas le
nombre demandé. Un run où 4 folds sur 5 échouent renvoie une structure de succès,
`n_folds: 1`, une moyenne sur un seul fold et une `consistency` de 0 ou 100 %. Rien ne
distingue ce résultat d'un run sain, sauf à lire les logs serveur.

**Correction** : compter les échecs et les remonter (`n_folds_demandes`, `n_folds_echoues`,
`erreurs`). Refuser de publier une moyenne sous un seuil de folds valides. ~10 lignes.

---

## BT-04 — L'équité mark-to-market porte un PnL latent sur des positions qui n'existent pas encore

**Sévérité P2 · CONFIRMÉ (lecture)**

Ordre des opérations dans la boucle, pour la barre `i` :

1. `_try_enter(ctx, signal, i)` ouvre à `open[i+1]` et pose `position["bar"] = i + 1`
   (`position_lifecycle.py:502` et `:627`) ;
2. la position est immédiatement ajoutée à `positions` (`backtest.py:560`) ;
3. `_mark_mtm(i)` (`backtest.py:563`) valorise **toutes** les positions ouvertes au
   `close[i]` (`backtest.py:439-443`).

Le PnL latent inscrit pour cette barre vaut donc `close[i] − open[i+1]`, sur une position
dont l'entrée est postérieure. C'est un point d'équité fictif, un par trade.

Ça compte, parce que `max_drawdown` se lit **uniquement** sur cette série
(`backtest_result.py:53-57`, correctif F-06). Le drawdown affiché intègre donc, à chaque
entrée, un écart d'une barre entre deux prix qui ne se sont jamais succédé dans une
position réelle. Sur des données à gaps (actions à l'ouverture, crypto sur événement),
l'écart n'est pas négligeable.

**Correction** : appeler `_mark_mtm(i)` **avant** la boucle d'ouverture, ou n'inclure dans
la valorisation que les positions telles que `position["bar"] <= i`. ~2 lignes.

---

## BT-05 — Deux définitions du benchmark Buy & Hold dans la même fonction

**Sévérité P2 · CONFIRMÉ (lecture)**

`backtest.py:666-669` — le B&H part de l'**ouverture** de la première barre tradée :

```python
if "open" in df.columns and warmup + 1 < len(df):
    first_price = float(df["open"][warmup + 1])      # B-13 : même première barre que le bot
```

`backtest.py:683` — la série servie à `compute_extended_metrics`, qui produit
`alpha_vs_bh`, part de la **clôture** de la barre de warmup :

```python
result._close_prices = [float(x) for x in df["close"][warmup:].to_list()]
```

`alpha` (`backtest.py:677`) et `alpha_vs_bh` (`backtest_result.py:282`) ne se réfèrent
donc pas au même benchmark. Les deux sont exposés côte à côte dans `to_dict()`
(`backtest_result.py:417,426`) et l'interface les affiche tous les deux. Un écart entre
eux se lit comme une information, alors que c'est un artefact de définition.

**Correction** : une seule série de référence, alignée sur `open[warmup+1]`. ~3 lignes.

---

## BT-06 — Le Monte-Carlo mesure un drawdown sur une autre base que le backtest

**Sévérité P2 · CONFIRMÉ (lecture)**

`monte_carlo.py:54-57` reconstruit l'équité par cumul des PnL **de trade** :

```python
equity = np.concatenate([[initial_capital], initial_capital + np.cumsum(shuffled)])
```

C'est-à-dire la courbe « un point par trade » — exactement celle que le correctif F-06 a
écartée pour le drawdown du backtest, au motif qu'elle « ignore les pertes latentes »
(`backtest_result.py:50-52`).

Résultat : `max_dd_p95` (Monte-Carlo) et `max_drawdown` (backtest) sont deux grandeurs
différentes, exprimées dans la même unité, affichées dans la même interface. Le premier
est **structurellement plus petit**. Un utilisateur qui lit « drawdown historique −18 %,
drawdown Monte-Carlo p95 −12 % » en conclut que l'historique a été malchanceux, alors que
la seule chose démontrée est que les deux ne mesurent pas la même chose.

**Correction** : soit renommer explicitement (`max_dd_p95_par_trade`), soit rééchantillonner
sur des blocs de la courbe MTM. Le renommage est honnête et coûte 1 ligne ; le
rééchantillonnage est juste et coûte une demi-journée.

---

## BT-07 — Le Monte-Carlo suppose des PnL indépendants de leur ordre, ce que le sizing dément

**Sévérité P2 · Observation méthodologique**

La permutation des PnL (`monte_carlo.py:53`) n'a de sens que si le PnL d'un trade ne
dépend pas de ce qui l'a précédé. Or le sizing du dépôt est **explicitement**
path-dépendant :

- courbe de dé-risquage en drawdown : `risk_amount *= _risk_multiplier(dd)`
  (`position_lifecycle.py:544`) ;
- frein de volatilité : `risk_amount *= _gate.volatility_brake_factor` (`:547`) ;
- circuit breakers : un trade peut être **refusé** selon l'historique récent
  (`:470-478`).

Permuter les PnL revient donc à simuler des séquences que le bot n'aurait pas pu produire :
une série de grosses pertes consécutives placées en tête aurait, en réalité, entraîné une
réduction de taille dès la troisième. Le Monte-Carlo **surestime** la queue de drawdown
sur ce point précis (ce qui est le sens prudent), mais le chiffre n'est pas la
distribution du bot — c'est celle d'un bot à taille fixe.

Ce n'est pas un défaut à corriger à la légère : la bonne réponse est de le **dire**. Une
ligne dans la sortie (`"hypothese": "PnL i.i.d., sizing path-dépendant non rejoué"`) vaut
mieux qu'une refonte. Reste que `prob_ruin_10pct` est un nom trop fort pour « probabilité
d'un drawdown de 10 % », ce qu'il calcule réellement (`monte_carlo.py:74`).

---

## BT-08 — Le Monte-Carlo hérite du biais FIN-01

**Sévérité P2 · Dérivé**

`monte_carlo.py:46` : `pnls = np.array([t["pnl"] for t in closed])`. Ce `t["pnl"]` est
celui que FIN-01 sous-estime pour tout trade fractionné. Toute la distribution
Monte-Carlo est donc décalée du même montant. Se corrige avec FIN-01, sans intervention
propre.

Même dépendance pour `by_exit_leg`, `by_strategy`, `by_setup`, `by_module`,
`by_exit_reason`, `by_structure_state`, `by_sequence_type`, `by_tier`,
`by_target_class` et les Sharpe par groupe (`backtest_result.py:311-374`).

---

## BT-09 — Un historique court produit un OOS vide, silencieusement

**Sévérité P2 · CONFIRMÉ (arithmétique)**

`is_oos.py:114-117` :

```python
split     = max(warmup + 100, int(n * (1.0 - oos_fraction)))
oos_start = min(n, split + max(int(embargo_bars), 0))
return df[:is_end], df[oos_start:], split
```

Le plancher `warmup + 100 = 310` n'est jamais confronté à `n`. Pour `n < 310`,
`split = 310 > n` : l'IS reçoit tout l'historique, **l'OOS reçoit un DataFrame vide** — et
la fonction retourne sans le signaler. Avec un embargo, le seuil monte encore.

Un OOS vide produit zéro trade, donc un PnL de 0 et des métriques nulles. Selon le gate
qui le consomme, « aucun trade OOS » se lit comme « pas d'edge » (rejet légitime) ou comme
« pas de perte » (acceptation illégitime). Les deux lectures sont fausses pour la même
raison : il n'y a pas eu de mesure.

**Correction** : lever ou retourner un marqueur explicite quand `n <= split`, à charge de
l'appelant de refuser la décision plutôt que de la prendre sur du vide. ~5 lignes.

---

## BT-10 — Les IS des folds se chevauchent, ce que la sortie ne dit pas

**Sévérité P3 · Observation**

`df_is = df[:is_end]` (`walk_forward.py:71`) est **cumulatif** : l'IS du fold 4 contient
les OOS des folds 0 à 3. Les cinq lignes `in_sample` de la sortie ne sont donc pas cinq
mesures indépendantes mais cinq fenêtres emboîtées.

C'est cohérent avec la méthode annoncée (`walk_forward.py:3-5` : « stabilité temporelle,
pas walk-forward de la littérature ») et le champ `"reoptimizes": False` le signale
honnêtement. Mais `"kind": "stability"` ne dit pas que les IS sont emboîtés, et un lecteur
qui compare les cinq lignes IS entre elles interprétera de la corrélation mécanique comme
de la stabilité.

---

## Ce qui a été vérifié et tenu

- **Causalité de la boucle** — `ctx.window = df.slice(0, i + 1)` (`backtest.py:488`) est
  posé avant toute décision de la barre `i`, et l'entrée se fait à `df["open"][i + 1]`
  (`position_lifecycle.py:502`). Les hooks `check_early_exit` et `check_scale_in` ne
  reçoivent que cette fenêtre.
- **Warmup dynamique** — pris comme le maximum des `warmup_bars` / `min_bars` déclarés
  (`backtest.py:346-353`), et propagé au benchmark B&H (correctif FIN-04 de la revue
  précédente, toujours en place).
- **Cache de pré-calcul** — la clé `(hauteur, largeur, borne basse, borne haute, dernier
  close, convention de lissage)` (`indicators_precompute.py:109-110`) est assez
  discriminante ; une collision exigerait deux séries identiques sur ces six critères.
  Pas de fuite par ce chemin.
- **Sharpe** — l'annualisation passe par `returns_per_year`, qui plafonne à la cadence des
  bougies et refuse d'inventer une durée (`performance_metrics.py:44-46`). `sharpe = None`
  sous `MIN_SIGNIFICANT_TRADES` observations : `None` (non mesurable) est bien distingué de
  `0.0` (ratio nul), et `profit_factor` fait de même quand il n'y a aucune perte.
- **Monte-Carlo** — la distinction séquence (permutation) / échantillonnage (bootstrap avec
  remise) est correcte, et le quantile de risque prend bien la queue basse
  (`percentile(max_dds, 5)`, `monte_carlo.py:72`) et non le percentile 95 d'une série
  négative.
- **Holdout** — découpé à la **fin** de l'historique et retiré de tout le pipeline de
  recherche, y compris du walk-forward via `df_recherche` (`is_oos.py:60-64`). C'est la
  bonne construction.
- **Purge et embargo** — effectivement calculés et passés par `auto_optimizer`
  (`auto_optimizer.py:288-289`, `:921-923`). Ils ne sont **pas** appliqués entre les folds
  du walk-forward, mais celui-ci rejoue un paramétrage figé sans réapprentissage : la fuite
  par les labels n'y a pas de vecteur.

---

## Récapitulatif

| ID | Sévérité | Preuve | Constat | Effort |
|---|---|---|---|---|
| BT-01 | **P1** | CONFIRMÉ | Folds OOS consommés à 81 % par le warmup | 1 j (avec revalidation) |
| BT-02 | **P1** | CONFIRMÉ | Walk-forward sous circuit breakers, backtest non | 30 min |
| BT-03 | P2 | CONFIRMÉ | Fold en échec silencieusement retiré de la moyenne | 1 h |
| BT-04 | P2 | CONFIRMÉ | MTM valorise des positions pas encore ouvertes | 15 min |
| BT-05 | P2 | CONFIRMÉ | Deux définitions du Buy & Hold | 15 min |
| BT-06 | P2 | CONFIRMÉ | Drawdown Monte-Carlo sur une autre base | 1 h |
| BT-08 | P2 | Dérivé | Monte-Carlo hérite du biais FIN-01 | — |
| BT-09 | P2 | CONFIRMÉ | OOS vide silencieux sous 310 barres | 30 min |
| BT-07 | P2 | — | Hypothèse i.i.d. contredite par le sizing | 15 min (documenter) |
| BT-10 | P3 | — | IS des folds emboîtés, non signalé | 15 min |
