# L1 — quatre systèmes de sortie, mesurés

Le moteur ne savait fermer qu'en entier. `docs/SPECS_SMC_ICT_ET_ADAPTATIVE.md`
§1 en faisait « le chantier prérequis n° 1 » : sans jambes, le schéma
`TP1 25 % → TP2 25 % → runner 50 %` des deux spécifications est inimplémentable.

Il est levé. Voici ce que ça donne.

```bash
python scripts/measure_exit_systems.py --data data/ohlcv
```

Mêmes signaux, mêmes frais, même découpe 65/35. **Seule la gestion de position
change** — détection, score et ciblage sont identiques d'un système à l'autre.
C'est la seule façon d'attribuer l'écart à la sortie et non au signal.

---

## 1. Les chiffres (OOS, 4 200 barres)

PnL % (net d'équité, `net_profit`) :

| système | BTC 1 h | BTC 4 h | ETH 1 h | ETH 4 h |
|---|---:|---:|---:|---:|
| `tp_fixe` *(actuel)* | −21,14 | −5,81 | −15,59 | −10,44 |
| `trailing_atr` | −20,49 | −0,57 | −13,89 | **−0,99** |
| `partiel_atr` | −21,78 | −2,04 | −11,48 | −8,15 |
| `partiel_struct` | **−19,75** | **−0,33** | **−8,44** | −7,77 |

Drawdown maximal % :

| système | BTC 1 h | BTC 4 h | ETH 1 h | ETH 4 h |
|---|---:|---:|---:|---:|
| `tp_fixe` | −23,96 | −12,65 | −16,37 | −17,06 |
| `partiel_struct` | **−20,16** | −11,94 | **−8,44** | −17,66 |

Profit factor :

| système | BTC 1 h | BTC 4 h | ETH 1 h | ETH 4 h |
|---|---:|---:|---:|---:|
| `tp_fixe` | 0,404 | 0,938 | 0,550 | 0,865 |
| `partiel_struct` | 0,288 | **1,147** | **0,765** | 0,880 |
| `trailing_atr` | 0,378 | 1,098 | 0,585 | **1,054** |

---

## 2. Ce que ça dit

### Le système actuel est le pire des quatre, dans les quatre cas

`tp_fixe` — le tout-ou-rien sur la poche de liquidité, qui tourne aujourd'hui —
est battu partout. Ce n'est pas marginal : sur ETH 1 h, `partiel_struct` réduit
la perte de 15,59 % à 8,44 % **et divise le drawdown par deux** (16,37 → 8,44).

C'est cohérent avec L0 : la cible n'était touchée que 28–36 % du temps. Sortir
une fraction à 1 R encaisse un gain que la cible pleine n'aurait jamais laissé
prendre.

### `partiel_struct` gagne 3 fois sur 4, `trailing_atr` la quatrième

Le trailing structurel (§30 — stop derrière le dernier pivot confirmé) est le
meilleur système sur BTC 1 h, BTC 4 h et ETH 1 h. Sur ETH 4 h, c'est le trailing
ATR pur qui l'emporte (−0,99 contre −7,77). L'écart n'est pas explicable par ce
jeu de mesures ; il est noté, pas expliqué.

### Le taux de réussite monte mécaniquement — ce n'est pas un progrès en soi

Le win-rate passe de 20–37 % à 28–53 % avec les jambes. C'est arithmétique :
TP1 à 1 R transforme en gagnants des trades qui seraient morts sur leur stop.
Ça ne dit rien de l'espérance, et c'est précisément pourquoi le tableau publie
PnL, PF, Sharpe et DD à côté.

### **Mais aucun système ne rend la stratégie rentable**

Les seize cases sont refusées par `beats_baseline`. Le meilleur résultat
absolu — BTC 4 h `partiel_struct` — vaut −0,33 % de PnL net pour un Sharpe de
0,026. C'est un match nul, pas un edge.

**La géométrie de sortie valait 2 à 7 points de PnL et jusqu'à la moitié du
drawdown. Elle ne valait pas le signe.** Conforme à ce que L0 annonçait : sur
1 h, les trades ne décollent pas, et aucune sortie ne répare une entrée qui ne
va nulle part.

### Un effet de bord chiffré : les jambes coûtent des frais

Sur BTC 4 h, `partiel_struct` affiche un profit factor de **1,147** — donc des
gains bruts supérieurs aux pertes brutes — pour un PnL net de **−0,33 %**. Les
deux ne se contredisent pas : le PF se calcule sur les PnL de clôture, le PnL
net sur l'équité, et l'écart est la somme des frais d'entrée (cf.
`docs/MESURE_GEOMETRIE_SORTIE.md` §3). Chaque jambe est un fill de plus.

