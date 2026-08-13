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

## 2. Élargir l'échantillon

Non traité. C'est une campagne de collecte de données (symboles, timeframes),
pas un travail de code, et elle ne change rien tant que le point 1 n'est pas
soldé — recalibrer sur un échantillon plus large des paramètres qu'on sait faux
ne ferait que déplacer le problème.

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

## 5. Bilan des quatre pistes

| piste | verdict |
|---|---|
| 1. Recalibrer les neuf stratégies | **20 couples sur 36 étaient faux** ; recalibrés, **4 seulement** donnent un candidat exploitable |
| 2. Élargir l'échantillon | non traité — mais le point 1 vient de montrer que c'est le **verrou** |
| 3. Fréquences mesurées (§79) | **close** — le mécanisme est inerte, les candidats sont mono-classe |
| 4. R/R net et funding (L2) | **livré**, off par défaut, en attente de mesure |

### Le point 2 change de statut

Il était listé troisième par prudence. La recalibration le remonte en tête :
**14 couples sur 20 dégénèrent en configurations à moins de dix trades OOS**.
Ce n'est pas un défaut des stratégies, c'est la métrique de sélection qui
récompense une rareté ne survivant pas au changement de période — le dépôt
l'avait déjà écrit, ce chantier le mesure sur vingt cas d'un coup.

Tant que les fenêtres OOS restent à ~2 000 barres et l'univers à deux symboles,
**aucune optimisation ne produira autre chose**. Plus de symboles et des
fenêtres plus longues ne sont pas un confort : c'est la condition pour que le
reste ait un sens.

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
