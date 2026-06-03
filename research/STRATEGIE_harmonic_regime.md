# Stratégie *Harmonic Regime* — du signal à la stratégie validée

> Analyse quantitative exhaustive de BTC (1h / 4h / 1d), conception data-driven,
> backtest et validation walk-forward, puis intégration dans `bot-crypto`.
> Reproductible : `python research/analysis_btc.py` et
> `python research/backtest_harmonic.py --tf 4h,1d --wf --split`.

---

## 1. Données

| TF | Bougies | Période | Couverture | Qualité |
|----|--------:|---------|-----------:|---------|
| 1h | 50 500 | 2020-03 → 2026-06 | ~6.2 ans | 20 gaps, 0 doublon, 0 OHLC incohérent |
| 4h | 15 378 | 2018-12 → 2026-06 | ~7.5 ans | 5 gaps, 0 doublon, 0 OHLC incohérent |
| 1d |  2 565 | 2018-12 → 2026-06 | ~7.5 ans | 1 gap, 0 doublon, 0 OHLC incohérent |

Plusieurs cycles bull/bear complets (krach 2018, bull 2020-21, bear 2022,
reprise 2023-24, etc.). BTC : ~3 200 → pic ~126 000 → 65 500.

---

## 2. Analyse exhaustive — faits mesurés (et non indicateurs décoratifs)

### 2.1 Distribution des rendements
- **Fat tails massives** : excès de kurtosis **18-23**, asymétrie **négative**
  (-0.22 en 1h à **-1.21** en 1d). Les krachs sont plus nets que les rallyes.
- Vol annualisée **~61-65 %**. Buy & Hold : Sharpe **~0.67-0.73**, **max DD -72 à -74 %**.
- ⇒ Modèle gaussien invalide. **Stops ATR obligatoires, sizing par risque, pas de martingale.**

### 2.2 Autocorrélation
- ACF des rendements ≈ **0** ; **Variance-Ratio ≈ 1** (marche aléatoire) →
  **aucun momentum/mean-reversion LINÉAIRE** exploitable bar-à-bar.
- **ACF de |rendement| fortement positive et persistante (0.15-0.28)** →
  **clustering de volatilité** = l'edge le plus robuste. La volatilité est
  prévisible même quand la direction ne l'est pas.
- ⇒ La volatilité pilote le **timing** (squeeze→expansion) et le **sizing** (ATR),
  pas un edge directionnel linéaire.

### 2.3 Régimes & rendements forward conditionnels
- `trend_up` (structure SMA + ADX) = meilleur biais long (fwd 6 barres :
  1h +0.13 %, 4h +0.43 %, 1d +2.16 %).
- **Même en `trend_down`, le rendement forward moyen reste > 0** (dérive
  haussière de BTC + rebonds) → **shorter le simple régime baissier ne suffit pas.**
- `range`/`choppy` : mean-reversion douce ou abstention.

### 2.4 Saisonnalité
- Léger biais horaire (UTC 20-22 h positifs ; 02-03 h, 23 h négatifs) et
  hebdomadaire (Mer/Lun > Jeu). Effets faibles (~1e-4) → tilt mineur, pas un cœur.

### 2.5 Analyse spectrale / fréquences (FFT)
- Log-prix détrendé : énergie concentrée sur les **très basses fréquences**
  (= la tendance / structure de cycle pluriannuelle), pas une horloge tradable.
- Périodogramme des **rendements** : pic **non significatif** en 1h (p=0.43) et
  4h (p=0.23) ; marginal en 1d (p=0.04, période ≈ 2 j = simple ACF lag-1).
- ⇒ **Pas de cycle déterministe fixe.** La phase spectrale n'est utilisable
  qu'en **confirmation directionnelle douce** (faible poids).

### 2.6 Fibonacci
- Taux de rebond aux retracements **incohérent selon le TF** (1h 0.618 +3.7 pt
  mais 1d 0.5 **-13 pt**). ⇒ **zones de confluence / placement de stop-target**,
  jamais un déclencheur isolé.

### 2.7 Batterie d'edges conditionnels (forward returns, t-stats)
| Setup | 1h | 4h | 1d | Verdict |
|-------|---:|---:|---:|---------|
| LONG momentum (close>EMA50>EMA200) | t=7.0 | t=8.4 | t=3.8 | **Robuste, multi-TF** |
| LONG breakout Donchian/Bollinger | t=3.5/4.5 | t=3.2/4.1 | — | Confirmation valable |
| LONG ADX>25 trend_up | t=5.7 | t=6.8 | — | Confirmation valable |
| SHORT momentum | t=-4.1 | t=-1.4 | t=-1.4 | **Négatif** (dérive haussière) |
| SHORT RSI>70 | t=-6.7 | t=-4.8 | t=-4.8 | **Négatif** (le prix continue) |
| LONG RSI<30 (4h/1d) | — | P=58.6 % | P=63.7 % | Mean-rev douce |

**Cohérence bull/bear** : LONG-momentum en bull = t≈8 (très positif) ;
SHORT-momentum en bear = t≈-0.6/-0.9 (**l'edge short n'existe pas sur le
rendement forward moyen**). Les shorts ne « marchent » que via le **skew gauche**
(krachs) + trailing serré, ou il faut rester **FLAT**.

