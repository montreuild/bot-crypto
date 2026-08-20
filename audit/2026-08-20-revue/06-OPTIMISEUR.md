# 06 — Optimiseur

Le delta durcit le garde-fou d'application des paramètres optimisés
(`app/engine/opt_scoring.py`, +24/−4) et enrichit `auto_optimizer.py`
(+44/−28), `compute_jobs.py` (+39/−9), `opt_bayesian.py`, `opt_workers.py`.

L'intention — ne plus promouvoir un paramétrage sur le seul win-rate, et
refuser une dégradation du drawdown — est saine. **Les deux mécanismes ajoutés
sont inertes en production.**

---

## OPT-01 — Les branches expectancy et profit-factor du garde-fou ne peuvent jamais s'activer (P1, CONFIRMÉ)

**Fichier** : `app/engine/opt_scoring.py:221-232`.
**Introduit par** : `fec34ed` (dans le delta).

### Le code

```python
b_pf  = baseline.get("profit_factor")
b_exp = baseline.get("expectancy")
_exp_ok = (oos_expectancy is not None and b_exp is not None
           and oos_expectancy > b_exp)
_pf_ok  = (oos_pf is not None and b_pf is not None and oos_pf > b_pf)
if not (_sharpe_ok or _exp_ok or _pf_ok or oos_wr > b_wr):
    return False, "aucune amélioration de qualité (…)"
if oos_wr > b_wr and not (_sharpe_ok or _exp_ok or _pf_ok):
    return False, "win-rate seul insuffisant (…)"
```

La règle voulue est : « Sharpe **ou** expectancy **ou** profit factor doit
s'améliorer ». Elle est morte des deux côtés à la fois.

**Côté baseline** — `job["baseline"]` est produit par `_run_baseline`
(`app/engine/auto_optimizer.py:335-343`), qui renvoie exactement :

```
trades, pnl, sharpe, wr, dd, alpha
```

Ni `profit_factor` ni `expectancy`. `b_pf` et `b_exp` valent donc
**toujours** `None`.

**Côté appelants** — aucun des deux chemins de production ne transmet les
nouveaux arguments :

| Appelant | `oos_dd` | `oos_pf` | `oos_expectancy` |
|---|:--:|:--:|:--:|
| `app/engine/auto_optimizer.py:653-657` (auto-apply) | oui | **non** | **non** |
| `app/api/routes/optimizer.py:349-362` (bouton « Appliquer ») | **non** | **non** | **non** |

`_pf_ok` et `_exp_ok` sont donc constamment `False`. La règle effective se
réduit à `_sharpe_ok`, c'est-à-dire **« Sharpe strictement meilleur que le
baseline », rien d'autre**.

### Scénario d'échec

Baseline `{trades: 20, pnl: 50, sharpe: 1.20, wr: 50, dd: 10}`.
Candidat OOS : PnL 300 (×6), profit factor 3,5, expectancy 15,0, Sharpe 1,19.

```
beats_baseline(20, 300.0, 50.0, 1.19, BASE, oos_pf=3.5, oos_expectancy=15.0)
  -> (False, "aucune amélioration de qualité (WR 50.0% vs 50.0%, Sharpe 1.19 vs 1.20)")
```

Refusé même en fournissant explicitement les deux métriques, parce que le
baseline ne les porte pas. Avec un baseline enrichi, le même appel renvoie
`(True, "ok")` — ce qui isole la cause.

Un paramétrage qui sextuple le PnL et double le profit factor est rejeté pour
0,01 de Sharpe.

### Vérification

**CONFIRMÉ** — reproduction directe sur `beats_baseline`, plus extraction par
introspection des clés réellement produites par `_run_baseline` :

```
clés du baseline produit par _run_baseline : ['trades','pnl','sharpe','wr','dd','alpha']
  'profit_factor' présent ? False
  'expectancy'    présent ? False
```

### Correctif proposé

Deux modifications indissociables :

1. `_run_baseline` (`auto_optimizer.py:335`) doit renvoyer aussi
   `profit_factor` et `expectancy` — `BacktestResult` les expose déjà ;
2. les deux appelants doivent transmettre `oos_pf` et `oos_expectancy`.

**Effort** : 1 h 30 (les deux appelants + le baseline + tests).

### Délégation IA

> `app/engine/opt_scoring.py::beats_baseline` accepte depuis peu `oos_pf` et
> `oos_expectancy`, mais ces branches ne s'activent jamais : le baseline produit
> par `app/engine/auto_optimizer.py::_run_baseline` ne contient pas les clés
> `profit_factor` / `expectancy`, et aucun des deux appelants de production ne
> passe les arguments.
> 1. Ajouter `profit_factor` et `expectancy` au dict renvoyé par
>    `_run_baseline`, depuis `BacktestResult.to_dict()`.
> 2. Transmettre `oos_pf` et `oos_expectancy` dans les deux appels :
>    `auto_optimizer.py` (fonction `_beats_baseline`) et
>    `app/api/routes/optimizer.py` (route apply).
> Critère d'acceptation : un test où le candidat améliore nettement le profit
> factor et l'expectancy mais pas le Sharpe doit être ACCEPTÉ. Ce test échoue
> sur le code actuel.

---

## OPT-02 — Le garde-fou drawdown ne protège pas le bouton « Appliquer » (P1, CONFIRMÉ)

**Fichier** : `app/api/routes/optimizer.py:349-362`.