C'est un argument direct pour L2 : tant que le R/R n'est pas calculé **net**,
on optimise une géométrie dont le coût n'entre pas dans la décision.

---

## 3. Ce qui est livré

**Moteur.** `Backtester._close_partial_at` — symétrique de `check_scale_in` :
encaisse le PnL de la jambe, réduit taille et notionnel au prorata, trace la
sortie. La position n'est journalisée qu'à sa clôture complète, et la courbe
d'équité garde **un point par trade, pas par jambe** — en changer la cadence
modifierait l'annualisation du Sharpe de tous les backtests existants.

**Contrat stratégie.** `signal["exits"] = [{"r": 1.0, "fraction": 0.25}, …]`,
chaque entrée portant soit `r` (multiple du risque), soit `price` (niveau
absolu). Le runner est le reliquat. Absent → tout-ou-rien inchangé.
`execution.plan_partial_targets` est **partagé backtest ↔ live** : deux
planificateurs auraient divergé dès le premier TP partiel.

**Trailing structurel (§30).** `StructureTrailingStop` — stop sous le dernier
pivot bas confirmé (long) / au-dessus du dernier pivot haut (short), avec la
même latence de confirmation que les swings du moteur SMC. Repli sur `mult × ATR`
tant qu'aucun pivot n'est confirmé, sinon le stop resterait figé sur toute une
impulsion. Point mort frais compris après TP1.

**Live.** `_partial_close_position`, symétrique de `_scale_in_position` :
ordre market sur la fraction, réserve du ledger redimensionnée, stop exchange
replacé, reliquat non négociable soldé. Mêmes priorités de sortie qu'au
backtest — gap, TP plein, jambes, early-exit, trailing — sinon la parité tombe
sur les barres où plusieurs sorties se déclenchent ensemble.

**Persistance.** Colonnes `exits` (JSON), `realized_pnl`, `size_initial` sur
`Trade`, plus les champs de journal L0. La migration de schéma du dépôt
(`_migrate_schema`, comparaison `PRAGMA table_info` ↔ modèle) les ajoute
automatiquement.

**Paramètres** (`smart_money`, tous **off** par défaut) : `use_partial_exits`,
`tp1_r`, `tp1_fraction`, `tp2_fraction`, `trail_mode`. Les `optimizer_results`
du YAML ont été mesurés en tout-ou-rien ; les rendre partiels en silence
invaliderait ces réglages.

1 760 tests passent, dont 15 nouveaux (`tests/test_partial_exits.py`).

---

## 4. Décision

**Ne pas activer `use_partial_exits` par défaut.** Le gain est réel et
reproductible sur quatre cases, mais il améliore un système perdant : le
promouvoir donnerait l'illusion d'un progrès de fond. Le réglage reste dans
`param_space`, à ré-arbitrer par l'optimiseur une fois L3/L4 livrés — c'est
alors seulement que la comparaison aura un sens, puisque le signal aura changé.

Ce que ce lot établit et qui ne bougera plus : **le tout-ou-rien est le plus
mauvais des quatre systèmes**, et il n'y a aucune raison de continuer à le
traiter comme la référence.
