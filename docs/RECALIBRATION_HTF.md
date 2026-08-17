# Recalibration après le correctif HTF — le résultat complet

Neuf stratégies avaient un filtre HTF **inerte en backtest et actif en
production** (`htf_trend(None)` renvoyait 0 faute de `df_htf`, que seul le live
fournit). Leurs `optimizer_results` portaient donc sur un comportement qui n'a
jamais existé.

Ce document clôt la campagne.

```bash
python scripts/recalibrate_htf_strategies.py --data data/ohlcv --barres 60000 --etape 1
python scripts/recalibrate_htf_strategies.py --data data/ohlcv --barres 20000 \
    --etape 2 --trials 30 --njobs 6
```

---

## 1. L'ampleur : 27 couples sur 36

| stratégie | bougent | Δ PnL | Δ trades |
|---|:--:|---:|---:|
| `fear_momentum` | 4/4 | +433,4 | **−908** |
| `multi_tf_sr` | 4/4 | +289,3 | −233 |
| `breakout_filtreHor` | 4/4 | +161,3 | −23 |
| `trend` | 4/4 | +69,4 | −79 |
| `pullback_trend` | 3/4 | +15,2 | −24 |
| `supertrend_macd` | 4/4 | −86,8 | −24 |
| `breakout` | 4/4 | −194,8 | −95 |
| `gemini_trend_follow` | 0/4 | — | — |
| `tvr_trend` | 0/4 | — | — |

Le filtre actif retire des trades dans **tous** les cas (−1 386 au total).

⚠ Sur la fenêtre tronquée à 6 000 barres, la même mesure donnait **20 sur 36** :
**le plafond sous-estimait le problème de sept couples.**

---

## 2. Le résultat : 15 candidats sur 27

| stratégie | cas | trades OOS | PnL | Sharpe | overfit | gate |
|---|---|---:|---:|---:|---:|:--:|
| `trend` | ETH 4 h | 19 | **+416,4** | 0,95 | 0,36 | ✅ |
| `multi_tf_sr` | ETH 4 h | **10** | **+371,7** | 1,35 | 0,0 | ✅ |
| `supertrend_macd` | BTC 4 h | **11** | +215,2 | 0,95 | 0,0 | ✅ |
| `breakout` | ETH 4 h | 23 | +208,6 | 0,80 | 0,37 | ✅ |
| `pullback_trend` | BTC 4 h | **90** | +206,2 | 0,38 | 0,78 | ✅ |
| `breakout` | ETH 1 h | 31 | +170,6 | 0,95 | 0,55 | ✅ |
| `supertrend_macd` | BTC 1 h | 20 | +157,3 | **2,09** | 0,0 | ✅ |
| `multi_tf_sr` | BTC 4 h | 14 | +133,7 | 0,60 | 0,55 | ✅ |
| `trend` | ETH 1 h | **36** | +126,8 | 0,64 | 0,0 | ✅ |
| `trend` | BTC 4 h | **10** | +86,8 | 0,53 | 1,01 | ✅ |
| `breakout` | BTC 1 h | 30 | +81,2 | 0,57 | 0,39 | ✅ |
| `multi_tf_sr` | ETH 1 h | 23 | +74,7 | 0,62 | 0,43 | ✅ |
| `breakout_filtreHor` | BTC 1 h | 18 | +69,3 | 0,61 | 0,0 | ✅ |
| `multi_tf_sr` | BTC 1 h | 20 | +51,9 | 0,79 | 0,31 | ✅ |
| `supertrend_macd` | ETH 4 h | 13 | +7,1 | 0,01 | 0,79 | ✅ |
| `fear_momentum` | 4 cas | 130 à 256 | **−165 à −216** | −0,50 à −2,48 | — | ❌ |
| `pullback_trend` | BTC 1 h | 110 | −42,7 | −0,80 | **10,0** | ❌ |
| autres | | | | | | ❌ |

**15 / 27 passent `beats_baseline`. 0 / 27 sous le plancher de dix trades.
19 / 27 ont un PnL OOS positif.**

### Les deux corrections ont fait exactement ce qu'on attendait

| | 6 000 barres, plancher 2 | 20 000 barres, plancher 10 |
|---|---:|---:|
| passent le gate | **4 / 20** | **15 / 27** |
| sous dix trades OOS | **14 / 20** | **0 / 27** |

Les optima dégénérés ont disparu, et pour deux raisons qui se cumulent : la
fenêtre donne assez de matière pour *trouver* des configurations à dix trades ou
plus, et le plancher unifié **force** l'optimiseur à les préférer. Ni l'un ni
l'autre n'aurait suffi.

---

## 3. Trois réserves, à lire avant d'appliquer quoi que ce soit

**`beats_baseline` est un test RELATIF.** Il dit « mieux que les paramètres
actuels », pas « rentable ». Les paramètres actuels étant précisément ceux
mesurés contre un filtre inerte, la barre est basse. `supertrend_macd` ETH 4 h
passe avec un PnL de **+7,1** et un Sharpe de **0,01** — c'est un match nul
présenté comme une victoire.

**Trois candidats sont pile au plancher.** `multi_tf_sr` ETH 4 h (10 trades,
+371,7), `supertrend_macd` BTC 4 h (11 trades), `trend` BTC 4 h (10 trades). Un
gros PnL sur dix trades a la forme d'un tirage heureux, pas d'un edge. Le
plancher les autorise ; il ne les recommande pas.

**Le surapprentissage n'est pas qu'un problème d'échantillon.** Cinq couples ont
un `overfit` saturé à 10,0, dont `pullback_trend` BTC 1 h avec **110 trades** et
`fear_momentum` BTC 4 h avec **130**. Avec de la matière, le ratio IS/OOS reste
dégradé : ce n'est plus imputable à la rareté.

### `fear_momentum` est le résultat négatif le plus solide de la campagne

Ses quatre couples portent **130 à 256 trades OOS** — les plus gros échantillons
de tout le lot — et sont **tous nettement perdants** (−165 à −216, Sharpe jusqu'à
−2,48).

Sur la fenêtre à 6 000 barres, trois de ses quatre couples **passaient** le gate.
C'était un artefact de taille d'échantillon. Avec de la matière, la stratégie
échoue sans ambiguïté — et c'est celle dont le correctif HTF déplaçait le plus le
PnL (+433).

---

## 4. Décision

**Aucun `optimizer_results` n'est modifié.** Appliquer un paramétrage est une
décision de trading, pas un correctif : les quinze candidats sont dans
`scripts/_recal_reprise.json`, prêts pour le chemin normal du dépôt
(`apply_best_params`, gardé par `beats_baseline`).

Ce que la mesure autorise à dire :

1. **Les `optimizer_results` de ces 27 couples sont invalides** — mesurés contre
   un filtre qui n'existait pas en production.
2. **Quinze remplaçants existent** et battent l'existant sur un échantillon
   honnête.
3. **`fear_momentum` ne devrait pas tourner** sur ces quatre couples, quel que
   soit son paramétrage.
4. **`gemini_trend_follow` et `tvr_trend` ne sont pas concernées** — leur
   résultat ne bouge pas, rien à recalibrer.

### Suite

- **Élargir l'univers** plutôt que la fenêtre pour les couples à dix trades :
  c'est le nombre de symboles qui manque, pas l'historique.
- **Chercher la cause du surapprentissage résiduel** sur les cinq couples à
  `overfit` 10,0 avec échantillon large — c'est le seul signal que ni la fenêtre
  ni le plancher n'expliquent.
