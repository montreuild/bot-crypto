# 01 — Synthèse exécutive

Audit du **2026-08-20** sur `bb94993`.
Delta audité : `a6659e1..HEAD` — 49 commits, 268 fichiers, +10 914 / −6 308,
depuis la livraison de l'audit du 18 août.

---

## L'essentiel en cinq points

**1. Le delta est net positif.** Dix corrections de fond ont été vérifiées :
1 402 faux trous de week-end éliminés sur une seule action, la fuite de warmup
des folds walk-forward corrigée sans effet de bord, l'identifiant de stop
exchange qui n'est plus perdu, `paper_mode` qui bascule du côté sûr, la cadence
de réentraînement ML enfin indexée sur le timeframe. Ce n'est pas un delta à
annuler.

**2. Deux gates de CI sont rouges sur HEAD.** `ruff check .` échoue (3 erreurs)
et `mypy` sur son périmètre CI échoue (1 erreur). **Toute PR ouverte depuis
HEAD part rouge**, quel qu'en soit le contenu. Correctif : 10 minutes.

**3. La comptabilité des coûts est fausse dans le reporting.** Les frais des
sorties partielles et des pyramidages disparaissent du champ journalisé :
**11 % à 24 % de sous-estimation mesurée**. Et le PnL par trade ne retranche
pas les frais d'entrée des pyramidages, si bien que la somme des PnL diverge de
la courbe d'équité. La courbe d'équité, elle, est juste — deux vérités
coexistent dans le même résultat de backtest.

**4. Six garde-fous ajoutés récemment ne sont reliés à aucune décision.** Le
refus de drawdown dégradé ne s'applique pas au bouton « Appliquer », qui est le
chemin par défaut. Le refus de modèle ML statistiquement nul n'empêche pas sa
publication. Les critères d'expectancy et de profit factor de l'optimiseur ne
peuvent jamais s'activer. Le code exprime la bonne intention ; le câblage
manque.

**5. Aucun constat P0.** Rien ne bloque l'exploitation. Les onze P1 sont des
écarts de justesse et des protections inertes, pas des pannes.

---

## Chiffres

| | |
|---|---:|
| Constats | **35** |
| dont P0 (bloquant) | **0** |
| dont P1 (majeur) | **11** |
| dont P2 / P3 | 15 / 9 |
| **Reproduits par exécution ou mesure** | **23** |
| Établis par lecture seule (PLAUSIBLE) | 4 |
| Améliorations vérifiées | 8 |

**Vérification exécutable** — 2 142 tests Python passés (241 s), couverture
`app/` à **67,10 %** (seuil CI 64 %), 190 tests frontend passés, `tsc` et
`eslint` verts, `ruff` et `mypy` **rouges**. Quatre des six gates de CI sont
verts, deux sont rouges.
Rejeu sur les données réelles : 645 fichiers parquet, 128 symboles,
1 848 671 barres actions et 610 249 barres crypto.

---

## Les onze constats majeurs

| ID | Constat | Mesure | Effort |
|---|---|---|---:|
| `FIN-01` | Les frais des jambes et pyramidages disparaissent du reporting | −11 % à −23,6 % | 1 h 30 |
| `FIN-02` | Le PnL journalisé omet les frais d'entrée des pyramidages | +0,4634 d'écart sur 245,45 | 1 h |
| `DAT-01` | Le seuil de détection de trous passe de 1,5×tf à 3×tf | 15 trous réels masqués sur BTC 1h | 45 min |
| `PERF-01` | La détection de trous est 1,75× à 5,5× plus lente | 386 s de surcoût sur le parc | 35 min |
| `OPT-01` | Les critères expectancy et profit factor sont inertes | Reproduit | 1 h 30 |
| `OPT-02` | Le refus de drawdown ne couvre pas l'apply manuel | DD 80 % vs 10 % accepté | 20 min |
| `BT-01` | Le walk-forward perd son régime de risque réaliste | Asymétrie avec le baseline | 30 min |
| `ML-01` | Le verdict « bloquer » du gate ML ne bloque rien | Reproduit | 1 h |
| `CI-01` | `ruff check .` rouge sur HEAD | 3 erreurs | 2 min |
| `CI-02` | `mypy` rouge sur le périmètre CI | 1 erreur | 5 min |
| `TEST-01` | Aucun invariant testé sur les frais ni le capital | — | 2 h |

---

## Un seul motif, six fois

Six des onze P1 ont exactement la même forme : **une protection est écrite,
mais rien ne l'appelle ou rien ne la lit.**

- `beats_baseline` accepte des critères d'expectancy et de profit factor — ni
  le baseline ni les appelants ne les fournissent ;
- le refus de drawdown existe — la route la plus utilisée ne passe pas la
  valeur ;
- `validate_model_quality` renvoie « bloquer » — personne ne consulte ce
  verdict pour décider ;
- la garde de chemin rapide de la détection de trous teste `cal is None` —
  `cal` n'est jamais `None` ;
