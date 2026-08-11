# `smc_ml_edge` — le backtest qui manquait

`docs/ML_ABLATION_SMC.md` se terminait par un avertissement : tout y était de
l'AUC, donc de la qualité de classement, et rien ne disait qu'un edge
directionnel de 0.60 se convertirait en rentabilité nette de frais. Ce
document répond à cette question.

**La réponse est non, pas en l'état.** La stratégie est nettement profitable
sur BTC/USDC et perdante sur ETH/USDC. Un résultat qui ne tient que sur un
symbole, avec 22 trades, n'est pas un edge : c'est du bruit qui a bien tourné.

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
