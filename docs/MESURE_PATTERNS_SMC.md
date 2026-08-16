# Mesure — motifs SMC/ICT, fréquence, enchaînements et composés

> Outillage livré le 2026-08-16 sur la branche
> `claude/ml-optimizer-backtest-perf-0x32so`. Un symbole, plusieurs timeframes.
>
> ⚠ **CE RAPPORT NE CONTIENT AUCUNE MESURE DE MARCHÉ.** L'environnement où
> l'outil a été écrit n'a ni `data/ohlcv/` ni accès exchange. Le script, les
> tests et les garde-fous sont livrés et validés ; les chiffres restent à
> produire en local, sur données réelles. Publier ici des résultats obtenus sur
> une marche aléatoire aurait été pire que ne rien publier — c'est d'ailleurs
> le refus que le script oppose lui-même quand le cache est vide.

---

## 1. Ce que l'outil fait

```bash
python scripts/analyze_smc_patterns.py --symbol BTC/USDC --tfs 15m,1h,4h
```

Trois étages, dans `app/engine/smc_patterns/` :

| Module | Rôle |
|---|---|
| `journal.py` | journal daté de tous les motifs, contexte HTF aligné causalement |
| `stats.py` | fréquence, transitions, mouvement suivant, **témoins** |
| `composites.py` | motifs composés, **découverte et confirmation disjointes** |

Aucun détecteur n'a été écrit. Tout vient du moteur existant —
`smc_structure.analyze` (swings, BOS/CHoCH/MSS, pools, sweeps, order blocks,
FVG, voids, breakers, rejection blocks), `smc_state` (14 états × 5 séquences),
`smc_sessions` (killzones, sessions), `ict` (Silver Bullet). Neuf familles
d'entités sont journalisées.

Sorties : `research/smc_patterns/<SYMBOLE>_*.parquet` + un `_resume.json`.

---

## 2. Les trois décisions qui font la valeur de l'outil

### 2.1 Chaque motif est daté à sa barre de CONNAISSABILITÉ

Le moteur rend des entités portant plusieurs indices — `index` (où le motif se
situe sur le graphique), `confirmed_at`, `created_at`. Se tromper d'indice ne
casse rien de visible : ça produit des rendements « après le motif » qui
contiennent déjà le mouvement. La table `CONNAISSABILITE` fixe la règle par
famille :

| Famille | Indice | Pourquoi |
|---|---|---|
| swing | `confirmed_at` | le pivot n'existe qu'une fois ses barres de droite connues |
| structure_event, sweep | `index` | la cassure est datée à sa clôture |
| order_block, breaker, rejection_block | `created_at` | la bougie d'origine est antérieure, le bloc n'est qualifié qu'à l'impulsion |
| FVG | `index + 1` | le gap se voit à la **troisième** bougie ; `index` est celle du milieu |
| liquidity_void | `end_index + 1` | la barre où l'on **constate** que la course ne se prolonge pas |
| liquidity_pool | `formed_at` | dernier renforcement — entité mutable, cf. §2.2 |

`liquidity_void` a été déplacé de `end_index` à `end_index + 1` **après
mesure** : à `end_index`, on ne sait pas encore que la course s'arrête là.

### 2.2 Le test de causalité dit le sens de l'écart toléré

`tests/test_smc_patterns_journal.py` compare, à 24 points de coupe, le journal
d'un préfixe de N barres au journal complet tronqué à N. Deux issues :

- un événement dans le **plein** absent du **préfixe** ⇒ le journal complet l'a
  daté à une barre où l'historique ne permettait pas de le connaître.
  **Interdit sans exception, et vérifié à zéro.**
- un événement dans le **préfixe** absent du **plein** ⇒ l'entité est mutable.
  Un pool de liquidité est renforcé à chaque swing qui le rejoint, et
  `formed_at` désigne le dernier renforcement : le journal complet
  **sous-compte** ces motifs. Sens conservateur, toléré pour les seules
  familles déclarées dans `FAMILLES_MUTABLES` — aujourd'hui `liquidity_pools`
  seul.

Mesuré sur 24 coupes : 4 écarts, tous du second type.

### 2.3 L'alignement inter-TF ne laisse voir aucune bougie non clôturée

