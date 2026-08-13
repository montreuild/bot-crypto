# L8 / L10 — seize mécanismes passés au harnais, zéro validé

Le plan posait en §6 la condition de réussite, et sa contrepartie :

> Si le point 1 échoue après L8, **la conclusion à publier est que la stratégie
> SMC règles-seules n'a pas d'edge exploitable dans cet espace de paramètres** —
> pas qu'il faut ajouter un module de plus.

C'est ce qui s'est produit. Ce document l'établit.

```bash
python scripts/measure_ablation_v3.py --data data/ohlcv
```

Chaque mécanisme est activé **seul** par-dessus la configuration du YAML, sur
BTC et ETH × {1 h, 4 h}, et mesuré sur les deux fenêtres de la découpe 65/35.

---

## 1. La règle, et pourquoi elle est celle-là

**Un mécanisme n'est validé que s'il améliore le PnL sur les DEUX fenêtres,
dans une majorité de cas.**

Elle n'est pas arbitraire, elle est le produit de deux faux positifs attrapés
pendant ce chantier :

- **L3 `no_pullback`** balayait 4 cas sur 4 en OOS — perte réduite de 83 % sur
  ETH 1 h, drawdown divisé par 3,4 — et ne répliquait pas en IS (2/4, et il
  détruisait le seul résultat rentable de la campagne).
- **L4 `expected_value`** gagnait sur une fenêtre et perdait sur l'autre dans
  les quatre cas, sans jamais gagner sur les deux.

Les deux avaient été choisis après lecture des résultats. Une règle qui ne gagne
que là où elle a été choisie n'est pas une règle, c'est un souvenir.

---

## 2. Le tableau

| mécanisme | cas gagnants sur **les deux** fenêtres | verdict |
|---|:--:|---|
| L1 sorties partielles | 1 / 4 | non concluant |
| L1 trailing structurel | 1 / 4 | non concluant |
| L1 trailing ATR seul | **2 / 4** | non concluant |
| L3 porte `direction` | **2 / 4** | non concluant |
| L3 porte `no_pullback` | **2 / 4** | non concluant |
| L4 cible valeur attendue | 0 / 4 | non concluant |
| L4 plafond stop 4 ATR | 0 / 4 | non concluant |
| L6 porte tier D | 0 / 4 | non concluant |
| L6 sizing par tier | 1 / 4 | non concluant |
| L10 SMT filtre | 0 / 4 | **inerte** |
| L10 Silver Bullet | 0 / 4 | **inerte** |
| L10 AMD | 0 / 4 | **inerte** |
| L10 Breaker retest | 1 / 4 | non concluant |
| L10 BPR reversal | 0 / 4 | non concluant |
| L10 Rejection blocks | 0 / 4 | non concluant |
| L10 Sweeps calendaires | 0 / 4 | non concluant |

**Zéro sur seize.** Le meilleur score est 2/4 — soit exactement ce qu'on
obtiendrait à pile ou face.

### Trois modules sont inertes, pas mauvais

`SMT filtre`, `Silver Bullet` et `AMD` affichent **+0,0 sur les deux fenêtres** :
activer leur drapeau ne change aucun trade. Ils dépendent de paramètres
compagnons (`smt_correlate_path`, seuils de bonus) que l'activation seule ne
fournit pas. Leur ligne du tableau ne dit donc rien de leur valeur — elle dit
que leur drapeau seul ne suffit pas à les mettre en marche. À reprendre si
quelqu'un veut vraiment les tester ; ce n'est pas une priorité au vu du reste.

### Deux modules coûtent cher

`Breaker retest` (−168,6 IS / −41,8 OOS sur le cas montré) et
`Sweeps calendaires` (−140,2 / −108,1) dégradent nettement. Le YAML de
`smart_money` documentait déjà le premier comme négatif (« −163 USDC / 220
trades 4h ») : **la mesure le confirme sur un protocole plus strict.**

---

## 3. Ce que ça veut dire, et ce que ça ne veut pas dire

### Ce qui est établi

