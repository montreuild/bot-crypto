# Suite de `ABLATION_SMC_V3.md` §5 — les quatre pistes, mesurées

`docs/ABLATION_SMC_V3.md` §5 recommandait quatre travaux après le constat
« seize mécanismes, zéro validé ». Ce document les traite dans l'ordre annoncé
et publie ce que chacun donne.

---

## 1. Les neuf stratégies dont le filtre HTF était inerte

**C'était « le seul travail dont on sait par construction qu'il porte sur des
chiffres faux ».** Confirmé, et l'ampleur est plus grande que prévu.

```bash
python scripts/recalibrate_htf_strategies.py --data data/ohlcv --etape 1
```

Chaque couple (stratégie, symbole, timeframe) est joué deux fois : une fois avec
le repli de rééchantillonnage neutralisé — le comportement d'avant L5 — et une
fois avec.

| stratégie | couples | qui changent | Δ PnL cumulé | Δ trades |
|---|---:|---:|---:|---:|
| `breakout` | 4 | **4** | +24,3 | −15 |
| `fear_momentum` | 4 | **4** | +272,5 | −169 |
| `multi_tf_sr` | 4 | **4** | +41,9 | −40 |
| `supertrend_macd` | 4 | **3** | **−168,7** | −10 |
| `breakout_filtreHor` | 4 | 2 | +11,6 | −3 |
| `trend` | 4 | 2 | +53,6 | −10 |
| `pullback_trend` | 4 | 1 | +11,0 | −1 |
| `gemini_trend_follow` | 4 | 0 | — | — |
| `tvr_trend` | 4 | 0 | — | — |

**20 couples sur 36 changent de résultat.** Sept stratégies sur neuf sont
touchées ; deux ne le sont pas (filtre désactivé dans leur YAML, ou jamais
mordant), et le savoir évite de les recalibrer pour rien.

Les cas les plus déplacés :

| cas | avant | après |
|---|---|---|
| `fear_momentum` ETH 4 h | 269 trades, −558,8 | 223 trades, −447,9 |
| `supertrend_macd` BTC 4 h | **11 trades, +242,0** | **4 trades, +143,6** |
| `supertrend_macd` ETH 4 h | 1 trade, +77,6 | **0 trade** |
| `multi_tf_sr` ETH 4 h | 19 trades, −45,8 | 6 trades, **+14,2** |
| `trend` ETH 1 h | 59 trades, −5,2 | 50 trades, **+45,0** |

Le filtre actif **réduit systématiquement le nombre de trades** (−248 au total,
sans une seule exception) et déplace le PnL dans les deux sens. `supertrend_macd`
BTC 4 h est le cas le plus grave : un `optimizer_results` sélectionné sur onze
trades dont **sept n'existent pas en production**. `multi_tf_sr` ETH 4 h et
`trend` ETH 1 h passent de perdants à gagnants — leurs réglages publiés sont
donc faux dans l'autre sens, ce qui n'est pas mieux.

**Aucun de ces vingt jeux de paramètres n'a été sélectionné contre le
comportement réel du bot.**

### La recalibration — 4 candidats exploitables sur 20

```bash
python scripts/recalibrate_htf_strategies.py --data data/ohlcv --etape 2 \
    --trials 30 --njobs 4
```

Outillage du dépôt : `split_is_oos`, `OptimizerSearchEngine`, sélection sur
l'IS et jamais sur l'OOS, puis lecture au travers de `beats_baseline` contre
les paramètres actuels **corrigés**.

| stratégie | cas | trades OOS | PnL OOS | Sharpe | gate |
|---|---|---:|---:|---:|:--:|
| `fear_momentum` | BTC 4 h | 59 | +7,1 | −0,42 | **OK** |
| `fear_momentum` | ETH 1 h | 49 | +52,1 | 0,17 | **OK** |
| `fear_momentum` | ETH 4 h | 75 | +3,9 | −0,19 | **OK** |
| `pullback_trend` | BTC 1 h | 29 | +47,8 | 0,48 | **OK** |
| `breakout` | ETH 4 h | **4** | +128,5 | 2,47 | refusé |
| `multi_tf_sr` | ETH 4 h | **4** | +121,8 | 1,83 | refusé |
| `trend` | BTC 1 h | **7** | +90,0 | 2,87 | refusé |
| `breakout_filtreHor` | BTC 1 h | **2** | +56,2 | 7,83 | refusé |
| … | | | | | |

