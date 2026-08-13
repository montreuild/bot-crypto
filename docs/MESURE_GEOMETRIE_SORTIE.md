# L0 — où meurent les positions, et où va l'argent

`docs/STRATEGY_SMC_ML_EDGE.md` §4 posait le diagnostic sans le chiffrer : « les
positions sortent avant la cible — trailing stop, `max_hold_bars`, ou stop trop
serré ». Ce document mesure. Le verdict n'est pas celui qui était supposé.

Reproduction :

```bash
python scripts/measure_exit_geometry.py --data data/ohlcv
```

`smart_money` avec les paramètres de son YAML (jamais les défauts de code),
12 000 dernières barres, frais 0,1 %/côté, spread 0,05 %.

---

## 1. Les chiffres

| cas | trades | sortie dominante | part | win% | MFE_R méd. | PnL_R méd. |
|---|---:|---|---:|---:|---:|---:|
| BTC 1 h | 14 | `stop_loss` | 71,4 % | 0,0 % | **0,44** | −1,08 |
| BTC 4 h | 64 | `trailing_stop` | 79,7 % | 41,2 % | **0,95** | **−1,02** |
| ETH 1 h | 110 | `stop_loss` | 69,1 % | 0,0 % | **0,34** | −1,05 |
| ETH 4 h | 127 | `stop_loss` | 66,1 % | 0,0 % | **0,49** | −1,02 |

Cible atteinte (le MFE a-t-il touché le TP demandé ?) :

| cas | cible touchée | MFE médian | TP demandé médian |
|---|---:|---:|---:|
| BTC 1 h | 28,6 % | 0,56 R | 2,91 R |
| ETH 1 h | 31,8 % | 0,89 R | 1,54 R |
| ETH 4 h | 36,2 % | 0,89 R | 1,79 R |

---

## 2. Ce que ça dit

### Sur 1 h et sur ETH 4 h : ce n'est PAS une sortie prématurée

Les trades perdants meurent sur leur stop initial après avoir montré un MFE
médian de **0,34 à 0,49 R**. Un trade qui n'a jamais dépassé un demi-R en sa
faveur n'a pas été coupé trop tôt : il n'est jamais allé nulle part. Le levier
n'est donc ni le trailing ni le time-stop sur ces cas — c'est **l'entrée, ou la
distance de stop rapportée au bruit**.

Corollaire chiffré : la cible n'est atteinte que **28 à 36 %** du temps, et le
MFE médian (0,56–0,89 R) vaut le tiers à la moitié du TP demandé (1,54–2,91 R).
Les cibles sont posées à des niveaux que le prix atteint une fois sur trois.
Avec un stop à 1 R en face, l'espérance est structurellement mince — ce qui est
cohérent avec l'échec OOS constaté.

### Sur BTC 4 h : là, oui, le trailing rend le gain

C'est le seul cas où `use_trailing` est actif, et il est éloquent :

- `trailing_stop` : 79,7 % des sorties, PnL médian **−1,02 R** alors que le MFE
  médian était de **0,95 R**. Le trailing rend donc à peu près l'intégralité du
  mouvement acquis ;
- `time_stop_stall` : 20,3 % des sorties, **69,2 % de gagnants**, PnL médian
  **+0,39 R**. Le seul bucket rentable du cas est celui qui coupe les trades qui
  stagnent.

Autrement dit, sur BTC 4 h, **couper vaut mieux que suivre**. C'est exactement
l'hypothèse de §4 de `STRATEGY_SMC_ML_EDGE`, mais elle ne vaut que sur ce cas —
la généraliser aux quatre aurait été faux.

### Le diagnostic dépend du timeframe, et c'est le résultat

Il n'y a pas un défaut de sortie unique. Il y en a deux, disjoints :

1. **1 h et ETH 4 h** — problème d'**entrée / de cible** : les trades ne
   décollent pas, les TP sont hors de portée.
2. **BTC 4 h** — problème de **sortie** : le trailing restitue le gain.

Un correctif unique appliqué aux quatre cas en dégraderait au moins deux.

---

## 3. Deux défauts de comptabilité trouvés en instrumentant

**Les frais d'entrée étaient écrasés.** `execution.close_pnl` ne rend que les
frais de *sortie* ; `_close_at` les écrivait tels quels dans `position["fees"]`,
par-dessus les frais d'entrée déjà portés par la position. `total_fees`
sous-déclarait donc un côté complet. Le PnL et la courbe d'équité, eux, étaient
justes — les frais d'entrée sont prélevés directement sur `ctx.capital` à
l'ouverture. **Correction de report seule : aucune décision, aucun backtest ne
change de résultat.**

**`total_pnl` n'est pas la variation d'équité.** Il somme les `pnl` de clôture,
qui ne retranchent pas les frais d'entrée. L'écart vaut exactement la somme de
ces frais. Plutôt que de le corriger en silence — ce qui déplacerait
`composite_score` et donc la sélection de l'optimiseur — les deux agrégats sont
désormais publiés côte à côte :

| clé | sens |
|---|---|
| `total_pnl` | somme des PnL de clôture (grandeur historique, inchangée) |
| `net_profit` | `final_equity − initial_capital` — la vérité |
| `total_entry_fees` | l'écart entre les deux, par construction |

`tests/test_backtest_journal.py` verrouille cette identité. **Décision à prendre
dans un lot ultérieur**, pas ici : basculer `composite_score` sur `net_profit`
change la sélection de tous les paramétrages déjà mesurés.

**Le slippage était reporté à 0.** Il n'y avait pas de champ dédié : le coût de
spread est embarqué dans le prix d'exécution. Il est désormais isolé
(`slippage_cost` par trade, `total_slippage_cost` agrégé) sans changer le PnL.

---

## 4. Ce qui est livré

- `by_exit_reason` dans `BacktestResult`, mêmes métriques que `by_setup` /
  `by_module` (calcul partagé `_group_metrics`).
- Ventilation des coûts par trade : `entry_fees`, `slippage_cost`,
  `funding_cost`, `gross_pnl`.
- Agrégats : `total_slippage_cost` (réel), `total_funding_cost`,
  `total_entry_fees`, `gross_profit`, `net_profit`.
- Champs de journal §99 sur le trade — `session`, `htf_bias`, `structure_state`,
  `sequence_type`, `sequence_id`, `market_event_id`, `tier`, `liquidity_swept`,
  `pd_zone`, `gross_rr`, `net_rr`, `score_breakdown`, `planned_stop`,
  `planned_tp`. Repris du signal : une stratégie qui ne les pose pas laisse des
  `None`, aucune n'est obligée de changer. Les lots L3–L6 les remplissent.
- `scripts/measure_exit_geometry.py`.

Aucun changement de comportement : 94 tests existants passent inchangés
(`test_backtest`, `test_backtest_by_module`, `test_backtest_live_parity`,
`test_smc`), 8 nouveaux dans `test_backtest_journal.py`.

---

## 5. Conséquence pour la suite du plan

L1 (sorties partielles) reste justifié — il est le seul moyen de tester
`TP1 + runner` contre le tout-ou-rien, et le cas BTC 4 h montre qu'il y a
quelque chose à y gagner. Mais **ce lot ne réparera pas les cas 1 h**, dont le
problème est en amont.

C'est un argument de plus pour L3 (mémoire de structure) et L4 (hiérarchie de
liquidité et qualité des cibles) : « la cible n'est touchée qu'une fois sur
trois » est précisément la question que `target_quality` et
`expected_target_value` posent.
