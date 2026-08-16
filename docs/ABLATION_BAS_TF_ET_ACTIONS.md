# Bas timeframes et actions — le harnais sur deux régimes de plus

Deux hypothèses à tester : **les bas timeframes produisent plus de structures**
(donc plus de trades, donc des verdicts plus solides), et **les actions sont un
régime différent** de la crypto.

Les deux se vérifient, et une troisième chose apparaît que je n'attendais pas.

```bash
python scripts/measure_ablation_v3.py --data data/ohlcv \
    --cas "BTC_USDC:15m,BTC_USDC:30m,ETH_USDC:15m,ETH_USDC:30m" --barres 40000
python scripts/measure_ablation_v3.py --data data/ohlcv \
    --cas "SU.PA:1d,FR.PA:1d,TTE.PA:1d,RMS.PA:1d,MRN.PA:1d,SW.PA:1d"
```

Les six tickers SBF 120 sont tirés **au hasard** (`random.seed(20260814)`) parmi
les 110 dont le journalier compte au moins 3 000 barres — pas choisis.

---

## 1. Les bas timeframes donnent bien plus de matière

| cas | trades IS | trades OOS |
|---|---:|---:|
| BTC 15 m | 186 | 106 |
| BTC 30 m | 238 | 136 |
| ETH 15 m | 189 | 107 |
| ETH 30 m | 227 | 124 |
| *(rappel BTC 4 h)* | *89* | *57* |

Deux à trois fois plus de trades qu'en 4 h. L'hypothèse tient.

**Quatre mécanismes distincts valident 4 cas sur 4** — le meilleur résultat de
tout le chantier :

| mécanisme | 2/2 | n OOS méd. |
|---|:--:|---:|
| L3 porte `direction` | **4/4** | 100 |
| L3 porte `no_pullback` | **4/4** | 80 |
| L6 tier *(`tier_sizing`, qui subsume `tier_gate` — cf. §3 bis)* | **4/4** | 104 |
| **`size_by_confluence` (témoin)** | **4/4** | 116 |

⚠ Le harnais liste `tier_gate` et `tier_sizing` comme deux entrées et les
compte donc deux fois. **Ce sont deux vues du même mécanisme** : §3 bis le
mesure.

Les quatre premiers modifient le nombre de trades (80 à 104 contre 116 pour la
référence) : ils agissent réellement sur la sélection.

**Aucun ne rend la stratégie rentable** : les références perdent −148 à −290 en
OOS. Ils atténuent.

## 2. Le témoin a intercepté une fausse attribution

Silver Bullet, AMD et Killzones affichent aussi **4/4**. Ils ne valent rien pour
autant : le harnais les teste avec `size_by_confluence` activé — sans quoi leur
bonus de score ne serait consommé par aucune décision — et **c'est ce
consommateur qui produit tout l'effet**.

Écart de chaque module au témoin seul, sur les douze cas :

| module | Δ IS | Δ OOS | nombre de trades |
|---|---|---|---|
| Silver Bullet | −2,3 à +4,5 | −1,3 à +2,0 | **identique partout** |
| AMD | **+0,00** (3 cas sur 4) | **+0,00** (4/4) | **identique partout** |
| Killzones | −3,9 à +1,3 | −0,8 à +0,0 | **identique partout** |

Sur des PnL de plusieurs centaines, ces écarts sont du bruit d'arrondi, et le
nombre de trades ne bouge **jamais**. Les trois modules restent inertes.

**Sans le témoin, j'aurais publié « Silver Bullet et AMD validés ».** Il a été
ajouté au harnais en même temps que le consommateur de score, précisément pour
qu'on ne puisse pas attribuer à un module ce que son échafaudage fait tout seul.
C'est le cinquième faux positif intercepté de ce chantier, et le premier qui
l'ait été *par construction* plutôt qu'après coup.

## 3. Les actions sont un autre régime

| ticker | IS | OOS |
|---|---|---|
| SU.PA | −88,0 (43 tr) | −94,6 (24) |
| FR.PA | +17,8 (46) | **+25,6** (23) |
| TTE.PA | +951,7 (8) | −52,1 (12) |
| RMS.PA | **+2 910,8** (22) | +27,1 (16) |
| MRN.PA | +17,4 (44) | −45,8 (26) |
| SW.PA | −128,8 (49) | −219,1 (26) |

**Un seul mécanisme valide : `L1 sorties partielles`** (4 cas sur 6) — celui-là
même qui échouait en 1 h. Aucune des portes de structure ne passe (`direction`
3/6, `no_pullback` 2/6), alors qu'elles dominaient en crypto.

⚠ **RMS.PA affiche +2 910 en IS pour +27 en OOS sur 22 trades** : la signature
d'un surapprentissage massif sur une action qui a monté sans discontinuer entre
2000 et la coupure. Ce chiffre ne doit pas être cité comme une performance.