**4 sur 20 passent. 14 sur 20 comptent moins de dix trades OOS.**

C'est exactement le biais que `docs/STRATEGY_SMC_ML_EDGE.md` §3 quinquies
documente : `composite_score` ne refuse un jeu de paramètres qu'en dessous d'un
seuil de non-dégénérescence bas, donc l'optimum IS est souvent une configuration
**hyper-sélective** — peu de trades, tous excellents en IS, et plus rien à
échantillonner en OOS. Les Sharpe de 7,83 sur deux trades et de 5,44 sur quatre
en sont la signature.

Les quatre qui survivent sont précisément celles qui gardent un échantillon
réel (29 à 75 trades) : `fear_momentum` et `pullback_trend`.

⚠ **`beats_baseline` est un test RELATIF.** Il dit « mieux que les paramètres
actuels », pas « rentable » : `fear_momentum` BTC 4 h le passe avec un Sharpe
de −0,42. Ce qu'on établit ici est que ces quatre jeux valent mieux que ceux qui
tournent, pas qu'ils vaillent la peine d'être promus.

### Ce qui n'est PAS fait, délibérément

**Les YAML n'ont pas été modifiés.** Choisir d'appliquer un paramétrage est une
décision de trading, pas un correctif : elle change ce que le bot engage en
production. Les vingt jeux candidats sont dans
`scripts/_recalibrage_htf.json`, prêts à être appliqués via le chemin normal du
dépôt (`apply_best_params`) — ou à être écartés.

Ce que la mesure autorise à dire, en revanche : **les `optimizer_results`
publiés pour ces vingt couples sont invalides** et devraient être marqués comme
tels, qu'on les remplace ou non.

### Un faux positif attrapé dans le protocole lui-même

La première version du script neutralisait le repli en re-important
`htf_trend` depuis un module qu'elle venait de patcher : la fonction s'appelait
elle-même. La récursion était **avalée par le `try/except` de l'Engine**, qui
journalise et continue — le script produisait donc zéro trade partout, sans
qu'aucune erreur ne remonte, et les chiffres auraient été plausibles et faux.

Corrigé, et `_verifier_le_patch` échoue désormais bruyamment si le patch ne fait
pas exactement ce qu'il prétend. Un outil de mesure a besoin de ses propres
garde-fous ; c'est le troisième faux positif de ce chantier, et le seul qui
portait sur l'instrument plutôt que sur le résultat.

---

## 2. Élargir l'échantillon — la limite était de mon fait

J'ai écrit que les fenêtres OOS de ~2 000 barres étaient le verrou. **Elles
l'étaient, mais parce que je tronquais les séries à 12 000 barres** (`--barres`,
puis 6 000 pour la recalibration). Les données disponibles sont beaucoup plus
larges :

| série | barres | période |
|---|---:|---|
| BTC/USDC 1 h | **51 909** | 2020-03 → 2026-08 |
| ETH/USDC 1 h | **47 191** | 2020-10 → 2026-08 |
| BTC & ETH 4 h | 15 769 | 2018-12 → 2026-08 |
| BTC & ETH 1 j | 2 630 | 2018-12 → 2026-08 |
| **123 actions SBF 120, journalier** | **5 000 à 6 800 chacune** | **2000 → 2026** |

Soit 4× plus de barres en crypto 1 h, et surtout **123 instruments actions dont
l'historique remonte à 2000** — l'échantillon décorrélé que
`docs/ML_ABLATION_SMC.md` §2 bis utilisait déjà pour valider le signal SMC.
XRP existe mais ne remonte qu'à 2025 (2 891 barres en 1 h) ; SOL est vide.

