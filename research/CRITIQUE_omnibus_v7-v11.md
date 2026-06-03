# Critique des stratégies Opus Omnibus V7 / V8 / V10 / V11

> Analyse et remise en question des hypothèses et de l'orientation de la lignée
> `opus_omnibus`, pour fonder une nouvelle stratégie (`volatility_squeeze`).
> Sources : code + docstrings des stratégies, et mesures de `research/analysis_btc.py`.

---

## 1. Ce que fait la lignée (V7 → V11)

Architecture commune : **deux modèles LightGBM** par timeframe —
- `p_event` = **amplitude** (y aura-t-il un mouvement significatif),
- `p_up` = **direction** (hausse vs baisse) —
puis **routing par régime** (Range / Trend Up / Trend Down / Choppy) vers
**6-8 setups** (SHORT_TD_HIGH, LONG_CHOPPY, SIGNAL_UP, LONG_RANGE_STRICT…),
chacun gaté par des seuils `amp_min` / `dir_min` / `dir_max` et doté de ses
propres TP/SL/max_bars/size. TF visés : **15m / 30m / 1h**.

| Version | Apport revendiqué |
|---------|-------------------|
| V7 | 6 setups, routing par priorité, SHORT_TD_HIGH (size×1.5) |
| V8 | + SIGNAL_UP (rebond après excès baissier, size×1.5) |
| V10 | corrections « data-driven » après backtest V9 (retune de setups) |
| V11 | labels multi-horizon, régime enrichi, **calibration isotone**, pruning |

---

## 2. Remise en question des hypothèses

### 2.1 ❌ Hypothèse fondatrice fausse : « la direction est prédictible par ML »
La **V10 l'admet noir sur blanc** (docstring) :
> « AUC_dir du modèle V4 ≈ **0.53** sur 1h (**quasi-aléatoire**), donc le seuil
> `p_dir > 0.55` ne filtre quasi rien. »

Un AUC de 0.53 = un classifieur **à peine meilleur que pile ou face**. Or **toute
la machinerie de routing repose sur `p_up`** : LONG_CHOPPY (`p_dir>0.58`),
SHORT_CHOPPY (`p_dir<0.42`), LONG_RANGE_STRICT (`p_dir>0.60`)… Ces setups
**filtrent sur du bruit**. Mes mesures le confirment (`analysis_btc.py` §3, §8) :
- ACF des rendements ≈ 0, **Variance-Ratio ≈ 1** → pas d'edge directionnel linéaire ;
- dans la batterie d'edges, **seuls les setups alignés sur une tendance établie**
  ont un t-stat significatif (LONG momentum t≈7-8) ; RSI/mean-reversion de
  direction = t≈0. **La direction n'est exploitable que = tendance, pas prédite.**

La V11 « corrige » par calibration isotone… mais **calibrer un classifieur à
AUC 0.53 donne des probabilités bien calibrées autour de 0.5** : toujours
inexploitables pour trancher la direction. On soigne le symptôme, pas la cause.

### 2.2 ✅ Ce qui est VRAI et sous-exploité : l'amplitude est prédictible
La même V10 note que **`p_amp` a un AUC ~0.68-0.75 (fiable)**. C'est cohérent
avec le **clustering de volatilité** que je mesure (ACF|r| ≈ 0.15-0.28, §3).
**La volatilité est l'edge robuste** — mais la lignée s'en sert seulement comme
filtre de gating, pas comme cœur directionnel de la stratégie.

### 2.3 ❌ Tapis roulant de sur-apprentissage
- **17 à 23 paramètres optimisables** (v7=21, v8=17, v10=19, **v11=23**) **+**
  ~6 seuils par setup → surface d'overfitting énorme.
- Les évolutions sont **tunées sur des post-mortems in-sample minuscules** : V10
  retire LONG_TU car « **-3.15 USDC sur 12 trades** », vante SHORT_TD_HIGH à
  « WR 95.8 % » (~24 trades). **Décider sur 12-24 trades, c'est ajuster le bruit.**
- **`oos_score: null` dans tous les YAML** (v7/v8/v10/v11) : **aucune validation
  out-of-sample enregistrée**. Les gains revendiqués sont in-sample, mono-symbole,
  mono-TF (BTC 1h, 5000 bougies).