C'est le piège propre au multi-TF, et il est plus facile à introduire que le
précédent. À la barre 1 h numéro *i*, un `join_asof` sur l'horodatage rend le
bucket 4 h **en cours** — celui dont on ne connaît pas encore la clôture. Les
statistiques deviennent alors spectaculaires et fausses.

`_index_htf_causal` retient la dernière barre HTF dont la **fin** précède ou
égale la fin de la barre LTF. Un test le vérifie barre à barre, et un second
test vérifie que la jointure naïve, elle, serait bien en avance — sans quoi le
premier ne prouverait rien.

---

## 3. Pourquoi les témoins, et pourquoi deux

« Ce motif est suivi de +0,4 % à 12 barres » ne dit rien tant qu'on ignore ce
que rend une barre quelconque du même échantillon. Sur une série haussière,
**tout** motif est suivi d'une hausse.

| Témoin | Construction | Question à laquelle il répond |
|---|---|---|
| inconditionnel | barres au hasard, à distribution de **sessions** identique | le motif fait-il mieux qu'une barre quelconque prise au même moment de la journée ? |
| décalé | les mêmes événements, décalés circulairement | l'effet vient-il du motif, ou de la façon dont ses occurrences se répartissent dans le temps ? |

Un motif qui bat le premier mais pas le second n'a pas d'edge propre : il
hérite de son calendrier. `survivants()` exige donc que **les deux** tombent.

Cette exigence n'est pas rhétorique : la campagne d'ablation du dépôt a produit
cinq faux positifs, et le dernier n'a été intercepté que parce qu'un témoin
avait été ajouté au harnais (cf. `docs/SUITE_ABLATION_V3.md`,
`docs/ABLATION_BAS_TF_ET_ACTIONS.md`).

---

## 4. Le chevauchement des fenêtres — l'erreur qu'il a fallu corriger

La première version comparait la moyenne observée au témoin via un intervalle
de confiance paramétrique, avec correction de Bonferroni. Sur une **marche
aléatoire**, elle sortait **125 « découvertes » significatives**.

La cause : à l'horizon 12 avec un motif toutes les trois barres, la même hausse
est comptée quatre fois. L'écart-type naïf suppose des observations
indépendantes, sous-estime massivement l'incertitude, et déclare significatif à
peu près tout. C'est une machine à faux positifs avec un vernis de rigueur.

Le remède est un **test de permutation** : le témoin décalé porte exactement le
même espacement d'événements et le même recouvrement de fenêtres, puisque ce
sont les mêmes événements décalés en bloc. La p-valeur est la position de la
moyenne observée dans la distribution des moyennes du témoin. Chevauchement,
autocorrélation et non-normalité sont alors dans **les deux bras** : ils
s'annulent au lieu de se cacher.

Après correction, sur deux graines aléatoires indépendantes :

```
graine 1 : 72 motifs mesurés → 0 survivant | 2 116 composés (30 884 hypothèses) → 0 survivant
graine 2 : 72 motifs mesurés → 0 survivant | 2 230 composés (29 970 hypothèses) → 0 survivant
```

C'est le test de non-régression le plus important du lot
(`test_aucune_decouverte_sur_du_bruit`) : sur du bruit, il n'y a rien à
trouver, donc toute découverte est un défaut de la méthode.

---

## 5. Motifs composés — trois garde-fous

1. **Espace borné et déclaré.** Longueur ≤ 3 maillons, fenêtre temporelle
   maximale, maillons pris dans le vocabulaire du journal. Filtrage par support
   minimal (`MIN_SIGNIFICANT_TRADES`) : un enchaînement vu quatre fois n'est
   pas un enchaînement. Une fenêtre trop dense (> 60 événements) est ignorée et
   signalée plutôt que de laisser l'énumération devenir cubique.

2. **Découverte et confirmation sur des barres disjointes.** Les composés sont
   cherchés sur les 70 % premiers de l'historique et comptés sur les 30 %
   finaux, qui n'ont servi à rien d'autre. Un composé absent de la tranche de
   confirmation est un artefact — et c'est le découpage qui le dit, pas une
   intuition a posteriori. Même principe que
   `app/core/is_oos.py::split_with_holdout`, appliqué aux motifs.