**L'effet est immédiat et mesuré.** Sur l'historique complet, `breakout`
BTC 1 h produit **58 à 125 trades OOS** là où la recalibration tronquée en
donnait 5. Les « optima dégénérés à moins de dix trades » que j'attribuais à la
métrique de sélection sont, au moins en partie, un artefact de la fenêtre que
j'avais choisie.

⚠ **Cela affaiblit une conclusion du chantier.** Les campagnes L0–L8 ont toutes
tourné sur 12 000 barres. Elles ne sont pas fausses — les comparaisons y étaient
internes et à données égales — mais leurs échantillons OOS (40 à 130 trades)
étaient inutilement petits, et les compartiments fins (`*_WARNING` à 3-5 trades,
classes de liquidité nobles à 1-7) auraient probablement été mesurables sur
l'historique complet. **Toute reprise doit partir de l'historique entier, pas de
`--barres 12000`.**

---

## 3. Des fréquences d'atteinte mesurées plutôt que postulées

```bash
python scripts/measure_target_probabilities.py --data data/ohlcv
```

Protocole en walk-forward strict : les fréquences sont **comptées sur l'IS** et
**appliquées sur l'OOS**, sans jamais être ré-estimées.

### Les fréquences mesurées contredisent §77, de nouveau

| classe | postulé §77 | BTC 1 h | BTC 4 h | ETH 1 h | ETH 4 h |
|---|---:|---:|---:|---:|---:|
| `PREV_DAY` | 0,70 | 0,70 | **0,47** | 0,70 | **0,50** |
| `SWING` | 0,40 | **0,34** | **0,55** | **0,27** | 0,39 |
| `INTERNAL` | 0,25 | **0,52** | **0,44** | **0,36** | **0,37** |

`INTERNAL` — le rang le plus bas de la hiérarchie — est atteint **1,5 à 2 fois
plus souvent** que la spécification le suppose, et sur BTC 1 h il dépasse
`SWING` qui est censé lui être supérieur. `PREV_DAY` est atteint bien moins
souvent qu'annoncé sur les timeframes hauts.

C'est la deuxième mesure indépendante qui contredit §77, après `by_target_class`
en L4. Le sens est cohérent : **les cibles lointaines sont rarement atteintes**,
et un barème qui les valorise le plus valorise ce qui arrive le moins.

### Mais injecter ces fréquences ne change **aucune** décision

| cas | `nearest` | `postulé` | `mesuré` |
|---|---:|---:|---:|
| BTC 1 h | −203,8 | −259,3 | **−259,3** |
| BTC 4 h | −76,9 | −47,2 | **−47,2** |
| ETH 1 h | −148,7 | −115,8 | **−115,8** |
| ETH 4 h | −101,8 | −190,5 | **−190,5** |

`postulé` et `mesuré` donnent des résultats **identiques au centime** dans les
quatre cas — même nombre de trades, même PnL, même drawdown. Les fréquences ont
pourtant changé du simple au double sur `INTERNAL`.

L'explication est mécanique : à une décision donnée, les cibles candidates
appartiennent presque toujours à **une seule classe**. Il n'y a rien à
arbitrer, donc le poids n'a aucune prise. Le mécanisme de §79 n'est pas faux —
il est **inerte** sur cette stratégie.

**La piste est close.** §78 et §79 ne peuvent rien apporter tant que le ciblage
ne produit pas de candidats de classes différentes, ce qui est une propriété du
détecteur, pas du barème.

### Un défaut du protocole de L4, trouvé ici

`docs/MESURE_HIERARCHIE_LIQUIDITE.md` comparait `actuel` (sans cibles
calendaires) à `expected_value` (**avec**). Les deux différaient donc par deux
choses, et l'échec attribué au mode de ciblage venait peut-être de l'ajout des
cibles calendaires.

Corrigé ici : les trois variantes partagent `use_calendar_liquidity: targets`.
Avec le confondant retiré, `postulé` bat `nearest` sur **2 cas sur 4** au lieu
de 0. Ça ne le valide pas — la règle des deux fenêtres reste à satisfaire —
mais **la conclusion de L4 avait été tirée sur une comparaison biaisée**, et il
faut le dire.

---

## 4. L2 — le R/R passe au net, les perpétuels paient un funding