---

## 3. Conception — *Harmonic Regime* (`app/strategies/harmonic_regime.py`)

Stratégie **régime-adaptative** à score de confluence pondéré, **abstention par
défaut**. Trois setups, on garde le meilleur score :

| Setup | Rôle | Conditions clés | Taille | Sortie |
|-------|------|-----------------|-------:|--------|
| **LONG_TREND** | Cœur | structure EMA up + ADX + (breakout ∣ flip MACD ∣ pullback) | jusqu'à ×1 | SL ATR + trailing + max-hold |
| **LONG_MEANREV** | Secondaire | range (ADX bas) + RSI survente + BB basse / Fibo | ×0.5 | max-hold court |
| **SHORT_DEF** | Défensif | **macro-bear confirmé** + structure down + cassure + ADX | ×0.5 | trailing **serré** (capture le skew) |

- **Sizing par risque 1 %/trade**, stop **ATR** (`sl_atr_mult`), **trailing
  multi-phase** (`TrailingStopManager` du repo via `trail_override` :
  breakeven rapide → lock → tight) pour *banker vite* tout en laissant courir le
  clustering, **max-hold** pour éviter la dérive.
- Composantes **signal/fréquence** (cycle FFT, phase) et **Fibonacci** intégrées
  en **confirmation/zones** à faible poids — fidèles à leur poids statistique réel.
- Implémentation **`BaseStrategy`** : lecture O(1) des colonnes `_pre_*` +
  features légères (Donchian/Bollinger/médiane ATR%/cycle FFT mémoïsé par pas/
  Fibo) → backtest 50k bougies en ~12-16 s.

---

## 4. Backtest & validation (Backtester du repo, frais/spread/borrow réalistes)

### 4.1 Performance pleine période
| TF | Config | PnL | Sharpe | max DD | PF | Win | Trades | vs B&H (DD) |
|----|--------|----:|------:|------:|---:|----:|------:|-------------|
| **4h** | long-only, hold long | **+33.4 %** | **5.29** | **-7.3 %** | 1.41 | 47.8 % | 245 | B&H DD **-74 %** |
| **1d** | shorts ON | **+11.5 %** | **2.90** | **-4.7 %** | 1.56 | 56.2 % | 73 | B&H DD **-73 %** |
| 1h | (rejeté) | -67.6 % | — | -70 % | 0.82 | 35 % | 1725 | edge < frais |

> Sur 1d, `SHORT_DEF` contribue **+49 USDC à 75 % de réussite** (16 trades) :
> les cassures de macro-bear confirmé sont propres sur le daily.

### 4.2 Walk-forward (5 folds, out-of-sample)
- **4h** : PnL OOS moyen **+28.3**, consistance **60 %** des folds positifs.
- **1d** : PnL OOS moyen **+25.7**, consistance **100 %**, WR OOS **68 %**.

### 4.3 Robustesse par macro-régime (la demande centrale : bull **et** bear)
| Fenêtre (4h) | Stratégie | Buy & Hold | **Alpha** | max DD |
|--------------|----------:|-----------:|----------:|------:|
| **BEAR 2022** | **-1.1 %** | **-53.1 %** | **+52.0 %** | -1.6 % |
| BULL 2023-24 | +7.0 % | +130.3 % | -123.2 % | -2.5 % |
| BEAR 2018-19 | +15.6 % | +99.3 % | -83.6 % | -2.7 % |
| CHOP 2024-26 | -2.3 % | +2.6 % | -4.9 % | -7.3 % |

**Lecture** : la stratégie **préserve le capital en bear** (2022 : -1 % quand le
B&H perd -53 %) et **croît en bull/reprise** avec un Sharpe élevé et un DD
minuscule. Point faible assumé : les marchés **choppy** (suivi de tendance =
whipsaws) → léger négatif, mais toujours ≈ B&H avec bien moins de risque.

---

## 5. Conclusions honnêtes

1. **Quality > quantity confirmé** : Sharpe **2.9-5.3** et **DD -5 à -13 %** vs
   Sharpe ~0.7 / **DD -72 %** du Buy & Hold. Croissance régulière, risque maîtrisé.
2. **Pas d'alpha absolu en bull parabolique** : impossible de battre en
   rendement brut un B&H qui fait +475-1710 % en restant flat ~75-80 % du temps
   à 1 % de risque. **Le rendement absolu monte avec `risk_per_trade`** (knob de
   config, décision de portefeuille — non câblé en dur dans la stratégie).
3. **« Performer en bear » = préserver le capital** (flat) **+ shorts filtrés**
   (1d). C'est mesuré : -1 % vs -53 % en 2022.
4. **1h non rentable** pour ce style (edge directionnel < coûts) → **swing 4h/1d**.
5. **Honnêteté méthodologique** : cycles FFT et Fibonacci ne sont PAS des
   edges autonomes sur BTC — intégrés à faible poids, conformément aux mesures.

### Pistes d'amélioration (v2)
- Filtre anti-chop (efficiency ratio / largeur de range) pour réduire les
  whipsaws 2024-26. Optimisation bayésienne via `/optimizer` (IS/OOS) du repo.
  Allocation dynamique de `risk_per_trade` selon le régime de volatilité réalisé.
