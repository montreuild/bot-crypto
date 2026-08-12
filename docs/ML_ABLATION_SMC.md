# Structure de marché, régime et triple barrière — ce qui a été mesuré

⚠ **Réserve, et sa levée.** BTC et ETH corrèlent à **0.835** sur les rendements
horaires alignés : les « quatre jeux indépendants » ci-dessous n'en sont pas.
La généralité a donc été retestée sur **six actions XPAR** — corrélées à 0.253
entre elles et 0.07–0.30 avec la crypto, sur un marché à séances et gaps de
nuit. **Le gain sur `dir` s'y retrouve 6 fois sur 6** (§2 bis). Le bloc SMC
n'est pas un artefact crypto.

Trois pistes ont été instrumentées puis mesurées par ablation, sur quatre jeux
indépendants (BTC/USDC et ETH/USDC, en 1 h et 30 min, 47 000 à 54 000 barres
chacun). Une seule paie. Les deux autres sont documentées ici avec leurs
chiffres, parce qu'un essai infirmé évite de le refaire.

| piste | verdict |
|---|---|
| **Bloc SMC / ICT** (21 colonnes) | **Gain net et répliqué : +0.073 d'AUC sur la tête `dir`** |
| Bloc régime explicite (6 colonnes) | Neutre — dans le bruit sur les deux têtes |
| Tête triple-barrière `tb` | Moins bonne que `dir` sur les 4 jeux — **reconfirmé sur holdout disjoint** |

Reproduire :

```bash
python scripts/measure_smc_ablation.py
```

```bash
python scripts/measure_triple_barrier.py
```

---

## 1. Le point de départ

`omnibus_full` (catalogue `v4_polars@1`, 437 colonnes) atteignait une AUC
d'amplitude d'environ 0.72 et une AUC de direction d'environ **0.53**. Le
modèle savait reconnaître qu'un mouvement notable arrivait, et ne savait
presque pas dire dans quel sens. C'était la limite structurante : une stratégie
qui aurait parié la direction sur ce signal seul n'aurait pas eu d'edge.

Deux constats sur le catalogue expliquent la piste suivie :

- le dépôt contient ~1 240 lignes de moteur SMC (`app/core/smc*.py`) dont la
  couche ML ne se servait pas — **zéro** colonne SMC, **zéro** import depuis
  `app/ml/` ;
- l'analyse technique classique est essentiellement **symétrique**. Un RSI à 70
  ne dit pas la même chose en tendance et en range, et 53 colonnes de moyennes
  mobiles décrivent surtout la distance au prix. La structure de marché
  (cassure haussière ou baissière, balayage de liquidité, position dans le
  dealing range) est **orientée par construction**.

## 2. Le bloc SMC — le seul qui paie

Écarts d'AUC contre la référence `v4`, même fenêtre, même split chronologique,
seul le catalogue change :

| jeu | Δ AUC amp | Δ AUC dir |
|---|---:|---:|
| BTC/USDC 1 h  | +0.0099 | **+0.0756** |
| ETH/USDC 1 h  | +0.0067 | **+0.0643** |
| BTC/USDC 30 m | +0.0053 | **+0.0885** |
| ETH/USDC 30 m | +0.0071 | **+0.0640** |
| *moyenne*     | *+0.0073* | ***+0.0731*** |

En valeur absolue, la tête `dir` passe d'environ **0.53 à 0.60** — d'un pile ou
face à peine biaisé à un edge directionnel réel. Le gain est du même ordre sur
les quatre jeux, ce qui écarte l'accident sur un symbole ou un timeframe.

### Pourquoi c'est crédible et pas une fuite

Un gain de cette taille sur la tête réputée impossible mérite la question. Ce
qui a été vérifié :

1. **Test de préfixe** (`tests/test_features_smc.py::test_aucune_fuite_temporelle`).
   Les features sont recalculées sur `df[:4000]` puis comparées au calcul sur
   `df[:6000]` tronqué. Si la valeur d'une barre changeait selon qu'on connaît
   ou non l'avenir, elle lirait le futur. **20 des 21 colonnes sont identiques
   au bit près.** La 21ᵉ (`smc_liq_sell_dist_atr`) diverge sur 0.3 % des
   barres, et dans le sens conservateur — voir §5.