Livré. `app/core/trade_economics.py` ne réimplémente aucune formule monétaire :
il compose celles d'`execution.py`, qui reste la source unique partagée
backtest ↔ live, et les applique **au moment du choix** plutôt qu'à la clôture.

**§27 — un perpétuel ne s'emprunte pas.** `venue_borrow_rate` rend désormais 0
sur une venue `perp`, et le portage passe par `funding_cost` : périodes
discrètes (8 h chez OKX) au lieu d'un intérêt composé continu, et surtout **un
signe** — un funding négatif est encaissé, pas payé. Facturer un perp au
`borrow_rate_daily` donnait un coût de portage toujours positif et de la
mauvaise magnitude. Tout backtest perp antérieur porte donc un coût de portage
faux.

**§2 §4 — la décision passe au net.** `min_net_rr` et `cost_multiple` filtrent à
l'entrée. Le second remplace avantageusement `min_gain_pct` : un seuil de gain
absolu de 0,4 % couvre largement les frais sur une venue à 0,08 % et pas du tout
sur une action à commission fixe et taxe de transaction ; rapporté aux coûts
réels de la venue, le même réglage tient sur les deux marchés.

Le test qui compte est `test_le_rr_net_annonce_correspond_au_r_realise` : un
trade qui touche exactement sa cible doit encaisser le R/R annoncé. Sans cette
identité, le chiffre ne servirait à rien.

**Off par défaut** : les `optimizer_results` du YAML ont été mesurés sur le R/R
brut. `net_rr` est journalisé quand même — c'est l'écart entre l'annoncé et
l'encaissé qu'il faut mesurer avant de refermer la porte.

---

## 4 bis. Faut-il revoir `composite_score` ?

**Oui, mais pas en premier — et le défaut n'est pas dans la formule.**

Le diagnostic est précis. `composite_score` pénalise la rareté par un seul
terme :

```python
trade_factor = min(n / 10, 1.0)      # pondéré 0.10 dans le bundle « qualité »
```

Une configuration à 2 trades perd donc **0,08 sur ~1,0**, pendant que ses
`sharpe_norm` (0,22), `wr` (0,15) et `pf/6` (0,15) peuvent tous saturer à 1,0.
Un Sharpe de 7,83 sur deux trades bat mécaniquement un Sharpe de 0,48 sur
vingt-neuf. C'est ce qu'on observe : 14 recalibrations sur 20 sortent à moins de
dix trades OOS.

Mais la vraie anomalie est ailleurs, et elle est structurelle :

| seuil | valeur | usage |
|---|---:|---|
| `MIN_TRADES_DEGENERATE` | **2** | plancher de la métrique de **sélection** |
| `MIN_SIGNIFICANT_TRADES` | **10** | plancher de **décision** (`beats_baseline`) |

**Le dépôt refuse de promouvoir sous dix trades, mais sélectionne avec un
plancher de deux.** Ce n'est pas un arbitrage de modélisation, c'est un écart
entre deux seuils qui devraient être le même — et c'est par cet écart que
passent les optima hyper-sélectifs.

*Livré* : le plancher est désormais lu dans `optimizer.min_trades`, **défaut
inchangé** (`MIN_TRADES_DEGENERATE`), câblé identiquement dans le chemin
séquentiel et dans les workers parallèles. Le poser à 10 aligne la sélection sur
la décision, en une ligne de config et sans toucher à la formule.

*Pourquoi « pas en premier »* : le point 2 vient de montrer que la moitié du
problème vient de la fenêtre, pas de la métrique. Sur l'historique complet, les
mêmes stratégies produisent 58 à 125 trades OOS au lieu de 5. **Durcir le
plancher avant d'élargir la fenêtre reviendrait à refuser des configurations
que la fenêtre elle-même rendait rares.** L'ordre est : historique complet
d'abord, plancher ensuite, et mesurer les deux séparément.

---

## 4 ter. Faut-il supprimer les `optimizer_results` obsolètes ?

**Non — pas tels quels, et pas pour la raison avancée.** Trois faits, dont deux
que je n'avais pas vus en écrivant §5.

