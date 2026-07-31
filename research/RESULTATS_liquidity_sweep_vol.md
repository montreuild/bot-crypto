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

## Optimisation IS/OOS (bayésien Optuna, 40 trials/TF) — 30m/1h/2h/4h/1d

Split 65 % IS / 35 % OOS (warmup 220 partagé), config OKX **réaliste** (frais
taker 0,1 %, sizing par risque). Reproduire :
`python research/optimize_pine.py liquidity_sweep_vol 30m,1h,2h,4h,1d`. Params
écrits dans `strategies/liquidity_sweep_vol.yaml` (`optimizer_results`).

| TF | Score IS | Score OOS | Overfit (IS/OOS) | OOS trades | OOS win % | OOS PnL ($/1000) | OOS DD |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 30m | −0.168 | −0.040 | 0.00 | 59 | 39.0 % | −19.85 (−2.0 %) | −6.10 % |
| 1h | 0.117 | −0.001 | **10.00** | 27 | 48.1 % | −1.55 (−0.2 %) | −5.21 % |
| 2h | 0.184 | 0.628 | 0.29 | 23 | 47.8 % | +40.05 (+4.0 %) | −2.66 % |
| **4h** | 0.265 | 0.292 | **0.90** | 23 | 47.8 % | +31.28 (+3.1 %) | −4.53 % |
| 1d | 0.202 | 0.241 | 0.84 | 14 | 35.7 % | +29.03 (+2.9 %) | −3.24 % |

Meilleurs paramètres :

| TF | ema_len | bb_mult | vol_mult | sl_mult | tp_mult |
|---:|---:|---:|---:|---:|---:|
| 30m | 200 | 2.0 | 1.5 | 1.2 | 2.5 |
| 1h | 150 | 2.0 | 2.0 | 2.0 | 3.0 |
| 2h | 200 | 2.2 | 2.0 | 1.5 | 3.0 |
| 4h | 250 | 2.2 | 2.0 | 1.5 | 2.5 |
| 1d | 250 | 2.2 | 1.2 | 1.2 | 3.5 |

**Lecture honnête :**

- **Le 30m n'a aucun edge** : scores IS **et** OOS négatifs (−0.17 / −0.04),
  −2 % sur 59 trades. La stratégie ne fonctionne pas à cette fréquence (bruit,
  filtre volume moins discriminant) — à écarter.
- **Le 1h surapprend** : score IS positif (0.117) mais OOS ≈ 0, **ratio d'overfit
  saturé à 10** (l'alerte du projet). Les bons params IS ne tiennent pas en OOS.
- **Le 4h est le plus robuste** : IS 0.265 et OOS 0.292 tous deux positifs et
  **quasi égaux (overfit 0.90 ≈ 1.0, l'idéal)**, +3,1 % sur 23 trades OOS,
  47,8 % WR. C'est le **seul TF où IS et OOS concordent** — et c'est justement le
  timeframe que tu annonçais pour ce script. Bon signe de cohérence.
- **Le 2h a le meilleur OOS** (+4,0 %) mais avec OOS (0.628) ≫ IS (0.184) : c'est
  le biais de **sélection sur le score OOS** (l'OOS n'est plus un vrai holdout).
  À valider en forward-test avant d'y croire.
- **Le 1d** est positif (+2,9 %) mais seulement **14 trades** et 35,7 % WR →
  échantillon trop mince.
- Convergence des params : `vol_mult` **relevé à 2.0** (filtre volume plus strict)
  sur 1h/2h/4h, et `ema_len` **allongé à 250** en 4h/1d — la stratégie gagne en
  sélectivité.

**Recommandation** : le **4h** est le candidat le plus crédible (IS≈OOS, échantillon
correct, aligné sur le TF d'origine). Le 2h est prometteur mais à confirmer
(sélection OOS). 30m et 1h sont à écarter (pas d'edge / surapprentissage). Faire
tourner le 4h en **paper/forward-test** (comparaison auto au cône Monte-Carlo)
avant tout capital réel.

## Conclusion

La **logique est fidèlement portée**, mais ce script est intrinsèquement moins
reproductible que `smart_trend_adx` à cause de sa dépendance au **volume** (non
comparable entre exchanges). Sur BTC/USDC OKX, la stratégie telle quelle est
**rentable en 1h/2h** (2h : +45.86 %, PF 1.473) et **perdante en 4h/1d**. Le
param_space (`ema_len`, `bb_mult`, `vol_mult`, `sl_mult`, `tp_mult`) est exposé pour
une optimisation IS/OOS via l'optimiseur du projet (cf. `research/optimize_smart_trend.py`
comme modèle).
