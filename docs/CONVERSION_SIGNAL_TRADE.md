# Conversion signal → trade, sur l'historique complet

Les mesures précédentes portaient sur 12 000 barres et concluaient qu'aucune
stratégie ne passait la validation hors-échantillon. Deux choses ont changé :
l'**historique complet** est utilisé (15 769 barres en 4 h, 51 909 en 1 h) et
les **modes de sortie** sont sélectionnables, donc l'effet de la gestion de
position devient attribuable.

**Le résultat principal : le mode de sortie est le levier dominant, et le
meilleur mode diffère selon le symbole.** C'est aussi la première fois de ce
dossier qu'une configuration ressort positive hors-échantillon avec un ratio de
surapprentissage acceptable.

Reproduire :

```bash
python scripts/measure_exit_conversion.py --trials 40 --jobs 4
```

---

## 1. Protocole — deux régimes, choisis par le coût mesuré

| stratégie | coût d'un backtest | protocole |
|---|---:|---|
| `smc_ml_edge` (1 h, 51 909 barres) | **176 s** (26 réentraînements) | ablation à paramètres fixes |
| `smart_money` (4 h, 15 769 barres) | **6 s** | recherche 40 essais par mode |

Une recherche de 40 essais sur `smc_ml_edge` coûterait des heures par case. On
fait donc varier **le seul mode de sortie**, ce qui isole exactement la question
posée. Pour `smart_money`, le budget permet une vraie recherche via
`OptimizerSearchEngine` — découpe IS/OOS canonique, sélection sur l'IS, report
sur l'OOS.