**1. Ils sont indexés par TIMEFRAME seul, pas par symbole.** 37 entrées au
total pour les neuf stratégies, couvrant `5m`, `15m`, `30m`, `1h`, `4h`, `1d`.
La campagne précédente n'a mesuré que `1h` et `4h` sur deux symboles : **les
deux tiers de ces entrées n'ont jamais été évaluées**, ni avant ni après le
correctif. Les supprimer en bloc, ce serait jeter ce qu'on n'a pas mesuré au
motif qu'on a mesuré autre chose.

**2. Supprimer n'est pas revenir à un état neutre.** Sans `optimizer_results`,
le bot retombe sur le bloc `params:` du YAML — des valeurs qui n'ont pas
davantage été validées contre le comportement réel. Le choix n'est pas « garder
du faux » contre « revenir au propre », mais **entre deux jeux non validés**.

**3. Mesuré, l'écart n'est pas systématiquement en faveur des défauts.**
`scripts/measure_optimizer_results_value.py` compare les deux sur l'OOS, à
correctif appliqué et sur l'historique complet. Premiers résultats :

| cas | optimisé | défauts | verdict |
|---|---|---|---|
| `breakout` BTC 1 h | 58 tr, −133,3 | 125 tr, −82,4 | défauts gagnent |
| `breakout` BTC 4 h | 21 tr, **+46,4** | 37 tr, −37,9 | optimisé gagne |
| `breakout` BTC 1 j | 3 tr, **+51,8** | 3 tr, −27,7 | optimisé gagne |
| `breakout` BTC 15 m | 169 tr, −369,6 | 148 tr, −404,5 | optimisé gagne |
| `breakout` ETH 1 h / 4 h / 1 j / 15 m / 5 m | — | — | **identiques** |

Sur les neuf premiers cas : 3 « optimisé gagne », 1 « défauts gagnent », **5
identiques**. Ce dernier chiffre est le plus parlant — pour cinq couples sur
neuf, l'`optimizer_results` **ne change strictement rien** au comportement.

*Ce qu'il faut faire à la place* : **invalider avec provenance, pas
supprimer.** Marquer ces entrées comme mesurées contre un filtre inerte —
l'information utile est « ce chiffre a été produit dans des conditions qui
n'existaient pas en production », et elle disparaît avec la ligne si on
l'efface. Le chemin normal du dépôt (`apply_best_params`, gardé par
`beats_baseline`) les remplacera au fil des recalibrations qui passent la porte.

La mesure complète tourne encore au moment où ces lignes sont écrites ; elle
tranchera entrée par entrée :

```bash
python scripts/measure_optimizer_results_value.py --data data/ohlcv
```

---

## 4 quater. Le harnais rejoué sur l'historique complet

Après la fusion des seuils et le retrait du plafond de 12 000 barres, le
harnais a été rejoué. `--barres` vaut désormais 60 000 par défaut, et `--cas`
prend une liste `symbole:tf`.

```bash
python scripts/measure_ablation_v3.py --data data/ohlcv \
    --cas "BTC_USDC:4h,BTC_USDC:1d,ETH_USDC:4h,ETH_USDC:1d"
```

Références sur historique complet (15 769 barres en 4 h, 2 630 en 1 j) :

| cas | IS | OOS |
|---|---|---|
| BTC 4 h | **+914,0** (89 trades) | −26,6 (57 trades) |
| BTC 1 j | +40,0 (14) | −36,9 (15) |
| ETH 4 h | −102,2 (96) | −168,8 (69) |
| ETH 1 j | −177,3 (15) | −62,3 (11) |

**Verdict : toujours 0 mécanisme validé sur 16.** Le meilleur reste 2 cas
sur 4 (`L1 sorties partielles`), exactement comme sur la fenêtre tronquée.

### Ce que ce rejeu établit, et ce qu'il corrige

**L'ablation était robuste à la fenêtre.** Je craignais que la troncature à
12 000 barres ait biaisé les seize verdicts ; elle ne l'a pas fait. Les mêmes
mécanismes échouent, dans les mêmes proportions, avec des échantillons OOS deux
à trois fois plus grands (médiane 36 trades contre 40–130 auparavant, mais sur
des fenêtres bien plus longues).