- `realistic_risk` devient configurable — la clé n'est posée nulle part ;
- un fichier de test annonce une propriété de conservation — aucun test ne
  l'exprime en égalité.

La cause est identifiable : le commit `fec34ed` applique en une fois
1 800 lignes de corrections touchant cinq domaines. Chaque correction y est
individuellement défendable ; aucune n'a pu être revue isolément, et aucune
n'était accompagnée d'un test échouant avant le correctif.

**C'est la recommandation centrale de cet audit** : pour chaque garde-fou,
exiger un test qui échoue d'abord. Les six défauts ci-dessus auraient tous été
attrapés par cette seule règle. Chaque test tient en une dizaine de lignes ; je
les ai spécifiés dans les fiches de délégation.

---

## Confrontation avec l'audit du 18 août

Le rapport `20-REVISION-2026-08-18.md` annonce 48 corrections livrées.
Vérification ligne à ligne contre le code : **39 confirmées, 6 seulement à
moitié câblées, 3 ayant introduit un défaut**.

Le cas le plus parlant : la révision annonce avoir corrigé l'écrasement de
`borrow_cost` à la clôture (`FIN-03`), et laisse en place le même défaut
d'écrasement sur `fees` — à la ligne juste au-dessus.

Rien n'a été annoncé livré à tort sur les points *restés ouverts* : ce suivi-là
est fiable. Détail dans `17-CONFRONTATION-AUDIT-PRECEDENT.md`.

---

## Plan de traitement

| Lot | Constats | Effort | Effet |
|---|---|---:|---|
| **1 — CI au vert** | `CI-01`, `CI-02` | **10 min** | Débloque toute PR |
| **2 — Comptabilité** | `FIN-01`, `FIN-02`, `TEST-01` | 4 h 30 | PnL et coûts justes |
| **3 — Trous et perf** | `DAT-01`, `DAT-03`, `PERF-01` | 1 h 30 | Détection rétablie, 386 s gagnées |
| **4 — Gates optimiseur** | `OPT-01`, `OPT-02`, `OPT-04` | 2 h | Les garde-fous s'appliquent |
| **5 — Décisions trading** | `BT-01`, `OPT-03`, `FIN-04` | — | **À trancher par vous** |
| **6 — Gate ML** | `ML-01`, `ML-02` | 4 h | Le refus devient effectif |
| **7 — Ergonomie** | `UX-01/02/03` | 2 h 15 | Sélecteur accessible |
| **8 — Outillage** | `API-01/02`, `LIVE-01/02` | 1 h 35 | Docker de test vert |
| **9 — Fond** | `TEST-02`, `ARCH-01`, `DETTE-01` | 4-6 j | Dette structurelle |

**Les lots 1 à 4 traitent 9 des 11 P1 en 8 h 10.**

---

## Trois décisions qui vous appartiennent

Ces trois changements du delta modifient le comportement de trading. Ce ne sont
pas des défauts, et je ne recommande pas de correctif automatique — ils
appellent un arbitrage explicite :

| Changement | Effet |
|---|---|
| `realistic_risk` du walk-forward passé à `False` (`BT-01`) | Le gate d'auto-apply valide sans circuit breakers, alors que le baseline les applique. À rendre symétrique — mais dans quel sens ? |
| Facteur de drawdown linéaire → hyperbolique (`OPT-03`) | Plus sévère sous 15 %, **beaucoup plus permissif au-delà de 30 %** : un paramétrage à 40 % de drawdown n'est plus éliminé d'office |
| Courbe de risque et frein de volatilité au pyramidage (`FIN-04`) | Modifie les résultats de toutes les stratégies qui pyramident |

---

## Un avertissement à ne pas manquer

Le rapport de révision du 18 août recommande de **revalider les paramètres
retenus par l'optimiseur**, les corrections ayant changé les chiffres des
backtests.

C'est juste, mais **cette revalidation ne doit pas être lancée avant les
correctifs `FIN-01` et `FIN-02`**. Les coûts journalisés sous-estiment
actuellement de 11 % à 24 % : revalider maintenant figerait des paramètres
choisis sur des coûts faux.

Ordre à respecter : lot 1 → lot 2 → puis revalidation.

---

## Où lire la suite

| Rapport | Contenu |
|---|---|
| `00-METHODE-ET-PERIMETRE.md` | Périmètre, règle de preuve, commandes exécutées |
| `02-ARCHITECTURE.md` | Graphe, hubs, couplage |
| `03-REVUE-DELTA.md` | Delta commit par commit, localisation des défauts |
| `04-MOTEUR-FINANCIER.md` | `FIN-01`, `FIN-02` et leurs reproductions |
| `05-BACKTEST.md` … `16-DETTE-TECHNIQUE.md` | Un rapport par domaine |
| `17-CONFRONTATION-AUDIT-PRECEDENT.md` | Vérification des 48 corrections annoncées |
| `99-REGISTRE.md` | Registre global, 35 constats, plan en 9 lots |
