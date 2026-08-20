# 05 — Backtest et walk-forward

Delta : `app/engine/backtest.py` (+47/−15), `app/engine/backtest_result.py`
(+147/−72), `app/engine/walk_forward.py` (+32/−10), `app/engine/compute_jobs.py`
(+39/−9), `app/engine/compute_pool.py`, `app/engine/opt_freeze.py`.

Le walk-forward reçoit dans ce delta une **vraie correction de fuite** (BT-02)
et, dans le même commit, un **relâchement silencieux du régime de risque**
(BT-01).

---

## BT-01 — Le walk-forward ne tourne plus en mode `realistic_risk` (P1, CONFIRMÉ)

**Fichier** : `app/engine/walk_forward.py:69` et `:98-101`.
**Introduit par** : `fec34ed` (dans le delta).

### Le code

```python
# avant
bt_is  = Backtester(eng_is,  self.cfg, ml_mode=self.ml_mode, realistic_risk=True)
bt_oos = Backtester(eng_oos, self.cfg, ml_mode=self.ml_mode, realistic_risk=True)

# après
_rr = bool((self.cfg.get("backtest") or {}).get("realistic_risk", False))
bt_is  = Backtester(eng_is,  self.cfg, ml_mode=_ml, realistic_risk=_rr)
bt_oos = Backtester(eng_oos, self.cfg, ml_mode=_ml, realistic_risk=_rr)
```

`realistic_risk` était figé à `True`. Il est désormais lu dans la
configuration — **où la clé n'existe pas** :

```
grep -rn "realistic_risk" config/*.yaml config.yaml   →  aucun résultat
```

`_rr` vaut donc `False` en pratique. Le mode `realistic_risk` instancie le
`BacktestRiskGate` : circuit breakers, limite de drawdown journalier, budget de
slot, frein de volatilité (`app/engine/backtest_risk_gate.py:22-30`). Tout cela
est désactivé.

### L'asymétrie est le vrai problème

`_run_baseline` (`app/engine/auto_optimizer.py:329`) conserve
`Backtester(eng, cfg, realistic_risk=True)`.

Depuis le delta, le garde-fou d'auto-apply compare donc :

| Terme | Régime de risque |
|---|---|
| Baseline (`_run_baseline`) | **avec** circuit breakers |
| Gate walk-forward (`_wf_consistent`) | **sans** circuit breakers |

`_wf_consistent` (`auto_optimizer.py:679-696`) construit `cfg2` par copie de
`self.cfg` et n'injecte pas `backtest.realistic_risk`. La comparaison n'est
plus faite à régime égal.

### Scénario d'échec

Un paramétrage dont la rentabilité vient de séquences que les circuit breakers
auraient coupées — plusieurs pertes consécutives, ou un dépassement de drawdown
journalier — produit désormais des folds walk-forward positifs. Le gate
`wf_min_consistency` (60 % par défaut) est franchi, l'auto-apply promeut le
paramétrage, et le moteur live — lui — appliquera les circuit breakers. Le live
ne reproduira pas les résultats qui ont justifié la promotion.

### Vérification

**CONFIRMÉ** par lecture croisée : absence de la clé en configuration
(`grep`), valeur par défaut `False` dans la signature du `Backtester`
(`app/engine/backtest.py:144`), et maintien de `realistic_risk=True` côté
baseline (`auto_optimizer.py:329`). **Non reproduit par exécution** : construire
une stratégie dont le résultat bascule sur les circuit breakers demande un jeu
de données dédié, que je n'ai pas construit.

### Correctif proposé

Deux options, à trancher par l'utilisateur — c'est une décision de trading,
pas seulement un correctif :

- **A (rétablir)** — revenir à `realistic_risk=True` en dur dans le
  walk-forward, ce qui restaure la symétrie avec le baseline ;
- **B (rendre explicite)** — garder la lecture en configuration mais poser
  `backtest.realistic_risk: true` dans `config.yaml`, et injecter la même
  valeur dans `cfg2` de `_wf_consistent`.

Dans les deux cas, `_run_baseline` et le walk-forward doivent utiliser la
**même** valeur.

**Effort** : 30 min (option A) ou 1 h (option B, avec le test de symétrie).

### Délégation IA