**En revanche, la troncature affectait bien la RECALIBRATION** — 5 trades contre
58 à 125 sur l'historique complet (§1). Les deux campagnes ne réagissaient pas
de la même façon au plafond, et il fallait les séparer : c'est l'optimisation
qui souffrait de la fenêtre, pas l'ablation.

**Conséquence sur la fusion des seuils** : le plancher unique à dix trades ne
mordra que rarement sur l'historique complet, où les configurations produisent
naturellement des dizaines de trades. Il reste un filet — il empêche qu'une
étude sur fenêtre courte fabrique à nouveau un Sharpe de 7,83 sur deux trades —
mais il n'est plus le levier que je décrivais en §4 bis. **Le levier était la
fenêtre.**

⚠ Signature d'overfit sur la référence elle-même : BTC 4 h affiche **+914 en IS
pour −26,6 en OOS** sur 89 trades. Ce n'est pas un mécanisme ajouté qui
surapprend, c'est `smart_money` avec ses paramètres publiés.

### Le cas 1 h renverse le verdict — quatre mécanismes validés

```bash
python scripts/measure_ablation_v3.py --data data/ohlcv \
    --cas "BTC_USDC:1h,ETH_USDC:1h" --sortie scripts/_ablation_1h.json
```

51 909 barres pour BTC, 47 191 pour ETH — **le plus grand échantillon
disponible**, et de loin : 199 à 338 trades par fenêtre contre 11 à 96 sur les
autres timeframes.

Références : BTC 1 h **−457,2 IS / −500,6 OOS** (338 / 199 trades),
ETH 1 h **−650,8 / −386,2** (309 / 153).

| mécanisme | BTC ΔIS/ΔOOS | ETH ΔIS/ΔOOS | n OOS méd. | verdict |
|---|---|---|---:|:--:|
| **L3 porte `no_pullback`** | **+108 / +170** | **+170 / +146** | 122 | **VALIDÉ** |
| **L6 sizing par tier** | +28 / +25 | +86 / +93 | 154 | **VALIDÉ** |
| **L3 porte `direction`** | +0 / +65 | +101 / +61 | 148 | **VALIDÉ** |
| **L6 porte tier D** | +1 / +13 | +56 / +29 | 154 | **VALIDÉ** |
| L10 Sweeps calendaires | +10 / +17 | +6 / −5 | 177 | 1 cas sur 2 |
| L1 sorties partielles | −169 / −54 | −44 / −52 | 182 | – |
| L10 Breaker retest | −226 / −122 | −134 / −205 | 327 | – |
| L10 SMT / Silver Bullet / AMD | +0 / +0 | +0 / +0 | 176 | **inertes** |

**Quatre mécanismes gagnent sur les deux fenêtres, sur les deux symboles.**
C'est la première fois depuis le début du chantier qu'un mécanisme satisfait la
règle, et c'est arrivé quand l'échantillon a été multiplié par quatre.

### Deux de mes verdicts sont renversés

**`no_pullback` (L3).** Je l'avais rejeté : il balayait 4 cas sur 4 en OOS sur
la fenêtre tronquée et ne répliquait pas en IS (2/4). Sur l'historique complet
en 1 h, il gagne **sur les deux fenêtres et sur les deux symboles**, avec des
marges qui ne sont pas marginales : +170 sur l'OOS BTC, +146 sur l'OOS ETH.
**Le rejet était un artefact de la fenêtre, pas une propriété du mécanisme.**
La règle des deux fenêtres avait raison de le refuser à l'époque — elle
travaillait sur 49 trades OOS ; elle en a 122 aujourd'hui.

**La porte `direction` (L3).** Je l'avais déclarée sans valeur (« pire sur
BTC 1 h, marginale ailleurs »). Elle valide aussi, quoique plus faiblement
(ΔIS de +0,4 sur BTC — positif mais négligeable, contre +101 sur ETH).

**Les tiers (L6)** n'avaient jamais rien validé : ils passent tous les deux.

### Ce que ça ne dit pas

