# `smc_ml_edge` — le backtest qui manquait

`docs/ML_ABLATION_SMC.md` se terminait par un avertissement : tout y était de
l'AUC, donc de la qualité de classement, et rien ne disait qu'un edge
directionnel de 0.60 se convertirait en rentabilité nette de frais. Ce
document répond à cette question.

**La réponse est non.** Et elle s'est durcie au fil des mesures : les premiers
chiffres (profitable sur BTC, perdante sur ETH) venaient de backtests sur la
fenêtre entière, donc in-sample. Sous une découpe IS/OOS honnête avec le même
budget d'optimisation pour chaque stratégie et chaque timeframe, **aucune des
huit configurations testées ne passe le garde-fou du dépôt** — voir §3
quinquies, qui fait foi sur ce point.

Reproduire :

```bash
python scripts/measure_smc_ml_edge.py
```

---

## 1. Les chiffres

12 000 barres en 1 h, capital 10 000, frais taker 0.1 %, spread 0.05 %,
réentraînement toutes les 2 000 barres. `bh` = buy & hold sur la même fenêtre.

### BTC/USDC — le marché baisse de 22 %, la stratégie gagne

| variante | trades | L/S | win | PnL | bh | Sharpe | DD max | PF | frais |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| top10 **+ filtre SMC** | 22 | 16/6 | 59.1 % | **+5.98 %** | −22.08 % | **0.79** | −3.2 % | **1.81** | 0.9 % |
| top10 sans filtre | 119 | 90/29 | 48.7 % | +5.04 % | −22.08 % | −0.11 | −10.7 % | 1.10 | 4.6 % |
| top20 sans filtre | 262 | 181/81 | 44.7 % | −1.78 % | −22.08 % | −1.13 | −17.9 % | 0.98 | 9.1 % |
| top5 sans filtre | 30 | 24/6 | 50.0 % | +0.27 % | −22.08 % | −0.19 | −8.3 % | 1.02 | 1.0 % |

### ETH/USDC — le marché monte de 8 %, la stratégie perd

| variante | trades | L/S | win | PnL | bh | Sharpe | DD max | PF | frais |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| top10 + filtre SMC | 30 | 25/5 | 43.3 % | −3.07 % | +8.13 % | −0.78 | −4.7 % | 0.78 | 1.2 % |
| top10 sans filtre | 102 | 86/16 | 47.1 % | −4.38 % | +8.13 % | −0.97 | −12.0 % | 0.86 | 2.5 % |
| top20 sans filtre | 259 | 199/60 | 44.4 % | −11.90 % | +8.13 % | −1.78 | −24.8 % | 0.83 | 6.2 % |
| top5 sans filtre | 49 | 47/2 | 40.8 % | −9.38 % | +8.13 % | −2.10 | −11.9 % | 0.49 | 1.2 % |

## 2. Ce que ces chiffres disent vraiment

**Le résultat BTC n'est pas du bêta.** Le marché perd 22 % sur la fenêtre et la
stratégie gagne 6 % en prenant les deux sens (16 longs, 6 shorts). Ce n'est pas
une position longue déguisée.

**Mais il ne réplique pas.** Sur ETH, les mêmes réglages perdent 3 %, dans un
marché qui monte de 8 %. Le modèle y a pourtant la même AUC dir (0.5961 contre
0.6072 sur BTC) — l'écart de qualité de classement est mince, l'écart de
résultat est total. C'est le fait central de ce document.

**Les frais décident, et ce point-là réplique.** Sur les deux symboles, le PnL
se dégrade monotonement avec le nombre de trades :

| sélectivité | trades | frais | PnL BTC | PnL ETH |
|---|---:|---:|---:|---:|
| top 10 % + filtre | 22–30 | ~1 % | +5.98 % | −3.07 % |
| top 10 % | 102–119 | 2.5–4.6 % | +5.04 % | −4.38 % |
| top 20 % | 259–262 | 6.2–9.1 % | −1.78 % | −11.90 % |

