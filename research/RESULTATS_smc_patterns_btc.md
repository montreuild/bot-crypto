# Mesure des motifs SMC — BTC/USDC, 4 timeframes

Première exécution complète de `scripts/analyze_smc_patterns.py` sur BTC/USDC en
**15m, 30m, 1h, 4h**, fenêtre de composés réglée à **2 barres** du TF le plus bas
(= 30 minutes). Étude de mesure : aucun détecteur n'a été modifié.

Reproduire :

```
python scripts/analyze_smc_patterns.py --symbol BTC/USDC --tfs 15m,30m,1h,4h --fenetre-composes 2
```

Les sorties parquet (`research/smc_patterns/`) ne sont pas suivies — voir
`.gitignore`. Les chiffres qui comptent sont ci-dessous.

## Données

| TF | Bougies | Début | Fin |
|---|---:|---|---|
| 15m | 57 037 | 2024-12-08 | 2026-08-07 |
| 30m | 53 519 | 2023-07-07 | 2026-08-07 |
| 1h | 51 909 | 2020-03-17 | 2026-08-07 |
| 4h | 15 769 | 2018-12-15 | 2026-08-07 |

Les profondeurs d'historique sont très inégales : le 15m ne remonte qu'à
décembre 2024, le 4h à décembre 2018. Toute comparaison inter-TF porte donc
aussi sur des régimes de marché différents, pas seulement sur des échelles
différentes.

## Volumétrie

