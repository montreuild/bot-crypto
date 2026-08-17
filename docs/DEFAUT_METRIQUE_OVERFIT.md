# L'overfit résiduel n'existait pas — c'était la métrique

`docs/RECALIBRATION_HTF.md` §3 signalait un signal inexpliqué : **cinq couples
gardaient un `overfit` saturé à 10,0 malgré des échantillons larges**, dont
`pullback_trend` BTC 1 h avec 110 trades. J'en concluais que « le
surapprentissage n'est pas qu'un problème d'échantillon » et que c'était la
piste qui restait.

**C'était faux.** Vérification faite, `overfit = 10,0` ne mesure aucun degré de
surapprentissage. Et en creusant, la métrique s'est révélée porter deux défauts
distincts, dont le second fausse le classement de l'optimiseur.

---

## 1. Le symptôme : `10,0` veut dire « score OOS ≤ 0 »

```python
return round(min(is_score / max(oos_score, 0.01), 10.0), 2)
#                              ^^^^^^^^^^^^^^^^^^^^
```

Le garde `max(oos_score, 0.01)` empêche la division par zéro — mais il
transforme **tout score OOS non positif** en un dénominateur de 0,01. Le rapport
vaut alors `100 × is_score`, donc sature à 10,0 dès que le score IS dépasse 0,1.

Vérifié sur les 27 couples recalibrés :

| `overfit` | signification réelle | n | dont `oos_score > 0` |
|---|---|---:|---:|
| **0,0** | `is_score ≤ 0` | 9 | 6 |
| 0,01–9,99 | vrai ratio, les deux scores positifs | 13 | 13 |
| **10,0** | `oos_score ≤ 0,01` | 5 | **0** |

Les cinq couples « surappris malgré 110 trades » sont exactement, et
uniquement, ceux dont le score OOS est négatif. **Le fait mesuré était le signe
du score, pas un degré de dégradation.**

## 2. Le vrai défaut : `0,0` est la MEILLEURE valeur, et signale un échec

```python
if is_score <= 0:
    return 0.0
```

Quand la configuration échoue **déjà en apprentissage**, la fonction rend la
valeur qui se lit « aucun surapprentissage ». Conséquence, sur des données
réelles :

| couple | PnL OOS | Sharpe | `overfit` |
|---|---:|---:|---:|
| `multi_tf_sr` ETH 4 h | **+371,7** | 1,35 | **0,0** |
| `fear_momentum` BTC 1 h | **−168,4** | **−2,48** | **0,0** |

Même note pour la meilleure et l'une des pires. L'échelle n'est pas monotone :
une stratégie qui échoue partout (0,0) est mieux notée qu'une qui apprend puis
se dégrade (10,0) — alors que la première est pire.

## 3. Le défaut conséquent : la pénalité récompense les scores négatifs

```python
if ovf > 2.5:
    return oos * (2.5 / ovf)
```

`2.5 / 10 = 0,25`. Sur un score **négatif**, multiplier par 0,25 le **rapproche
de zéro** : c'est une amélioration du classement, pas une pénalité.

Reconstitué depuis la campagne :

| couple | `overfit` | score brut | score « pénalisé » |
|---|---:|---:|---:|
| `fear_momentum` BTC 4 h | 10,0 | **−0,433** | **−0,108** |
| `fear_momentum` ETH 4 h | 10,0 | −0,394 | −0,099 |
| `pullback_trend` BTC 1 h | 10,0 | −0,085 | −0,021 |
| `supertrend_macd` ETH 1 h | — | −0,099 | −0,099 |

`fear_momentum` BTC 4 h est **quatre fois pire** que `supertrend_macd` ETH 1 h en
brut, et se classe **devant** lui après « pénalité ». Sur les huit configurations
perdantes de la campagne, l'ordre réel et l'ordre utilisé diffèrent à partir du
4ᵉ rang.

L'effet est borné — ces configurations sont de toute façon refusées par
`beats_baseline`, qui exige un PnL OOS positif. Mais l'optimiseur les compare
entre elles pendant sa recherche, et le classement qu'il utilise est faux.

---

## 4. Correction

**`overfitting_ratio` rend `NaN` sur les trois cas dégénérés** — run non
significatif, score IS ≤ 0, score OOS ≤ 0 — au lieu de `0.0` et de la saturation
à 10,0. Un ratio n'a de sens que si les deux termes sont positifs ; rendre un
nombre les ferait entrer dans un classement où ils n'ont rien à faire.

**`_penalized_score` n'applique la pénalité qu'à un score positif.** `NaN` et
score ≤ 0 laissent la valeur intacte — c'est déjà le chemin prévu pour `NaN`, on
lui ajoute le cas du signe.

Les deux consommateurs en aval traitent déjà `NaN` correctement :
`_penalized_score` le teste explicitement, et le bandeau
`OptimizerWarnings` (`overfit > 2`) est faux sur `NaN` comme il l'était sur 0,0
— donc aucun avertissement n'apparaît ni ne disparaît à tort.

`tests/test_overfit_metric.py` verrouille les deux, avec les chiffres réels de
la campagne.

---

## 5. Ce que ça change aux conclusions publiées

**`docs/RECALIBRATION_HTF.md` §3, troisième réserve, est retirée.** « Le
surapprentissage n'est pas qu'un problème d'échantillon » n'était pas un
résultat : c'était une lecture erronée d'un indicateur de signe.

Ce qui reste vrai, et que la correction rend enfin lisible : **huit couples sur
27 ont un score OOS négatif**, dont les quatre de `fear_momentum`. C'est le même
fait, énoncé sans détour — et il était déjà dans la colonne « PnL OOS ».

**Aucun candidat retenu ne change.** Les quinze qui passaient `beats_baseline` le
passent toujours : le gate exige un PnL OOS positif, donc aucun d'eux n'était
concerné par les cas dégénérés.