**Aucun de ces mécanismes ne rend la stratégie rentable.** La référence 1 h perd
−500,6 en OOS sur BTC ; `no_pullback` la ramène à −330,6. C'est une réduction de
perte de 34 %, pas un edge. Les quatre validés **atténuent**, ils ne retournent
rien.

**Le verdict dépend du timeframe.** En 4 h et 1 j, sur historique complet
également, aucun mécanisme ne valide — mais ces cas comptent 11 à 96 trades.
Il est plus probable que le 1 h ait assez d'échantillon pour trancher que
l'inverse ; ça reste une hypothèse, pas un fait.

**Trois modules restent inertes à 199 trades** : SMT, Silver Bullet et AMD
affichent +0,0 partout. Leur drapeau seul ne met rien en marche, et cette fois
l'échantillon ne peut plus servir d'excuse.

**`Breaker retest` est confirmé nettement négatif** sur le plus grand
échantillon : −226/−122 sur BTC, −134/−205 sur ETH. Le YAML le documentait,
c'est désormais établi sur 327 trades.

---

## 5. Bilan des quatre pistes

| piste | verdict |
|---|---|
| 1. Recalibrer les neuf stratégies | **20 couples sur 36 étaient faux** ; recalibrés, **4 seulement** donnent un candidat exploitable |
| 2. Élargir l'échantillon | **fait — et c'était bien le verrou** : ×4 de fenêtre fait passer l'ablation de 0 à 4 mécanismes validés |
| 3. Fréquences mesurées (§79) | **close** — le mécanisme est inerte, les candidats sont mono-classe |
| 4. R/R net et funding (L2) | **livré**, off par défaut, en attente de mesure |

### Le point 2 était bien le verrou — et il l'était deux fois

Il était listé troisième par prudence. Il aurait dû être premier, pour deux
raisons que le chantier a séparées :

- **Sur la recalibration** : 14 couples sur 20 dégénéraient à moins de dix
  trades OOS sur fenêtre tronquée. Sur historique complet, les mêmes
  stratégies produisent 58 à 125 trades.
- **Sur l'ablation** : 0 mécanisme validé sur fenêtre tronquée et sur les
  timeframes hauts ; **4 sur 16 en 1 h sur historique complet**, dont deux que
  j'avais explicitement rejetés.

**Trois de mes conclusions ont été rendues fausses par une seule décision
technique — le `--barres 12000` que j'avais choisi sans le mesurer.** C'est le
défaut le plus coûteux de tout ce chantier, et il n'était ni dans la
spécification, ni dans le code du dépôt.

### Ce qu'il reste à faire

1. **Décider de l'activation des quatre mécanismes validés.** C'est une décision
   de trading : ils réduisent la perte de 25 à 34 % sans la retourner. Les
   activer engage le bot sur un comportement mesuré mais toujours perdant.
2. **Rejouer 4 h et 1 j avec plus de symboles** plutôt que plus de barres : leur
   historique est déjà complet, c'est l'univers qui manque. Les 123 dailies
   SBF 120 sont là pour ça.
3. **Retirer les trois modules inertes** (SMT, Silver Bullet, AMD) ou réparer
   leur activation : à 199 trades, +0,0 partout n'est plus imputable à
   l'échantillon.
4. **Refaire tourner la recalibration sur historique complet** — celle de §1 a
   tourné sur 6 000 barres, et on sait maintenant ce que ça vaut.

Deux constats transverses méritent d'être retenus :

- **§77 a été contredit trois fois**, par trois mesures indépendantes : le
  découpage `by_target_class` (L4), les fréquences d'atteinte (ici), et le fait
  que le seul compartiment à échantillon exploitable soit le rang le plus bas.
  La hiérarchie de liquidité de la spécification ne décrit pas ce marché.
- **Trois faux positifs ont été attrapés** en tout : `no_pullback` (L3),
  `expected_value` (L4, sur une comparaison elle-même biaisée), et la récursion
  du script de mesure. Les deux premiers l'ont été par la règle des deux
  fenêtres ; le troisième par un contrôle ajouté après coup. C'est le contrôle
  qui manque encore le plus souvent.
