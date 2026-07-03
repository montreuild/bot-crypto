# Backtest local — `smart_trend_adx` (portage « BTC Smart Trend V6 - ADX Filter »)

Portage du PineScript fourni (EMA200 trend-filter + pullback Bollinger de la barre
précédente + ADX + RSI + bougie de confirmation ; bracket fixe SL 1.2×ATR /
TP 2.5×ATR, sans trailing). Backtest sur les parquet **BTC/USDC (OKX)**, fenêtre
**2024-01-01 → 2026-07-01**, en répliquant les hypothèses TradingView :
100 % de l'équité par trade, commission **0,05 %/côté**, pas de slippage, spot.

Reproduire : `python research/backtest_smart_trend.py`

## Référence TradingView (fournie par l'auteur)

| Métrique | Valeur |
|---|---|
| PnL | **+19 %** |
| Trades | **63** |
| Gagnants | **16** |
| Profit factor | **1.451** |

## Résultats locaux (ATR/ADX en RMA Wilder, comme `ta.atr`/`ta.dmi`)

| TF | Trades | Gagnants | Win % | Profit factor | Rendement | TP/SL |
|---:|---:|---:|---:|---:|---:|---:|
| 1h | 107 | 35 | 32.7 % | 1.092 | +6.03 % | 35/72 |
| **2h** | **56** | **17** | **30.4 %** | **0.896** | **−3.85 %** | 17/39 |
| 4h | 35 | 13 | 37.1 % | 1.092 | +4.24 % | 13/22 |
| 1d | 8 | 4 | 50.0 % | 1.969 | +20.28 % | 4/4 |

## Lecture

- **Le timeframe du backtest TradingView est très probablement le 2h** : 56 trades
  et 17 gagnants localement, contre 63 trades / 16 gagnants en référence — la
  **logique d'entrée/sortie est donc fidèlement portée** (même densité de signaux).
- **Le profit factor et le rendement diffèrent** (2h local : PF 0.896 / −3,85 %
  vs +19 % / 1.451). Deux causes principales :
  1. **Données différentes.** La référence TradingView porte sur un autre
     instrument (probablement `BTCUSDT` d'un autre exchange / un index BTC), alors
     que le backtest local utilise **BTC/USDC OKX**. Avec un bracket fixe, quelques
     bougies qui touchent le TP au lieu du SL (ou l'inverse) suffisent à faire
     basculer le PF sur ~56 trades — la série de prix sous-jacente change tout.
  2. **Incohérence interne des chiffres de référence.** Avec un bracket FIXE
     TP 2.5×ATR / SL 1.2×ATR (R:R ≈ 2.08) et des sorties uniquement par SL/TP,
     un profit factor de **1.451** implique un taux de gain d'environ **41 %**
     (≈ 26 gagnants/63), pas 16/63. Un ratio 16/63 (≈ 25 %) donne au contraire
     un PF ≈ **0.71** (perdant). Les métriques `16/63` et `PF 1.451` fournies ne
     sont donc pas conciliables entre elles pour cette logique — à revérifier côté
     TradingView (fenêtre exacte, `pyramiding`, sorties additionnelles, symbole).

## Écarts de modélisation assumés (documentés dans la stratégie)

- **ATR & ADX** recalculés en **RMA de Wilder** (`ewm alpha=1/n`) pour coller à
  `ta.atr` / `ta.dmi`, au lieu de l'EMA-span du reste du projet (améliore la
  fidélité : sur 2h, 73→56 trades, plus proche des 63 de référence).
- **Bollinger** : écart-type de **population** (ddof=0), comme `ta.stdev`/`ta.bb`.
- **RSI** : déjà en RMA côté projet = `ta.rsi`.
- **SL/TP** ancrés sur le `close` du signal (comme Pine), vérifiés **intrabar**
  avec **priorité au stop** en cas d'ambiguïté high/low (hypothèse conservatrice,
  = défaut TradingView).
- **Exécution** à l'**ouverture de la barre suivante** (`open[i+1]`) — comme
  `strategy.entry` par défaut en Pine.

## Optimisation IS/OOS (bayésien Optuna, 40 trials/TF)