3. **Le décompte d'hypothèses est celui de l'ÉNUMÉRATION.** `mine()` retourne
   `n_enumeres` — toutes les séquences distinctes rencontrées, avant filtrage
   par support. Corriger sur le nombre de survivants reviendrait à ne pas
   corriger : ce sont précisément le support et le filtrage qui ont fait le
   tri. Le décompte s'affiche **en tête** de la sortie, pas en note de bas de
   page.

Les composés inter-TF sont permis (`4h|SWEEP → 1h|MSS → 15m|OB_RETEST`) : les
maillons sont ordonnés par **temps**, jamais par index de barre — un index 4 h
et un index 1 h ne sont pas comparables.

---

## 5 bis. Coût mesuré en multi-TF — à lire avant de lancer

L'énumération des composés à trois maillons croît avec le carré du nombre
d'événements par fenêtre, et le multi-TF empile les événements. Mesuré sur
**26 947 événements** (15m/30m/1h/4h, ~125 jours), pour la seule énumération
L3 :

| `--fenetre-composes` | événements/fenêtre | séquences L3 distinctes | durée | mémoire |
|---|---|---|---|---|
| 2 barres | 6 | 78 000 | 2,6 s | 19 Mo |
| 4 barres | 10 | 142 000 | 6,6 s | 39 Mo |
| 6 barres | 15 | 178 000 | 11,5 s | 65 Mo |
| 8 barres | 19 | 214 000 | 17,9 s | 85 Mo |
| **12 barres (défaut)** | 28 | 252 000 | 35,4 s | 139 Mo |

Ces durées sont celles d'UNE énumération ; `mine` en fait quatre (deux
longueurs × deux tranches) et la mesure des composés une de plus. Sur quatre
timeframes au défaut de 12 barres, l'étape composés n'a pas terminé en neuf
minutes dans nos essais.

**Recommandation pour un premier lancement multi-TF** : commencer à
`--fenetre-composes 4`. La fenêtre est exprimée en barres du TF LE PLUS BAS —
4 barres de 15 m font une heure, ce qui est déjà large pour un enchaînement
SMC. Monter ensuite si le besoin s'en fait sentir, en sachant ce que ça coûte.

Le garde-fou `MAX_EVENEMENTS_FENETRE` (60) ne mord pas ici : à 28 événements
par fenêtre on est sous le seuil, et c'est le nombre de séquences DISTINCTES
retenues qui pèse, pas la densité. Un élagage à la Apriori — ne former un
triplet que si ses deux paires sont déjà fréquentes — réduirait cet espace
d'un ordre de grandeur ; il n'est pas implémenté.

---

## 6. Ce que l'outil ne fait pas

- **Un seul symbole par exécution.** Le multi-symboles change la nature du
  travail (mise en commun, corrélation entre paires) et mérite son propre lot.
- **Aucun branchement vers les stratégies ni l'optimiseur.** Étude de mesure.
- **Ni route API ni écran UI.** À décider une fois qu'on saura quoi afficher.
- **Aucun détecteur nouveau.** Si un concept ICT manque au dépôt, il manquera
  aussi au journal : c'est signalé, pas comblé en douce.

---

## 7. À faire en local, avec les données

```bash
# 1. alimenter le cache (le backtest CLI passe par le même CandleStore)
python cli.py --backtest BTC/USDC --timeframes 15m,1h,4h --limit 50000

# 2. lancer l'analyse
python scripts/analyze_smc_patterns.py --symbol BTC/USDC --tfs 15m,1h,4h

# 3. vérifier que les garde-fous tiennent toujours
python -m pytest tests/test_smc_patterns_journal.py \
                 tests/test_smc_patterns_stats.py \
                 tests/test_smc_patterns_composites.py -q
```

Trois questions à se poser en lisant la sortie, dans cet ordre :

1. **Combien d'hypothèses ont été testées ?** C'est la première ligne affichée.
   Trente mille composés énumérés, cela veut dire que le hasard a eu trente
   mille occasions de produire un beau chiffre.
2. **Que disent les témoins ?** `moyenne_pct` seule ne se lit pas.
   `ecart_decale` et `p_decale` se lisent.
3. **Combien de survivants ?** Zéro est un résultat publiable, et probablement
   le plus fréquent. Un rapport qui ne listerait que ce qui marche serait un
   échec de la mesure, pas un succès de la stratégie.