2. **Validation hors-temps.** Le split est chronologique (80 / 20) : la mesure
   porte sur des barres postérieures à tout l'entraînement.
3. **Réplication** sur 4 jeux — mais voir la réserve en tête de document :
   BTC et ETH corrèlent à 0.835, ce ne sont pas 4 échantillons indépendants.
   L'argument tient contre l'accident sur un symbole ou un timeframe, pas
   comme preuve de généralité.
4. **Mécanisme lisible.** Les deux colonnes qui dominent la tête `dir` sont les
   distances au FVG non comblé le plus proche, au-dessus et en dessous :

   ```
   tete dir : top 20 (BTC/USDC 1h, AUC 0.6096)
      1. smc_fvg_up_dist_atr      gain=5918.3   <-- SMC
      2. smc_fvg_dn_dist_atr      gain=5736.6   <-- SMC
      3. RSI_14_d3                gain=3322.3
      4. ret                      gain=3295.9
      ...
      8. smc_void_n_open          gain=2158.8   <-- SMC
     10. smc_liq_buy_dist_atr     gain= 803.1   <-- SMC
   ```

   Le rapport de ces deux distances dit littéralement **de quel côté se trouve
   le déséquilibre non comblé le plus proche**. C'est la thèse centrale d'ICT,
   et le modèle la retrouve tout seul. Ce n'est pas une colonne isolée au
   comportement inexplicable : 7 des 40 features les plus utiles sont
   nouvelles, réparties sur plusieurs familles.

   Sur la tête `amp`, la répartition est différente et tout aussi lisible —
   l'ATR domine (l'amplitude est de la volatilité), mais `smc_killzone` monte
   au 6ᵉ rang : les killzones de Londres et New York sont bien les moments où
   les grands mouvements se produisent.

## 2 bis. Le test de généralité : six actions décorrélées