L'outillage du dépôt est réutilisé plutôt que réécrit : la version maison
précédente avait réintroduit un problème déjà résolu (fenêtre OOS sans barres de
rodage, d'où 0 trade partout).

## 2. Un défaut de conception trouvé par la mesure

La première passe a donné `jambes = 0` partout, et `tp1_tp2_runner` rendait un
backtest **rigoureusement identique** à `as_declared`. Diagnostic :

- le moteur teste la **cible fixe** (ligne 936) **avant** les cibles partielles ;
- `smc_ml_edge` fixe `sl_atr_mult = tp_atr_mult = 2.5`, donc sa cible tombe
  exactement à **1R** — pile où TP1 devrait se déclencher.

La cible fixe soldait donc toujours la position en entier, et le mode ne
décidait de rien. Un mode qui promet de laisser courir un reliquat tout en
gardant une cible à 1R se contredit lui-même : les trois modes « laisser
courir » **retirent désormais la cible fixe**. Après correction, les jambes
partielles se déclenchent (36 sur BTC, 26 sur ETH, 127 sur ETH 4 h).

## 3. Ablation à paramètres fixes — historique complet

### `smc_ml_edge` · 1 h

| mode | BTC PnL | BTC PF | ETH PnL | ETH PF |
|---|---:|---:|---:|---:|
| `as_declared` | **+4.25 %** | 1.112 | **+16.46 %** | 1.777 |
| `sl_tp` | **+4.88 %** | 1.130 | +13.73 % | 1.664 |
| `trailing` | −1.64 % | 0.953 | +11.69 % | 1.572 |
| `trailing_after_profit` | −0.45 % | 0.988 | +13.16 % | 1.652 |
| `tp1_tp2_runner` | −0.40 % | 0.989 | +13.79 % | 1.679 |

Sur l'historique complet, `smc_ml_edge` est **positive sur les deux symboles** —
ce qu'elle n'était pas sur 12 000 barres. Mais les modes suiveurs la
**dégradent** : ses entrées sont courtes, et laisser courir ne leur convient pas.

⚠️ Buy & hold sur la même fenêtre : **+881 %** (BTC) et **+406 %** (ETH). La
stratégie est positive et très loin derrière le simple achat-conservation.

### `smart_money` · 4 h

| mode | BTC PnL | ETH PnL | ETH win |
|---|---:|---:|---:|
| `as_declared` | +153.50 % | −8.68 % | 35.98 % |
| `sl_tp` | +171.70 % | −8.68 % | 35.98 % |
| `trailing` | +153.50 % | **+52.90 %** | 37.04 % |
| `trailing_after_profit` | +153.47 % | +39.05 % | 37.58 % |
| `tp1_tp2_runner` | +72.92 % | +27.36 % | **47.34 %** |

**C'est ici que le levier saute aux yeux** : sur ETH, passer de `as_declared` à
`trailing` fait passer le résultat de **−8.68 % à +52.90 %**, sans toucher au
signal. La seule chose qui change est la façon de sortir.

`tp1_tp2_runner` fait moins de PnL mais remonte nettement le taux de réussite
(35.98 % → 47.34 %) et réduit le drawdown — profil plus régulier, gain moindre.

## 4. Recherche par mode, jugée hors-échantillon — le juge

40 essais par case, sélection sur l'IS, report sur l'OOS (5 520 barres).

### BTC/USDC · 4 h

| mode | OOS PnL | trades | Sharpe | surapprentissage |
|---|---:|---:|---:|---:|
| `as_declared` | +254.8 | 30 | 0.83 | 0.88 |
| **`sl_tp`** | **+472.5** | 28 | **1.10** | **0.60** |
| `trailing` | +254.8 | 30 | 0.83 | 0.88 |
| `trailing_after_profit` | +254.8 | 30 | 0.83 | 0.88 |
| `tp1_tp2_runner` | +102.9 | 30 | 0.50 | 1.05 |

### ETH/USDC · 4 h

| mode | OOS PnL | trades | Sharpe | surapprentissage |
|---|---:|---:|---:|---:|
| `as_declared` | −56.1 | 34 | −0.46 | **10.0** (plafond) |
| `sl_tp` | −58.8 | 30 | −0.30 | **10.0** |
| `trailing` | −54.7 | 34 | −0.45 | **10.0** |
| `trailing_after_profit` | −54.8 | 34 | −0.45 | **10.0** |
| **`tp1_tp2_runner`** | **+7.3** | 34 | −0.07 | **0.87** |

Sur ETH, **quatre modes sur cinq saturent le ratio de surapprentissage** à son
plafond de 10.0 : la recherche a trouvé sur l'IS quelque chose qui ne survit pas
au changement de période. Seul `tp1_tp2_runner` échappe à ce diagnostic, et
c'est aussi le seul avec un PnL hors-échantillon positif.

## 5. Ce que ça change, et ce que ça ne prouve pas

**Le mode de sortie est le levier dominant identifié jusqu'ici.** Aucun ajout de
feature du dossier SMC n'a produit un écart comparable à celui de la ligne ETH
(−8.68 % → +52.90 %). Cela confirme le diagnostic posé dans
`docs/STRATEGY_SMC_ML_EDGE.md` §4 : le problème n'était pas la détection.

**Mais le meilleur mode diffère selon le symbole** — `sl_tp` sur BTC,
`tp1_tp2_runner` sur ETH — et c'est exactement le motif qui, plus tôt dans ce
dossier, s'est révélé être du bruit plutôt qu'un besoin de réglage par actif. La
prudence s'impose : BTC et ETH corrèlent à **0.835**, donc ce ne sont pas deux
échantillons indépendants.

**Trois réserves à garder en tête :**

1. **Buy & hold écrase tout.** +1 739 % sur BTC 4 h et +1 588 % sur ETH contre
   au mieux +472 hors-échantillon. Aucune de ces stratégies ne justifie
   aujourd'hui d'être préférée à l'achat-conservation sur ces fenêtres.
2. **`sl_tp` sur BTC a un profil de loterie** : 20 trades, 10 % de réussite,
   profit factor 9.53. Le gain tient à quelques trades énormes — fragile par
   construction, quoi qu'en dise le Sharpe.
3. **Le gate de promotion du dépôt (`beats_baseline`) n'a pas été appliqué** à
   la partie B : les chiffres rapportés sont ceux de l'optimiseur, pas un feu
   vert de mise en production.

## 5 bis. Répartition du PnL par poste de sortie

`by_exit_reason` compte des TRADES ; il ne dit pas d'où vient l'argent dans un
trade fractionné. `by_exit_leg` le dit — et son invariant est que **la somme des
postes redonne exactement le PnL total**.

ETH/USDC 4 h, mode `tp1_tp2_runner` :

| poste | n | PnL | part | WR |
|---|---:|---:|---:|---:|
| `complet · trailing_stop` | 89 | **−1 629.28** | 46.1 % | **0 %** |
| `runner` | 80 | +1 132.91 | 32.1 % | 100 % |
| `tp2` | 47 | +418.13 | 11.8 % | 100 % |
| `tp1` | 80 | +351.88 | 10.0 % | 100 % |
| **total** | | **+273.64** | 100 % | |

La lecture est nette : le reliquat rapporte plus que les deux jambes réunies,
**mais les trades qui n'atteignent jamais TP1 coûtent davantage que ce que les
trois postes gagnants rapportent**. Le net tient à peu de chose.

### Le piège que cette table a d'abord tendu

La première version n'agrégeait que les trades FRACTIONNÉS. Or une jambe
partielle ne se déclenche qu'en atteignant sa cible : ces trades sont gagnants
par construction. Le tableau affichait donc **+1 902.92 pour une stratégie qui
gagne +273.64** — soit 695 % du PnL réel — avec **100 % de réussite sur chaque
ligne**. Un lecteur y voyait une stratégie qui ne perd jamais.

Les 89 trades sortis en une fois (−1 629.28) sont désormais un poste à part
entière, et un test impose l'invariant de somme. C'est la seule assertion qui
attrape ce genre d'erreur : aucune vérification de forme ne l'aurait vue.

### Vérification du sens SHORT

Les cibles partielles et le stop doivent être du bon côté de l'entrée, sinon une
cible serait touchée dès la première barre et le backtest afficherait un taux de
réussite flatteur sans lever d'erreur. Mesuré sur les 72 shorts d'ETH 4 h :

- **0** stop mal placé (tous au-dessus de l'entrée) ;
- **0** jambe au-dessus de l'entrée ;
- pire short sorti à **−1.02 R** — le stop plafonne bien la perte.

⚠ Aucun poste `stop_loss` n'apparaît : dès que le suiveur est actif, il remplace
le stop initial et toute perte est étiquetée `trailing_stop`. C'est un choix
d'étiquetage préexistant du moteur, pas une perte non plafonnée.

## 6. Suite

1. **Trancher `sl_tp` contre `tp1_tp2_runner` sur des actifs décorrélés.** Les
   six actions XPAR déjà en cache (corrélation moyenne 0.253) sont le bon jeu
   pour cela — c'est le seul moyen de savoir si l'écart BTC/ETH est un effet de
   symbole ou du bruit.
2. **Passer les meilleures configurations par `beats_baseline`** avant toute
   conclusion opérationnelle.
3. **Comprendre pourquoi les modes suiveurs dégradent `smc_ml_edge`.** Ses
   entrées sont courtes (`max_hold_bars: 12`) ; un suiveur n'a peut-être pas le
   temps de s'exprimer, auquel cas le mode et l'horizon de détention doivent
   être réglés ensemble, pas séparément.