### 2.4 ❌ Mauvais timeframes (sous le mur des frais)
La lignée trade **15m/30m/1h**. Or `analysis_aggressive.py` / `analysis_scalp.py`
mesurent que l'edge breakout/momentum **net de frais** y est **négatif** (round-trip
≈ 0.30 % > edge ; gross directionnel ≈ 0 à bas TF). Quelle que soit la finesse du
ML, **le terrain de jeu est perdant après frais**.

### 2.5 ❌ Mean-reversion en Choppy = parier la direction dans le bruit
LONG_CHOPPY / SHORT_CHOPPY tentent de **prédire le sens d'un rebond en régime
choppy** — précisément le régime où la direction est la moins prédictible. C'est
combiner les deux faiblesses (direction + chop).

### 2.6 ❌ Complexité opérationnelle / fragilité
Entraînement **LightGBM inline** (réentraînement périodique) → résultats
**path-dépendants et non déterministes**, fichiers modèles à gérer, calibration,
pruning, logs jsonl… Forte surface de bugs, validation difficile, reproductibilité
faible (lightgbm même pas dans l'environnement de test par défaut).

---

## 3. Synthèse : que garder, que jeter

| Hypothèse omnibus | Verdict | Décision |
|-------------------|---------|----------|
| Direction prédictible par ML (`p_up`) | ❌ AUC≈0.53 (aléatoire) | **Jeter** — ne PAS prédire la direction |
| Amplitude/volatilité prédictible (`p_amp`) | ✅ AUC≈0.7 + ACF\|r\| | **Garder** — cœur de la stratégie |
| Mean-reversion en Choppy | ❌ direction du bruit | **Jeter** — abstention en chop |
| Short en Trend Down confirmé | ~ marginal | Garder, conservateur |
| 15m/30m/1h | ❌ < frais | **Jeter** — exécuter en 4h |
| 6-8 setups, 20+ params, ML inline | ❌ overfit/fragile | **Jeter** — règle simple, ~6 params, déterministe |

---

## 4. Nouvelle stratégie — `volatility_squeeze` (l'antithèse)

**Thèse** : *trader la volatilité (prévisible), pas la direction (aléatoire).*

- **Timing par la volatilité** : détecter la **compression** (squeeze : largeur de
  Bollinger dans son percentile bas) — l'edge amplitude réel, sans ML.
- **Direction = tendance établie**, jamais prédite : on ne prend que la
  **cassure d'expansion alignée sur la tendance** (EMA50/200 + pente). En chop /
  hors tendance → **abstention** (pas de fade).
- **Règle, pas ML** : ~6-8 paramètres, déterministe, reproductible, zéro fichier
  modèle, zéro réentraînement.
- **4h** (au-dessus du mur des frais).
- Sortie : trailing (laisse courir l'expansion) + stop ATR + max-hold.

C'est le **coiled-spring** : ressort comprimé qui se détend dans le sens de la
tendance. On conserve l'unique edge robuste de la lignée (volatilité) et on
élimine ses erreurs (direction-ML, chop-fade, bas TF, complexité).

### 4.1 Résultats — la critique validée empiriquement
Backtest 4h, 7.5 ans, frais/spread/borrow réalistes (`research/backtest_squeeze.py`),
config long-only + squeeze strict (percentile 0.20, ADX≥22) :

| Métrique | volatility_squeeze (4h) | À comparer aux assertions Omnibus |
|----------|------------------------:|-----------------------------------|
| Rendement 7.5 ans | **+68.7 %** | (Omnibus : in-sample, ~120 trades, non-OOS) |
| Sharpe | 13.7\* | — |
| max DD | **-5.8 %** | — |
| Profit factor | **2.49** | — |
| **Walk-forward OOS** | **consistance 80 %** | (Omnibus : `oos_score: null`) |
| BEAR 2022 | **-1.2 %** vs B&H -53 % | préserve le capital |
| BULL 2023-24 | **+12.0 %** (PF 3.9) | — |
| 1h (TF de l'Omnibus) | **-41.6 %** | confirme : bas-TF sous le mur des frais |

\* Sharpe gonflé par le faible nombre de trades (85 sur 7.5 ans) ; PF 2.49 et la
**consistance OOS 80 %** (meilleure de toutes les stratégies du repo) sont les
métriques fiables. **8 paramètres, déterministe, zéro ML** — contre 23 paramètres
+ ML inline non-validé OOS pour V11. La discipline (ne trader que l'edge réel)
bat la complexité.