Les échantillons sont petits (12 à 26 trades OOS) : le journalier ne produit pas
assez de trades sur 26 ans pour trancher. **Élargir aux 110 tickers disponibles
plutôt qu'allonger l'historique** est la suite naturelle.

---

## 3 bis. ⚑ Correction : mes deux mécanismes L6 n'en font qu'un

Question posée après coup — `size_by_confluence` fait-il doublon avec le sizing
par tier ? La mesure répond non, **mais elle en découvre un autre, dans mon
propre travail**.

BTC 30 m, 30 000 barres, découpe 65/35 :

| variante | n IS | PnL IS | n OOS | PnL OOS |
|---|---:|---:|---:|---:|
| référence | 191 | −413,4 | 98 | −185,7 |
| `tier_gate` seul | 178 | −386,0 | 86 | −173,8 |
| `tier_sizing` seul | 178 | **−366,0** | 86 | **−173,7** |
| **`tier_gate` + `tier_sizing`** | **178** | **−366,0** | **86** | **−173,7** |
| `size_by_confluence` seul | 191 | −398,1 | 98 | −187,0 |
| **`conf` + `tier_sizing`** | 178 | **−343,7** | 86 | **−169,6** |

**`tier_sizing` subsume `tier_gate`.** Le tier D reçoit un facteur de 0,0 : la
taille tombe à zéro et le trade est refusé au sizing. Activer les deux donne
donc **exactement** le résultat de `tier_sizing` seul — mêmes trades, même PnL,
au centime.

Conséquence directe : **leurs deux validations à 4 cas sur 4 ne sont pas deux
preuves, c'en est une.** Le tableau du §1 les compte séparément ; il ne faut pas
lire « cinq mécanismes validés » mais **quatre** — `no_pullback`, `direction`,
le tier (une fois), et `size_by_confluence`.

**`size_by_confluence` n'est pas un doublon.** Les deux leviers agissent sur le
même hook (`size_factor`) et se multiplient, mais pas sur la même chose :

- le **tier SUPPRIME** — 191 trades tombent à 178 ;
- la **confluence RÉÉCHELONNE** — les 191 trades sont conservés, seule leur
  taille change.

Ils partagent une entrée (le score sert aux deux, `_tier` le consulte), sans se
recouvrir pour autant. Et **les composer bat chacun pris seul** : −343,7 en IS
contre −366,0 et −398,1. C'est la meilleure des six variantes testées.

⚠ Sur cette fenêtre, `size_by_confluence` seul est légèrement **pire** que la
référence en OOS (−187,0 contre −185,7), alors qu'il validait 4/4 dans la
campagne §1 sur 40 000 barres. Deux fenêtres différentes : la sensibilité est
réelle et doit tempérer l'enthousiasme du §4.

---

## 4. Ce que l'ensemble établit

**Le verdict dépend du régime, et c'est désormais mesuré sur quatre familles :**

| régime | mécanismes validés |
|---|---|
| crypto 15 m / 30 m | L3 ×2, L6 ×2 *(+ `size_by_confluence`)* |
| crypto 1 h | L3 ×2, L6 ×2 |
| crypto 4 h / 1 j | **aucun** |
| actions 1 j | **L1 sorties partielles** seul |

Les portes de structure (L3) et les tiers (L6) valident sur **les trois
timeframes crypto à échantillon suffisant** — 15 m, 30 m et 1 h — et échouent là
où l'échantillon est petit (4 h, 1 j, actions). C'est cohérent avec l'hypothèse
qu'ils agissent vraiment, plutôt qu'avec un artefact.

`size_by_confluence`, qui n'était pas dans le périmètre du chantier, valide
aussi bien qu'eux et n'avait jamais été mesuré ainsi. **C'est le candidat le
plus économique de la campagne** : il existe déjà, il est off par défaut, et il
ne demande aucun code neuf.

**Aucun ne rend la stratégie rentable sur aucun régime.**

## 5. Suite

1. **Étendre les actions aux 110 tickers** plutôt qu'à six : c'est l'univers qui
   manque, pas l'historique.
2. **Mesurer `size_by_confluence` pour lui-même**, hors harnais — il n'a jamais
   fait l'objet d'une campagne dédiée alors qu'il valide partout où la crypto a
   de l'échantillon.
3. **Décider de l'activation** des quatre mécanismes crypto : décision de
   trading, ils réduisent la perte sans la retourner.
4. **Retirer ou réparer SMT, Silver Bullet et AMD.** SMT fonctionne mais mord
   rarement ; les deux autres n'ont aucun effet mesurable même avec un
   consommateur de score. Les garder « disponibles mais désactivés » est
   défendable ; les garder sans le dire ne l'est pas.