À 262 trades, 9.1 % de frais transforment un brut positif en net négatif. Le
profit factor suit la même pente (1.81 → 1.10 → 0.98 sur BTC). L'edge, quand il
existe, est mince : il ne survit pas à un trade tous les 45 barres.

**Le filtre SMC était le bon pari — j'attendais l'inverse.** Le raisonnement
initial était qu'un filtre dur sur des colonnes que le modèle voit déjà ne
ferait que retirer des trades. Mesuré, il fait mieux que ça sur BTC : il
divise les trades par 5 (119 → 22) et fait passer le win rate de 48.7 % à
59.1 %, le Sharpe de −0.11 à 0.79, le drawdown de −10.7 % à −3.2 %. Il retire
les *mauvais* trades, pas des trades au hasard. Sur ETH il améliore aussi le
résultat (−4.38 % → −3.07 %) sans le rendre positif.

L'explication plausible : le modèle utilise les colonnes SMC pour classer, mais
un classement n'est pas un veto. Une barre peut être dans le top 10 % de
`p_event` tout en étant structurellement mauvaise à trader ; la règle dure
exprime quelque chose que la fonction de score ne peut pas exprimer.

## 3. Pourquoi une bonne AUC ne fait pas une bonne stratégie

Trois raisons, dans l'ordre où elles mordent.

**L'AUC porte sur toutes les barres, la stratégie sur le décile supérieur.**
Une AUC de 0.60 dit que le modèle classe mieux qu'au hasard *en moyenne*. Rien
ne garantit que l'edge soit uniformément réparti — il peut être concentré là où
la stratégie ne va pas, ou absent là où elle va.

**L'AUC ignore l'asymétrie gain/perte.** Elle ne connaît que l'ordre. Une
stratégie a besoin que ses gains dépassent ses pertes, ce qui dépend
entièrement du stop, de la cible et de la durée de détention — trois choses
dont le modèle n'a jamais entendu parler.

**L'AUC ignore les frais.** C'est le point le plus brutal, et le tableau
ci-dessus le chiffre.

## 3 bis. Le modèle n'est pas symbole-dépendant, le stop l'était

Question posée après coup : l'écart BTC/ETH vient-il de ce qu'il faudrait un
modèle par symbole, ou seulement des seuils de décision ? Deux mesures y
répondent, et la réponse est « ni l'un ni l'autre ».

**Les modèles se transfèrent presque sans perte.** Entraînement sur 12 000
barres, évaluation sur un holdout de 4 000 barres jamais vues :

| entraîné sur | testé sur | auc_amp | auc_dir |
|---|---|---:|---:|
| BTC | BTC | 0.6895 | 0.5746 |
| BTC | **ETH** | 0.6767 | 0.5659 |
| ETH | **BTC** | 0.6472 | **0.5794** |
| ETH | ETH | 0.6652 | 0.5798 |

