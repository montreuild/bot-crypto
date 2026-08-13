# L3 — la mémoire de structure, et le filtre qui n'a pas répliqué

> ## ⚑ CORRIGÉ — les deux portes valident sur un échantillon quatre fois plus grand
>
> Les §2 et §3 de ce document concluent que la porte `direction` « ne vaut
> rien » et que `no_pullback` « n'est pas validé ». **Les deux conclusions sont
> fausses**, et pour la même raison : elles reposent sur des fenêtres tronquées à
> 12 000 barres, soit 49 trades OOS.
>
> Rejoué sur l'historique complet en 1 h (51 909 barres BTC, 47 191 ETH,
> 122 à 148 trades OOS), les deux portes **gagnent sur les deux fenêtres et sur
> les deux symboles** :
>
> | mécanisme | BTC ΔIS/ΔOOS | ETH ΔIS/ΔOOS |
> |---|---|---|
> | `no_pullback` | **+108 / +170** | **+170 / +146** |
> | `direction` | +0 / +65 | +101 / +61 |
>
> La règle des deux fenêtres n'avait pas tort de refuser à l'époque — elle
> travaillait sur ce qu'on lui donnait. **C'est la fenêtre qui était le défaut,
> pas le verdict.** Détail et nuances : `docs/SUITE_ABLATION_V3.md` §4 quater.
>
> Ce qui reste vrai : aucun des deux ne rend la stratégie rentable. `no_pullback`
> ramène la perte OOS BTC de −500,6 à −330,6 — une réduction de 34 %, pas un edge.

Le moteur SMC produit un biais ternaire — `res["_trend_arr"] ∈ {−1, 0, +1}` —
qui bascule dès la première clôture au-delà du dernier swing. Rien n'y distingue
un pullback d'un retournement, et une clôture marginale suffit à retourner le
biais. `app/core/smc_state.py` ajoute la mémoire que §60–§64 réclament.

Puis il fallait répondre à la question que le plan posait : **cette mémoire
change-t-elle une décision ?** La réponse est plus intéressante que prévu, et
elle inclut un faux positif attrapé en route.

```bash
python scripts/measure_structure_gate.py --data data/ohlcv
```

---

## 1. Ce que le module produit

Douze états (§60.1), les niveaux protégés (§64), et une convention interne
unique pour BOS / MSS / CHoCH (§61) — le dépôt n'en avait aucune :

| terme | définition retenue | conséquence |
|---|---|---|
| **BOS** | clôture au-delà du dernier swing **dans le sens** de la structure, avec displacement | continuation, met à jour le biais |
| **MSS** | balayage de liquidité → displacement → cassure du dernier LH (haussier) / HL (baissier) | changement **interne** → `*_WARNING` |
| **CHoCH** | première cassure contraire **sans** displacement suffisant | signal précoce, ne change **rien** |

La conséquence pratique est §62/§63 : un premier MSS contraire ne retourne pas
le biais externe. Il arme un avertissement ; la confirmation exige un nouveau
LH/HL puis un BOS. Si le prix échoue à le former et casse dans le sens initial,
c'est un `FAILED_*_REVERSAL` (§73).

**Causalité prouvée, pas affirmée.** Un swing n'est utilisé qu'à partir de son
`confirmed_at`, jamais de son `index`. `tests/test_smc_state.py` vérifie que la
série calculée sur `df[:k]` est le préfixe exact de celle calculée sur `df`
entier, pour sept valeurs de `k`, états et niveaux protégés compris.

---

## 2. L'état sépare — mais pas là où la spécification le dit

`by_structure_state`, porte désactivée (l'état est journalisé même quand il ne
filtre rien), fenêtre OOS :

| cas | pire compartiment | n | PF | meilleur compartiment | n | PF |
|---|---|---:|---:|---|---:|---:|
| BTC 1 h | `BULLISH_PULLBACK` | 18 | **0,299** | `REVERSAL_BEARISH_PENDING` | 19 | 0,632 |
| BTC 4 h | `REVERSAL_BEARISH_PENDING` | 13 | 0,793 | `BEARISH_WARNING` | 5 | 1,082 |
| ETH 1 h | `BEARISH_PULLBACK` | 20 | **0,386** | `BULLISH_WARNING` | 4 | 1,623 |
| ETH 4 h | `BULLISH_WARNING` | 4 | 0,497 | `REVERSAL_BULLISH_PENDING` | 18 | 1,001 |

Deux lectures :

1. **La dispersion est réelle** — de 0,30 à 1,08 de profit factor entre états
   d'un même cas. L'état n'est pas du bruit.
2. **Les compartiments `*_WARNING` comptent 3 à 5 trades.** Aucune conclusion
   n'est possible dessus, dans un sens ou dans l'autre. Le postulat de §62 —
   « entrer en avertissement est pire qu'entrer en confirmé » — **n'est pas
   testable à cet échantillon**, et le dire est plus utile que de trancher.