BTC et ETH ne sont pas deux échantillons — ils corrèlent à 0.835. Six actions
XPAR (Airbus, LVMH, L'Oréal, TotalEnergies, Sanofi, BNP) corrèlent à **0.253**
entre elles et **0.07 à 0.30** avec la crypto : marché à séances, gaps de nuit,
microstructure et participants entièrement différents. C'est le test que le
dossier n'avait jamais passé.

Écarts contre `v4`, ~4 576 barres 1 h par titre (plafond Yahoo : 730 jours) :

| titre | Δ AUC amp | Δ AUC dir |
|---|---:|---:|
| AIR.PA | −0.0278 | **+0.0744** |
| MC.PA | +0.0061 | **+0.0477** |
| OR.PA | −0.0016 | **+0.0600** |
| TTE.PA | +0.0236 | **+0.0400** |
| SAN.PA | −0.0251 | **+0.0211** |
| BNP.PA | −0.0025 | **+0.0184** |
| **poolé (6 titres, 27 456 barres)** | −0.0065 | **+0.0931** |

**6 sur 6 positifs sur la direction**, moyenne +0.043, et +0.093 en poolant.
Sur le pool, `auc_dir` passe de 0.5092 — le hasard — à 0.6023.

Deux lectures importantes :

- **le gain est spécifique à la direction.** Sur l'amplitude, l'effet est nul
  et de signe variable (moyenne −0.005). C'est cohérent avec la crypto, où le
  gain amp (+0.007) était marginal à côté du gain dir (+0.073) : la structure
  de marché dit le SENS, pas l'ampleur — ce que l'ATR dit déjà ;
- **l'effet est plus net là où la référence est faible.** Le pool part de 0.509
  et gagne 0.093 ; BNP part de 0.540 et ne gagne que 0.018. Quand l'analyse
  technique classique donne déjà un signal directionnel, SMC ajoute moins.

Reproduire (nécessite `python scripts/backfill_equities.py --symbols … --tf 1h`) :

```bash
python scripts/measure_smc_on_equities.py
```

## 3. Le bloc régime — mesuré neutre

Idée : `classify_regime` existait et pilotait le routing des setups, mais
aucune colonne ne portait sa CONCLUSION. Ses six ingrédients (`ADX`,
`MM_bullish_align`, `MM_bearish_align`, `DI_diff`, `slope_SMA20`,
`BB_width_rank100`) étaient présents, donc le modèle devait redécouvrir la
règle à partir d'eux. Lui donner la conclusion devait économiser des splits.

Mesuré, l'effet n'existe pas :

| jeu | Δ AUC amp | Δ AUC dir |
|---|---:|---:|
| BTC/USDC 1 h  | −0.0036 | +0.0009 |
| ETH/USDC 1 h  | −0.0066 | −0.0016 |
| BTC/USDC 30 m | +0.0007 | +0.0010 |
| ETH/USDC 30 m | −0.0004 | −0.0009 |
| *moyenne*     | *−0.0025* | *−0.0002* |

Ajouté **par-dessus** le bloc SMC, le bilan reste nul : +0.0018 de moyenne sur
`dir`, −0.0006 sur `amp` — le signe change d'un jeu à l'autre.

L'interprétation la plus simple est que LightGBM reconstruit déjà la règle sans
peine : `classify_regime` est une poignée de comparaisons de seuils sur des
colonnes présentes, c'est-à-dire exactement ce qu'un arbre fait nativement. On
ne lui a rien appris.

Le bloc est **conservé** dans `omnibus_smc` — il ne coûte que 6 colonnes sur
464, ne dégrade rien, et rend le régime lisible dans les diagnostics — mais il
ne faut pas lui attribuer le gain : **tout le gain vient du bloc SMC.**

## 4. La tête triple-barrière — infirmée

Idée : `dir` demande « le rendement à t+h est-il positif ? ». La question
ignore le CHEMIN — un mouvement qui finit à +2 % après être passé par −3 % est
un succès pour `dir` et une position stoppée pour le bot. La tête `tb` demande
« une position longue ouverte ici, avec ce stop et cette cible, aurait-elle
touché la cible avant le stop ? », c'est-à-dire la décision réellement prise.

Mesuré dans le MÊME entraînement (mêmes features, même fenêtre, même split —
seule la question change) :

| jeu | AUC `dir` | AUC `tb` | écart |
|---|---:|---:|---:|
| BTC/USDC 1 h  | 0.6055 | 0.5688 | −0.0367 |
| ETH/USDC 1 h  | 0.5973 | 0.5515 | −0.0458 |
| BTC/USDC 30 m | 0.6118 | 0.5896 | −0.0222 |
| ETH/USDC 30 m | 0.5907 | 0.5704 | −0.0203 |

`tb` est moins bien discriminée que `dir` sur les quatre jeux. L'explication la
plus vraisemblable est que la cible triple-barrière est **plus difficile** : il
faut avoir raison sur le sens, sur l'ampleur ET sur l'ordre d'arrivée des deux
barrières. Une fois le bloc SMC en place, `dir` n'est plus le maillon faible
qu'elle était, et la reformulation ne rachète pas ce surcroît de difficulté.

Deux réserves, honnêtement :

- les AUC ne sont pas strictement comparables — `tb` a ~34 % de positifs contre
  ~50 % pour `dir` ;
- l'AUC mesure le classement, pas la rentabilité. `tb` répond à la question du
  trader ; il est possible qu'un seuil sur `tb` sélectionne de meilleurs trades
  malgré une AUC plus basse. **Trancher demande un backtest, pas une AUC** — et
  c'est le travail de la stratégie qui consommera la recette.

Le schéma `triple_barrier` et la recette `omnibus_smc_tb` sont conservés comme
véhicule de mesure. **Ils ne sont pas destinés à la production en l'état** :
`lgbm_amp_dir_bundle` n'écrit que `.amp.lgb` et `.dir.lgb`, donc la tête `tb`
est entraînée puis perdue à la sauvegarde. `TrainedRecipe._save_bundle` émet
désormais un avertissement explicite plutôt que de la laisser disparaître en
silence. Servir `tb` demanderait un format de persistance multi-têtes — à ne
construire que si un backtest montre que `tb` sélectionne mieux.

## 5. Deux défauts trouvés par le test de causalité

Le test de préfixe a rapporté deux vrais défauts pendant l'écriture, qu'aucun
test de valeur ou de forme n'aurait vus.

**Un ordre de clés qui dépendait de la longueur de la série.** Un FVG porte
`mitigated_at` (le prix est entré dans le gap) et `filled_at` (le gap est
refermé). En lisant `filled_at` en premier, la fin de validité changeait selon
la fenêtre analysée : sur un préfixe s'arrêtant avant le comblement,
`filled_at` valait `None` et on retombait sur `mitigated_at` ; sur la série
complète, `filled_at` masquait `mitigated_at`. Deux réponses pour la même
barre. Corrigé en prenant le **minimum** des fins candidates.

**Des compteurs qui étaient des horloges.** Sans péremption, une zone jamais
invalidée par le prix restait comptée indéfiniment, et le compteur mesurait le
temps écoulé plutôt que la structure : `smc_breaker_n_fresh` corrélait à
**0.96** avec l'indice de barre, `smc_ob_bear_n` à 0.86, `smc_fvg_up_n` à 0.82.
Une telle colonne laisse le modèle reconnaître l'époque du jeu d'entraînement —
AUC flatteuse en validation, rien en direct. Corrigé par une durée de vie
maximale de 200 barres, qui a aussi un sens métier : un order block que le prix
n'a pas revisité depuis 200 barres n'est plus un niveau surveillé. Après
correction, la corrélation maximale tombe à 0.23.

**La divergence résiduelle, et pourquoi elle est acceptable.** Le moteur SMC
ré-agrège les poches de liquidité : un cluster `{3263, 3271}` formé en 3274
devient `{3263, 3277, 3281}` formé en 3284 quand des touches ultérieures
apparaissent au même niveau. La poche existe donc en temps réel entre ces deux
barres et disparaît de l'analyse a posteriori. Le SENS décide de la gravité :
c'est le calcul **hors-ligne qui voit MOINS** que le direct, jamais plus. Un
modèle entraîné là-dessus est pénalisé, pas avantagé — l'inverse d'une fuite.
L'écart touche 0.3 % des barres sur une seule colonne ; le test le borne à 1 %
pour signaler tout changement de comportement du moteur.

## 6. Ce qui est livré

| élément | rôle |
|---|---|
| `app/ml/features_smc.py` | 21 features SMC causales, distances normalisées en ATR |
| `app/ml/features_catalog.py` → `v5_smc@1` | v4 (437) + SMC (21) + régime (6) = **464 colonnes**, bascules d'ablation `smc` / `regime` |
| `app/ml/labelling.py` → `triple_barrier` | 3ᵉ tête `tb`, barrières en ATR, convention pessimiste |
| `app/ml/recipe.py` → `label_params` | bloc `labels.params:` désormais lu et intégré au hash |
| `recipes/omnibus_smc.yaml` | **la recette recommandée** |
| `recipes/omnibus_smc_tb.yaml` | véhicule de mesure de la 3ᵉ tête |
| `tests/test_features_smc.py` | causalité, stationnarité, contrat (7 tests) |
| `tests/test_labelling_triple_barrier.py` | logique des barrières sur séries construites (12 tests) |
| `tests/test_features_catalog_v5.py` | bascules d'ablation, `label_params` (11 tests) |

### Le `labels.params` manquant

En instrumentant les barrières, un défaut de câblage est apparu :
`load_recipe` lisait `features.params` mais **pas** `labels.params`. Un bloc

```yaml
labels:
  params:
    tb_tp_atr: 1.5
```

n'était lu par personne — la recette aurait décrit une cible et l'entraînement
en aurait construit une autre, en silence. `label_params` est ajouté par
symétrie avec `features_params`, et **entre dans `Recipe.hash()`** : deux jeux
de barrières définissent deux cibles différentes, les laisser hors du hash leur
ferait partager une lignée d'artefacts et le gate comparerait des candidats qui
ne répondent pas à la même question.

## 7. Limites connues

- **Le journalier reste hors de portée.** En 1 d, ni `omnibus_full` ni
  `omnibus_smc` n'atteignent le plancher `auc_floor: 0.55` sur les ~2 600
  barres du dépôt. Le gate rejette les candidats journaliers — comportement
  voulu, pas une panne.
- **Le coût de construction du bloc SMC est réel** : ~0.8 s pour 8 000 barres,
  ~4 s pour 52 000, linéaire. L'entraînement passe d'environ 25 s à 75-110 s
  par modèle. Négligeable pour un réentraînement toutes les 800 barres, à
  surveiller si la cadence devait descendre.
- **Aucun backtest n'a encore été fait.** Tout ce document parle d'AUC, donc de
  qualité de classement. Qu'un edge directionnel de 0.60 se transforme en
  rentabilité nette de frais dépend entièrement de la stratégie qui l'exploite
  — c'est l'étape suivante.