**142 646 événements, 18 motifs distincts.** Fréquences les plus hautes (15m,
qui domine le classement simplement parce qu'il a le plus de barres) :

| Motif | n | / 1000 barres |
|---|---:|---:|
| SWINGS:LOW | 5 504 | 96.5 |
| SWINGS:HIGH | 5 473 | 96.0 |
| SWEEPS:BUY_SIDE | 4 848 | 85.0 |
| SWEEPS:SELL_SIDE | 4 831 | 84.7 |
| FVGS:BULLISH | 4 149 | 72.7 |
| FVGS:BEARISH | 4 060 | 71.2 |
| REJECTION_BLOCKS:BULLISH | 2 138 | 37.5 |
| CHOCH | 2 085 | 36.6 |
| BOS | 1 968 | 34.5 |
| ORDER_BLOCKS:BEARISH | 1 340 | 23.5 |

Composés (fenêtre 30 min, longueurs 2 et 3) : **73 010 énumérés → 19 452 retenus
par support → 13 880 confirmés** sur la tranche de confirmation (coupure
découverte/confirmation au **2025-10-02**). Les têtes de liste sont toutes des
chaînes de sweeps de même sens, du 15m vers le 30m :

| n découverte | n confirmation | Séquence |
|---:|---:|---|
| 1 910 | 2 458 | `15m\|SWEEPS:SELL_SIDE → 15m\|SWEEPS:SELL_SIDE → 30m\|SWEEPS:SELL_SIDE` |
| 2 207 | 2 347 | `15m\|SWEEPS:BUY_SIDE → 15m\|SWEEPS:BUY_SIDE → 30m\|SWEEPS:BUY_SIDE` |
| 1 847 | 2 235 | `15m\|SWEEPS:SELL_SIDE → 30m\|SWEEPS:SELL_SIDE` |
| 2 029 | 2 071 | `15m\|SWEEPS:BUY_SIDE → 30m\|SWEEPS:BUY_SIDE` |

## Résultat annoncé : 0 survivant

Le script conclut « Aucun motif ni composé ne se distingue de ses témoins ».

**Ce zéro n'est pas une mesure.** Il est structurellement garanti par la
combinaison du test et de la correction, indépendamment des données.

Les p-values viennent d'un test de permutation à `N_TIRAGES_TEMOIN = 200`
(`app/engine/smc_patterns/stats.py`). Le plus petit p atteignable est donc
**1/201 = 0.00498** — et c'est exactement le `min p_decale` observé, sur les deux
tables. Or les seuils corrigés sont :

| | Hypothèses | α Bonferroni | p plancher du test |
|---|---:|---:|---:|
| Motifs | 360 | 1.39 × 10⁻⁴ | 0.00498 |
| Composés | 73 010 | 6.85 × 10⁻⁷ | 0.00498 |

Le seuil exigé est **sous le plancher** du test : d'un facteur 36 pour les
motifs, d'un facteur ~7 300 pour les composés. Aucune ligne ne peut passer,
quelle que soit la force du signal. Le test n'a pas la résolution nécessaire
pour rejeter quoi que ce soit à ce seuil.

## Lecture

- **Les motifs seuls ne montrent rien de convaincant, correction ou pas.** Sur
  360 lignes mesurées, **26 passent les deux témoins à p < 0.05 brut**, soit
  7,2 % — à peine au-dessus des 5 % attendus du pur hasard. C'est le seul
  énoncé que cette exécution soutient réellement.

- **Les 9 lignes à p < 0.01 sont saturées au plancher.** Six d'entre elles
  affichent exactement 0.00498, c'est-à-dire « aucun des 200 tirages n'a fait
  mieux » : leur vraie p-value est inconnue, seulement bornée. Il faudrait plus
  de tirages pour les départager.

  | TF | Motif | h | n | Moyenne | Écart témoin |
  |---|---|---:|---:|---:|---:|
  | 15m | BREAKERS:BEARISH | 24 | 1 245 | +0.104 % | +0.115 |
  | 1h | LIQUIDITY_POOLS:SELL_SIDE | 12 | 1 070 | −0.168 % | −0.247 |
  | 1h | LIQUIDITY_POOLS:SELL_SIDE | 24 | 1 069 | −0.108 % | −0.284 |
  | 4h | FVGS:BEARISH | 3 | 807 | −0.150 % | −0.237 |
  | 30m | BREAKERS:BEARISH | 3 | 1 074 | +0.085 % | +0.078 |

- **Les amplitudes sont sous le coût de transaction.** Le meilleur écart au
  témoin de tout le tableau vaut **0.28 %**, et la plupart tournent autour de
  0.1 %. Avec `DEFAULT_TAKER_FEE = 0.001`, un aller-retour coûte 0.2 %. Même en
  supposant ces écarts réels et stables, il n'y a pas de marge exploitable
  telle quelle — c'est un signal de filtrage éventuel, pas un signal d'entrée.

- **Le taux de 32 % de composés à p < 0.05 (22 210 / 69 396) n'est pas une
  preuve.** Ces séquences ont été présélectionnées par support au moment du
  minage ; c'est précisément ce filtrage qui fabrique l'apparence de
  significativité, et c'est la raison d'être de la correction sur
  `n_enumeres`. Le raisonnement du code est bon ; c'est sa résolution qui
  manque.

- **Les composés dominants sont probablement un artefact de définition.** Un
  sweep 15m suivi d'un sweep 30m de même sens dans une fenêtre de 30 minutes
  décrit en grande partie le *même* événement de marché vu à deux échelles, pas
  un enchaînement de deux événements. Avant d'en tirer quoi que ce soit, il
  faudrait vérifier le taux de recouvrement temporel des deux maillons.

## Suites possibles

Pour que la mesure puisse conclure, au choix :

1. **Monter `N_TIRAGES_TEMOIN`** à au moins 1/α — soit ~7 200 tirages pour les
   motifs. Faisable. Hors de portée pour les composés (il en faudrait ~10⁶).
2. **Passer de Bonferroni à un contrôle du FDR** (Benjamini-Hochberg), beaucoup
   moins brutal sur 73 000 hypothèses et adapté à une étude exploratoire.
3. **Réduire le nombre d'hypothèses** en amont : moins d'horizons, moins de TF,
   ou une présélection déclarée avant mesure.

Le plus économique est probablement (2) pour les composés et (1) pour les
motifs. Aucune de ces pistes n'a été implémentée ici : cette exécution est une
mesure, pas une modification du protocole.