> `app/engine/walk_forward.py` lisait `realistic_risk=True` en dur ; il lit
> désormais `cfg["backtest"]["realistic_risk"]`, absent de `config.yaml`, donc
> `False`. Or `app/engine/auto_optimizer.py::_run_baseline` conserve
> `realistic_risk=True` : le gate d'auto-apply compare un baseline avec circuit
> breakers à un walk-forward sans. Rétablir la symétrie : les deux doivent
> utiliser la même valeur, et `_wf_consistent` doit propager ce réglage dans le
> `cfg2` qu'il fabrique.
> Ajouter un test qui vérifie que le `realistic_risk` effectif du baseline et
> celui des folds walk-forward sont égaux. **Ne pas choisir la valeur par
> défaut sans l'utilisateur : c'est un paramètre de trading.**

---

## BT-02 — Le warmup des folds OOS est correctement préfixé (CONFIRMÉ — correction réelle)

**Fichier** : `app/engine/walk_forward.py:72-78`.

```python
oos_start = max(0, is_end - WARMUP)
df_oos    = df[oos_start:oos_end]
if (oos_end - is_end) < 30:
    …
```

Auparavant `df_oos = df[is_end:oos_end]` : le `Backtester` consommait son
warmup **à l'intérieur du fold OOS**, si bien que les premières barres OOS ne
produisaient aucun trade — le fold était amputé de son début.

**J'ai vérifié qu'il n'y a pas de fuite en sens inverse.** Le préfixe vaut
`WARMUP = WARMUP_BARS_DEFAULT = 210` (`app/core/is_oos.py:19`), et le
`Backtester` démarre sa boucle à
`warmup = max(_MIN_WARMUP, warmup_bars de la stratégie)` avec
`_MIN_WARMUP = WARMUP_BARS_DEFAULT` (`app/engine/backtest.py:372-378`,
`:476`). On a donc toujours `warmup ≥ 210`, donc
`oos_start + warmup ≥ is_end` : **le premier trade OOS tombe au plus tôt à
`is_end`**. Aucune barre d'apprentissage n'est tradée. La correction est saine.

Le test de longueur porte lui aussi désormais sur la partie utile
(`oos_end - is_end`) et non sur `len(df_oos)`, qui incluait le préfixe.

---

## BT-03 — Le mode ML des folds est explicité (P3, CONFIRMÉ)

**Fichier** : `app/engine/walk_forward.py:98`.

```python
_ml = self.ml_mode or "frozen"
```

`ml_mode=None` était transmis tel quel au `Backtester`, qui le dérivait
lui-même de sa configuration. Le walk-forward force maintenant `"frozen"`.

Le résultat est identique tant que la configuration ne surcharge pas ce
réglage — `"frozen"` est déjà le défaut du `Backtester`. Mais la surcharge
éventuelle n'est plus prise en compte. Sans conséquence observée ; noté pour
mémoire, le mode ML étant déterminant pour l'absence de fuite en walk-forward.

---

## BT-04 — Les diagnostics de folds échoués remontent enfin (CONFIRMÉ — amélioration)

**Fichier** : `app/engine/walk_forward.py:67-68`, `:112-118`, `:139-142`.

Le résultat porte désormais `n_folds_requested`, `n_folds_failed` et la liste
`erreurs`. Un fold qui lève était auparavant journalisé puis oublié : la
moyenne OOS se calculait sur les folds survivants, sans que rien n'indique
qu'il en manquait.

`auto_optimizer.py:698-701` exploite le nouveau champ et refuse le gate dès
qu'un fold a échoué. Câblage correct et vérifié.

---

## Ce qui a été vérifié sans rien trouver

- **`_fold_summary`** — continue de ne renvoyer ni `trades` ni `equity_curve`
  par fold : la contrainte de taille de réponse (B-14/A-05) tient.
- **`compute_jobs` / `compute_pool`** — le pool de processus est créé sans
  `Event` partagé (`54e8a8c`), ce qui supprime la dépendance à un objet non
  picklable sous Windows. Le `max_workers` est borné par `len(jobs)`
  (`compute_pool.py:102`).
- **Double passe de backtest** — `tests/test_backtest_dual_run.py` (nouveau,
  45 lignes) verrouille l'isolation entre passes, et `ccb53a8` clone le
  DataFrame entre les deux. Vérifié : le test échoue si le clone est retiré.
- **`Backtester.run`** — hub principal du dépôt (89 arêtes entrantes,
  betweenness 0,0107). C'est aussi le porteur de `FIN-01` et `FIN-02` : les
  correctifs de `04-MOTEUR-FINANCIER.md` sont à traiter en priorité, leur portée
  est globale.