Le delta ajoute un refus si le drawdown OOS dégrade le baseline de plus de
25 % (`opt_scoring.py:233-239`). Ce garde-fou dépend de l'argument `oos_dd`.

`auto_optimizer.py:657` le transmet. **La route d'application manuelle ne le
transmet pas.**

Or le code de cette même route note que le chemin manuel est le plus fréquent :
`auto_apply` est désactivé par défaut (`app/api/routes/optimizer.py:345-348`).
Le garde-fou ajouté ne couvre donc que le chemin le moins emprunté.

### Scénario d'échec

Baseline avec un drawdown de 10 %. Candidat OOS avec un drawdown de **80 %**,
PnL 300, WR 60 %, Sharpe 2,0 :

```
# route manuelle, telle qu'appelée aujourd'hui (oos_dd absent)
beats_baseline(20, 300.0, 60.0, 2.0, BASE)            -> (True,  "ok")

# auto-apply, qui passe oos_dd
beats_baseline(20, 300.0, 60.0, 2.0, BASE, oos_dd=80.0)
  -> (False, "drawdown OOS (80.0%) dégrade le baseline (10.0%) au-delà de +25 %")
```

Le clic « Appliquer » accepte un paramétrage qui multiplie le drawdown par 8.

### Vérification

**CONFIRMÉ** — reproduction directe des deux appels côte à côte.

### Correctif proposé

Passer `oos_dd` dans la route, en le prenant du holdout comme les autres
métriques :

```python
oos_dd=_h.get("dd", result.get("best_oos_dd")),
```

**Effort** : 20 min. À livrer avec OPT-01, qui touche le même appel.

### Délégation IA

> Dans `app/api/routes/optimizer.py`, l'appel à `beats_baseline` de la route
> d'application manuelle ne transmet pas `oos_dd`, alors que
> `auto_optimizer.py` le fait. Le garde-fou de drawdown ajouté récemment est
> donc inerte sur le chemin manuel — qui est le chemin par défaut, `auto_apply`
> étant désactivé. Transmettre `oos_dd` depuis le holdout
> (`_h.get("dd", result.get("best_oos_dd"))`), au même titre que `trades`,
> `pnl`, `wr` et `sharpe`.
> Test : la route doit renvoyer 409 pour un job dont le drawdown holdout
> dépasse de plus de 25 % celui du baseline, et l'accepter avec `force=true`.

---

## OPT-03 — Le score composite change de forme sur le drawdown (P2, CONFIRMÉ — changement de paramétrage)

**Fichier** : `app/engine/opt_scoring.py:86`.

```python
- dd_factor = max(0, 1.0 - dd / 30)      # linéaire, nul au-delà de 30 %
+ dd_factor = 1.0 / (1.0 + dd / 15.0)    # hyperbolique, jamais nul
```

Comportement comparé :

| Drawdown | Avant | Après |
|---:|---:|---:|
| 0 % | 1,00 | 1,00 |
| 10 % | 0,67 | 0,60 |
| 15 % | 0,50 | 0,50 |
| 30 % | **0,00** | 0,33 |
| 60 % | 0,00 | 0,20 |

Deux effets opposés. En dessous de 15 % le nouveau facteur est **plus sévère**.
Au-dessus de 30 %, l'ancien annulait complètement le score — un paramétrage à
40 % de drawdown était éliminé d'office ; il conserve désormais un tiers de son
score et peut l'emporter s'il compense en PnL.

**Ce n'est pas un défaut de correctness** — c'est une décision de paramétrage
de trading, qui change le classement de tous les essais d'optimisation. Elle
relève de l'utilisateur, pas d'un correctif automatique. Signalée pour qu'elle
soit décidée explicitement plutôt que subie.

**Vérification** — lecture du code et évaluation numérique de la fonction.

---

## OPT-04 — La première condition contient un terme redondant (P3, CONFIRMÉ)

**Fichier** : `app/engine/opt_scoring.py:226`.

```python
if not (_sharpe_ok or _exp_ok or _pf_ok or oos_wr > b_wr):
```

Le terme `oos_wr > b_wr` ne change jamais la décision finale : quand il est le
seul vrai, la condition suivante (`:230`) rejette de toute façon. Il ne modifie
que le message d'erreur retourné.

Sans conséquence fonctionnelle. À simplifier pour que la règle réelle
(« Sharpe, expectancy ou PF ») se lise directement.

**Effort** : 5 min.

---

## Ce qui a été vérifié sans rien trouver

- **Deflated Sharpe** — le câblage `n_trials` / `min_deflated_sharpe`
  (`opt_scoring.py:243+`) est correct sur les deux chemins, et couvert par
  `tests/test_deflated_sharpe_gate.py` (11 tests verts).
- **`_wf_consistent`** — le gate walk-forward utilise bien `df_recherche`
  (hors holdout) et les `best_params` figés, sans re-optimisation par fold.
  L'assertion statique de `tests/test_apply_guard.py:79` le verrouille.
  En revanche le régime de risque de ce gate a changé : voir `BT-01` dans
  `05-BACKTEST.md`.
- **`deflated_sharpe_ratio`** — la signature passe de
  `trial_sharpes_std: float = None` à `float | None`. Correction de typage
  pure, sans effet à l'exécution.
- **Nouveau champ de diagnostic** — `n_folds_requested`, `n_folds_failed` et
  `erreurs` remontent désormais dans le résultat walk-forward, et
  `auto_optimizer.py` refuse le gate si `n_folds_failed > 0`. C'est un
  durcissement réel et correctement câblé.