Sur `smart_money`, dans l'espace de paramètres actuel, sur BTC et ETH en 1 h et
4 h, **aucun des seize mécanismes — dont neuf construits par ce plan — ne
produit une amélioration qui survit au changement de fenêtre.** La stratégie
reste perdante en OOS dans les quatre cas, comme
`docs/STRATEGY_SMC_ML_EDGE.md` §3 quinquies l'avait établi avant ce chantier.

Ajouter un dix-septième mécanisme n'a aucune raison de changer ça. C'est
exactement l'avertissement de §111 de la spécification — « PLUS DE CONCEPTS ≠
MEILLEURE STRATÉGIE » — vérifié sur seize essais.

### Ce qui n'est pas établi

- **Que ces mécanismes soient sans valeur en général.** Quatre cas et deux
  symboles ne permettent pas de le dire. Le protocole exclut les faux positifs ;
  il ne prouve pas l'absence d'effet.
- **Que le signal SMC soit sans valeur.** Il reste solidement mesuré par
  ailleurs — `docs/ML_ABLATION_SMC.md` §2 bis, six actions décorrélées. C'est sa
  **conversion en trades** qui échoue, et ce chantier n'a pas trouvé où.
- **Que rien n'ait été gagné.** Voir ci-dessous.

---

## 4. Ce que ce chantier a réellement produit

Aucun gain de performance. Mais six choses qui n'existaient pas :

1. **Un défaut de parité corrigé** (L5) : neuf stratégies avaient un filtre HTF
   inerte en backtest et actif en live. Leurs `optimizer_results` sont à
   recalibrer — ce qui veut dire que tous les réglages publiés pour elles
   reposaient sur une simulation qui ne correspondait pas à la production.
2. **Deux défauts de comptabilité corrigés** (L0) : frais d'entrée écrasés,
   `total_pnl` confondu avec la variation d'équité.
3. **La sortie partielle** (L1), verrou architectural ouvert depuis
   `docs/SPECS_SMC_ICT_ET_ADAPTATIVE.md` §1, avec sa parité live.
4. **Une convention BOS / MSS / CHoCH** (L3), unique dans le dépôt.
5. **Sept axes de mesure** qui n'existaient pas : `by_exit_reason`,
   `by_structure_state`, `by_sequence_type`, `by_tier`, `by_target_class`,
   la ventilation des coûts, et le journal de décision par trade.
6. **La règle des deux fenêtres**, et le harnais qui l'applique en une commande.

Le point 1 justifie à lui seul le chantier : il invalidait silencieusement une
partie des mesures publiées du dépôt.

---

## 5. Ce qu'il faut faire maintenant — et ne pas faire

**Ne pas** ajouter de mécanisme SMC supplémentaire. Seize essais suffisent à
dire que le rendement marginal est nul dans cet espace.

Dans l'ordre, ce qui reste plausible :

1. **Recalibrer les neuf stratégies touchées par le correctif HTF** (L5). Leurs
   paramètres ont été optimisés contre un filtre inerte ; personne ne sait ce
   qu'ils valent une fois le filtre actif. C'est le seul travail dont on sait
   par construction qu'il porte sur des chiffres faux.
2. **Élargir l'échantillon avant toute nouvelle conclusion.** Quatre cas, deux
   symboles. `docs/STRATEGY_SMC_ML_EDGE.md` §4 le demandait déjà et personne ne
   l'a fait. Les compartiments qui intéressent (`*_WARNING` de §62, classes de
   liquidité nobles de §77) comptent 1 à 7 trades : ils ne sont pas mesurables
   aujourd'hui, quel que soit le mécanisme.
3. **Estimer les fréquences d'atteinte par classe de liquidité** en
   walk-forward (§79), et rebrancher `meilleure_cible` dessus — `proba` est déjà
   un paramètre. C'est la seule piste de L4 qui n'a pas été invalidée, parce
   qu'elle n'a pas encore été essayée avec des chiffres mesurés.
4. **L2 (R/R net et funding)**, non traité par ce chantier. L1 a montré
   qu'un profit factor de 1,147 pouvait correspondre à un PnL net négatif : tant
   que le coût n'entre pas dans la décision d'entrée, on optimise une géométrie
   dont on ne paie pas le prix.