Split chronologique 65 % in-sample / 35 % out-of-sample (warmup 220 partagé),
config OKX **réaliste** (frais taker 0,1 %, sizing par risque plafonné) — c.-à-d.
les hypothèses *live*, pas celles de TradingView. Reproduire :
`python research/optimize_smart_trend.py`. Params écrits dans
`strategies/smart_trend_adx.yaml` (section `optimizer_results`).

| TF | Score IS | Score OOS | Overfit (IS/OOS) | OOS trades | OOS win % | OOS PnL ($/1000) | OOS DD |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1h | 0.343 | 0.367 | 0.94 | 29 | 41.4 % | +11.28 (+1.1 %) | −3.60 % |
| 2h | −0.047 | 0.719 | 0.00 | 20 | 60.0 % | +83.52 (+8.4 %) | −1.16 % |
| 4h | 0.483 | 0.452 | 1.07 | 9 | 55.6 % | +29.38 (+2.9 %) | −2.10 % |
| 1d | 0.488 | 0.578 | 0.84 | 4 | 75.0 % | +55.06 (+5.5 %) | −1.06 % |

Meilleurs paramètres :

| TF | ema_len | adx_thresh | rsi_long | rsi_short | sl_mult | tp_mult |
|---:|---:|---:|---:|---:|---:|---:|
| 1h | 150 | 25 | 35 | 55 | 1.2 | 2.5 |
| 2h | 200 | 20 | 35 | 60 | 1.2 | 2.5 |
| 4h | 150 | 25 | 35 | 60 | 1.2 | 2.0 |
| 1d | 150 | 25 | 40 | 55 | 1.0 | 2.5 |

**Lecture honnête (à lire avant d'y croire) :**

- **Pas de surapprentissage au sens IS/OOS** : tous les ratios < 1.1 (seuil d'alerte
  du projet : 2.5). Bonne nouvelle sur la stabilité IS→OOS.
- **MAIS l'optimiseur sélectionne sur le score OOS** : l'OOS n'est donc plus un vrai
  holdout ici (biais de sélection). Les métriques OOS ci-dessus sont **optimistes**.
  Une validation propre exige un **3ᵉ segment jamais vu** (ou le forward-test
  glissant du projet, `oos_tracker`, en live/paper).
- **Puissance statistique faible en haut de TF** : 4h = 9 trades OOS, 1d = 4 trades.
  Le win-rate 75 % (1d) sur 4 trades n'est pas exploitable. Le seul TF avec un
  échantillon décent est le **1h (29 trades)** → +1,1 %, 41 % WR, DD −3,6 %.
- **Le 2h est suspect** : meilleur OOS (+8,4 %) mais **score IS négatif** — c.-à-d.
  la config était perdante sur l'in-sample et brille seulement sur la période
  récente. Signal typique de params calés sur un régime favorable, pas d'un edge
  robuste. À ne pas déployer sans forward-test.
- Convergence des params : `rsi_long` resserré à **35** (dip plus profond exigé) et
  `adx_thresh` relevé à **25** (tendance plus forte) sur la plupart des TF — cohérent
  avec une meilleure sélectivité des entrées.

**Recommandation** : traiter ces params comme des *candidats*, les faire tourner en
**paper/forward-test** (le bot compare automatiquement le réel à la fourchette
Monte-Carlo simulée) avant tout capital réel. Le 1h est le point de départ le plus
crédible statistiquement ; le 2h/4h/1d demandent plus de trades pour conclure.

## Conclusion

La stratégie reproduit fidèlement la **logique et les seuils** du PineScript (densité
de trades quasi identique en 2h). Le rendement absolu n'est pas reproductible à
l'identique faute du même flux de prix (OKX BTC/USDC vs symbole TradingView), et les
chiffres de référence `16/63` + `PF 1.451` sont mutuellement incohérents pour ce
bracket. Sur BTC/USDC OKX, la logique telle quelle n'est pas rentable en 2h ; elle
l'est marginalement en 1d (+20 %, PF ~1.97, mais seulement 8 trades). Une
optimisation IS/OOS (`ema_len`, `adx_thresh`, `rsi_long/short`, `sl_mult`, `tp_mult`)
est disponible via l'optimiseur du projet (param_space déjà exposé).
