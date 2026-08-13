# L4 — la hiérarchie de liquidité de §77 ne tient pas

Le plan annonçait en §3.5 : « poser une probabilité à la main puis maximiser
dessus, c'est optimiser une croyance ». Le mécanisme a été construit quand même,
avec des poids déclarés et un chemin pour les remplacer par des fréquences
mesurées. Puis mesuré.

```bash
python scripts/measure_target_quality.py --data data/ohlcv
```

---

## 1. §77 est contredit là où l'échantillon est utilisable

Profit factor par classe de cible visée, fenêtre OOS :

| cas | `SWING` | `PREV_DAY` | `PREV_WEEK` | `INTERNAL` |
|---|---:|---:|---:|---:|
| BTC 4 h | **2,157** (n=19) | 2,066 (n=6) | **0,136** (n=5) | 0,284 (n=10) |
| ETH 4 h | 1,044 (n=23) | 0,474 (n=9) | **1,495** (n=7) | 0,915 (n=11) |
| ETH 1 h | 0,469 (n=28) | 1,058 (n=6) | 0,000 (n=1) | 0,690 (n=6) |

L'ordre de la spécification est `HTF_EXTERNAL > PREV_WEEK > PREV_DAY > SESSION >
SWING > INTERNAL`. Le seul compartiment à échantillon exploitable (19 à 28
trades) est **`SWING`** — le rang le plus bas de la hiérarchie — et c'est le
meilleur sur BTC 4 h, où il bat `PREV_WEEK` d'un facteur **16**.

Les classes nobles comptent 1 à 7 trades. Elles ne permettent de conclure ni
dans un sens ni dans l'autre, ce qui est déjà une conclusion : **la hiérarchie
est postulée, elle n'est pas mesurée, et là où on peut la vérifier elle
s'inverse.**

Explication plausible, non vérifiée ici : une cible lointaine est rarement
atteinte, et son R/R théorique flatteur ne compense pas sa faible fréquence.
C'est exactement ce que §79 (`probabilité × gain`) prétend arbitrer — mais avec
des probabilités inventées, l'arbitrage se fait dans le mauvais sens.

## 2. `target_mode: expected_value` est rejeté

PnL net, les deux fenêtres :

| cas | actuel IS | EV IS | actuel OOS | EV OOS |
|---|---:|---:|---:|---:|
| BTC 1 h | −104,8 | **−12,5** | −211,4 | −263,6 |
| BTC 4 h | **+327,0** | −69,8 | −58,1 | −68,4 |
| ETH 1 h | −170,9 | −198,6 | −155,9 | **−124,2** |
| ETH 4 h | −108,2 | **+28,7** | −104,4 | −192,7 |

Il gagne sur une fenêtre et perd sur l'autre, dans les quatre cas, sans jamais
gagner sur les deux. Sur BTC 4 h il détruit le seul résultat IS franchement
rentable (+327 → −70) — le même symptôme que `no_pullback` en L3.

**Rejeté. `target_mode` reste `nearest` par défaut.**

## 3. `max_stop_atr` (§23) : sans effet

Résultats **identiques au bit près** sur trois cas sur quatre : avec le
`sl_buffer_atr` actuel, la distance entrée→stop ne dépasse quasiment jamais
4 ATR. Le plafond ne mord pas. Sur ETH 1 h, où il mord marginalement, il est
légèrement négatif.

Ce n'est pas un défaut du plafond : c'est que le problème qu'il vise
(« le prix a quitté son POI ») ne se manifeste pas sous cette forme ici.
Laissé à `0` (off), disponible dans `param_space`.

---

## 4. Ce qui est livré et qui reste utile

`app/core/smc_quality.py` — fonctions pures et causales, 32 tests :

| §  | fonction | statut |
|---|---|---|
| 77 | `classe_liquidite`, `POIDS_CLASSE` | livré, **hiérarchie non validée** |
| 78/79 | `valeur_attendue`, `meilleure_cible` | livré, **rejeté par la mesure** |
| 67 | `dealing_range` (avec sa provenance) | livré |
| 66 | `irl_erl` — liquidité interne vs externe | livré |
| 65 | `inducement` | livré |
| 83 | `qualite_balayage` | livré |
| 84 | `qualite_displacement` | livré |
| 15 | `taux_mitigation` (continu, pas booléen) | livré |
| 85 | `rang_fvg` — `MSS_FVG` > `HTF_FVG` > … | livré |
| 86 | `qualite_order_block` | livré |
| 91 | `opens_calendaires` | livré |

Plus `by_target_class` dans `BacktestResult`, et la conservation de la **clé**
calendaire (`pdh` / `pwl`…) au lieu d'un niveau anonyme — sans elle, la
hiérarchie n'aurait même pas été mesurable.

Ces briques restent des **entrées candidates du score unique de L6** : leur
échec ici porte sur le CIBLAGE, pas sur leur valeur comme features de
qualification. C'est ce que L6 et L8 doivent trancher, séparément.

---

## 5. Ce que ce lot établit

1. **La hiérarchie de liquidité de §77 n'est pas validée**, et s'inverse sur le
   seul compartiment à échantillon exploitable.
2. **Un arbitrage `probabilité × gain` avec des probabilités postulées dégrade
   le résultat.** §3.5 du plan avait raison de le prévoir ; c'est maintenant
   mesuré, pas argumenté.
3. La voie qui reste ouverte est celle que §3.5 décrivait : **estimer les
   fréquences d'atteinte par classe en walk-forward** (L8) et rebrancher
   `meilleure_cible` dessus. Le code l'accepte déjà — `proba` est un paramètre.
4. Deux règles rejetées coup sur coup (L3 `no_pullback`, L4 `expected_value`)
   par le même mécanisme : **gagner sur une fenêtre et perdre sur l'autre**. La
   vérification croisée IS/OOS instaurée en L3 a payé deux fois.