Le modèle BTC fait mieux sur ETH (0.6767 d'AUC amplitude) que le modèle d'ETH
sur son propre marché (0.6652), et le modèle ETH bat celui de BTC sur la
direction de BTC. Il n'y a pas de « modèle BTC » et de « modèle ETH » : la
séparation par symbole — qui a toujours été en place, aucune mesure de ce dépôt
n'a jamais mutualisé — n'apporte rien.

⚠ À noter : sur ce holdout franchement disjoint, `auc_dir` vaut **0.57–0.58**,
contre 0.60 mesuré sur le split de validation d'entraînement. L'edge est réel
mais un cran plus mince que ce qu'annonce `docs/ML_ABLATION_SMC.md`.

**L'échelle des sorties, elle, diffère massivement — et c'est une propriété du
MODÈLE, pas du marché :**

| modèle | marché | seuil amp q90 | seuil écart dir q80 |
|---|---|---:|---:|
| BTC | BTC | 0.4723 | 0.0230 |
| BTC | ETH | 0.5513 | 0.0229 |
| ETH | BTC | 0.3929 | **0.1171** |
| ETH | ETH | 0.4711 | **0.1119** |

Le modèle entraîné sur ETH produit des probabilités directionnelles **5 fois
plus étalées**, quel que soit le marché où on l'applique. C'est exactement ce
que le mécanisme de quantiles absorbe — et la confirmation a posteriori que des
seuils absolus ne pouvaient pas fonctionner (§5).

**Le vrai coupable était un paramètre partagé, pas un besoin de
personnalisation.** Balayage de la géométrie de sortie, même grille sur les
deux symboles :

| variante | ETH PnL | ETH PF | ETH win | BTC PnL | BTC PF | BTC win |
|---|---:|---:|---:|---:|---:|---:|
| référence `sl1.5 tp2.5 h12` | −3.07 % | 0.78 | 43.3 % | +5.98 % | 1.81 | 59.1 % |
| cible serrée `tp1.5` | −2.52 % | 0.73 | 50.0 % | +4.47 % | 1.54 | 64.0 % |
| **stop large `sl2.5`** | **−0.05 %** | **0.99** | **61.1 %** | **+9.18 %** | **6.45** | **78.9 %** |
| hold long `h24` | +1.32 % | 1.17 | 52.6 % | +6.87 % | 1.80 | 55.0 % |
| hold court `tp1.5 h6` | +0.81 % | 1.21 | 64.3 % | +3.38 % | 1.45 | 64.0 % |
| très sélectif | +0.39 % | 1.38 | 80.0 % | +1.64 % | 2.56 | 75.0 % |

Élargir le stop de 1.5 à 2.5 ATR améliore **les deux symboles sur tous les
axes**. Le diagnostic du §4 est confirmé : le stop coupait les positions avant
leur cible, et il le faisait sur les deux marchés. L'asymétrie BTC/ETH du §1
était donc en partie l'effet d'un mauvais réglage COMMUN, pas d'une différence
de nature entre les deux actifs.

⚠ **Ce que ces chiffres ne prouvent pas.** 18 à 30 trades par configuration :
un Sharpe de 2.88 sur 19 trades n'est pas une mesure fiable. Et la meilleure
des 6 configurations a été choisie sur les données qui l'évaluent — de la
sélection en échantillon. Ce qui rend le résultat crédible malgré tout, c'est
que la MÊME configuration gagne sur les deux symboles et que deux leviers
indépendants (stop plus large, détention plus longue) pointent dans le même
sens. Cela reste deux symboles, une fenêtre, un timeframe.

**Ce réglage n'a pas été appliqué par défaut** : `sl_atr_mult` est un paramètre
de trading, pas un correctif. Le changer est une décision d'exploitation.

**Enseignement de méthode.** `recipes/omnibus_full.yaml` écartait un bloc `1d`
au motif qu'« un override qui aide un symbole et en abîme un autre n'est pas un
réglage, c'est du bruit ». L'inférence est fautive : « aide l'un, abîme
l'autre » est précisément ce qu'on observerait s'il fallait un réglage par
symbole. Les deux hypothèses prédisent la même chose, et rien ne les avait
séparées. La bonne justification, dans ce cas précis, était la TAILLE
D'ÉCHANTILLON — le journalier n'offre ~2 600 barres pour des AUC de 0.42 à 0.55,
autour du hasard. Même conclusion, meilleure raison ; la mauvaise raison aurait
pu faire écarter un effet réel ailleurs.

## 3 ter. Corrélation, pooling, `tb` : quatre réponses mesurées

### La « réplication sur 4 jeux » n'en était pas une

BTC et ETH corrèlent à **0.835** sur les rendements horaires alignés (47 105
barres communes). Ce sont deux vues d'un même processus, pas deux échantillons
indépendants. Toute affirmation de ce dossier reposant sur « répliqué sur les
deux symboles » — l'ablation SMC comme le balayage de sorties — doit être lue
avec cette réserve : le SENS des effets est confirmé, leur généralité ne l'est
pas.

Le dépôt ne permet pas de faire mieux aujourd'hui : seuls BTC et ETH ont un
historique 1 h exploitable (51 984 et 47 266 barres). XRP n'en a que 550 et
corrèle de toute façon à 0.73. Le test de généralité honnête serait des ÉPOQUES
disjointes du même actif, ou une classe d'actifs non crypto.

### Le pooling BTC+ETH améliore les deux têtes

`train_multi` existait, testé, et n'avait jamais servi sur crypto. Mesuré sur
le holdout de 4 000 barres jamais vues :

| modèle | holdout | auc_amp | auc_dir |
|---|---|---:|---:|
| solo | BTC | 0.6968 | 0.5891 |
| **poolé BTC+ETH** | BTC | **0.7085** | 0.5892 |
| solo | ETH | 0.6685 | 0.5803 |
| **poolé BTC+ETH** | ETH | **0.6824** | **0.5939** |

Gain sur l'amplitude dans les deux cas (+0.012 et +0.014), sur la direction
nettement sur ETH (+0.014) et neutre sur BTC. C'est cohérent avec la
corrélation de 0.835 : le pooling n'apporte pas de la diversité, il apporte du
VOLUME sur un processus quasi identique — et c'est précisément le régime où il
aide. Le symbole qui gagne le plus est celui qui a le moins d'historique.

**Non câblé dans la stratégie** : `smc_ml_edge` entraîne un modèle par symbole.
Router son entraînement vers `train_multi` est le chantier suivant le plus
rentable du dossier.

### `tb` : confirmé sans valeur, sur mesure plus exigeante

| modèle | holdout | auc_amp | auc_dir | **auc_tb** |
|---|---|---:|---:|---:|
| solo | BTC | 0.7018 | 0.5922 | **0.5586** |
| poolé | BTC | 0.7085 | 0.5966 | **0.5499** |
| solo | ETH | 0.6779 | 0.5774 | **0.5667** |
| poolé | ETH | 0.6849 | 0.5957 | **0.5546** |

`tb` est sous `dir` dans les **quatre** cas, et le pooling la DÉGRADE (−0.009,
−0.012) là où il améliore `dir`. Le verdict de `docs/ML_ABLATION_SMC.md` §4
tenait sur le split de validation d'entraînement ; il tient encore sur un
holdout franchement disjoint, ce qui est une mesure plus dure.

La réserve qui restait — « l'AUC mesure le classement, pas la rentabilité,
seul un backtest tranchera » — est levée autrement : le backtest a montré que
ce qui manquait n'était pas une meilleure cible mais un stop correct. `tb`
encodait une géométrie de sortie ; il était plus simple, et plus efficace, de
corriger directement celle de la stratégie.

### Les deux leviers de sortie ne s'additionnent pas

| | `h12` | `h24` |
|---|---:|---:|
| BTC `sl2.5` | **+9.18 %** (PF 6.45) | +5.39 % (PF 2.08) |
| ETH `sl2.5` | **−0.05 %** (PF 0.99) | −0.65 % (PF 0.90) |

Stop plus large et détention plus longue traitaient la MÊME cause : des
positions coupées avant leur cible. Le stop corrigé, prolonger la détention
n'ajoute que de l'exposition. `max_hold_bars` reste donc à 12, et les défauts
retenus sont `sl_atr_mult: 2.5`, `tp_atr_mult: 2.5`, `max_hold_bars: 12`.

### Paramétrage par symbole et timeframe : le mécanisme existe déjà

Un bloc `optimizer_results:` du YAML de stratégie, indexé par timeframe puis
par symbole, est superposé aux `params:` par
`app.core.param_resolution.resolve_strategy_params` — empruntée à l'identique
par le backtest et par le live. « Un paramétrage pour BTC 1 h, un pour ETH 1 h,
un pour XRP 30 min » est donc déjà exprimable sans une ligne de code.

**Aucun bloc n'a été écrit**, délibérément : sur ~20 trades et deux symboles
corrélés à 0.835, des overrides par symbole figeraient du bruit dans un fichier
versionné. Le mécanisme est là quand une mesure le justifiera.

## 3 quater. Règles contre modèle : chacun sur son timeframe

Le dépôt contenait déjà une stratégie SMC **non-ML** : `smart_money`, 1 616
lignes, 5 setups, confluence volume / FVG inversé / chandeliers, divergence SMT
— et une ablation de setups déjà faite, consignée dans son YAML (breakers
écartés : −163 USDC sur 220 trades en 4 h ; BPR : pas d'edge stable). Elle
n'avait jamais été mesurée contre `smc_ml_edge`.

| symbole / TF | stratégie | trades | win | PnL | buy & hold | Sharpe | PF | frais |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| BTC 1 h | `smc_ml_edge` | 19 | 78.9 % | **+9.18 %** | −22.1 % | 2.88 | 6.45 | 0.4 % |
| BTC 1 h | `smart_money` | 14 | 28.6 % | −3.94 % | −24.5 % | −0.97 | 0.59 | 1.0 % |
| **BTC 4 h** | `smc_ml_edge` | 11 | 36.4 % | −1.90 % | +461 % | −0.41 | 0.56 | 0.2 % |
| **BTC 4 h** | **`smart_money`** | 64 | 46.9 % | **+42.87 %** | +443 % | 0.59 | **1.74** | 5.0 % |
| ETH 1 h | `smc_ml_edge` | 18 | 61.1 % | −0.05 % | +8.1 % | −0.21 | 0.99 | 0.4 % |
| ETH 1 h | `smart_money` | 110 | 30.9 % | −19.82 % | +26.9 % | −1.79 | 0.71 | 6.4 % |
| ETH 4 h | `smc_ml_edge` | 29 | 44.8 % | −5.07 % | +400 % | −0.55 | 0.61 | 0.3 % |
| ETH 4 h | `smart_money` | 126 | 34.1 % | −12.07 % | +409 % | −0.40 | 0.89 | 5.6 % |

**Chacune gagne sur le timeframe pour lequel elle a été réglée, et perd
ailleurs.** `smart_money` fait +42.87 % avec un profit factor de 1.74 en 4 h —
son seul TF documenté comme tradable — et perd en 1 h ; `smc_ml_edge`, réglée
en 1 h, y fait +9.18 % et perd en 4 h. Il n'y a pas de vainqueur général.

**Une première version de cette mesure était fausse**, et la correction est
instructive : elle passait `params={}` — donc les défauts de code, pas le YAML
— et ne testait que le 1 h. `smart_money` y sortait à 127 trades pour −17.56 %.
Avec ses vrais paramètres : 14 trades, −3.94 %. Comparer une stratégie sur des
réglages que personne ne fait tourner ne mesure rien.

⚠ **Le buy & hold écrase les deux sur la fenêtre 4 h** (+443 %, soit ~5,5 ans
couvrant tout le cycle haussier). +42.87 % est un très mauvais résultat en
valeur absolue. La comparaison n'est pas tout à fait équitable — une stratégie
qui prend les deux sens n'est pas un investissement long — mais l'ordre de
grandeur doit rester sous les yeux.

**Conséquence pour la question « peut-on faire mieux en non-ML ? »** : la
stratégie existe, elle est déjà ablatée setup par setup, et son meilleur
résultat mesuré bat tout ce que la version ML a produit. En réécrire une
dupliquerait 1 616 lignes de travail déjà fait. Le levier utile est ailleurs —
`smc_ml_edge` n'a jamais été réglée pour le 4 h, et `smart_money` n'a jamais
reçu de filtre ML sur ses setups.

## 3 quinquies. La comparaison équitable — et ce qu'elle détruit

Objection reçue, et fondée : opposer `smc_ml_edge` réglée à la main en 1 h à
`smart_money` optimisée en 4 h ne compare pas deux stratégies, mais deux
réglages. Les **quatre cases** ont donc été traitées à l'identique — même
découpe IS/OOS (`split_is_oos`, 65/35), même métrique de sélection
(`composite_score`), même budget (40 trials de recherche aléatoire sur le
`param_space` déclaré), sélection sur l'IS **et jamais sur l'OOS**.

| symbole | TF | stratégie | OOS trades | OOS PnL | OOS PF | OOS Sharpe | buy & hold | valide |
|---|---|---|---:|---:|---:|---:|---:|:--:|
| BTC | 1 h | `smart_money` | 8 | **+5.00 %** | 2.43 | 1.40 | −6.55 % | ❌ |
| BTC | 1 h | `smc_ml_edge` | 1 | +0.52 % | — | — | −39.05 % | ❌ |
| BTC | 4 h | `smart_money` | 1 | −2.32 % | — | — | +13.10 % | ❌ |
| BTC | 4 h | `smc_ml_edge` | 140 | −8.99 % | 0.79 | −0.90 | +145.05 % | ❌ |
| ETH | 1 h | `smart_money` | 2 | −1.83 % | — | −26.47 | −5.93 % | ❌ |
| ETH | 1 h | `smc_ml_edge` | 0 | — | — | — | −52.26 % | ❌ |
| ETH | 4 h | `smart_money` | 1 | −2.19 % | — | — | −19.98 % | ❌ |
| ETH | 4 h | `smc_ml_edge` | 21 | −3.75 % | 0.61 | −0.63 | +13.31 % | ❌ |

**Aucune des huit cases ne passe `beats_baseline`**, le garde-fou du dépôt
(≥ 10 trades OOS, PnL OOS positif, meilleur que les paramètres par défaut, et
amélioration d'au moins un critère de qualité).

### Ce que cela retire aux chiffres précédents

Les résultats des §1 et §3 quater — +9.18 % pour `smc_ml_edge` en BTC 1 h,
+42.87 % pour `smart_money` en BTC 4 h — étaient des backtests **sur la fenêtre
entière, sans découpe**. Autrement dit in-sample. Sous une validation honnête,
ils ne tiennent pas. Ils restent dans ce document parce qu'effacer un chiffre
qu'on a publié est pire que le corriger, mais **c'est ce tableau-ci qui fait
foi**.

Deux signaux de surapprentissage confirment la lecture : le
`overfitting_ratio` sature à son plafond (10.0) dans les deux cases ayant assez
de trades, et le **Deflated Sharpe** — qui corrige le biais de 40 essais
successifs (López de Prado 2014) — tombe à **0.0** partout. Après correction du
test multiple, aucun Sharpe observé n'est significatif.

### Un défaut du protocole, trouvé et corrigé en route

La première version de cette mesure donnait **0 trade OOS partout** pour
`smc_ml_edge`. La cause n'était pas la stratégie mais le découpage : la fenêtre
OOS faisait 4 200 barres et le `warmup_bars` de 3 000 en mangeait la quasi-
totalité. On mesurait le protocole.

Corrigé en faisant **démarrer la passe OOS `warmup` barres avant la coupure** :
le rodage se consomme sur des barres IS et le premier trade possible tombe
exactement à la frontière. C'est ce que `_oos_trade_window_bars` fait déjà dans
l'optimiseur du dépôt — que j'aurais dû lire d'abord.

### Un biais de la métrique de sélection, à connaître

`composite_score` ne refuse un jeu de paramètres qu'en dessous de
`MIN_TRADES_DEGENERATE`, seuil bas. Sur 40 trials, l'optimum IS est donc
souvent une configuration **hyper-sélective** (`amp_top_q: 0.05`,
`dir_top_q: 0.10`) : peu de trades, tous excellents en IS — et plus rien à
échantillonner en OOS. C'est ce qui produit les cases à 0, 1 et 2 trades du
tableau, pour les deux stratégies. La sélection récompense une rareté qui ne
survit pas au changement de période.

### Ce que ce résultat ne dit pas

40 trials sur un espace de 864 combinaisons, et des fenêtres OOS de ~4 200
barres, ne permettent pas de conclure qu'aucun edge n'existe. Ce qui est
établi est plus étroit, et suffisant pour la décision : **dans l'espace de
paramètres actuel et sous une validation honnête, ni l'une ni l'autre de ces
stratégies n'est prête à être promue.** Le signal SMC reste, lui, solidement
mesuré (§2 bis du document d'ablation, 6 actions décorrélées) — c'est sa
conversion en trades qui échoue.

## 4. Ce qui reste à faire, dans l'ordre

Le prochain levier n'est **pas** plus de features. Le signal a été mesuré et
répliqué sur 4 jeux ; c'est la CONSTRUCTION DU TRADE qui n'a jamais été testée.
Les réglages actuels (`sl_atr_mult: 1.5`, `tp_atr_mult: 2.5`,
`max_hold_bars: 12`) ont été choisis par analogie avec les barrières mesurées
du schéma `triple_barrier` — jamais optimisés.

Un indice concret : sur ETH top5, 40.8 % de trades gagnants pour un profit
factor de 0.49 signifie que les gains moyens sont bien plus petits que les
pertes moyennes. Avec une cible à 2.5 ATR et un stop à 1.5 ATR, le rapport
devrait être l'inverse. Autrement dit **les positions sortent avant la cible**
— trailing stop, `max_hold_bars`, ou stop trop serré pour la volatilité réelle.
C'est là qu'il faut chercher.

Dans l'ordre :

1. **Balayer la géométrie de sortie** (`sl_atr_mult`, `tp_atr_mult`,
   `max_hold_bars`) par l'optimiseur du dépôt, sur les deux symboles à la fois.
   C'est le seul bloc jamais mesuré.
2. **Vérifier pourquoi les positions se ferment.** Le backtest expose
   `exit_reason` ; sa distribution dira si les trades meurent sur stop, sur
   trailing ou sur expiration.
3. **Élargir l'échantillon** avant toute conclusion : 22 trades ne permettent
   de conclure ni dans un sens ni dans l'autre. Plus de symboles, plus de
   timeframes.
4. **Alors seulement**, reconsidérer la tête `tb`. Le backtest est le juge que
   `docs/ML_ABLATION_SMC.md` réclamait pour trancher entre `dir` et `tb` ; il
   n'a pas de sens tant que la géométrie de sortie n'est pas réglée, puisque
   c'est précisément ce que `tb` encode.

## 5. Deux défauts trouvés en écrivant la stratégie

**Des seuils absolus qui ne prenaient aucun trade.** La première version
filtrait sur `p_event > 0.55` et `|p_up − 0.5| > 0.12`. Valeurs d'apparence
raisonnable, et pourtant : la tête `amp` cible le top 20 %, donc ses sorties
tournent autour de 0.35 (mesuré : 0.10 à 0.67), et `p_up` ne s'étale que de
0.398 à 0.599. Le premier seuil était au 91ᵉ centile, le second n'était jamais
atteint. La stratégie ne prenait **aucun** trade, sans erreur ni avertissement.

Les portes sont désormais des **quantiles** calibrés sur la distribution des
prédictions du modèle dans sa propre fenêtre d'entraînement — recalculée à
chaque réentraînement, donc jamais dérivée. « Prendre les 10 % de barres où ce
modèle est le plus confiant » garde son sens quel que soit le symbole, le
timeframe ou le millésime du modèle.

**Un backtest quadratique.** `score()` tronquait le frame de features à la
barre courante puis sélectionnait 464 colonnes, matérialisant tout l'historique
visible à chaque barre. En découpant la ligne AVANT de choisir les colonnes, on
passe d'un backtest qui ne terminait pas à ~26 ms par barre.

## 6. Le cache de features et sa légitimité

`prepare_for_backtest` calcule les features une fois sur la série entière ;
`score()` lit la ligne de la barre courante. C'est une fuite si — et seulement
si — les features ne sont pas causales.

C'est exactement ce que vérifie
`tests/test_features_smc.py::test_aucune_fuite_temporelle`, écrit avant la
stratégie précisément pour la rendre possible :
`tests/test_strategy_smc_ml_edge.py::test_le_cache_ne_lit_jamais_le_futur` le
re-vérifie au niveau de la stratégie, en comparant les features vues avec cache
et celles vues par un calcul en temps réel, barre par barre.

Sans cette garantie, les chiffres du §1 ne vaudraient rien — et la tentation
serait grande de les croire, puisqu'ils sont flatteurs sur BTC.