Ce qui a un échantillon utilisable (12 à 20 trades) et qui saigne, ce sont les
états `*_PULLBACK`. La spécification ne les désigne pas.

---

## 3. Le filtre qui a semblé marcher, et qui n'a pas répliqué

Trois modes de porte ont donc été mesurés : `direction` (celui de la spec, le
sens de l'entrée doit suivre l'état), `no_pullback` (suggéré par le tableau
ci-dessus, et **absent de la spécification**), et `both`.

Sur la fenêtre OOS, PnL net :

| cas | `off` | `direction` | `no_pullback` | `both` |
|---|---:|---:|---:|---:|
| BTC 1 h | −211,4 | −216,9 | **−123,4** | −130,8 |
| BTC 4 h | −58,1 | −52,7 | **−40,1** | −40,4 |
| ETH 1 h | −155,9 | −107,8 | **−27,1** | −27,1 |
| ETH 4 h | −104,4 | −99,2 | −89,0 | **−77,3** |

`direction` — la porte de la spécification — **ne vaut rien** : pire sur
BTC 1 h, marginal ailleurs. `no_pullback` gagne 4 fois sur 4, réduit la perte de
83 % sur ETH 1 h et y divise le drawdown par 3,4 (−16,4 % → −4,8 %).

C'était trop beau, et pour une raison précise : **cette règle a été choisie
après avoir lu le découpage par état sur cette même fenêtre OOS.** C'est une
sélection sur le jeu de test. La fenêtre IS, elle, n'a pas servi à former
l'hypothèse — elle en est donc la vérification indépendante.

| cas (IS, 7 800 barres) | `off` | `no_pullback` | verdict |
|---|---:|---:|:--:|
| BTC 1 h | −104,8 (PF 0,909) | **−10,4 (PF 1,143)** | ✅ |
| BTC 4 h | **+327,0 (PF 1,473)** | +6,3 (PF 1,090) | ❌ |
| ETH 1 h | −170,9 (PF 0,750) | **−119,6 (PF 0,792)** | ✅ |
| ETH 4 h | **−108,2 (PF 0,912)** | −148,5 (PF 0,787) | ❌ |

**La réplication échoue : 2 sur 4, et sur BTC 4 h la porte détruit le seul
résultat franchement rentable de toute la campagne (+327 → +6).**

Le balayage 4/4 de la fenêtre OOS était un artefact de la sélection. C'est
exactement le mécanisme que `docs/STRATEGY_SMC_ML_EDGE.md` §3 quinquies décrit,
appliqué cette fois à une règle plutôt qu'à un jeu de paramètres.

**`no_pullback` n'est pas validé. Aucune porte n'est activée.**

⚠ Au passage : BTC 4 h IS affiche +327 pour un PF de 1,473 — le seul résultat
nettement rentable produit depuis le début de ce plan, et il est **in-sample**.
À ne pas citer comme une performance.

---

## 4. Ce qui est livré

- `app/core/smc_state.py` — `structure_states(df, res, params)` : douze états,
  niveaux protégés, événements MSS/BOS qualifiés, type de séquence. Passe unique
  O(n) sur des entités déjà produites par `analyze` ; le moteur SMC n'est pas
  modifié.
- Convention interne BOS / MSS / CHoCH, documentée en tête du module et **seule
  référence du dépôt**.
- `structure_gate` ∈ `off` / `direction` / `no_pullback` / `both` dans
  `param_space` — **`off` par défaut**.
- `structure_journal` (défaut **on**) : l'état est journalisé même sans porte.
  Sans ça, on ne pourrait pas mesurer si la porte vaut quelque chose.
- `by_structure_state`, `by_sequence_type`, `by_tier` dans `BacktestResult`.
- `tests/test_smc_state.py` — 19 tests, dont sept de préfixe causal.

1 779 tests passent.

---

## 5. Ce que ce lot établit

1. **La convention BOS/MSS/CHoCH existe enfin**, une seule fois, et les lots
   suivants s'y adossent. C'est l'acquis durable.
2. **La porte de structure de la spécification (§60, mode `direction`) ne vaut
   rien** sur les quatre cas mesurés. Elle ne sera pas activée.
3. **Le postulat de §62 n'est pas testable** avec 3 à 5 trades par compartiment
   d'avertissement. Il faudra plus de symboles avant d'y revenir — c'est un
   objectif pour L8, pas une conclusion.
4. **Un filtre qui balaie 4 cas sur 4 peut ne rien valoir.** La vérification
   croisée IS/OOS l'a montré en une passe. Elle devient obligatoire pour toute
   règle dérivée d'une lecture de résultats dans la suite de ce plan.

La valeur du moteur de structure, à ce stade, est celle d'un **axe de journal et
d'un substrat pour L4 et L6** — pas celle d'un filtre.
