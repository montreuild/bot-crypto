# Backtest local — `liquidity_sweep_vol` (portage « BTC Liquidity Sweep V7 - Volume Footprint »)

Portage du PineScript fourni : sweep de liquidité (mèche perçant la Bollinger de la
barre courante + clôture DANS la bande = rejet) confirmé par un **volume > 1.5× la
moyenne**, en tendance EMA200 ; bracket fixe **SL 1.5×ATR / TP 3.0×ATR** (R:R = 2.0),
sans trailing. Backtest parquet **BTC/USDC (OKX)**, fenêtre **2024-01-01 → 2026-07-01**,
hypothèses TradingView répliquées (100 % équité, 0,05 %/côté, sans slippage, spot).

Reproduire : `python research/backtest_pine.py liquidity_sweep_vol`

## Référence TradingView (fournie par l'auteur — **4h**)

| Métrique | Valeur |
|---|---|
| PnL | **+33.49 %** |
| Trades | **75** |
| Gagnants | **18** |
| Profit factor | **1.542** |

## Résultats locaux (ATR Wilder = ta.atr, BB population = ta.bb)

| TF | Trades | Gagnants | Win % | Profit factor | Rendement | TP/SL |
|---:|---:|---:|---:|---:|---:|---:|
| 1h | 166 | 62 | 37.3 % | 1.118 | +15.18 % | 62/104 |
| 2h | 95 | 42 | 44.2 % | **1.473** | **+45.86 %** | 42/53 |
| **4h** | **37** | **12** | 32.4 % | 0.853 | −8.24 % | 12/25 |
| 1d | 8 | 1 | 12.5 % | 0.144 | −34.98 % | 1/7 |

## Lecture

- **En 4h (le TF de référence), la densité de trades ne colle pas** : 37 trades
  localement contre 75 chez toi. La cause principale est le **volume**, qui est
  **spécifique à l'exchange** : le filtre `volume > 1.5 × SMA(volume, 20)` ne se
  déclenche pas à la même fréquence sur BTC/USDC (OKX) que sur le symbole
  TradingView (probablement BTCUSDT Binance, beaucoup plus liquide et au profil de
  volume différent). C'est la variable la plus fragile de ce portage — bien plus
  que pour `smart_trend_adx` (qui n'avait pas de filtre volume).
- **Sur les données OKX, l'edge est en bas de TF** : le **2h** est le meilleur
  (PF 1.473, +45.86 %, 95 trades) et son PF est proche de ta référence 4h (1.542) —
  mais c'est une coïncidence de TF, pas une correspondance. Le **1h** est aussi
  rentable (PF 1.118). À l'inverse, **4h et surtout 1d perdent** (1d = 8 trades,
  1 seul gagnant → non significatif).
- **Chiffres de référence de nouveau incohérents entre eux** : avec un bracket fixe
  TP 3.0×ATR / SL 1.5×ATR (R:R = 2.0) et des sorties uniquement par SL/TP, un
  profit factor de **1.542** implique un taux de gain d'environ **43 %**
  (≈ 33 gagnants/75), pas 18/75. Un ratio 18/75 (24 %) donne au contraire un
  PF ≈ **0.63** (perdant). Les métriques `18/75` et `PF 1.542` ne sont donc pas
  conciliables pour cette logique — à revérifier côté TradingView (symbole exact,
  fenêtre, `pyramiding`, sorties additionnelles).

## Écarts de modélisation assumés

- **ATR** en RMA de Wilder (`ta.atr`), **Bollinger** en écart-type de population
  (`ta.bb`), bandes de la **barre courante** (comme le Pine).
- **Volume** : `SMA(volume, 20)` de la barre courante, seuil `× vol_mult` — identique
  au Pine, mais **dépendant du flux de volume de l'exchange** (voir ci-dessus).
- **Exécution** au `open[i+1]` ; SL/TP intrabar avec **priorité au stop**.

## Conclusion

La **logique est fidèlement portée**, mais ce script est intrinsèquement moins
reproductible que `smart_trend_adx` à cause de sa dépendance au **volume** (non
comparable entre exchanges). Sur BTC/USDC OKX, la stratégie telle quelle est
**rentable en 1h/2h** (2h : +45.86 %, PF 1.473) et **perdante en 4h/1d**. Le
param_space (`ema_len`, `bb_mult`, `vol_mult`, `sl_mult`, `tp_mult`) est exposé pour
une optimisation IS/OOS via l'optimiseur du projet (cf. `research/optimize_smart_trend.py`
comme modèle).
