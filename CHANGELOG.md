# 📝 Changelog

Historique des versions du Crypto Bot.

---

## [Non publié]

### 🏛 La venue devient la source unique de vérité spot/margin

`venues.default` était **vide** et `venues.assign` aussi : tout symbole crypto
retombait sur `default_venue_from_cfg`, qui fabriquait une venue à partir des
globales `exchange.margin` / `trading.margin_mode` / `trading.max_leverage`. Sur
la config livrée, cette venue s'appelait **`margin-isolated`** — le nom exact
d'une entrée de `venues.defs`, mais avec un levier différent (1 au lieu de 3).
Deux objets homonymes et divergents, indiscernables dans les logs et sur les
positions ; les trois `defs` du fichier n'étaient jamais lues.

`venues.default` est désormais **obligatoire** dès que `venues.defs` existe, et
toute venue référencée doit exister — `_validate_venues` refuse le démarrage
sinon. Le repli sur les globales subsiste pour une config sans bloc `venues:`,
mais son nom est préfixé `auto:` : plus jamais homonyme.

Deuxième défaut, plus coûteux : **le coût d'emprunt était facturé sans regarder
la venue**. `borrow_cost` était appelé inconditionnellement des deux côtés
(`backtest.py` et `position_close_mixin.py`), donc chaque trade SBF 120 payait
`trading.borrow_rate_daily` = 0,072 %/jour, soit **~30 %/an d'intérêt fictif sur
un achat au comptant**. C'est maintenant la venue qui tranche
(`Venue.borrows` / `effective_borrow_rate`) : `margin` et `perp` empruntent, le
spot jamais — ni en crypto, ni en actions. Au passage, le warning
« paper + margin » annonçait un remède inopérant (`margin: false` ne supprimait
pas l'emprunt, porté par le taux global) ; il pointe désormais vers
`venues.default`.

Le bot ne connaît **pas** les enveloppes fiscales (CTO, PEA) : c'est une notion
de compte, pas de moteur. Une venue actions, c'est `market_type: spot` +
`max_leverage: 1` + `allow_short: false`. Un garde-fou
(`_enforce_market_coherence`) ramène à leur valeur neutre le levier, le
`margin_mode` et le taux d'emprunt déclarés sur une venue spot, en le
journalisant, plutôt que de les honorer à moitié.

Comportement inchangé sur la config livrée : `default: margin-isolated` à
levier 1 = ce que produisait le repli. Les témoins de `test_generic_parity` ont
bougé du seul montant des intérêts fictifs supprimés (signaux et nombre de
trades identiques).

### 🔄 D6 — le lifecycle décide seul, `manual_active` devient `force_active`

Les **15 slots forcés ACTIF** dans `config.yaml` sont retirés : la machinerie
candidat → essai → actif → retiré décide seule. La clé est renommée
`lifecycle.force_active` (l'ancien nom reste lu, avec un WARNING de
dépréciation) — `manual_active` sonnait comme un réglage de routine alors que
c'est un court-circuit du cycle de vie.

Deux précisions que ni l'audit V12 ni `ANALYSE_CRITIQUE` n'avaient relevées :

1. **La liste ne décidait pas quels bots tradent.** Les audits en concluaient un
   risque de « trader des setups non validés OOS » : inexact. La sélection vient
   du classement OOS (`optimizer_results` + `MIN_VIABLE_SCORE` +
   `trading.top_strategies_per_tf`, cf. `get_active_strategies_per_tf`), qui
   s'appliquait déjà. Ce que la liste pilotait, c'est l'**état** et les
   transitions.
2. **Le forçage bloquait aussi le RETRAIT.** `_propose` retourne `ACTIF` avant
   toute autre règle : un slot forcé échappait aux deux règles de sortie (budget
   effondré, live qui contredit la simulation en perdant). Un bot forcé perdant
   n'était **jamais** retiré, donc jamais ré-optimisé. C'est le vrai coût du
   forçage ; il est désormais verrouillé par un test.

L'incohérence `manual_active` (15) ↔ `slot_budgets` annoncée à 7 slots par les
audits n'en comptait en réalité qu'**un** (`trend_rider::1h::BTC/USDC`),
lui-même absent de `manual_active`.

### 🗂 La configuration est découpée par responsabilité

`config.yaml` (342 lignes) mélangeait cinq responsabilités sans rapport. Il ne
porte plus que le sommaire (`include:`) ; chaque fichier de `config/` est aligné
sur une brique du code : `venues.yaml`, `risk.yaml`, `data.yaml`,
`lifecycle.yaml`, `ops.yaml`. La config effective est **identique** à la version
monolithique (vérifié section par section), et une config sans `include:` reste
valide.

Une section vit dans **un seul** fichier : la déclarer deux fois fait échouer le
chargement, plutôt que de laisser l'ordre de lecture trancher en silence.

Le point délicat était l'**écriture** : l'UI persiste budgets, forçages et
params optimisés via `update_config_yaml(fn)`. Celui-ci route maintenant chaque
section modifiée vers son fichier propriétaire, ne réécrit que les fichiers
touchés, et préserve les commentaires. `deploy/backup.sh` archive `config/`
autant que `config.yaml` — sauvegarder le seul fichier racine ne ramènerait plus
que le sommaire.

### 🔗 Le maillon manquant entre le sizing et le stop réellement posé

`_initial_stop_distance` — la fonction qui fournit `stop_dist` à `compute_size`
en live — n'apparaissait dans **aucun test**. `compute_size(stop_dist=…)` était
verrouillé d'un côté, le stop posé à l'ouverture de l'autre, mais rien ne
vérifiait que les deux parlent de la même distance. Les deux chemins recopient
la même chaîne de priorité (`sl_atr_mult` → `stop_hint` → trailing) dans deux
fichiers distincts : une divergence silencieuse de facteur 2,5 était possible
sans qu'un test tombe.

Vérification faite, le repli est correct — il lit bien `live.trailing.trail_wide`
et non un 2,5 en dur — et le sur-risque annoncé par l'audit n'existe que sur le
chemin dégradé (stop illisible → `stop_dist = 0` → ATR brut), désormais chiffré
plutôt que commenté. S'y ajoute la parité « même signal ⇒ même distance au stop
des deux côtés », sur six signaux représentatifs.

### 🏷 Les pages canoniques perdent le suffixe `-v2`

`-v2` datait de la coexistence avec les pages Jinja2, supprimées depuis : le
suffixe ne distinguait plus rien. `/portfolio`, `/bots` et `/settings` sont les
pages ; les anciennes URLs restent redirigées en 308 (elles ont vécu en prod, et
un 308 est mis en cache durablement par les navigateurs). Sidebar, palette de
commandes, manifest PWA et specs e2e alignés.

### 🧹 `app/web`, la structure `timeframes` morte et des commentaires périmés

`app/web/` ne contenait plus qu'un `__init__.py` **vide** depuis la suppression
des templates Jinja2, sans aucun `import app.web` nulle part.

La structure racine `cfg["timeframes"]` était **fabriquée** à chaque chargement à
partir du seul `trading.timeframe`, alors que le bot tourne sur
`trading.timeframes` (5 TF). Personne ne la lisait sauf le log de démarrage, qui
annonçait donc `TF=['1h']` pendant que le scanner couvrait 15m/30m/1h/4h/1d. Le
schéma hérité reste lu ; il n'est simplement plus inventé.

Le bloc d'avertissement SBF 120 de `config.yaml` annonçait `verified: false` et
un backfill à faire : les deux étaient faux — `data/universe/sbf120.yaml` porte
`verified: true` (as_of 2026-07-26) et le cache contient 15m/30m/1h/4h/1d pour
~120 titres. L'avertissement « machine neuve » et la limite Yahoo (~88 bougies
intraday sur actions européennes) restent, eux, valables.


### 🧹 Les caches de marché sortent du dépôt, `starlette` est épinglé

`data/ohlcv/` et `data/derivatives/` sont des **caches** que le bot reconstruit
seul. 79 fichiers y étaient suivis pour 14 Mo, produisant un diff binaire à
chaque cycle de scan — 180 fichiers modifiés en permanence, noyant les
changements de code sans rien apporter : un parquet d'OHLCV n'est pas
relisible, et sa version d'hier n'a aucune valeur d'archive. Ils sont
désormais ignorés et détachés (les fichiers restent sur disque). Restent suivis
à dessein : `data/universe/`, `data/oos_tracker.json`,
`data/backtest_history.json` — écrits par décision, pas par accumulation.
Conséquence assumée : **un clone neuf démarre avec un cache vide**, à amorcer
par `scripts/backfill_equities.py` ou le premier cycle du scanner.

`starlette==0.38.6` est épinglé alors que c'est une transitive de `fastapi`,
contre la règle d'en-tête du fichier. La règle suppose que les transitives sont
tenues par leur parent ; ce n'est vrai que d'un côté. L'environnement de dev
s'est retrouvé en starlette 1.3.1 — incompatible avec `fastapi==0.115.0`, qui
demande `<0.39` — et `APIRouter()` levait `TypeError: Router.__init__() got an
unexpected keyword argument 'on_startup'` : toute la collecte des tests API
échouait, sur un composant que personne n'avait touché.

### 🎯 `gate.holdout_bars` 1500 → 1400, et ce que la mesure a démenti

Le seuil d'éligibilité au pooling passe de 1 750 à 1 650 barres, ce qui fait
entrer 2 titres de plus en journalier (114 → 116 sur 120).

**Ce réglage ne débloque pas le 4h ni le 15m**, contrairement à ce qu'on
pouvait attendre — et aucun réglage du gate ne le fera. Le plafond y est celui
de **Yahoo** : rétention de 60 jours en 15m/30m et de 730 jours en 1h, dont le
4h est ré-agrégé côté client. Cela borne à **1 428** barres en 15m, **714** en
30m et **1 445** en 4h, sous le seuil quoi qu'on fasse. Aucun backfill ne peut
aller chercher ce qui n'existe pas à la source. Débloquer ces TF demanderait un
holdout **par timeframe** (~300), donc un bloc `gate_by_tf` sur le modèle de
`hp_by_tf` — inscrit au plan comme ML-20, avec l'arbitrage qu'il suppose :
300 barres en 15m valent ~9 séances.

La mesure ML-17 a été **rejouée** après passage de `backfill_equities.py`, qui
a approfondi le cache journalier (médiane ~1 000 → **6 824** barres). Le constat
de la veille — « 116 titres sur 117 ne peuvent produire aucun modèle seuls » —
est caduc : ils sont maintenant 17 sur 120. La comparaison solo vs poolé passe
de n = 1 à **n = 8** : 6 titres où le pooling aide, 1 où il coûte, 1 équivalent,
écart moyen **+0,016**. Ces huit titres étant précisément ceux qui ont assez
d'historique pour s'en passer, ce que le chiffre établit n'est pas un gain mais
l'**absence de dégradation** — ce qui autorise à servir tout l'univers avec un
modèle unique au lieu de maintenir deux régimes.

### 🔒 Sécurité — l'alerte de crash exfiltrait le log, les sauvegardes étaient world-readable

`notify-crash.py` envoyait les 20 dernières lignes de `bot.log` à Telegram et
CallMeBot. Le filtre en place ne masquait que ce qui **ressemble** à un secret
(`token=`, hexadécimal long) ; il n'a jamais protégé symboles, tailles de
position et soldes — qui sont la vraie fuite. Le log n'est plus transmis par
défaut : `notifications.crash_include_log: false`, et l'alerte dit où regarder
sur la machine. Un défaut sûr doit être le silence, pas la confiance dans un
filtre par motifs.

`backup.sh` recopiait `config.yaml` (clés API exchange, tokens de notification)
et `trades.db` avec le umask par défaut, soit `0644` : la sauvegarde était un
contournement des permissions du fichier d'origine. `umask 077` + `chmod
600`/`700`, avec rattrapage des archives déjà écrites par les versions
antérieures.

Bumps CVE : `jinja2` 3.1.4 → **3.1.6** (et non 3.1.5, elle-même vulnérable à
CVE-2025-27516), `sqlalchemy` 2.0.30 → 2.0.32.

### 📊 Observabilité — métriques Prometheus et logs structurés

`/metrics` expose l'état du bot (`app/core/metrics.py`). Les métriques **métier**
sont dérivées de `EventHub.publish` plutôt que semées dans `live_trader` et
`position_manager` : le hub voit déjà passer chaque ouverture, clôture, signal et
événement de risque, donc l'instrumenter une fois garantit que métriques et flux
WebSocket racontent la même histoire. `prometheus-client` reste **optionnel** —
sans lui tout est no-op et `/metrics` répond 503. Les requêtes HTTP sont
libellées par *template* de route, jamais par URL concrète : c'est le mode de
panne classique d'une instrumentation Prometheus, silencieux jusqu'à ce que la
mémoire du serveur enfle des semaines plus tard.

Le handler **fichier** écrit maintenant du JSON Lines (`logging.format`, `text`
pour revenir en arrière) ; la console garde son format coloré. Chaque ligne porte
un `correlation_id` qui relie entre elles toutes celles d'une même requête ou
d'un même job — avec trader, retrain et optimiseur écrivant dans le même
fichier, l'entrelacement rendait jusqu'ici impossible de suivre une opération.
Un `ContextVar` ne traversant pas un thread, les jobs de fond transportent
l'identifiant explicitement (`run_with_correlation`). Les ~900 f-strings du
dépôt ne sont **pas** réécrites : le structuré s'ajoute autour, via `extra=`.

Au passage : `_ColorFormatter` mutait `record.levelname`, si bien que les
séquences ANSI finissaient dans `bot.log` — visible dans les rotations archivées
(`[[32mINFO[0m]`). Le fichier n'était grep-able qu'en connaissant les codes
d'échappement.

### 🎚️ La calibration isotone était activée en 1 h malgré la mesure contraire

`omnibus_v4_multi` portait `calibrate: true` pour **tous** les timeframes, alors
que la mesure (ECE **+461 %** en 1 h contre −47 % / −67 % en 15 m / 30 m) était
écrite depuis longtemps. Les recettes acceptent désormais un bloc `hp_by_tf:`,
et la sous-décision ouverte est tranchée en sa faveur plutôt qu'une recette par
TF.

Sa précédence est **volontairement inversée** : le bloc par TF l'emporte sur les
`params` reçus. La raison est mécanique, pas philosophique — chaque stratégie
recopie `hp` dans ses `_DEFAULTS` et ses `fixed_params`, valeurs sans timeframe
qui arrivent donc toujours dans `params`. Traité comme un simple défaut, le bloc
n'aurait jamais été appliqué : on aurait écrit un réglage inerte, exactement
l'écart entre le doc et le code qu'on corrigeait. Appliqué sur les **deux**
chemins d'entraînement (`recipe_trainer` et `MLBackend._train_impl_wrapper`).

### 🧺 Le pooling multi-symboles était testé mais appelé par personne

`recipe_trainer.train_multi` avait ses tests et aucun chemin depuis l'API ou
l'UI. `train_multi_and_publish` le branche : `symbols[]` sur `/api/ml/train`
(exige une recette), champ « Pooling multi-symboles » sur la page Modèles. Le
holdout est prélevé **par symbole avant** l'entraînement, et le gate arbitre sur
la moyenne **non pondérée** des scores par titre — pondérer par le nombre de
barres laisserait le titre au plus long historique décider seul, ce que le
pooling cherche précisément à éviter.

**Mesuré sur données actions réelles** (`scripts/measure_pooling_equities.py`,
depuis le cache local, sans réseau) : sur 117 titres `.PA` en 1 d, **un seul**
(AC.PA) a l'historique requis pour un modèle solo. Les 116 autres ne peuvent
produire aucun modèle sans pooling — la prédiction « ~13,7 ans pour un titre
seul » est confirmée par la mesure, pas par le calcul. Le modèle poolé sur 16
titres rend **AUC amp 0,641** sur un holdout jamais vu (0,764 en validation),
15/16 au-dessus du plancher 0,55. La direction reste **au niveau du hasard**
(0,505), cohérent avec la mesure crypto. Solo vs poolé sur AC.PA — seule
comparaison possible : 0,700 → 0,713, soit un garde-fou contre une dégradation
et non une preuve de gain, sur n = 1.

Ce que la mesure révèle en passant : c'est `gate.holdout_bars: 1500` (~6 ans de
séances Euronext), et non la recette, qui exclut 101 titres du pool.

### 🧾 Les artefacts `stat48_*` n'étaient pas promouvables par le gate

`_train` déléguait à `recipe_trainer` depuis l'étape C, mais l'**écriture**
restait celle de la classe : `save_lgb_with_scaler` produit un meta.json
`format_version: 1` sans liste de features ni médianes. Le scorer générique ne
pouvait donc pas reconstruire la matrice d'entrée du holdout, retournait
`unsupported_format`, et le gate concluait « comparaison manuelle requise » quoi
qu'il arrive. `scoring_statistique_opus_v4`/`v5` conservent désormais le
`TrainedRecipe` complet et `save_model` délègue à `TrainedRecipe.save`. Mesuré
sur le même artefact : `{'unsupported_format': True}` → `{'auc_amp': …}`.

`ml_dynamic_threshold` reste en dehors : son `_train` n'a jamais été migré (il
entraîne encore via son propre `_train_lgbm`), donc l'équivalence n'y est pas
acquise et la bascule y serait un pari.

### 🧭 Le routeur de venues masquait tout le contrat du provider actions

Symptôme : `scripts/backfill_equities.py --tf 1d` plafonnait **chaque** titre du
SBF 120 à ~2447 barres depuis `2017-01-01` — la fondation d'OKX — alors que
Yahoo sert AC.PA depuis le 3 janvier 2000. En 1 h, même mécanique : ~900 barres
au lieu des 730 jours servis par Yahoo.

`ProviderRouter` route `fetch_ohlcv` par symbole, mais son `__getattr__` renvoie
**tout le reste** à l'exchange par défaut, c'est-à-dire l'exchange crypto. Les
quatre points de contrat que `YFinanceProvider` expose au `CandleStore` étaient
donc systématiquement lus sur ccxt : `min_since_ms` (plancher 2017 au lieu de 0),
`bars_span_ms` (temps calendaire continu au lieu du temps de séance),
`drop_zero_volume` (barres à volume nul rejetées, alors qu'elles sont légitimes
sur une valeur peu liquide) et `fetch_ohlcv_max` (**absent** : l'amorçage
`period='max'` livré juste avant n'était jamais emprunté en live).

`CandleStore` résout maintenant le provider réellement interrogé pour le symbole
(`_provider_for`) avant de lire l'un de ces quatre attributs. Les venues crypto
ne voient aucun changement — le routeur leur rend l'exchange par défaut, comme
avant. Après correction, AC.PA passe de 2447 à **6824 barres journalières**
(2000-01-02 → 2026-07-26) et de 897 à **4459 barres horaires**.

### 🕐 Les bornes du cache OHLCV se lisaient en heure locale

La colonne `time` est un `Datetime` **naïf** qui porte de l'UTC.
`datetime.timestamp()` la relisait en heure **locale** : sur une machine à UTC+1,
les bornes du cache repartaient une heure trop tôt. Ce n'était pas inoffensif —
`before_ms` trop bas faisait s'arrêter le backfill historique **avant** les
bougies qui touchent le cache, laissant un trou permanent d'un fuseau à la
jonction, que rien ne venait jamais combler. Le test
`test_an_already_seeded_cache_catches_up_its_depth` échouait déjà pour cette
raison, invisible sur un CI en UTC.

Même bug dans `OHLCVCache._drop_forming_candle` : la dernière bougie paraissant
plus vieille d'un fuseau, une bougie 15 m / 30 m / 1 h en formation n'était
**jamais** élaguée à Paris — le live scorait sur un `close` provisoire (repaint)
que le backtest, lui, ne voit pas. Un helper unique `candle_store.epoch_ms()`
porte désormais la conversion.

### 🧬 Étape C — entraîner depuis la seule recette, sans classe `Strategy`

`features.catalog` était déclaré par les recettes depuis l'étape B mais
**n'était dispatché nulle part** : il n'entrait que dans `Recipe.hash()`. Seule
une classe `Strategy` savait construire des features, d'où l'asymétrie relevée
par §2 de la conception — lecture pilotée par la recette (`build_predictor`),
écriture pilotée par la stratégie (`Strategy().fit`). C'est la raison pour
laquelle la page « Modèles », indexée par recette, doit demander une stratégie.

- **Nouveau — `app/ml/features_catalog.py`** : registre de catalogues, contrat
  uniforme `FeatureSet` (frame aligné + noms). Les trois catalogues du dépôt
  sont branchés. `stat48` construisait un `np.ndarray` **anonyme** ; ses 56
  colonnes sont désormais nommées dans l'ordre exact de construction, verrouillé
  par test contre la sortie réelle du constructeur.
- **Nouveau — `app/ml/labelling.py`** : registre de schémas de labellisation.
  `amp_dir_quantile` (quantile d'amplitude, deux têtes) et `vol_adaptive_dir`
  (seuil adaptatif à la volatilité, une tête — ce que `dyn_threshold_v1`
  revendique). Le schéma est **déclaré** (`labels.scheme:`), pas déduit des
  têtes : deux recettes à tête unique peuvent viser des cibles différentes.
- **Nouveau — `app/ml/recipe_trainer.py`** : `train(recipe, df, tf)` n'importe
  aucune stratégie — verrouillé par un test qui fait échouer tout import de
  `app.strategies.*` pendant l'entraînement. `supports()` refuse explicitement
  ce qu'il ne sait pas reproduire (calibration isotone, élagage de features,
  recettes `proxy`) plutôt que de produire silencieusement un autre modèle.

**Le gain mesurable** : `save_lgb_with_scaler` ne sérialise ni features ni
médianes, donc le scorer générique rapportait `unsupported_format` et le gate
concluait « keep » quoi qu'il arrive — `stat48_v4`/`stat48_v5` n'étaient
**jamais promouvables**. L'artefact produit par le chemin recette porte ses
noms de colonnes : le gate le score enfin.

**Calibration isotone et élagage** sont désormais portés : `supports()` accepte
`omnibus_v4_multi`, la recette de production. L'élagage passe par l'artefact
(`train_meta["kept_features"]`, réinjectable via `params`) plutôt que par un
attribut de processus — même comportement, mais inspectable.

**`/api/ml/train` accepte `recipe=`** et un nouveau `GET /api/ml/recipes` liste
les recettes avec leur entraînabilité. La page « Modèles » propose donc les
recettes en tête de liste : le formulaire parle enfin le vocabulaire de la
table de registre juste au-dessus. Le sweep reste piloté par la stratégie —
`window_sweep` n'a pas encore de variante recette.

**Équivalence mesurée.** `scripts/check_recipe_trainer_equivalence.py`
donne deux résultats opposés, et c'est le point :

- **`omnibus_v4_multi` : écart 0.00000000, corrélation 1.000000** face à
  `MLBackend`. Basculer une recette omnibus ne change PAS le modèle produit —
  c'est la mesure qui rend la bascule sûre. Verrouillé par test.
- **`stat48_v5` : écart 0.000000, corrélation 1.0000** — après correction de
  deux défauts, un de chaque côté (voir ci-dessous).

Aucune bascule automatique pour autant : les `_train` autonomes restent en
place. Changer le chemin d'une recette reste une décision d'exploitation.

### 🧬 Étape C (suite) — les `_train` autonomes disparaissent, et le pooling multi-symboles

- **`scoring_statistique_opus_v4/v5` : 257 lignes de boucle LightGBM supprimées.**
  `_train` délègue à `recipe_trainer` et ne garde que ce qui appartient
  légitimement à la stratégie : le cache de features du backtest et l'état ML
  en mémoire. `recipe_trainer.train()` accepte désormais un `FeatureSet` déjà
  construit (`features_catalog.from_matrix`), sans quoi router `score()` par
  lui aurait recalculé les features à chaque réentraînement walk-forward — un
  correctif payé d'une régression de performance sur la boucle chaude.
  Équivalence vérifiée sur **les deux** chemins : `fit()` (écart 0.000000) et
  `score()` (10 fenêtres, **0 divergence** de signal, `p_event`/`p_up` à 6
  décimales).
- **Nouveau — `recipe_trainer.train_multi(recipe, {symbole: df}, tf)`.** Une
  recette entraînée sur plusieurs symboles mis en commun. Trois pièges traités
  explicitement : les features sont construites **par symbole** (une fenêtre
  glissante ne doit jamais traverser une jointure entre deux titres — ce sont
  les matrices X/y qui sont empilées, jamais les bougies) ; le découpage est
  **temporel et commun**, pas indiciel (couper à 80 % des lignes mettrait le
  premier symbole en entraînement et le dernier en validation) ; les niveaux de
  prix n'entrent pas dans la matrice, les labels étant des rendements. Un titre
  trop court est écarté avec sa raison sans faire échouer le lot, et la
  provenance nomme les symboles poolés.

  Vérifié : 8 titres de 1 200 barres journalières, **chacun sous `min_bars`
  donc inentraînable seul**, produisent ensemble 7 672 lignes d'entraînement.

- **Piège polars signalé** : `Series.to_numpy()` sur une colonne `Datetime`
  **segfaute** en polars 1.0.0 (la version épinglée) — pas d'exception, le
  process meurt. Le découpage temporel passe par `.dt.epoch("s")`. Aucun autre
  site du dépôt n'utilise ce motif ; un test de garde le verrouille.

### 🐛 Deux défauts trouvés en cherchant l'origine de la divergence stat48

Le premier diagnostic accusait `n_estimators` (300 codé en dur contre 500
déclaré). **C'était faux** : l'early stopping tranche bien avant 300.

- **`scoring_statistique_opus_v4/v5` — entraînement sur 250 barres.**
  `_get_or_build_features` retombe, hors backtest, sur les 250 dernières
  barres : dimensionnement correct pour `score()`, qui ne lit que la dernière
  ligne, mais `_train` l'empruntait aussi. **Quelle que soit la fenêtre passée
  à `fit()` — 3 400 barres depuis le gate, 8 000 depuis le runner — le modèle
  n'apprenait que sur 200 lignes plus 50 de validation**, alors que la recette
  annonce `min_bars: 2000`. Aucun message ne le signalait. Nouveau
  `_features_for_training`, qui construit sur toute la fenêtre reçue ;
  `score()` garde sa fenêtre courte, y bâtir des milliers de barres à chaque
  appel coûterait cher pour une seule ligne lue.
- **`recipe_trainer` — `max_bin` codé en dur.** Le module imposait le 63 de
  MLBackend, donc aucune recette ne pouvait décrire un modèle au défaut
  LightGBM (255) : c'était reproduire le défaut même qu'on corrige. Les
  réglages LightGBM viennent maintenant du bloc `hp:` de la recette
  (`_LGB_KEYS`), et `stat48_v4`/`stat48_v5` déclarent `max_bin: 255` — ce que
  leurs stratégies utilisent réellement.

### 🏛 G2 — les actions SBF 120 sont activées (données, pas exécution)

Tout le code G2 était livré ; il restait inactivé, et un maillon manquait.

- **`app/core/bot_identity.py`** — `resolve_venue` gagne un échelon
  « univers » : la clé `venue:` d'un `data/universe/*.yaml` **activé dans
  `scanner.universe`** route ses membres. `universe_venue()` existait et était
  testé, mais aucun code de production ne l'appelait : activer `scanner.universe`
  ajoutait bien les 120 instruments au scanner, tous résolus sur la venue crypto
  par défaut — donc cherchés chez OKX et jamais chez le provider actions. La
  seule alternative documentée était d'écrire une ligne d'`assign` par titre.
  Précédence : `assign` explicite (slot, puis symbole) > univers > `assign`
  par stratégie > `venues.default`. Un univers non référencé ne route rien.
- **`config.yaml`** — venue `euronext-paper` (XPAR, EUR, `fractional: false`,
  TTF, plancher de courtage), `scanner.universe: [sbf120]`,
  `min_volume_by_asset_class.equity` (le seuil crypto de 5 M$/24 h excluait
  tout le SBF 120) et le bloc `providers.yfinance`. `can_execute: false` :
  **aucun ordre n'est transmis**, le bot émet une notification de trade — G3
  reste à faire.
- **Nouveau — `scripts/backfill_equities.py`** : amorce le cache Parquet pour
  un univers. Le runner ML ne fetch jamais (il lit le cache, d'où sa
  reproductibilité) ; sur crypto le scanner remplit le cache tout seul, sur
  actions rien ne l'avait jamais fait. Incrémental et réentrant, un titre en
  échec n'interrompt pas les autres, et il annonce à l'avance les troncatures
  imposées par Yahoo (1 h → 730 j, 15 m/30 m → 60 j, journalier illimité).

Le paquet `yfinance` **n'était pas** ajouté aux dépendances : il réinstalle
pandas, retiré en phase 6. Ce choix a été **repris ci-dessous** — l'API chart
publique ne ramène plus de données.

### 🐛 Actions — `limit` tronquait du mauvais côté, le backfill n'avançait qu'au compte-gouttes

Symptôme : en relançant le backfill, le cache 15 m grandissait un peu à chaque
fois — 612 → 816 → 952 → 1000 — puis **plafonnait pile à 1000**, la valeur de
`limit`. Le 1 h, lui, restait bloqué à 897 barres.

`fetch_ohlcv` violait la sémantique ccxt. Avec un `since`, ccxt rend les
`limit` premières bougies **à partir de** `since` ; sans `since`, les `limit`
**dernières**. Le provider rendait la queue dans les deux cas. Toute demande
tournée vers le passé recevait donc la tranche la plus récente — c'est-à-dire
des bougies déjà en cache. Le backfill en concluait « rien de plus ancien » et
n'engrangeait que le mince résidu antérieur au cache, de moins en moins à
chaque passe, jusqu'à plus rien au-delà de `limit` bougies.

La preuve était dans les données sans qu'on la voie : **le 4 h remontait au
2025-04-02 (480 jours) alors que le cache 1 h du même titre s'arrêtait au
2026-02-17 (160 jours)**. Or le 4 h est agrégé depuis le 1 h — même donnée
Yahoo, quatre fois plus de profondeur, uniquement parce que l'agrégation
divise le nombre de bougies **avant** la troncature.

- `_finalize` tronque désormais par la tête quand `since` est fourni, par la
  queue sinon. La pagination du `CandleStore` (`since = batch[-1][0] + 1`)
  reposait déjà sur ce contrat : elle sautait jusqu'à la dernière bougie
  disponible au lieu d'avancer d'un lot.
- `since=0` n'est plus confondu avec « pas de borne ». `if since:` traitait
  zéro comme absent, or le provider actions abaisse `min_since_ms` à 0 — une
  action cote avant 2017 — donc la valeur circule réellement.

Mesuré, cache court au départ, source profonde, cinq relances : `1000 → 1000
→ …` avant, `2370` dès la première après. Combiné au chemin profond ci-dessous,
la profondeur complète est atteinte en une passe.

### 🛑 `backfill_equities.py` refuse un timeframe inconnu avant la première requête

Un `--tf` mal découpé (coquille, virgule manquante, shell qui scinde
l'argument) lançait tout de même les 121 titres, avec deux avertissements par
symbole et « 0 barres » partout. Le timeframe est maintenant validé d'emblée,
avec la liste des valeurs acceptées.

### 📥 Amorçage actions — `period='max'` : la source décide de sa profondeur

`bars_span_ms` traduit un nombre de bougies en fenêtre calendaire, mais reste
une **estimation** : heures de séance, jours fériés, ancienneté de
l'introduction. Sur un amorçage à froid, la seule bonne réponse à « quelle
profondeur ? » est « toute celle qui existe » — et Yahoo sait la donner
directement via `range=max`, sans qu'on ait à la deviner.

- **`YFinanceProvider.fetch_ohlcv_max(symbol, tf)`** : traduit
  `yf.Ticker(t).history(period='max', interval=...)`. Retaille pas la réponse
  (l'amorçage jetterait la profondeur qu'il vient de payer), agrège toujours
  les intervalles que Yahoo ne cote pas (4 h ← 1 h), et alimente le cache de
  réponses avec une borne basse nulle — donc toute demande ultérieure, si
  profonde soit-elle, part du cache.
- **`CandleStore`** l'utilise à deux endroits : l'amorçage d'un cache vide, et
  le backfill d'un cache déjà peuplé mais resté court (script de backfill,
  version antérieure du bot) — c'est le seul moyen qu'un cache existant
  rattrape sa profondeur. Le contrat est optionnel : **ccxt ne l'expose pas, la
  crypto garde exactement le chemin paginé précédent.**
- Quand l'amorçage profond a réussi, le backfill historique qui suivait est
  **supprimé** : on connaît déjà sa réponse. Une requête au lieu de deux.
- Un chemin profond qui rend une liste vide (quota, granularité non servie) ou
  qui lève retombe sur le chemin borné, au lieu de laisser le cache vide.

Les fenêtres calculées restent nécessaires pour les appels **bornés** —
incrémental, backfill ciblé — où l'on demande volontairement une tranche.

### 🏷️ Univers — `FDJUP.XC` corrigé en `FDJU.PA`

Le ticker rendait 1 bougie en 15 m et 50 en 1 h là où les autres membres en
ont plusieurs centaines : il brûlait du quota Yahoo pour rien à chaque cycle.
Son cache Parquet erroné est supprimé.

### 🔁 Actions — fin de la boucle « cache insuffisant / aucune bougie supplémentaire »

Les logs répétaient sans fin, pour chacun des 98 titres du SBF 120 et chaque
timeframe :

```
[CandleStore] STLAP.PA/15m — cache insuffisant (361/500 bougies) — tentative de récupération de 139 bougies historiques
[CandleStore] STLAP.PA/15m — aucune bougie historique supplémentaire disponible sur l'exchange (cache : 361 bougies)
```

Deux causes indépendantes, chacune reproduite avant correction.

**1. La fenêtre demandée était calculée en temps calendaire.** Une bougie
crypto occupe exactement sa durée — 500 × 15 min = 125 h de mur. Une action ne
cote que pendant sa séance : 8 h 30 sur XPAR, 5 jours sur 7, fériés déduits,
soit **~25 % du temps calendaire**. Reculer de 125 h ne ramenait donc qu'une
centaine de bougies. Mesuré sur un faux Yahoo à historique profond : **500
demandées, 117 rendues**. Le cache restait sous le compte visé, et le cycle
suivant reposait la même question.

- Nouveau contrat `bars_span_ms(tf, count)` sur le provider : « combien de
  temps de mur couvrent `count` bougies ici ». `CandleStore._fetch_full` et
  `_fetch_historical` s'en servent pour viser assez loin. **Un exchange qui ne
  l'expose pas — tout ccxt — conserve exactement le comportement précédent**
  (`count × durée`), la crypto ne ferme jamais.
- Réglable par venue : `session_hours` et `trading_days_per_week` dans
  `providers.yfinance`. Surestimer est gratuit (une seule requête, la
  profondeur reste plafonnée par Yahoo, la réponse est retaillée à l'arrivée) ;
  sous-estimer produit la boucle. D'où une marge de 15 %.

**2. Le cache de réponses ignorait la profondeur demandée.** La clé était
`(ticker, intervalle)`. Or le store fait **deux** appels par cycle : d'abord
l'incrémental (fenêtre étroite, depuis la dernière bougie connue), puis le
backfill historique (fenêtre profonde). Le second était servi par la réponse
du premier — donc sans la moindre bougie ancienne, ce que le store lit comme
« le fournisseur n'a rien de plus ». **Le backfill historique n'a jamais
fonctionné sur actions**, et aucune requête ne partait pour le prouver.

- `period1` entre dans la **valeur** de l'entrée, pas dans la clé : une entrée
  ne sert que les demandes qu'elle couvre réellement. Une demande plus étroite
  continue donc de faire mouche (le cache garde son intérêt), une demande plus
  profonde repart sur le réseau.
- Le rafraîchissement incrémental n'écrase plus une fenêtre profonde tout juste
  payée : les deux réponses sont fusionnées en gardant la borne basse.

**3. La question était reposée à chaque cycle.** Quand la profondeur maximale
est réellement atteinte — titre récemment introduit, plafond Yahoo — le compte
visé est hors d'atteinte *pour toujours* : redemander ne coûte que du quota, et
c'est ce qui alimentait les 429. `CandleStore` mémorise désormais qu'un
backfill n'a rien ramené et ne le rejoue pas avant 6 h. Le mémo tombe dès que
la borne basse du cache recule (historique arrivé par une autre voie) et il est
**daté**, parce qu'un 429 pendant le backfill est indiscernable d'un historique
épuisé — sans expiration, un incident réseau gèlerait le symbole définitivement.

Mesuré de bout en bout sur trois cycles, faux Yahoo à historique profond :

| | avant | après |
|---|---|---|
| Bougies rendues (500 demandées) | 185 → 219 → 314 | **500 dès le 1ᵉʳ cycle** |
| Ligne « cache insuffisant » | à chaque cycle | aucune |
| Titre réellement plafonné (120 bougies) | 2 requêtes + 2 lignes par cycle, sans fin | 1 requête, 1 ligne, **puis silence** |

### 🌐 Provider actions — `yfinance` devient le chemin unique (fin des 429 Yahoo)

Sur une machine sans le paquet `yfinance`, le provider retombait sur l'API
chart publique de Yahoo, appelée avec `requests`. Ce repli ne fonctionne plus :
Yahoo exige désormais un couple **cookie/crumb** sur cet endpoint et répond
`429` sans lui — quel que soit le throttling, et sur le premier appel. Le
disjoncteur de quota jouait alors son rôle (5 refus → 15 min de coupure) mais
sur un provider qui ne pouvait de toute façon rien ramener : le bot annonçait
`backend=API chart` puis passait 98 titres en silence.

- **`app/core/yfinance_provider.py`** : `import yfinance` **direct**, en tête de
  module. Plus de `try/except ImportError`, plus de bascule `prefer_yfinance`,
  plus de `_fetch_via_chart_api` — un seul chemin, donc un seul comportement à
  expliquer. Un paquet manquant échoue maintenant à l'import, avec le nom du
  paquet, au lieu de dégrader en silence vers un chemin mort.
- **`requirements.txt`** : `yfinance==1.5.2` devient une dépendance du projet.
  ⚠ Il **réinstalle pandas** (~23 Mo avec ses transitives). Contrepartie
  assumée : c'est le prix des actions. La frontière pandas ne dépasse pas
  `YFinanceProvider._fetch_bars()`, aucun autre module ne l'importe, et la
  résolution reste sans conflit (`yfinance` demande `requests>=2.31`, `ccxt`
  épingle `2.34.2`).
- **Détection du quota corrigée** : `yfinance` signale le 429 par
  `YFRateLimitError`, dont le **message ne contient pas « 429 »**. Le test
  `"429" in str(e)` — écrit pour l'API chart — ne l'aurait jamais reconnu : le
  disjoncteur ne se serait plus jamais ouvert. La détection porte désormais sur
  le type (`_is_rate_limited`), le test sur la chaîne restant pour les erreurs
  réseau brutes remontées par `curl_cffi`.
- **Message du disjoncteur corrigé** : il annonçait « pause de 900s pour tout
  le processus », ce qui se lit comme un `sleep` global — et qu'on cherchait
  ensuite en vain dans les logs, puisque le cycle continuait à pleine vitesse.
  Il annonce maintenant ce qui se passe réellement : les appels Yahoo sont
  court-circuités (retour immédiat, aucune attente) pendant la fenêtre, les
  actions étant servies par le seul cache Parquet.
- **Barres `NaN`** : pandas remplit les trous de cotation par `NaN` là où l'API
  chart renvoyait `None`. Or `nan <= 0` vaut `False` : le filtre de prix
  existant les laissait passer, et elles seraient ressorties dans les
  indicateurs bien plus loin. `_clean` teste maintenant la finitude, et un
  volume `NaN` retombe à `0.0` (`NaN or 0.0` renvoie `NaN`, `NaN` étant « vrai »).
- Les objets `yfinance.Ticker` sont réutilisés par symbole : le premier appel
  résout le fuseau de la place, une requête réseau qu'il serait absurde de
  refaire à chaque bougie demandée.

### 🔬 Page « Modèles » — les diagnostics d'entraînement deviennent visibles là où on expérimente

Le panneau de diagnostics (top features + gain pour amplitude et direction,
état et erreur de calibration, AUC direction par régime, importances par
régime, similarité de Spearman entre régimes) existait déjà dans les deux UI
et était produit par `app/ml/backend/trainer.py`. Il n'était simplement
atteignable dans aucun des deux moments où l'on en a besoin.

- **Après un entraînement** — la carte de job n'affichait que décision /
  raison / AUC. En **dry-run** c'est particulièrement coûteux : ce mode
  n'écrit rien au registre, il n'existe donc aucune version où aller lire les
  diagnostics ensuite, alors que c'est le mode fait pour expérimenter. Les
  résultats de `train_and_publish`, `maybe_refresh` et `window_sweep` (sur la
  meilleure fenêtre) portent désormais `train_meta`, affiché par les deux UI.
- **Sur un candidat rejeté** — le panneau ne lisait que la version ACTIVE
  (`m.active.train_meta`), masquant exactement le cas où l'on enquête : un
  candidat publié en `keep` qu'on hésite à promouvoir. Chaque ligne de
  l'historique de versions gagne un bouton « diagnostics » ;
  `/api/ml/registry/versions` renvoyait déjà `train_meta` par version, aucun
  changement d'API n'était nécessaire.
- **`app/ml/policy.py`** — les diagnostics du candidat sont capturés **avant**
  le `reset_model()` de la branche « keep ». Ce reset vide `_train_meta` : une
  lecture plus tardive aurait rendu `{}` précisément sur les rejets.
- **Nouveau — `app.ml.scoring.resolve_train_meta`** : lit `_train_meta` avec
  le même repli de clé que `save_model` et pour la même raison (`fit()` indexe
  sous `"default"`, `score()` sous le symbole). Ambigu ⇒ `{}` : afficher les
  diagnostics d'un autre modèle sous ce nom serait pire que de n'en afficher
  aucun.

Limite inchangée : seules les recettes entraînées par `MLBackend` (les quatre
stratégies à mixin) instrumentent leur entraînement. `stat48_v4/v5` n'écrivent
que `n_train/n_valid/auc_*`, `dyn_threshold_v1` rien — leur panneau reste
vide tant que l'entraînement n'est pas unifié.

### 🐛 Cycle d'entraînement ML — un entraînement lancé depuis l'UI produit enfin un artefact

Cinq défauts indépendants se combinaient pour qu'aucun entraînement lancé
depuis la page « Modèles » n'aboutisse, chacun se signalant par un message qui
désignait la mauvaise cause.

- **`app/api/routes/ml.py`** — le champ « Stratégie » était validé contre les
  noms de MODULE (`opus_omnibus_v11`) alors que la table du registre, juste
  au-dessus dans la même page, est indexée par RECETTE (`omnibus_v4_multi`) :
  recopier une ligne du tableau donnait « Stratégie inconnue ». Un nom de
  recette est désormais résolu vers la stratégie qui le déclare ; une recette
  partagée par plusieurs stratégies reste refusée, mais en nommant les
  candidates. Le timeframe est validé avant de lancer le job (« 15min » pour
  « 15m » échouait une minute plus tard sur un « aucune donnée en cache » qui
  accusait le cache).
- **`app/ml/train_runner.py`** — `load_offline_ohlcv` sert le cache Parquet
  brut, sans les colonnes `_pre_*` que le chemin live reçoit de
  `scanner.fetch_ohlcv`. Les stratégies « bespoke » (`scoring_statistique_
  opus_v4/v5`) échouaient donc en `fit()` depuis l'UI seulement. Pré-calcul
  idempotent ajouté à la source.
- **`app/ml/model_registry.py`** — `publish()` exigeait un bundle
  `.amp.lgb` + `.dir.lgb` quelle que soit la recette : `dyn_threshold_v1`
  (`persistence: lgbm_single`, un seul booster `.lgb`) ne pouvait
  **structurellement pas** être publiée, entraînement réussi ou non. Le layout
  suit maintenant le `persistence:` de la recette (`model_suffixes`), et
  `missing_artifacts()` permet à l'appelant de constater l'absence au moment
  où elle se produit.
- **`scoring_statistique_opus_v4/v5`, `ml_dynamic_threshold`** — `save_model()`
  indexait le magasin de modèles par le TF déduit du nom de fichier, alors que
  `fit()` y écrit sous `"default"` (et `score()` sous le symbole). Sans
  correspondance, l'écriture était un no-op **silencieux**, révélé deux couches
  plus loin par un « auc_amp indisponible (labels mono-classe / holdout
  dégénéré) » puis un « artefacts absents » — deux diagnostics qui accusaient
  les données. Repli de clé explicite + WARNING au lieu du silence, et
  `policy.maybe_refresh` / `train_runner` vérifient l'artefact juste après
  l'écriture.
- **`app/live/auto_opt_mixin.py`** — le validateur de nom de stratégie
  n'acceptait que les minuscules et écartait donc `breakout_filtreHor`, un
  module bien réel de `app/strategies/`, invisible au live sans autre signal
  qu'un WARNING au démarrage. La garde (pas de `.`, `/`, `\`) est conservée ;
  la casse n'y contribuait pas.
- **UI (`models.html` + `frontend/src/app/models/page.tsx`)** — les champs
  « Stratégie » et « Timeframe » deviennent des listes fermées (stratégies
  découvertes sur disque via `/api/config`, timeframes de la table canonique),
  ce qui supprime la confusion recette/stratégie à la source.

Limite connue inchangée : les artefacts `stat48_v4`/`stat48_v5` sont publiés
mais restent en `keep`, le scorer générique ne sachant pas lire le format
`save_lgb_with_scaler` (pas de liste de features sérialisée) — promotion
manuelle depuis la page « Modèles ».

### 🏛 G2 — Actions SBF 120 en paper (calendrier, sizing, frais, provider, notification de trade)

Lève les **3 points de couplage** listés au plan directeur §4.2 (calendrier de
marché, sizing/coûts par venue, provider actions). Principe suivi de bout en
bout : **rien de spécifique aux actions dans le moteur** — tout est porté par la
`Venue`, dont les défauts reproduisent exactement le comportement crypto. Une
configuration crypto existante n'emprunte aucun code nouveau (le routeur de
providers n'est même pas instancié tant qu'aucune venue n'en déclare un).

**Notification de trade** — le livrable central tant que l'exécution réelle
(G3) n'est pas branchée. Une venue `can_execute: false` ne transmet **aucun**
ordre : le bot calcule le trade, le suit comme une position paper, et émet un
ticket portant **symbole, direction, prix d'ouverture, stop-loss,
take-profit**, plus quantité, notionnel, R:R, stratégie et venue. Envoyé en
**synchrone** et jamais throttlé — c'est le seul chemin vers l'exécution, il ne
peut pas être perdu dans une queue saturée. Le message « position ouverte »
habituel est volontairement supprimé dans ce cas : il laisserait croire à un
fill réel. Symétrique à la sortie (« TRADE À SOLDER »). La décision est prise
dans `_open_position`/`_close_position`, donc valable quel que soit le câblage
— y compris sans routeur.

- **Nouveau — `app/core/market_calendar.py`** : protocole `MarketCalendar`,
  `AlwaysOpenCalendar` (défaut de toute venue → 24/7, comportement historique
  strictement inchangé), moteur `SessionCalendar` déclaratif (fuseau, plusieurs
  plages par jour, fériés à date fixe **et mobiles** via un comput de Pâques
  sans dépendance, demi-séances), `XPAR` livré en dur, adaptateur
  `exchange_calendars` utilisé s'il est installé. Toute autre place se déclare
  dans `config.yaml › calendars`, sans toucher au code.
- **Nouveau — `app/live/market_hours_mixin.py`** : filtre les entrées hors
  séance (log throttlé à 15 min/symbole), garde-fou par signal, et clôture
  avant fin de séance (`close_at_session_end`). Les positions **déjà ouvertes
  restent gérées** marché fermé — le trailing doit se recalculer et un stop
  touché au gap d'ouverture doit être constaté ; seules les *entrées* sont
  bloquées. Un calendrier qui lève est traité comme « ouvert » : un calendrier
  cassé ne doit pas geler le trading en silence.
- **Nouveau — `app/core/yfinance_provider.py`** : provider actions data-only,
  deux backends (le paquet `yfinance` s'il est installé, sinon l'API chart
  publique via `requests`) — **aucune dépendance obligatoire ajoutée**.
  *(Révisé depuis : le repli API chart a été retiré et `yfinance` est devenu
  une dépendance — voir « Provider actions — `yfinance` devient le chemin
  unique » plus haut.)*
  Limitations Yahoo traitées explicitement plutôt que subies : profondeur
  plafonnée par granularité (1 m → 7 j, intraday → 60 j, 1 h → 730 j) avec
  troncature **avertie une seule fois** par symbole/TF ; intervalles inexistants
  (3 m, 2 h, 4 h, 6 h, 8 h, 12 h) ré-agrégés depuis l'intervalle de base et
  **ancrés sur l'epoch** pour que le cache Parquet incrémental déduplique ;
  throttling process-wide + backoff exponentiel sur 429 + cache TTL ;
  dégradation gracieuse (liste vide, jamais d'exception qui tue un cycle).
- **Nouveau — `app/core/provider_router.py`** : route chaque appel marché vers
  le provider de la venue du symbole, derrière la **même** interface que
  `RobustExchange` — aucun site d'appel modifié. `build_market_provider` rend
  l'exchange **inchangé** si aucune venue ne déclare de `data_provider`.
- **Nouveau — `app/core/universe.py` + `data/universe/sbf120.yaml`** : univers
  d'instruments versionnés, cumulés avec `scanner.symbols` via
  `scanner.universe`. ⚠ Le fichier SBF 120 est un instantané constitué **hors
  ligne** et marqué `verified: false` — la composition de l'indice est révisée
  trimestriellement. **Nouveau `scripts/check_universe.py`** interroge le
  provider ticker par ticker pour repérer les radiés/renommés (un ticker mort
  ne lève aucune erreur : il produit un symbole qui ne score jamais).
- **`app/core/execution.py`** : `quantize_size` (arrondi **à la baisse** au
  lot / à l'unité entière — arrondir au-dessus engagerait plus de risque que le
  sizing n'autorise), `quantize_price` (grille `tick_size`), `venue_trade_cost`
  (commission % + fixe + plancher + taxe de transaction, assiette à l'achat
  pour la TTF). Appliqués sur les **trois** chemins — ouverture, scale-in,
  clôture — côté live *et* backtest, pour que la parité tienne.
- **`app/core/bot_identity.py`** : `Venue` gagne `calendar`, `data_provider`,
  `can_execute`, `close_at_session_end`, `close_before_close_min`, `fee_pct`,
  `fee_fixed`, `fee_min`, `transaction_tax_pct`, `tax_on_buy_only`,
  `min_notional`. Tous neutres par défaut.
- **`app/core/candle_store.py` — deux hypothèses crypto retirées**, elles
  auraient troué l'historique actions en silence : le plancher `since` à
  2017 (fondation d'OKX — une action cote souvent depuis les années 1990) et
  le rejet des barres à volume nul (signe de données cassées en crypto,
  parfaitement normal sur une valeur peu liquide). Désormais pilotés par le
  provider (`min_since_ms`, `drop_zero_volume`), défauts inchangés.
- **`app/engine/scanner.py`** : mode univers, et seuil de liquidité
  surchargeable **par classe d'actif** (`scanner.min_volume_by_asset_class`) —
  le seuil crypto de 5 M$/24 h exclurait la quasi-totalité du SBF 120.
- **`config.yaml`** : venue `euronext-paper`, `scanner.universe`,
  `providers.yfinance` et `calendars` fournis **en commentaire** — rien n'est
  activé par défaut, la marche à suivre est décrite sur place.
- **Tests** : 6 fichiers, **121 tests** (`test_market_calendar`,
  `test_venue_costs`, `test_universe`, `test_yfinance_provider`,
  `test_provider_router`, `test_equity_paper_flow` — ce dernier monte un vrai
  `LiveTrader` et vérifie le parcours complet). Chaque comportement actions a
  son pendant « non-régression crypto ». Suite : 1 051 → **1 172 verts**, zéro
  régression.

> ⚠️ **Limitation méthodologique, à arbitrer avant d'entraîner du ML sur
> actions** : Yahoo plafonne l'intraday à ~60 jours (15 m) et ~2 ans (1 h). Les
> fenêtres d'entraînement calibrées sur la crypto (~40 k barres) ne sont donc
> pas atteignables en intraday actions — soit du journalier, soit un
> fournisseur payant (EOD Historical Data).
>
> **Non traité, assumé** : `bars_per_year` reste calé sur 365 j × 24 h, donc le
> Sharpe annualisé d'un backtest actions est sous-estimé (séance Euronext :
> 8,5 h, 252 j/an). Les comparaisons restent valides à classe d'actif
> constante, pas entre crypto et actions. À corriger avec G3.

### 🧹 ML — Retrait du pack V4 figé (étape A de l'architecture unifiée)

Le pack V4 de mai 2026 n'était conservé que sur une intuition. Mesuré sur
holdout commun (`scripts/compare_legacy_vs_retrained.py`), un ré-entraînement
le bat sur les **3 timeframes** — `auc_amp` 0.598→0.638 (15m), 0.656→0.674
(30m), 0.600→0.663 (1h) — et sur **aucun régime** son avantage ne survit à un
IC 95 % bootstrap apparié. Le verdict tient de 5 000 barres d'entraînement à
tout l'historique (15 promotions sur 15). Détail :
`docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md` §1.5.

- **Supprimé** : `opus_stat_pretrained_v4`, `opus_omnibus_v7_pretrained`, `v8`,
  `v9`, `v10` (+ leurs 5 YAML), `scripts/migrate_v4_to_registry.py`, le mode
  `v8` du graphique scanner, et le chemin legacy du registre
  (`_legacy_artifact`, `import_legacy`, champ `ArtifactRef.legacy`, repli sur
  l'ancien layout plat, colonne « Legacy » des deux UI).
- **Préservé** : les setups `SHORT_TD` et `LONG_PULLBACK_TU`, que seules v8/v9
  portaient, sont repris dans `opus_omnibus_v11` **désactivés** — activables
  par YAML, donc désormais optimisables. Neutralité et activabilité
  verrouillées par `tests/test_omnibus_recovered_setups.py`.
- **Archivé** : les 9 fichiers du pack vivent sous `models/_archive/`, ignoré
  par `list_recipes()` — lisibles pour ré-examen, jamais résolus.
- **Rétrocompat retirée** (le bot n'est pas en production) : `use_pretrained_ml`
  — `ml_mode` devient le seul levier, avec un défaut explicite `"frozen"` — les
  ré-exports de `app/ml/policy.py`, l'alias `recipe_gate_defaults`
  (→ `resolve_gate_spec`), et les arguments `label_horizons`/`amp_top_pct` en
  direct de `score_holdout`, qui permettaient de scorer avec une convention de
  labels différente de celle déclarée par la recette.

- **Deux tests morts réactivés**, découverts en cherchant les références aux
  fichiers supprimés : `test_feature_store_integration` et
  `test_scoring_alignment` portaient un `pytest.importorskip("sklearn")` alors
  que le dépôt n'a plus sklearn depuis `phase6-sklearn-removal` — ils
  skippaient donc silencieusement, sans plus rien vérifier. Ils passent
  désormais réellement.

Bilan : **−4 230 lignes nettes**, 46 → 41 stratégies, 14 → 8 stratégies ML,
947 → **958 tests** verts (lents inclus), 0 skip.

### 📐 ML — Indicateurs : ATR/ADX/DI passent au lissage de Wilder (décision 13)

Le dépôt lissait ATR/ADX/DI en `ewm_mean(span=n)` — α = 2/(n+1) — là où la
définition de Wilder veut α = 1/n. Pour n=14 : **un Wilder de période 7,5 sous
un nom qui annonce 14**. Le RSI voisin était déjà en α = 1/n, ce qui ne laissait
guère de doute sur l'involontaire. Deux ADX incompatibles cohabitaient donc :
corrélation **0.75**, écart absolu moyen 9.5 points, verdict `ADX ≥ 20`
différent sur **21,6 %** des barres.

Corrigé **après mesure**, pas par principe : réoptimisation complète sous
chaque convention puis comparaison sur une fenêtre jamais vue par l'optimiseur
(`scripts/compare_adx_smoothing.py`) — écart ≤ **0.022** de score, de signe
variable. La sur-réactivité n'était pas ce qui faisait vivre ces stratégies.

- `indicators_core.atr`/`atr_series`/`atr_val` délèguent à `atr_wilder`,
  désormais **l'unique implémentation** du lissage Wilder ; `adx`/`adx_val`
  passent en α = 1/n **aux quatre étages** (ATR, +DI, −DI, ligne ADX) ;
  `supertrend` suit. `indicators_precompute` a Wilder par défaut.
- **Moteur SMC exclu, et vérifié** : il ne lit aucune colonne `_pre_*` et était
  déjà en Wilder délibérément (alignement `ta.atr`). L'empreinte SHA256 de
  `smc.analyze` sur 3 000 barres est **identique avant/après**. Un test
  interdit à tout module SMC de lire `_pre_atr14`/`_pre_adx14`.
- **Action restante** : les seuils ADX des YAML sont désaccordés (choisis face
  à un ADX qui valait 35, appliqués à un ADX qui vaut 28) — **réoptimiser**.

### 🔑 ML — La dimension symbole quitte la clé du registre (décision 11)

Le registre rangeait par `(symbole, TF, recette)` alors que le trainer live
n'entraînait que sur BTC et que le pipeline servait ce modèle à **tous** les
symboles : un artefact sous `BTC_USDC/` décidait en réalité sur ETH et XRP. La
dimension nommait une partition inexistante.

Mesuré avant de trancher (`scripts/measure_symbol_transfer.py`) : matrice de
transfert, coupure temporelle commune, IC 95 % bootstrap apparié, 3 TF.
**17 des 18 cellules indiscernables du bruit** ; ETH ne gagne rien à son propre
modèle (0.634 vs 0.638 en 1h) et XRP n'a pas de quoi s'en entraîner un.

- Layout : `{base_dir}/{tf}/{recette}/{version_id}/`. Le symbole
  d'entraînement subsiste en **provenance** (`ArtifactRef.train_symbol`).
- `load_models()` n'a plus besoin de `scanner` — ce paramètre produisait un
  « pas de modèle » silencieux suivi d'un réentraînement inutile.
- Le script découvre les symboles depuis le store et mesure les corrélations :
  **à rejouer tel quel** quand un actif d'une autre classe entre dans le bot.

### 🧩 ML — Étapes B à G : recette, prédicteur, presets, fusion omnibus

- **B** — La recette devient un objet de premier ordre (`app/ml/recipe.py`,
  7 fichiers `recipes/*.yaml`) et le contrat `Predictor` gagne
  4 implémentations (`app/ml/predictor.py`).
- **C** — Le cycle de vie ML devient un mixin partagé (`MLBackendMixin`) après
  preuve que les `_train_impl` autonomes produisaient des modèles
  **byte-identiques** à `MLBackend` (écart 0.000000, corrélation 1.0000).
- **E** — `ProxyPredictor` absorbe 4 variantes `_no_ml` sur 5 : **1 491 → 262
  lignes**. `dynamic_threshold_no_ml` reste un fork, motivé.
- **D.1/D.2** — `opus_omnibus_v10_retrained` (952 → 101 L) et `opus_omnibus_v7`
  (572 → 129 L) deviennent des presets de V11, équivalence prouvée par
  énumération du domaine (2 520 puis 7 744 combinaisons, avec contre-épreuve).
  `SHORT_TD` redevient optimisable.
- **G** — Calibration isotone et élagage mesurés : calibration à garder mais
  **à désactiver en 1h** (ECE +461 % contre −47 %/−67 % ailleurs) — *décision
  écrite, application encore à faire*. Élagage : non mesurable par ce protocole.

**Trois bugs silencieux préexistants** trouvés par ce travail : snapshot vide du
cache d'entraînement (V11/V12 perdaient leur modèle dès le 2ᵉ essai
d'optimisation → zéro signal), `defaults` ignoré par `MLBackend.fit` (une
stratégie déclarant `calibrate: False` s'entraînait calibrée), et sorties
anticipées mortes sur les variantes sans ML (0 déclenchement sur 320 contre 164
pour le fork remplacé).

### 🛡 Sprint 0 — Correctifs P0 sécurité financière & config

- **[S0-01]** Sync spot/margin propage l'équité à l'allocateur
  (`allocator.update_equity`) — les budgets de slots suivaient l'ancien capital.
- **[S0-02]** Retour de `create_order` validé sur les 5 sites d'appel
  (ouverture, stops/OCO, scale-in, clôture) : `None`/status
  `rejected|canceled|expired` → plus de position fantôme ; un échec de
  CLÔTURE remet la position en gestion + alerte critique.
- **[S0-03]** CORS : `DELETE`/`PUT` ajoutés (`/api/optimize/job` était
  inutilisable depuis le frontend).
- **[S0-04]** `_expand_env` strict : variable `${...}` absente = démarrage
  refusé en live (WARNING en paper) — fini les credentials vides silencieux.
- **[S0-05]** `scripts/setup.sh` génère un `.env` avec `WEB_API_KEY`
  aléatoire ; `.env` ajouté au `.gitignore` ; `web.api_key: ${WEB_API_KEY}`.

### 🔒 Sprint 1 — Concurrence & sécurité

- **[S1-01]** `RLock` sur `RiskManager` (23 méthodes `@_locked`) — état
  partagé thread trading / threads API.
- **[S1-02]** `RLock` sur `OHLCVCache` (fetch réseau hors verrou).
- **[S1-03]** `update_equity`/`check_correlation` verrouillées
  (`CapitalAllocator`).
- **[S1-04]** Auth WebSocket par cookie HttpOnly (query param en fallback
  non-navigateur).
- **[S1-05]** `NEXT_PUBLIC_API_KEY` supprimée du bundle client —
  `credentials: 'include'` + `EventSource withCredentials`.
- **[S1-06]** Routes backtest/replay/optimizer : vérifié non bloquantes
  (threadpool AnyIO de Starlette) — aucun changement requis, documenté.
- **[S1-07]** `_pre_execution_check` renforcé : solde spot libre réel et
  margin level critique vérifiés avant chaque entrée.
- **[S1-08]** Section `live.trailing` dédiée — le trailing live ne suit plus
  silencieusement les changements de `backtest.*` (repli + WARNING).

### 🌍 Sprint 2 — Abstractions multi-actifs (G1, comportement crypto inchangé)

- **[S2-01]** `app/core/providers.py` : protocoles `MarketDataProvider`/
  `ExecutionProvider` (PEP 544) — `RobustExchange` conforme sans modification.
- **[S2-02]** `Venue` étendue : `asset_class`, `quote_currency`, `tick_size`,
  `lot_size`, `fractional`, `allow_short` ; `venues.assign[symbol]` supporté.
- **[S2-03]** Devise de cotation neutralisée : `min_volume_quote_24h` (alias
  rétro-compatible de `min_volume_usdc_24h`), scan dynamique par
  `Venue.quote_currency` au lieu du littéral `/USDC`.
- **[S2-04]** `BaseStrategy.asset_classes` + marquage crypto-only de
  `funding_flow`/`derivatives_reversion` + filtre dans `_build_active_per_tf`.
- **[S2-05]** Golden test de parité backtest BTC/USDC avant/après
  (`tests/test_generic_parity.py`).

### 📊 Sprint 4 — Qualité des métriques & optimiseur

- **[S4-01/02]** Sharpe live aligné sur le backtest : courbe d'équité
  synthétique par trade, ordre chronologique corrigé (`get_trades` renvoie
  DESC), annualisation par `bars_per_year(tf)` (source unique
  `app/core/timeframes.py`) au lieu d'un `sqrt(252)` fixe.
- **[S4-03]** Data leakage du ré-entraînement ML final documenté par design +
  mode opt-in `optimizer.ml_final_train_mode: is_only`.
- **[S4-04]** Deflated Sharpe Ratio (Bailey & López de Prado 2014) dans
  `opt_scoring.py` — stdlib `statistics.NormalDist`, autonome (pas encore
  câblé dans `composite_score`, suivi séparé).
- **[S4-05]** Métriques par stratégie en une passe (pré-groupement, fini le
  triple refiltrage O(n×k)).
- **[S4-06]** `_load_db_stats` : agrégats globaux en SQL
  (`get_trade_global_aggregates`, COUNT/SUM/MAX).
- **[S4-07]** Test de régression scale-in/budget cumulé (l'item « scale-in
  sans validation » du plan d'amélioration était faux — `can_allocate`
  couvre déjà le cumul).

### 🧹 Sprint 7 — Nettoyage code mort + CI/lint (Vague 3 de l'audit)

Périmètre §4.1 du plan directeur, hors **DEAD-01** (8 générations Opus/stat
jamais promues) et **TEST-11** (tests smoke stratégies, bloqué par DEAD-01) —
exclus explicitement de ce sprint.

- **[DEAD-02]** Suppression de `scoring_statistique_opus_v3.py` (579 lignes)
  et de `strategies/scoring_statistique_opus_v3.yaml` — zéro appelant
  (grep exhaustif Python + YAML).
- **[DEAD-03]** `XRP/USDC` ajouté à `scanner.symbols` (`config.yaml`) —
  données OHLCV présentes sur disque mais symbole absent du scan ;
  pas de données `derivatives` pour XRP (gap connu, sans impact bloquant).
- **[DEAD-05]** `opus_omnibus_v11.py` (stratégie **active**,
  `manual_active: opus_omnibus_v11::30m`) : les deux `del ds_tr, ds_va`
  dupliqués (bloc 1050-1094) remplacés par un `try/finally` englobant —
  suppression garantie une seule fois par itération, plus de dépendance à
  deux points de sortie distincts.
- **[DEAD-06]** Suppression des 5 fonctions publiques jamais appelées :
  `config.strategy_file_path`, `execution.cap_notional`,
  `database.get_lifecycle_events`, `feature_store.get_provider`/
  `list_providers` — zéro référence externe re-vérifiée avant suppression.
- **[DEAD-07]** Nettoyage `ruff --select F` (façade `indicators.py`
  exclue via `ruff.toml`) : 73 imports inutilisés + 15 f-strings sans
  placeholder auto-fixés ; 17 variables locales mortes retirées à la main
  (lecture des lignes concernées confirmée sans effet de bord avant
  suppression).
- **[DEAD-09]** `scripts/__pycache__` déjà propre (aucun `.pyc` résiduel) —
  vérifié, rien à faire.
- **[TEST-01]** `.github/workflows/ci.yml` : job `lint` (`ruff check .`) +
  job `test` (`pytest tests/ -q -m "not slow"`), Python 3.12.
- **[TEST-04/05]** `ruff.toml` (remplace flake8/pyflakes — `line-length=120`
  aligné sur la convention `CONTRIBUTING.md`, règles `F/W/E/I`) et
  `mypy.ini` (non strict, `ignore_missing_imports`) ; `flake8` retiré de
  `requirements.txt` au profit de `ruff==0.15.8`.
- **[TEST-06]** `pytest.ini` : markers `slow`/`strategy_smoke` déclarés
  (aucun test actuel ne dépend de `data/ohlcv`/`data/derivatives` versionnés
  — vérifié, toute la suite tourne sur données synthétiques/`tmp_path` ;
  rien à isoler pour l'instant).
- 649/649 tests verts après chaque étape.

### 💰 Sprint 8 — Quick wins financiers & sécurité

Périmètre §4.2 du plan directeur, hors **FIN-01** (frais dynamiques par
palier VIP OKX) — exclu explicitement de ce sprint.

- **[FIN-04]** Benchmark Buy & Hold : correctif du warmup figé à 210 barres
  dans `_add_buy_and_hold` (désynchronisé du warmup dynamique réel de la
  boucle de trading dès qu'une stratégie déclare `warmup_bars`/`min_bars` >
  210) — le prix de départ du B&H (et donc l'alpha) est maintenant calculé
  sur la MÊME fenêtre que le backtest.
- **[FIN-06]** Compteur de frais par catégorie (taker/maker/borrow/stop) :
  colonnes `Trade.fee_taker`/`fee_maker`/`exit_reason` (auto-migrées),
  `get_fee_breakdown()` + `GET /api/stats/fees`. `exit_reason` distingue
  enfin le motif de CLÔTURE du motif d'OUVERTURE (`Trade.reason`, conflatés
  auparavant) sur les 9 chemins de fermeture du live
  (stop_loss/trailing_stop/take_profit/gap/early_exit/manual). `fee_taker`/
  `fee_maker` reflètent honnêtement l'absence actuelle de distinction
  maker/taker à l'exécution live (100 % taker) plutôt que de la simuler.
- **[FIN-07]** Slippage paper proportionnel à la taille : nouveau
  `trading.paper_slippage_model: size` (défaut `static`, comportement
  inchangé) réutilise la formule d'impact de `backtest.slippage_model: size`
  (BT-10), extraite en fonction partagée `app.core.execution.
  size_impact_cost`. Volume moyen 20 barres lu depuis le cache déjà rempli
  par `OHLCVCache.get()` (nouveau `get_avg_quote_volume`, aucun fetch réseau
  dédié) ; repli silencieux sur le slippage statique si absent.
- **[STRAT-06/BT-13]** Compteur diagnostique `diagnostics.tp_sl_ambiguous_bars`
  (backtest) : mesure les barres où stop ET take-profit auraient tous deux
  été touchés (ambiguïté intrabar high/low) — n'affecte pas la résolution
  (le stop continue de toujours l'emporter).
- **[SEC-04]** Rate-limiting par endpoint : `Limiter` déplacé de
  `app/api/main.py` vers `app/api/state.py` (évite l'import circulaire avec
  les modules de routes) ; ~25 endpoints sensibles/coûteux (contrôle bot,
  backtest/optimizer/replay, écritures config, refetch data, slots,
  paramètres de risque) décorés `@state.limiter.limit(...)` avec des limites
  plus strictes que le `default_limits` global (300/minute).
- **[SEC-05]** `deploy/backup.sh` : sauvegarde datée de `trades.db` (via
  `sqlite3.backup()` Python, cohérent sous WAL) + `config.yaml` +
  `strategies/*.yaml`, rétention automatique — remplace les one-liners cron
  ad hoc de `DEPLOY.md` §9.
- **[ARCH-07]** Derniers littéraux `"BTC/USDC"` de code (hors docstrings/UI)
  migrés vers `DEFAULT_CONFIG_SYMBOL` (`app/live/ohlcv_cache.py`,
  `app/core/config.py`).
- **[BT-05/STRAT-03]** `scripts/audit_param_space.py` : liste chaque
  stratégie avec la cardinalité de son `param_space` vs `optimizer.n_trials`,
  avertit si la couverture < 1e-4 (`--strict` pour un code de retour non-nul
  en CI).
- **[PERF-01]** Cache LRU de `indicators_precompute.py` : taille
  configurable via `config.yaml:perf.precompute_cache_size` (défaut 16 → 128).
- 689/689 tests verts après chaque étape.

### 🔬 Post-Sprint 8 — Comparatif DEAD-01 : 4h/1j + optimiseur v12

Suites du comparatif fonctionnel de la famille opus_omnibus/opus_stat (préparation
de la décision DEAD-01).

- **Garde-fou timeframe levé** : `_SUPPORTED_TFS`/`_detect_timeframe` (14 fichiers,
  `opus_omnibus_v12` par héritage de v11) n'autorisaient que 15m/30m/1h en dur —
  toute exécution sur 4h/1j sortait silencieusement sans signal. Détection 4h/1j
  ajoutée (mêmes tolérances relatives) et `_SUPPORTED_TFS` étendu en conséquence.
- **Cache d'entraînement pour `ml_dynamic_threshold`** (sous-modèle RandomForest
  de `opus_omnibus_v12`, filtre de confirmation/veto post-hoc sur V11) : branché
  sur le cache process-wide existant (`app.core.train_cache`, déjà utilisé par
  v7/v10_retrained/v11/v11_followsetup/opus_stat_retrained_v4) — aucun des
  hyperparamètres d'entraînement (`lookahead`, `vol_multiplier`, `n_trials`,
  `model_type`) n'étant dans le `param_space` optimisé de v12, chaque retrain
  (+ random search interne 8 essais + fit OOS de validation, jusqu'à ~10 fits
  par appel, tous les 200 appels de `score()`) était strictement redondant
  d'un trial de l'optimiseur à l'autre — jusqu'à ~40-60 % du coût par trial
  d'après la docstring de v12. Ajout de l'alignement de fenêtre glissante
  (`aligned_train_window`, même correctif que v11) pour que la dérive du
  déclenchement de retrain entre trials (dépendante des trades ouverts par
  les seuils testés) n'empêche pas les hits de cache. Vérifié : résultats de
  backtest strictement identiques cache ON/OFF ; `StrategyOptimizer.
  random_search(n_trials=10)` sur 1h/8000 barres passe de « n'aboutit jamais
  en 300 s » à 67 s (29 hits / 4 misses).
- 693/693 tests verts (+4 nouveaux, `tests/test_ml_dynamic_threshold_cache.py`).
- **`random_search` : `n_jobs` réellement câblé.** Le paramètre était accepté
  mais la boucle restait toujours séquentielle — contrairement à
  `bayesian_search`, qui dispose déjà d'un `ProcessPoolExecutor` robuste
  (`_run_parallel` : contexte `spawn`, cap mémoire anti-OOM via
  `_safe_worker_count`, repli séquentiel si le pool casse) utilisé par
  `_bayesian_search_legacy`/`_optuna_parallel` mais jamais par
  `random_search`. `n_jobs>1` délègue maintenant à cette même infra
  existante ; `n_jobs<=1` reste la boucle inline inchangée (même
  `early_stop_patience`, non supporté en mode parallèle — tous les trials
  sont soumis d'un coup, comme la phase d'exploration bayésienne). Mesuré :
  12 trials sur `opus_omnibus_v8_no_ml`/1h/8000 barres, 10.6 s (n_jobs=1) →
  3.5 s (n_jobs=3), soit ×3.0 sur 3 workers.
- **`rolling_slope`/`rolling_hurst` (`app/core/indicators_market.py`)
  vectorisés.** Les deux étaient des boucles Python par fenêtre glissante
  (O(n·window)). `rolling_slope` : la pente `cov(x,y)/var(x)` avec
  `x=arange(window)` fixe et centré se réduit à une corrélation par noyau
  fixe (`np.correlate`), exacte bit-à-bit. `rolling_hurst` : pas linéaire
  (méthode R/S), vectorisé par lot sur toutes les fenêtres
  (`sliding_window_view` + régression log-log en forme fermée avec
  réductions `nan*` pour un nombre de lags valides variable par fenêtre),
  avec repli sur l'implémentation scalaire d'origine pour toute fenêtre
  contenant un NaN (troncature `arr[~isnan]` non vectorisable sans casser
  l'alignement — cas rare en pratique, essentiellement le warmup en tête de
  série). Vérifié bit-exact (écart < 1e-6) contre les implémentations
  d'origine sur données BTC/USDC réelles + cas limites synthétiques (NaN
  dispersés, segment constant). Mesuré sur BTC/USDC 1h/3000 barres :
  `rolling_slope` ×233, `rolling_hurst` ×57.
- 712/712 tests verts (+19 nouveaux : `tests/test_optimizer_n_jobs.py`,
  `tests/test_indicators_market_rolling.py`).
- **Refonte de `param_search_optim`** (option activée par défaut sur
  `random_search`/`bayesian_search`/`grid_search` — PAS un 4e mode de
  recherche — qui gèle les paramètres à faible impact avant la recherche
  demandée). L'implémentation initiale dépistait sur une fenêtre de données
  RÉDUITE, en plus du budget `n_trials` demandé, sur un `ProcessPoolExecutor`
  séparé de celui de la recherche principale : payait un 2e spawn/ré-import
  complet de l'appli (coût quasi fixe, dominant pour les stratégies
  multi-modèles ML type `opus_omnibus_v12` : 195-201 s mesurés contre
  68-75 s sans réduction) et pouvait dépenser plus d'essais en dépistage
  qu'il n'y avait de budget (mesuré : 749 s contre 287 s sur
  `opus_omnibus_v9`, 21 paramètres). Nouveau design : le dépistage est EN
  BUDGET (les premiers essais de la recherche elle-même, sur la fenêtre
  complète, jamais un essai en plus) et partage un seul pool de process
  entre dépistage et recherche (`_open_pool`/`_submit_wave`, remplace 3
  blocs de création de pool quasi identiques). Le chemin Optuna/TPE gèle via
  `optuna.samplers.PartialFixedSampler` sur l'importance fANOVA (repli sur
  l'estimateur marginal existant si indisponible) sans muter
  `param_space` — pas de backup/restore nécessaire pour ce chemin. Le mode
  grid dépiste désormais sur la fenêtre complète (partage le pool avec son
  énumération, elle-même parallélisée par `n_jobs` pour la première fois) et
  gèle par cardinalité cible plutôt qu'une fraction fixe de 30 %. Mesuré sur
  `opus_omnibus_v12` (le cas qui motivait la refonte) : 137-140 s → ~63 s
  (pool unique), reproduit sur 3 runs indépendants.
  Deux régressions de fond débusquées en vérification réelle (pas par les
  tests unitaires, qui utilisent un `_eval` simulé) et corrigées avant
  merge : (1) un paramètre observé à une seule valeur distincte dans le
  dépistage rendait un impact 0.0 — indiscernable d'un impact « mesuré et
  réellement plat » — menant à geler ce paramètre sur aucune donnée plutôt
  que sur un signal ; corrigé en rendant NaN (jamais gelable en mode
  facultatif) ce cas précis. (2) Insuffisant en soi : avec aussi peu
  d'essais de dépistage que de paramètres (8 essais pour les 21 de
  `opus_omnibus_v9`), CHAQUE paramètre varie simultanément à presque chaque
  essai — l'estimateur marginal reste noyé dans le bruit de confusion
  inter-paramètres même une fois le cas NaN exclu (mesuré : 20/21 paramètres
  gelés à partir de 8 essais, à l'identique avant et après le correctif
  NaN). Nouveau garde-fou `_MIN_SCREEN_PER_PARAM` : sous ce ratio essais/
  paramètres, le mode facultatif (random/bayesian) ne gèle plus RIEN plutôt
  que de figer des paramètres sur un signal non fiable — le mode grid
  (réduction obligatoire) n'est pas concerné. Score final OOS sur
  `opus_omnibus_v9` inchangé (~0.64) que la réduction gèle 0 ou 20
  paramètres, confirmant qu'aucune perte de qualité ne résultait du
  garde-fou plus prudent.
- 741/741 tests verts (+29 nouveaux/réécrits :
  `tests/test_param_search_optim.py`, `tests/test_optimizer_n_jobs.py`).
- **Revue approfondie de la refonte ci-dessus, 3 défauts trouvés et corrigés
  avant tout autre travail dessus** : (1) `_run_parallel` comptait les
  SUCCÈS de sa boucle par vagues, pas les tentatives — un trial en échec
  (worker KO/timeout/erreur stratégie) faisait ré-échantillonner au-delà du
  budget `n` demandé, et pour `grid_search` (sampler = énumération finie via
  `next()`) pouvait lever `StopIteration` en pleine grille ; corrigé en
  comptant les tentatives, `_run_parallel` les retourne pour que la phase B
  de `random_search` décompte sur le même registre. (2) Comptabilité fragile
  à la réutilisation d'instance : `self.results[-k:]` et `n_trials -
  len(self.results)` supposaient une liste vide en entrée d'appel —
  remplacés par un index de base capturé au début de chaque méthode.
  (3) `optimize_two_phase` ne transmettait pas `param_search_optim` à
  `_dispatch` : le toggle utilisateur était silencieusement ignoré pour les
  jobs `ml_tune_hp` — plombé de bout en bout (signature, les deux appels,
  le site d'appel d'`auto_optimizer`). Docs API/UI remises au design actuel
  (décrivaient encore l'ancien dépistage hors budget sur fenêtre réduite).
- 743/743 tests verts.

### 🧹 Résolution de la dette lint pré-existante du dépôt (773 → 0 erreur)

`ruff check .` n'avait jamais été vert depuis la création du job CI
(Sprint 7) — 773 erreurs pré-existantes sur 163 fichiers, aucune liée aux
sprints de ce document. Traité en trois passes, chacune vérifiée par la
suite de tests complète avant la suivante :

- **Mécanique et sûre** (`ruff --fix`) : tri des imports (193), imports/
  variables/f-strings inutilisés (20).
- **Instructions compactées en une ligne** (`autopep8 --select=E701,E702,
  E401`, 458 occurrences) : vérifié bit-exact (tests identiques avant/après)
  avant application au dépôt réel — la même passe en mode `--aggressive`
  incluant `E501` a été rejetée après coup car elle cassait des f-strings en
  pleine chaîne dans 5 fichiers (littéraux non fermés) ; ces ~37 lignes trop
  longues ont été re-wrappées à la main.
- **Noms de variable ambigus** (`l`, 41 occurrences → `lo`/`lvl`/`ls` selon
  le contexte réel : prix bas OHLC, niveau S/R, perte RSI) : la première
  tentative par regex (`\bl\b`) a corrompu du texte français dans des
  docstrings/commentaires (élisions `l'` — ex. « l'intérieur » →
  « lo'intérieur »), détectée avant commit et intégralement annulée ;
  refaite par renommage au niveau des tokens Python (`tokenize`, jamais dans
  une string/commentaire), vérifiée sans collision de portée.
  Deux ré-exports intentionnels (`CleanJSONResponse`, `available_memory_
  bytes`) supprimés par erreur par un fixer automatique malgré leur
  `# noqa: F401` — détecté par échec de collection pytest, restauré.
- `.github/workflows/ci.yml` job `lint` **vert pour la première fois**
  (vérifié sur le commit réel via l'API GitHub, pas seulement en local).
  743/743 tests inchangés, 0 régression fonctionnelle, aucun fichier
  touché en dehors du périmètre lint (pas de refactor, pas de changement de
  comportement).

### 🔒 Exclusion mutuelle backtest ↔ optimisation (contention CPU/mémoire)

- `/api/backtest` et `/api/optimize/start` tournent dans le même process
  serveur sans portillon partagé (`_bt_semaphore` vs `_job_semaphore`/
  `_acquire_mem_slot` de `AutoOptimizer`, chacun scopé à sa propre famille) —
  un batch d'optimisation (potentiellement des dizaines de jobs LightGBM
  concurrents) et un backtest manuel pouvaient se marcher dessus. Chaque
  route refuse désormais l'autre pendant qu'elle tourne (429, message
  explicite) plutôt que de risquer la contention CPU/OOM. Le message remonte
  tel quel jusqu'à l'UI (Next.js : `toast.error` déjà branché sur
  `ApiError` ; legacy HTML : panneau de log déjà branché sur `detail`) sans
  aucun changement frontend. 2 tests de régression ajoutés
  (`tests/test_api_routes.py`).

### 🔬 Re-comparatif DEAD-01 : méthodologie de production, 15 stratégies × 5 TF sans exception

Suite du comparatif du 2026-07-18/19 (Post-Sprint 8), refait intégralement
après les accélérations optimiseur livrées entre-temps, avec deux
différences méthodologiques majeures par rapport au premier passage :

- **Dimensionnement de fenêtre IS/OOS calqué sur la production**
  (`auto_fetch_limit`/`split_is_oos`, les mêmes fonctions que
  `/api/optimize/start`/`AutoOptimizer` utilisent réellement) au lieu du cap
  fixe arbitraire (8000 bougies, split 70/30) du script ad hoc précédent —
  qui sous-dimensionnait l'OOS pour les stratégies ML à gros warmup et
  produisait un score dégénéré (-999, 0 trade OOS) pour `opus_omnibus_v12`
  sur TOUS les TF, y compris après le passage à `bayesian_search` documenté
  au Post-Sprint 8. Avec le dimensionnement correct, v12 obtient un score
  OOS réel et fini sur 4 TF sur 5 (négatif sur 15m/30m/1h, fortement positif
  sur 4h : 0.60).
- **Aucune stratégie sautée** : le premier comparatif avait un
  `SKIP_OPT_STRATS` explicite pour v9/v10/v11_followsetup/v12 sur 4h/1j
  (leur optimisation dépassait 300-500 s et avait dû être tuée
  manuellement) — ces 4 lignes n'avaient donc aucun résultat optimisé, sur
  aucun TF. Ce re-run a fait tourner l'optimisation complète (10 essais,
  `n_jobs=2-3`) pour les 15 stratégies × 5 TF sans aucune exception —
  ~5h de calcul en tâche de fond (fenêtres 2-3× plus grandes que le cap
  fixe précédent), 2 stratégies ayant dû être relancées individuellement
  après un timeout dû à la contention CPU du batch (`-P2` concurrent),
  cf. section précédente pour le garde-fou correspondant côté API.
- Verdict DEAD-01 mis à jour (détail complet dans le rapport HTML remis à
  l'utilisateur, hors dépôt) : 6/8 candidats restent des suppressions
  nettes, confirmées avec des échantillons 2-4× plus grands qu'avant. Les 2
  cas « discutables » (`v11_followsetup`/`v11_followsetup_no_ml`) le
  restent, mais le dossier a changé de forme — la variante *no_ml* se
  renforce (1h optimisé passe positif), la variante ML s'affaiblit (score
  OOS de recherche positif partout mais aucun gain traduit sur le backtest
  complet, signal probable de surapprentissage à seulement 10 essais).
- **Découverte hors périmètre DEAD-01** : `v11` et `v12` (actives en
  production, pas candidates DEAD-01) ressortent négatives sur leur TF de
  production habituel (1h) dans ce test, et positives uniquement sur 4h — un
  TF où elles ne sont pas utilisées en production. Signalé pour examen
  séparé, pas tranché ici (un seul run BTC/USDC, pas de repli walk-forward).
- Aucun fichier supprimé — décision de suppression toujours en attente côté
  utilisateur.

### 🔬 DEAD-01 : révision du verdict v9 + investigation légitimité du pkl figé

Suite de discussion utilisateur sur le re-comparatif ci-dessus, deux volets :

**Révision du verdict `opus_omnibus_v9`.** Classé « supprimer » à tort dans
la première passe du verdict, sur le seul motif que `v10` n'en hérite pas
— un critère de versioning, pas de qualité (l'utilisateur l'a relevé
explicitement : « il ne faut pas que le versioning soit un motif de
DEAD-01 »). Les chiffres du re-comparatif montrent au contraire le signal
le plus fort de toute la lignée v7-v10 (1h : 384 trades, PnL +561.5,
Sharpe 49.4 — supérieur à `v8` ET `v10`, tous deux gardés). Vérifié par
import direct des modules (`_DEFAULT_SETUPS`) : `v9` porte une couverture
Trend-Up réelle (`LONG_TU`/`LONG_PULLBACK_TU`) que `v10` a explicitement
écartée (`LONG_PULLBACK_TU` jugé « inefficace sur 1h » dans son propre
historique de développement) — un jugement délibéré, mais non confirmé sur
la fenêtre de données de ce comparatif. `v9` retiré de la liste DEAD-01 :
**5/7 candidats restants** sont des suppressions à critère structurel fort
(`v7`, `v7_pretrained`, `v10_retrained`, `v11_no_ml`, `opus_stat_retrained_v4`
— chacun un sous-ensemble strict ou un jumeau redondant d'une stratégie
gardée, vérifié par comparaison byte-à-byte des tables de setups).

**Investigation de la légitimité du pkl figé V4** (`opus_stat_pretrained_v4`,
dépendance dure de `v7_pretrained`/`v8`/`v9`/`v10`) — l'edge le plus solide
mesuré dans toute la famille méritait vérification plutôt que confiance :

- **Test de fuite** : reconstruction des features/labels V4 exacts sur la
  série courante, comparaison AUC in-sample (avant `split_idx` stocké dans
  le pkl) vs OOS réel (après). Résultat : **pas de fuite** — in-sample ≈
  OOS partout (amplitude 15m 0.764 vs 0.695 ; amplitude 1h 0.708 vs
  **0.761**, OOS meilleur ; direction quasi-aléatoire des deux côtés,
  0.50-0.58). Une vraie mémorisation ferait exploser l'AUC in-sample —
  absent ici. Split chronologique respecté, early-stopping efficace.
- **Origine du WR de 83-90 %** : pas une direction juste (AUC 0.53, pile ou
  face) — un filtre de **sélectivité sur l'amplitude** (AUC OOS 0.70-0.76,
  vrai signal) combiné à l'asymétrie TP serré/SL large et au biais long
  sur une fenêtre haussière. Confirmé sur le batch complet : les 5
  stratégies à pkl figé sont la seule catégorie positive de toute la
  famille sur 30m/1h.
- **`v8_no_ml`/`v10_no_ml` (actives en `manual_active`)** : le batch
  (défauts de classe) les sous-estimait — leurs vrais `optimizer_results`
  de production font +52/+85 de PnL — mais elles restent **franchement
  perdantes** (PnL −95/−110, WR 36-38 %) malgré un `oos_score` de
  production positif (0.76-0.77) qui a dû motiver leur promotion. Même
  divergence score-OOS-vs-backtest-complet que `v11_followsetup` ci-dessus.
- **Expérience (sans fuite, split temporel strict 2020-2025 train /
  2025-2026 test)** : geler la recette V11 (labels multi-horizon,
  calibration isotone, 437 features) sur 40 000 barres — pour tester si
  elle combine la « meilleure recette » à l'avantage d'échantillon du V4 —
  **hypothèse réfutée**. À seuils par défaut : 6 trades seulement (non
  concluant). Desserrage progressif des seuils jusqu'à volume comparable à
  v8/v10 (~190 trades, delta 0.15) : WR chute à 53.4 %, PnL −32, PF 0.81,
  contre v8 WR 82.5 %/PnL +238/PF 4.84 au même volume. Le V4 figé bat la
  recette V11 figée à volume égal.
- **Diagnostic causal** (AUC OOS de chaque sous-modèle v11 séparément,
  fenêtre test strictement postérieure) : la **direction n'a aucun edge
  dans aucune des deux recettes** (V11 0.532, V4 0.54 — le multi-horizon
  n'y change rien, 0.531 identique en mono). L'**amplitude** a un edge
  réel dans les deux, mais celui de V11 est plus faible (0.674-0.700 vs
  0.76) et le multi-horizon le **dégrade** au lieu de l'améliorer (0.700
  contre son propre label mono, seulement 0.674 contre le label
  multi-horizon qu'il prédit). Facteur aggravant probable : 437 features
  vs 40 sur le même échantillon.
- **Conclusion actionnable** : le pkl figé est légitime et porte le
  meilleur discriminateur d'amplitude de la famille — aucune remise en
  cause de conserver `v8`/`v9`/`v10` pour DEAD-01. Toute amélioration
  future du modèle devrait cibler un meilleur discriminateur d'amplitude
  (moins de features, horizon unique) — jamais la direction, qui n'a de
  edge dans aucune recette testée.

Rapport HTML mis à jour (§06, nouvelle section) remis à l'utilisateur, hors
dépôt. Nom de la future stratégie maître unifiée révisé de `OpusBase`
(trop spécifique à la lignée existante) vers **`setup_router`** (décrit la
fonction réelle : router vers une table de setups configurable selon le
régime, avec source de signal — pkl figé / ré-entraîné / proxy / ML V11 —
et mode de sortie enfichables). Aucun fichier supprimé, aucun changement
de code de stratégie — investigation pure via scripts de scratchpad de
session.

Deux items de backlog ajoutés au plan directeur (§4.5) à partir de ces
constats : **ML-01** (gating de promotion `manual_active` par walk-forward
au lieu d'un `oos_score` sur un seul split) et **ML-02** (gestion du cycle
de vie des modèles pkl : fenêtre d'entraînement dimensionnée — le trainer
auto ne fetche que ~1560 barres contre les ~40k qui font l'edge —,
provenance/métadonnées dans le pkl, script d'entraînement reproductible
committé, ré-entraînement périodique sur grande fenêtre glissante, et flag
pour optimiser les seuils contre un modèle figé).

### 📚 Documentation

- `docs/PLAN_DIRECTEUR_MULTI_ACTIFS.md` : fusion des 3 plans (audit
  `docs/audit/`, plan complémentaire du 14/07, plan multi-actifs) — état
  vérifié contre le code, backlog consolidé (§4), ~119 items faits /
  ~81 restants.
- `docs/audit/00-INDEX.md` : Vagues 0-2/4-6 marquées réalisées, Vague 3
  identifiée non réalisée (reprise en Sprint 7 du plan directeur).

---

## [12.17.0] - 2026-07-11

### 🛡 Audit Vagues 1-2 : sécurité + intégrité de la mesure (14 items)

**Vague 1 — Sécurité (docs/audit)** :
- **[UI-01]** XSS corrigée : échappement unifié sur `escHtml()` partagé
  (data.html laissait passer les guillemets dans un attribut onclick →
  boutons passés en data-attributes + délégation d'événement).
- **[OPS-02]** `web.host=0.0.0.0` sans `web.api_key` → **blocage** au
  chargement (override dev : `ALLOW_INSECURE_WEB=1`).
- **[OPS-03]** Unit systemd `crypto-bot-watchdog.service` : le dead-man
  switch a enfin un lecteur en production.
- **[OPS-04]** `paper_mode=false` sans canal de notification → log CRITICAL.
- **[OPS-05]** 6 endpoints GET (settings/changelog/optimize status/stream/
  results/spaces) exigent désormais la clé API.
- **[OPS-07]** CandleStore : écriture parquet **atomique** (tmp + os.replace).

**Vague 2 — Intégrité de la mesure** :
- **[BT-02]** Monte-Carlo réparé : bootstrap avec remise pour l'équité
  finale/prob_profit (la permutation donnait p5=p95) ; permutation conservée
  pour drawdown/ruine (statistiques d'ordre).
- **[BT-03]** `max_notional_pct` unique (0.20) backtest/live — le backtest
  plafonnait à 50 % du capital quand le live exécute 20 %. Delta documenté
  (smart_money BTC 4h : FULL +483→+329 mais PF 1.67→1.90 — le backtest dit
  désormais la vérité du live).
- **[ARCH-01]** Parité : `_merge_params` (live) filtre `_GLOBAL_PARAM_KEYS`
  comme le backtest — une clé globale glissée dans optimizer_results ne peut
  plus modifier le risque en live.
- **[BT-04]** Le bouton « Appliquer » passe le MÊME garde-fou que l'auto-apply
  (refus 409 motivé, override `force=true` loggé).
- **[BT-06]** Seuils unifiés (app/core/stats_thresholds.py) :
  MIN_SIGNIFICANT_TRADES=10 pour toute décision engageante (apply, promotion
  ACTIF — fidelity_min_fills 2→10) ; 2 réservé à l'anti-dégénérescence.
- **[BT-07]** Gate **walk-forward** sur l'auto-apply : best_params figés,
  consistency ≥ 60 % sur 5 folds requise (désactivable optimizer.wf_gate).
- **[BT-08]** Convention IS/OOS unique (app/core/is_oos.py) : WARMUP=210 et
  fraction 0.35 dédupliqués (3 sites auto_optimizer + optimizer) —
  byte-identique (test d'équivalence des indices de coupure).
- **[BT-09]** Le backtest applique la même **courbe de dé-risquage en
  drawdown** que le live (×0.75 >5 %, ×0.5 >10 % — app/core/risk_curve.py).
  BTC 4h byte-identique (aucun DD >5 % sur ce run).

25 nouveaux tests. 508 tests OK.

### 🛠 Audit Vague 0 : régressions per-symbole corrigées (BT-01, BT-12, OPS-01, UI-02/03/04)

Exécution de la « Vague 0 » du plan d'audit (docs/audit/00-INDEX.md) — les six
chemins secondaires qui supposaient encore l'ancien slot 2-parties :

- **[BT-01]** `/api/optimize/apply` transmet désormais le `symbol` du job à
  `apply_best_params` — un apply manuel n'écrase plus le mapping par symbole
  des autres paires. Tests de coexistence + migration d'entrée héritée.
- **[BT-12]** `/api/optimize/start` accepte `symbols` (CSV) : boucle
  fetch+jobs par symbole (réponse `per_symbol`), mono-symbole inchangé.
- **[OPS-01]** Clés héritées 2-parties (`slot_budgets`, `disabled_slots`,
  `manual_active`) : repli par préfixe vers les slots 3-parties (helper
  `_lookup_legacy`, log au chargement, priorité à la clé exacte ; la
  désactivation d'un forçage lève aussi la clé héritée). 6 tests.
- **[UI-02]** Écran /config : panneau « Overrides par symbole » par stratégie
  (sélecteurs TF+symbole, éditeur, liste des overrides existants) ; l'API
  `strategy-params` accepte `timeframe`+`symbol` et écrit dans
  `optimizer_results[tf][symbole]` via `apply_best_params` (oos_score
  préservé, base intacte) ; nouveau `GET /api/config/strategy-overrides`.
- **[UI-03]** /audit n'écrase plus les résultats OOS entre symboles ;
  `backtest_history` passe au slot 3-parties.
- **[UI-04]** /trades : filtre « Slot » à 3 parties (`strat::tf::symbole`,
  libellé « Strat TF · Paire ») — ne mélange plus les paires.

483 tests OK (13 nouveaux).

---

## [12.16.0] - 2026-07-11

### 🎯 Configs par symbole : `optimizer_results[strat][tf][symbol]` + slots par symbole

**Séparation complète des configs par symbole** : une stratégie a une config BTC
ET une config ETH distinctes qui **coexistent** (l'une n'écrase plus l'autre), au
lieu d'un unique jeu par `(stratégie, timeframe)` partagé par tous les symboles.

**Phase 1 — résolution des paramètres**
- **`resolve_strategy_params(cfg, tf, symbol)`** accepte un `symbol`. Schéma
  `optimizer_results[strat][tf][symbol]`, **rétro-compatible** (`_select_symbol_entry`) :
  une entrée HÉRITÉE (sans dimension symbole) est réputée calibrée pour **BTC/USDC**
  (`DEFAULT_CONFIG_SYMBOL`) et **ne s'applique plus aux autres symboles** (avant,
  la config BTC déteignait silencieusement sur ETH).
- `Backtester.run` + routes scanner transmettent le `symbol`. **BTC 4h
  byte-identique** ; ETH retombe sur ses params de base sauf config dédiée.

**Phase 2 — notion de slot `strategy::tf` → `strategy::tf::symbol`**
- **`get_active_strategies_per_tf`** sélectionne les slots actifs **par (tf, symbole)**
  (top-N par symbole ; itère `scanner.symbols`).
- **`bot_identity`** (`slot_key`, `bot_id`, générations, `resolve_venue` avec
  précédence `strat::tf::symbol`), **`capital_allocator`** (`SlotBudget.symbol`,
  clés de slot), **`signal_pipeline`** (score par symbole), **`live_trader`**,
  **`oos_tracker`** (+ `get_closed_trades_for_slot(symbol=…)`), **`apply_best_params`**
  (écrit sous `[tf][symbol]`, migre une entrée héritée vers BTC/USDC) : tous
  câblés sur le symbole. 12 tests (résolution + slots + identité + allocateur).

**Activation + auto-optimiseur par symbole**
- **`trend_rider` activée** (`enabled: true`) : sur 4h elle tourne sur **ETH/USDC**
  (config OOS +286) ; sur BTC elle reste sous le top-N, donc n'y trade pas.
- **Auto-optimiseur bouclé par symbole** : la ré-optimisation planifiée et la
  re-optimisation de cycle de vie itèrent désormais `scanner.symbols` — chaque
  paire écrit sa propre `optimizer_results[tf][symbol]` (via `apply_best_params`
  avec `symbol`). L'auto-apply reste borné par « bat la baseline » et la
  viabilité OOS, donc une paire où une stratégie ne marche pas ne s'active pas.

**Phase 3 — Web / UI**
- `bot_identity.parse_slot_key` (décompose `strat::tf[::symbol]`, robuste aux "/"
  des symboles). Route **portfolio** : parse 3-composantes + expose `symbol` par
  bot. Route **trades** `/api/strategy/{slot_key}/performance` : accepte le format
  à symbole et filtre les trades par symbole. Écran **Bots** : puce symbole sur
  chaque carte + aide mise à jour (bot = stratégie × timeframe × symbole).

**Calibration (vrai Backtester, 4h, OOS 2024+)**
- **smart_money** : edge **spécifique BTC** (OOS +148). **Négatif OOS sur ETH pour
  les 24 configs testées** → non calibrable sur ETH ; reste BTC-only (config héritée).
- **trend_rider** : config **ETH dédiée** ajoutée (`adx_min=22, chop_max=60,
  trail_mult=3.5`) → **OOS +286 / PF 1.27 / 77 tr** (vs BTC OOS ~+44). Les deux
  coexistent dans `trend_rider.yaml`. Résultat concret : sur 4h, `trend_rider`
  tourne sur **ETH/USDC** et `smart_money` sur **BTC/USDC**, chacun sa config.

---

## [12.15.0] - 2026-07-10

### 🔀 SMT divergence : primitive moteur + câblage smart_money (MESURÉ non pertinent)

Nouvelle primitive **`smc.smt_series(df, correlate_path, lookback)`** (moteur,
générique) : divergence SMT `{−1, 0, +1}` entre l'actif tradé et un actif corrélé
(Parquet/CSV), aligné CAUSALEMENT par timestamp via `ict.align_series` +
`ict.smt_divergence`. Dégradation gracieuse (None si corrélé absent). `vizion`
délègue désormais son `_load_smt` à cette primitive (dé-duplication, cohérence).

**Câblage dans `smart_money`** (paramètres OFF par défaut → byte-identique,
vérifié par l'empreinte de régression) : `smt_bonus` (+`smt_conf` au score si la
divergence CONFIRME le sens), `smt_filter` (rejette un setup CONTREDIT par une
divergence opposée), point d'injection unique à la résolution des candidats.
Corrélé configurable (`smt_correlate_path`, ex. ETH pour BTC). Ajoutés au
`param_space` (leviers d'optimiseur).

**Mesure honnête (vrai Backtester, 4h, split 2/3 OOS=2024+)** :

| Variante | BTC 4h OOS | ETH 4h OOS |
|----------|-----------|-----------|
| baseline | +148 (PF 1.46, 52 tr) | −93 (PF 0.74, 51 tr) |
| SMT bonus | **identique** | **identique** |
| SMT filter | −0.1 / −1 trade | **identique** |

→ **SMT n'est PAS pertinent comme confluence/filtre à la barre d'ENTRÉE de
smart_money.** Mécanisme : le signal SMT ne se déclenche qu'à un nouvel EXTRÊME
sur `lookback` (≈ 9 % des barres), or smart_money entre sur des RETESTS d'OB/
breaker — loin des extrêmes → recouvrement quasi nul aux barres d'entrée (0 à 1
trade modifié sur 8 ans). Le câblage est CONSERVÉ (off par défaut, levier
d'optimiseur) ; une variante « SMT à la barre du SWEEP » resterait à tester.

### 🔌 Données OHLCV : correctif fetch + page « Données » + Fast Analyse Scanner

- **Fix cache** : `CandleStore._load` force désormais l'ORDRE canonique des
  colonnes (`time` en premier). Corrige le crash `unable to vstack, column names
  don't match: "open" and "time"` provoqué par un Parquet écrit avec un ordre
  différent — le `pl.concat` ne casse plus, et les fichiers déjà écrits sont
  réparés au prochain fetch.
- **Fix « 0 bougie »** : le `since` du premier fetch (full/historical) est
  désormais borné à `2017-01-01` (`_MIN_SINCE_MS`). Corrige le cas où
  `now - 50000 × 4h ≈ 2003` faisait renvoyer un tableau VIDE par l'exchange
  (OKX/Binance rejettent un `since` trop ancien) → plus aucune paire ne reste
  bloquée à 0 bougie.
- **Nouvelle page `/data` (« 🗄 Données »)** : tableau de l'état du cache par
  paire/TF (bougies, plage de dates, taille) via `GET /api/data/status`, plus
  un formulaire de **fetch manuel** sur une paire + un TF ARBITRAIRES (pas
  forcément dans la config) via `POST /api/data/refetch?symbol=&tf=&bars=`, et
  un « Recharger tout (config) ». Remplace l'ancien bouton dashboard.
- **Cadre « ⚡ Fast Analyse & optimisation »** (Scanner, entre Graphique et
  Prédictions) : screening d'indicateurs (tendance / retour-à-la-moyenne, frais
  taker vs maker, split IS/OOS) sur la paire + TF affichés, via
  `GET /api/scanner/fast_analysis`. Reprend la logique de l'ancien script
  `analyze_indicators.py`, désormais intégrée à l'UI.
- **Nettoyage** : suppression des scripts `fetch_data.py` / `analyze_indicators.py`
  et de tout le support actions/ETF/Yahoo (CAC 40, Eutelsat, Capital B). Le
  moteur est 100 % crypto (OKX/Binance via ccxt).

### 🎬 Modèle @Vizion-fr : détecteurs + stratégie `vizion` (stricte)

D'après les transcriptions de deux vidéos ICT de @Vizion-fr (entrée sur Order
Block validée par le timeframe alignment). Nouveaux détecteurs dans `ict.py` :
- **propulsion_block** : OB imbriqué dans un OB antérieur de même sens ;
- **nested_order_block** : imbrication multi-timeframe (OB LTF ⊂ OB HTF) ;
- **align_series** : alignement causal générique de deux actifs crypto par
  timestamp — préparation d'une SMT divergence sur N'IMPORTE quel couple corrélé.

Helper moteur **`smc.htf_analysis`** (bucketing causal partagé avec
`htf_trend_series` via `_htf_buckets` — extraction **byte-identique**, vérifiée)
qui expose l'analyse SMC HTF + le mapping LTF→bucket pour récupérer les OB HTF
actifs à chaque barre.

Nouvelle stratégie **`vizion`** (`enabled: false`) appliquant STRICTEMENT la
checklist : OB avec la tendance en discount, ayant pris de la liquidité,
déclenché sur rebalance de FVG, IMBRIQUÉ dans un OB HTF, confirmé par SMT
(chemin d'actif corrélé GÉNÉRIQUE, dégradation gracieuse si absent). Chaque case
est un paramètre activable → mesurable isolément.

**Mesure honnête (BTC 4h, sans SMT)** : la checklist stricte donne **1 seul
trade** sur 8 ans (0 OOS) — les gates cumulés (sweep+FVG+imbrication HTF)
filtrent presque tout. Relâchée à « OB en discount avec la tendance », c'est
+66 OOS (48 trades) — mais c'est déjà le cœur du smart_money. Confirme le caveat
des vidéos (exemples cherry-pické) : le modèle strict n'est pas tradable sur BTC
faute d'échantillon. À re-mesurer sur d'autres TF/actifs et avec SMT (données
ETH). 453 tests OK.

### 🧊 app/core/ict.py — détecteurs ICT automatisables (briques réutilisables)

Nouveau module regroupant les concepts ICT (Inner Circle Trader) automatisables
qui manquaient au moteur SMC, en primitives PURES et CAUSALES — non câblées à une
stratégie (on mesurera/activera ensuite, discipline du projet) :

- **Consequent Encroachment** (niveau 50 % d'un FVG) ;
- **Balanced Price Range** (chevauchement FVG haussier + baissier) ;
- **Unicorn model** (Breaker chevauché par un FVG de même polarité) ;
- **Projections en écarts-types** d'une jambe (grille de TP −1/−2/−2.5/−4 SD) ;
- **Silver Bullet windows** (fenêtres horaires ICT, UTC) ;
- **Judas Swing** (faux mouvement à l'ouverture de session, sweep + reclose) ;
- **SMT divergence** (deux actifs corrélés alignés divergent sur un extrême —
  le plus prometteur, exploitable dès qu'on aura ETH/SOL en local).

Toutes causales (signal à ``i`` ⇐ données ≤ ``i``, vérifié). Le reste du canon
ICT (structure, liquidité, sweeps, OB, FVG, voids, premium/discount, OTE,
killzones, HTF, AMD) est déjà dans le moteur smart_money.

**Cohérence** : trois détecteurs purs ICT qui vivaient dans la *stratégie*
smart_money — `fvg_overlap`, `inverted_fvg_overlap` (IFVG), `measured_move_target`
(symétrie de jambe) — sont **extraits vers `ict.py`** (primitives réutilisables),
la stratégie les importe. Backtest **byte-identique** (config 4h : 168 signaux,
empreinte inchangée). Les helpers du MOTEUR (`killzone_flags`, `premium_discount`,
`liquidity_targets`…), couplés au graphe d'entités et partagés avec la route
scanner, restent dans `smc.py` (API publique du moteur). 445 tests OK.

### ❌ Stratégie LTF mean-reversion : NON construite (mesurée perdante sur BTC)

Demande : bâtir une stratégie LTF mean-reversion « maker ». Après mesure
rigoureuse (BB/RSI/VWAP, filtres range, reclaim confirmé, 15m/30m/1h, frais
taker ET maker), **le mean-reversion est négatif sur BTC quel que soit le
régime de frais** : BTC est un actif de MOMENTUM, il traverse les bandes au lieu
de réverser. Le positif-sous-maker mesuré précédemment était l'entrée
STRUCTURELLE (SMC), pas du mean-reversion. Conformément à la discipline (ne pas
livrer d'edge négatif), aucune stratégie n'a été créée. Le cadre
« ⚡ Fast Analyse » du Scanner permet désormais de trouver un symbole où le
mean-reversion fonctionne réellement (instruments range-bound).

### 🤖 ML-02 : registre de modèles daté, gate de promotion, entraînement reproductible

Suite de l'investigation légitimité du pkl figé (ci-dessus) : le slot modèle
unique `models/{stratégie}_{tf}.*`, écrasé sans comparaison par trois
écrivains différents, devient un registre daté versionné par (symbole, TF,
recette) — cf. `docs/CONCEPTION_CYCLE_DE_VIE_ML.md` pour l'architecture.

- **`app/ml/model_registry.py`** : layout `models/{symbole}/{tf}/{recette}/
  {version}/`, `resolve(as_of=…, pin=…)` (jamais un modèle qui a vu les
  données évaluées), garde anti-chevauchement, `decisions.jsonl` (audit),
  repli sur l'ancien layout plat le temps qu'une recette soit migrée.
- **`app/ml/policy.py`** : gate de promotion — candidat entraîné jusqu'à
  `T-holdout`, scoré contre le sortant sur le MÊME holdout (AUC par rang
  Mann-Whitney, numpy pur — pas de sklearn/scipy dans ce dépôt), promu
  seulement s'il ne régresse pas. Fonctionne pour toute stratégie
  `BaseStrategyML` (format de persistance à 3 fichiers déjà universel).
- **Migration V4** : les modèles quittent
  `app/strategies/opus_stat_pretrained_v4_data/` pour
  `models/BTC_USDC/{tf}/opus_stat_pretrained_v4/legacy/`, comme tous les
  autres modèles — prédictions vérifiées byte-identiques avant/après.
- **Backtester** : `ml_mode` (`frozen`/`inline`/`simulated_live`) remplace
  `use_pretrained_ml` (conservé, compat) ; `simulated_live` rejoue la
  politique de rafraîchissement complète bar par bar (backtests fidèles au
  ré-entraînement périodique du live) ; `result.ml_info` expose la version
  résolue et le repli inline — fini le switch silencieux vers l'entraînement
  inline quand aucun modèle n'est publié.
- **Live trainer** : charge la dernière version promue (alerte si périmée
  >2× l'intervalle configuré), réentraîne via le gate au lieu d'un
  `fit()+save_model()` aveugle (seul garde-fou avant : AUC>0).
- **Optimiseur** : `ml_mode` optionnel (`cfg["optimizer"]["ml_mode"]`,
  défaut `"inline"` inchangé) pour optimiser les seuils contre un modèle
  figé sans réentraîner à chaque essai ; dimensions `setup_*_dir_min/dir_max`
  retirées de `param_space` (v11/v12) — AUC direction mesurée 0.53-0.54,
  au niveau du hasard y compris in-sample.
- **`scripts/train_model.py`** + `app/ml/train_runner.py` : script
  d'entraînement committé et reproductible (le pkl V4 avait été généré par
  un script hors dépôt, irrégénérable) — dry-run par défaut, `--publish`
  pour la publication gatée réelle, `--windows` pour comparer plusieurs
  tailles de fenêtre sur un holdout commun.

UI Modèles (E7) et passe de confirmation post-optimisation non construites
dans cette passe — cf. `CONCEPTION_CYCLE_DE_VIE_ML.md` §7 pour le détail
exact de ce qui reste. 66 tests ajoutés, 812 tests OK (0 régression sur les
746 préexistants).

### 🤖 ML-02/E7 : pages « Modèles » (Jinja + Next.js) + corrections du gate

UI Modèles construite dans les deux surfaces web (registre, historique de
versions/décisions, pin/promotion manuelle, entraînement + window sweep
asynchrones). En la testant en conditions réelles, plusieurs écarts ont été
trouvés et corrigés dans le gate lui-même :

- **`app/ml/policy.recipe_gate_defaults`** : le gate scorait TOUJOURS les
  candidats sur des labels multi-horizon `[1,3,6]` (pensés pour V11), y
  compris pour `opus_stat_retrained_v4`/`opus_stat_pretrained_v4` qui
  labellisent en single-horizon (`t+1`) — écart mesuré : AUC auto-rapporté à
  l'entraînement 0.732 vs AUC gate holdout 0.702 sur le MÊME modèle (labels
  différents, pas du bruit). Les deux stratégies déclarent maintenant
  `label_horizons: [1]` dans leur `fixed_params`, introspecté par le gate.
- **`policy.score_holdout`** : les recettes à persistance non-V4 (ex.
  `ml_dynamic_threshold`, un seul `{path}.lgb`) faisaient échouer
  silencieusement le scoring, avec le message trompeur "labels mono-classe /
  holdout dégénéré" (suggère un problème de données, pas d'architecture).
  Diagnostic distinct maintenant renvoyé : format de persistance non reconnu
  par ce scorer générique.
- **Warning LightGBM `bagging_by_query`** au chargement des 6 boosters
  legacy V4 : paramètre inerte (valeur 0, propre au ranking, sans effet sur
  un objectif binaire) hérité de l'entraînement original — retiré du texte
  des `.lgb` après vérification des prédictions byte-identiques avant/après.
- **Métadonnées registre V4 enrichies** : `best_auc` (0.0 → AUC amplitude
  réelle par TF) + `train_meta` (AUC direction par régime, lift horaire/jour,
  formule EV) recouvrés d'une analyse quantitative externe fournie par
  l'utilisateur — corrige le badge "n/m" trompeur dans les deux UI.
- **Sizing horaire gradué** (`opus_stat_pretrained_v4`/`opus_stat_retrained_v4`)
  : le filtre binaire (13h-20h UTC ou skip) est complété par un multiplicateur
  de taille continu dérivé du lift empirique par heure (pic ×2.43 à 14h UTC,
  plancher 0.2 la nuit) — dégradé au lieu d'un plateau plat à l'intérieur de
  la fenêtre active.
- **AUC direction par régime instrumentée dans V11/V12**
  (`app/ml/backend/trainer.py`, `train_meta["auc_dir_by_regime"]`) pour
  trancher si la purge `dir_min`/`dir_max` de l'optimiseur (ci-dessus) est
  justifiée pour CES modèles précisément (pas seulement par analogie avec le
  pkl V4 autonome). **Résultat mesuré (BTC/USDC, 15m/30m/1h, fenêtres de 20k-
  40k barres, labels single ET multi-horizon) : AUC direction par régime
  0.47-0.54 partout, y compris Trend Down — pas de signal régime-conditionnel
  reproduit sur les modèles propres de V11/V12**, contrairement au pkl V4
  (0.86-0.88 en Trend Down sur son propre test OOS). La purge reste donc
  justifiée en l'état ; `param_space` non modifié. Hypothèse la plus probable :
  méthodologie d'entraînement différente (features/pruning/calibration) plutôt
  qu'un artefact de la seule granularité des labels (testé et écarté).

### 🤖 ML-02 : le scoring du gate devient un contrat porté par la recette

Les correctifs de la passe précédente étaient des cas particuliers empilés
dans le gate (une recette single-horizon ici, un format non lu là, un
paramètre LightGBM patché à la main dans 6 fichiers). Le problème de fond :
`policy.score_holdout` **supposait** un format de persistance (bundle
amplitude+direction V4), un catalogue de features (V4) et une définition de
labels — chaque écart devenait une exception à coder.

Le scoring appartient désormais à la recette (`app/ml/scoring.py`) :

- **`gate_spec`** (déclaratif, sur `BaseStrategyML`) — `label_horizons`,
  `amp_top_pct`, `metric` : pour les recettes qui utilisent le scorer par
  défaut avec d'autres conventions. Les clés d'exploitation (seuils,
  fenêtre) restent au YAML, et `gate_metric` prime sur le `metric` déclaré.
- **`score_holdout()`** (classmethod surchargeable) — pour les recettes dont
  le format diffère réellement. `classmethod` par construction : on score un
  artefact sur disque (souvent le sortant), jamais l'état en mémoire.
- `policy.score_holdout` devient un dispatcher, avec repli sur le scorer par
  défaut si la recette n'a rien surchargé.

Ce que le contrat a révélé — **le bug touchait 6 stratégies, pas 2** :
`opus_omnibus_v7`, `opus_omnibus_v10_retrained`, `scoring_statistique_opus_v4`
et `v5` labellisent aussi en `t+1` et étaient gatées contre `[1,3,6]`, comme
`opus_stat_retrained_v4`. Toutes déclarent maintenant leur convention ;
`opus_omnibus_v12` hérite de celle de v11 sans duplication, et v11 dérive la
sienne de `fixed_params` pour que les deux ne puissent pas diverger.

- **`ml_dynamic_threshold` est réellement gatable** (au lieu de « format non
  supporté ») : scorer dédié qui charge son booster unique, construit SES
  features et SES labels à seuil de volatilité adaptatif, et arbitre sur
  `auc_dir` — la recette n'a pas de modèle d'amplitude.
- **`scoring_statistique_opus_v4/v5`** : leur format (`save_lgb_with_scaler`)
  ne sérialise ni features ni médianes → diagnostic honnête au lieu d'un
  `auc_amp` silencieusement absent.
- **Warning LightGBM générique** (`app/ml/lgb_logging.py`) : les messages
  passent par `register_logger` vers le logging Python, et le motif
  « Ignoring unrecognized parameter » est dégradé en DEBUG. Il est inoffensif
  par construction (LightGBM énumère puis ignore — prédictions vérifiées
  identiques). Le patch manuel des 6 `.lgb` legacy est **annulé** : le
  correctif est dans le code et couvre tout artefact futur.

### 🔬 Importance des features par régime (V11/V12) — réponse mesurée

L'importance « gain » de LightGBM est globale et ne peut pas dire si le
modèle lit d'autres signaux selon le régime. Les attributions par échantillon
(`predict(..., pred_contrib=True)`, moyennées par bucket de régime) le
peuvent — ajoutées à `train_meta` avec une similarité inter-régimes
(Spearman sur le vecteur complet + recouvrement des tops), exposée dans les
deux UI.

**Mesure réelle (BTC/USDC 15m/30m/1h, fenêtres 20k–40k barres) : Spearman
0.93–0.999 entre TOUTES les paires de régimes**, recouvrement des top-15 de
60–93 %. Le modèle direction de V11 hiérarchise les mêmes features partout —
la paire la moins similaire est bien `trend_up`/`trend_down` (sens attendu)
mais très loin d'une spécialisation. Cohérent avec l'AUC par régime
(0.47–0.54 partout, mesurée à la passe précédente) : le modèle ne se contente
pas de mal performer par régime, il ne *regarde* pas autre chose. La purge
`dir_min`/`dir_max` de l'optimiseur reste donc justifiée pour V11/V12 ;
`param_space` inchangé.

---

## [12.14.0] - 2026-07-09

### 🆕 Nouvelle stratégie `trend_rider` (indicateurs classiques, long-biaisée)

Stratégie construite UNIQUEMENT à partir d'indicateurs existants, par campagne
de mesure méthodique :

1. **Screening individuel** de ~22 indicateurs comme signal autonome (BTC 4h,
   sortie identique) : aucun n'a d'edge fort ; seul l'alignement de tendance
   EMA200 + EMA20/50 est positif sur toutes les périodes.
2. **Combinaison** : la confluence naïve et les triggers momentum sur-tradent et
   perdent ; le **long-only** (biais haussier crypto — les shorts saignent) sur
   un « trend-hold » filtré régime devient positif sur les deux périodes.

Setup : entrée LONG au **front montant** d'un régime de tendance filtré
[close > EMA200, EMA20 > EMA50, ADX > seuil, DI+ > DI−, choppiness < 55], sortie
**trailing** (laisse courir), SL initial 1,5×ATR. Confluences (CVD, volume, ADX
fort, bougie, RSI) disponibles pour le sizing.

Validation BTC 4h (vrai Backtester) : **FULL +370 (PF 1.18, Sharpe 2.4), OOS
2024-26 +49 (PF 1.11, oos_score +0.183)** — tradable, edge modeste et
**indépendant** du smart_money structurel. 1d testé : OOS négatif (non tradable).

Fichiers : `app/strategies/trend_rider.py` + `strategies/trend_rider.yaml`.
**`enabled: false`** — complément expérimental à valider en forward avant toute
promotion live. 7 tests unitaires (contrat, front de régime, long-only,
causalité live↔backtest). 435 tests OK.

---

## [12.13.0] - 2026-07-08

### 🔬 smart_money : re-test 15m/30m/1h avec l'arsenal complet — restent non tradables

Question : les LTF peuvent-ils devenir tradables avec les nouveaux leviers
(trailing, sizing, choppiness, confirmation bougie) ? Batterie complète relancée
par TF sur le vrai Backtester. **Réponse : non.** Le combo 4h ne transfère pas —
le trailing DÉGRADE les LTF (bruit). Seule la sélectivité extrême aide, sans
jamais rendre l'OOS positif :

| TF | baseline OOS sc | meilleur (choppiness<50) | tradable |
|---|---|---|---|
| 1h | −0.078 | **−0.021** (OOS −10,3 vs −38,8) | ❌ |
| 30m | −0.155 | **−0.098** (OOS −48,8 vs −77,4) | ❌ |
| 15m | −0.079 | **−0.045** (OOS −22,6 vs −39,3) | ❌ |

Structurel : SMC sur BTC en LTF est dominé par le bruit + les frais 0,1 %/côté
sur des jambes trop courtes ; filtrer agressivement stoppe l'hémorragie mais
effondre le nombre de trades (chiffres uniques) sans créer d'edge. Le filtre
`chop_filter_max: 50` est enregistré dans les configs 1h/30m/15m comme « moins
pire » (cohérent avec la philosophie du fichier), scores OOS mis à jour. Seul le
4h reste tradable.

### 🎯 smart_money : croisement indicateurs × SMC (choppiness + confirmation bougie)

Croisement des nouveaux indicateurs avec la stratégie SMC (chacun testé comme
filtre/confluence sur la config 4h via le vrai Backtester). **Deux gagnants nets
activés sur le 4h**, le reste neutre/négatif :

- **Filtre Choppiness < 61.8** (`chop_filter_max`) : ne trader QU'hors congestion
  (l'idée « OB en tendance, pas en chop »). FULL +387→**+483** (PF 1.51→1.69,
  Sharpe 5.2→6.7), OOS +108→**+117**, et **DD réduit** (−14 %→−9 %).
- **Confirmation bougie** (`candle_bonus`) : bonus +0.05 si pin bar / engulfing
  dans le sens du setup → via le sizing, monte les setups confirmés (qualité par
  trade très élevée). OOS +117→**+129**, score 0.359→**0.372**, trades constants.

Écartés (mesurés neutres/négatifs) : VWAP session (coupe les entrées de repli),
CVD slope (coupe trop), RSI-divergence, VSA.

Config 4h finale : trailing 3.5×ATR + time-stop conditionnel 12 + sizing par
confluence + choppiness<61.8 + confirmation bougie → **FULL +503 (PF 1.72,
Sharpe 6.8, DD −8.7 %), OOS +129 (PF 1.55, score 0.372)**. Tous OFF par défaut
(byte-identique), ajoutés au `param_space` ; seul le 4h les active. 428 tests OK.

### 📐 indicators_core : nouveaux indicateurs réutilisables (VWAP, CVD, Choppiness…)

Batch d'indicateurs génériques (importables par toute stratégie via la façade
`app.core.indicators`), en vue de les croiser avec SMC :

- **VWAP** : `rolling_vwap` (glissant), `session_vwap` (ancré jour UTC),
  `vwap_bands` (± k×σ, cibles/sur-extension).
- **CVD** (`cvd`) : Cumulative Volume Delta approximé OHLCV (multiplicateur
  money-flow) — divergences prix/CVD = absorption.
- **Choppiness** (`choppiness`) : tendance (< 38.2) vs congestion (> 61.8).
- **Keltner** (`keltner`) : EMA ± mult×ATR (canal, cibles TP).
- **Value Area** : `smc.volume_profile` renvoie désormais `va_low`/`va_high`
  (70 % du volume autour du POC) — additif, signaux SMC inchangés.
- **Price action** : `pin_bar` (marteau/étoile), `engulfing` (avalement),
  `vsa_signal` (No Demand / No Supply).
- **Divergences cachées** (`rsi_divergence_hidden`) : continuation, complément
  de `rsi_divergence` (régulières).

Toutes causales (fenêtres passées + barre courante), pures, testées (5 tests).
Microstructure (carnet d'ordres, spoofing, niveaux de liquidation) NON incluse :
nécessite des données L2/temps réel non backtestables sur OHLCV historique.

### 🧩 smart_money : 3 pistes SMC optionnelles (OFF par défaut)

Audit complet de la stratégie contre la checklist SMC de référence : **15/19
concepts déjà implémentés et validés** (swings, BOS/CHoCH, BSL/SSL, sweeps, FVG,
voids, OB avancés, breakers, premium/discount, confluence multi-facteurs, SL
dynamique, TP-liquidité, killzones, MTFA). Les 4 restants ont été mesurés
individuellement ET conjointement sur BTC 4h : tous perdants ou déjà couverts.

Trois sont implémentés **désactivés par défaut** (backtest byte-identique,
exposés au `param_space` pour d'autres TF/symboles/régimes) :

- `ext_structure_filter` (+ `ext_swing_len`) — **1d** structure interne/externe :
  2ᵉ analyse causale à pivots plus larges, gate de degré supérieur composé avec
  le HTF. Mesuré ❌ (OOS +108→+73 : coupe les retournements gagnants).
- `tp_measured_move` — **4b** symétrie de jambe : projection d'amplitude de la
  dernière jambe comme cible TP candidate (bracket). ⚠️ inerte en trailing, pire
  que le TP-liquidité en bracket.
- `inv_fvg_bonus` — **4c** inversion de rôle des FVG : bonus de confluence si un
  FVG opposé mitigé chevauche l'entrée. ≈ neutre (déjà couvert par les breakers).

La 4ᵉ piste (**5d** TP partiel / scale-out) est **abandonnée** : elle exigerait
un scale-out transverse du moteur d'exécution (backtest + live) pour une feature
mesurée négative (variance↓ mais PnL↓). Aucune modif de la config live (4h reste
trailing + time-stop conditionnel + sizing par confluence). 422 tests OK.

### ⚖️ smart_money : sizing pondéré par confluence (4h)

Empilé sur le trailing : au lieu d'un risque fixe par trade, on **alloue plus
aux setups à forte confluence** via le hook natif `size_factor` du
Backtester/live (« demi-Kelly ×confidence ») :

    size_factor = clip(1 + size_conf_slope × (score − size_conf_center), 0.4, 1.7)

Centré sur le score moyen (≈ 0.83 sur le 4h) → exposition globale ≈ inchangée :
c'est une RÉALLOCATION du risque, pas un cran de levier. Mesuré (vrai
Backtester, BTC 4h), empilé sur le trailing :

| Config | FULL 2018→26 | OOS 2024→26 | oos_score |
|---|---|---|---|
| + trailing 3.5×ATR | +318, PF 1.44, Sh 4.9 | +81, PF 1.34 | +0.291 |
| **+ sizing par confluence** | **+387, PF 1.51, Sh 5.2** | **+108, PF 1.46** | **+0.332** |

Amélioration MONOTONE avec la pente → le score du moteur est réellement
prédictif. Améliore tout sur les deux périodes **à exposition et DD égaux**
(−4,3 %) et **récupère le score composite OOS** que le trailing avait cédé
(0.332 > 0.327 du time-stop pur). Un 3ᵉ levier testé (taille scalée par régime)
est écarté : PnL en hausse mais Sharpe plat → exposition, pas edge.

`size_by_confluence: false` reste le défaut (byte-identique) ; le 4h l'active
via `optimizer_results`. Params `size_by_confluence`/`size_conf_slope`/
`size_conf_center` ajoutés au `param_space`. 418 tests OK.

---

## [12.12.0] - 2026-07-07

### 🏃 smart_money : trailing stop pour laisser courir les gagnants (4h)

Suite au time-stop (dont le gain en nombre de trades restait modeste), deux
idées testées pour exploiter davantage la volatilité :

1. **Trailing stop plutôt que time-stop** — au lieu du TP fixe, un stop suiveur
   à `trail_mult`×ATR (`TrailingStopManager` du Backtester) laisse **courir les
   gagnants**. Le time-stop devient **conditionnel** (`check_early_exit`) : après
   `time_stop_bars`, il ne coupe QUE les trades **stagnants** (MFE < `ts_profit_r`×R),
   jamais un gagnant qui court. On ride les tendances ET on coupe la chop.
2. **Plusieurs positions concurrentes** — pour ne pas rester bloqué sur un slot.

Mesuré via le **vrai Backtester** (chemin de prod, BTC 4h) :

| Config | FULL 2018→26 | OOS 2024→26 | oos_score |
|---|---|---|---|
| time-stop pur | +168, PF 1.23, Sh 2.9 | +84, PF 1.35 | +0.327 |
| **trailing 3.5×ATR + ts12** | **+318, PF 1.44, Sh 4.9** | +81, PF 1.34 | +0.291 |

**Idée 1 retenue** pour le 4h (`use_trailing: true`, `trail_mult: 3.5`,
`time_stop_bars: 12`) : le trailing **récupère l'upside des tendances** que le
time-stop pur sacrifiait (backtest complet quasi ×2, Sharpe 2.9→4.9) **sans
coûter au régime récent** (PnL OOS +81 vs +84 = égalité). Recul assumé : score
composite OOS un peu plus bas (0.291 vs 0.327), laisser courir = plus de vol.

**Idée 2 rejetée (mesurée pire)** : passer de 1 à 2+ positions n'ajoute ~+5 sur
le complet mais dégrade le régime récent (+21→+14, PF 1.34→1.21) ; le slot unique
filtre involontairement les signaux marginaux (espérance négative).

`use_trailing: false` reste le défaut de base (bracket fixe, validé toutes
périodes) ; nouveaux paramètres `use_trailing`/`trail_mult`/`ts_profit_r` ajoutés
au `param_space` de l'optimiseur. Backtest byte-identique avec le défaut (off).

### ⏱ smart_money : time-stop pour exploiter la volatilité choppy (4h)

Diagnostic depuis le Smart replay (« beaucoup de signaux, peu exploités ») :
sur le 4h récent, 37/45 sweeps sont contre-tendance (ignorés par design) ; le
vrai problème est que les trades pris **stagnent dans la chop** en attendant
une cible lointaine, ce qui bloque le slot de position unique et empêche de
capter les signaux suivants.

Cinq idées mesurées isolément (full + OOS) ; quatre échouent (entrées de
retournement sweep+CHoCH, breakeven/TP partiel, filtre ADX — toutes négatives
sur le régime récent). La gagnante : un **time-stop** (`time_stop_bars`, porté
par le mécanisme natif `exit_after_bars` du Backtester) qui coupe les positions
stagnantes après N barres. Sur le 4h :
- OOS 2024-2026 : **−19 → +96 USDC**, PF 1.08 → 1.41, DD −7,4 % → −4,0 % ;
- prend PLUS de trades (58 vs 51 : le slot se libère plus tôt) ;
- score OOS **doublé** (+0.354 vs +0.174), IS toujours positif, walk-forward
  consistance 40 % → 60 %.

Retenu pour le 4h (`optimizer_results`, choix utilisateur) — **pari de régime
assumé** : sacrifie le PnL des fortes tendances (backtest complet 400→206,
sous-période 2021-2024 négative), optimisé pour le régime choppy récent que la
méthodologie OOS du projet privilégie. `time_stop_bars: 0` reste le défaut de
base (validé toutes périodes) ; ajouté au `param_space` de l'optimiseur.
Backtest byte-identique avec le défaut (off). 415 tests OK.

### 🔧 SMC : correction des 10 findings de la revue de code

Suite à une revue complète (8 angles) de la branche, correction de tous les
findings vérifiés :

- **Cohérence UI/live** : `/api/scanner/smc` applique désormais l'overlay
  `optimizer_results` par timeframe (`resolve_strategy_params`) — la page Smart
  graph reflète la config RÉELLEMENT tradée (4h : min_score 0.75), au lieu des
  params de base.
- **Thread API protégé** : `/api/scanner/smc_replay` (backtest synchrone) est
  borné par un sémaphore non-bloquant (HTTP 429 si saturé) + cache court TTL,
  comme `/api/replay` — plus de risque d'affamer le bot live.
- **Mémoïsation de l'analyse** : `score()`/`trade_plans()`/`check_early_exit()`
  partagent un cache `(res, aux)` clé sur la dernière barre close ; le live ne
  relance plus `smc.analyze` (~130 ms → 0.3 ms entre deux barres identiques),
  et les endpoints ne l'exécutent plus qu'UNE fois par requête (vs 3).
- **Biais HTF aligné sur `_HTF_MAP`** (source unique de vérité d'app/live) :
  4h→1d au lieu d'un ×4 arbitraire ; ≥ aussi bon (4h : PF 1.52 vs 1.49) et
  cohérent partout (scanner, backtest, live). Défauts 4h re-validés :
  145 trades, WR 46.9 %, +40.1 %, PF 1.523, Sharpe 8.2.
- **Réutilisation des indicateurs canoniques** : `atr_wilder` ajouté à
  `indicators_core` (source unique du lissage Wilder, partagé SMC/Pine) ;
  `_vol_ratio_arr`/`_ema_arr` délèguent à `volume_ratio`/`ema` ;
  `_regression_channel` devient un wrapper de `regression_channel_at`.
- **Primitive de zones partagée** : `ZonesPrimitive` + palette extraites dans
  `base.html` (`SmcChart`), utilisées par Smart graph ET Smart replay (fin de
  la divergence de clipping entre les deux copies).
- **Factorisation** : helpers `_htf_ok`/`_dir_gate` partagés par `_signal_at`
  et `trade_plans` (garantit que les plans appliquent les mêmes filtres durs
  que le signal) + test de parité plan-immédiat ↔ `score()`.
- **Perf & nettoyage** : gardes CHoCH via tableau trié mémoïsé (fin du
  O(événements²) dans `prepare_for_backtest`) ; suppression de 2 clés mortes
  aux noms inversés dans `smc.DEFAULTS`.

Backtest 4h byte-identique vérifié après chaque refactor mécanique. Suite
complète : 414 tests OK (dont parité trade_plans/score et helpers).

---

## [12.11.0] - 2026-07-04

### ⏯ Page « Smart replay » : rejeu bougie par bougie de l'analyse SMC

Nouvelle page `/smartreplay` (menu Analyse) pour rejouer le cours et **voir
l'analyse évoluer comme le moteur la découvrait** : swings affichés à leur
confirmation seulement, zones qui naissent/se font toucher/s'invalident,
BOS/CHoCH et sweeps au fil de l'eau, trendlines recalculées à chaque barre.
Contrôles play/pause/pas-à-pas/vitesse (2→20 barres/s)/slider + raccourcis
clavier. Les **trades sont ceux du vrai Backtester** (paramètres par TF
résolus) : bracket entrée/SL/TP visible pendant la position (PnL latent),
dénouements ✓ TP / ✗ SL, et panneaux Performance cumulée / Journal des
trades / Lecture à la barre — l'outil d'évaluation de pertinence des
configurations. Architecture : UNE requête `/api/scanner/smc_replay`
précalcule tout (le moteur causal expose les indices de cycle de vie de
chaque entité), le navigateur reconstruit l'état à n'importe quelle barre —
lecture fluide et scrubbing instantané sans appel serveur. Vérifié par
navigation Playwright (chargement, saut, lecture auto, captures).

### 🧭 SMC : biais multi-timeframe, volume profile, killzones, AMD, rejection blocks — mesurés un par un

Cinq nouveaux enrichissements implémentés dans le moteur (`app/core/smc.py`)
et la stratégie `smart_money`, puis **mesurés ISOLÉMENT** sur BTC/USDC
15m→1d (historique complet + dernier tiers pseudo-OOS 2024-2026) :

- **Biais multi-timeframe** (`htf_trend_series` : structure BOS/CHoCH sur
  buckets horloge ×4, mapping causal par barre) — ✅ **seul enrichissement
  gagnant sur tous les TF**, activé par défaut (`htf_filter: soft`).
  4h : PF 1.485 vs 1.414, DD −8.0 vs −9.9, OOS +23 vs −8 ; **la période
  2024-2026 repasse positive (PF 1.03)**. Nouveaux défauts 4h :
  161 trades, WR 46.6 %, +41.0 %, Sharpe 7.5.
- **Volume profile** (`volume_profile` : POC/HVN/LVN causals) — confluence
  neutre au seuil 0.55, cibles légèrement négatives → off par défaut,
  exploités par les configs par TF à seuils élevés.
- **Killzones/sessions** (LDN 07-10, NY 12-15 UTC) — aucun edge horaire
  mesurable sur BTC 24/7 → off (bonus et filtre).
- **AMD / Power of Three** (compression → sweep de manipulation) — bonus
  neutre au seuil 0.55 → off, exploré par l'optimiseur.
- **Rejection blocks** (mèches de swing ≥ 0.5×ATR, setup REJECTION_RETEST)
  — dilue le PF global mais améliore l'OOS 4h/2h → off, exploré par
  l'optimiseur.

Calibration PAR TIMEFRAME (grille IS 2/3 / OOS 1/3, sélection sur le PF OOS,
score officiel `composite_score` du repo) écrite dans
`strategies/smart_money.yaml` → `optimizer_results`. Page Smart graph :
calques « Rejection blocks » et « Volume profile », biais HTF et session
dans la Lecture du marché. 35 tests SMC, suite complète 412 OK.

### 📌 Smart graph : tableau « Trades à ouvrir » (plans recommandés)

Nouvelle méthode `Strategy.trade_plans()` (smart_money) exposée via
`/api/scanner/smc` : en plus du signal immédiat, elle anticipe les setups EN
ATTENTE — retests des order blocks frais et sweeps potentiels des poches de
liquidité actives, avec les mêmes filtres durs que la stratégie (tendance,
côté momentum, EMA200, gain > 0,4 %, RR minimal). La page Smart graph affiche
ces plans dans un tableau : statut (⚡ maintenant / ⏳ en attente), sens,
setup, **déclencheur à attendre**, entrée/SL/TP recommandés, gain potentiel,
RR, distance au prix et score minimum ; un clic trace les niveaux du plan sur
le graphique et détaille le motif. 30 tests SMC (contrat des plans inclus).

### 💡 Page « Smart graph » + enrichissements SMC (voids, breakers, structure, cycle)

Nouvelle page dédiée **`/smartgraph`** (menu Analyse → Smart graph) : chart
d'analyste complet façon « pro trader » — zones en **vrais rectangles ombrés**
(primitive canvas lightweight-charts) pour l'offre/demande, les poches de
liquidité BSL/SSL, les FVG, les liquidity voids et les breakers ; **zigzag de
structure** (peaks/troughs) avec labels HH/HL/LH/LL et flèches BOS/CHoCH ;
trendlines + canal de régression ; **projection de cycle** (expected
peak/trough à la borne du canal) ; price lines entrée/SL/TP du signal courant.
Calques activables, deep-link `?symbol=…&tf=…`, panneaux « Lecture du
marché », « Signal smart_money » et « Zones actives ».

Moteur `app/core/smc.py` enrichi :
- **Liquidity Voids** : runs de ≥3 bougies directionnelles traversant
  ≥2.5×ATR, cycle open → mitigated → filled — bords opposés utilisés comme
  cibles de TP par la stratégie (`use_void_targets`, **+65 USDC** sur BTC 4h) ;
- **Breaker Blocks** : OB invalidé → polarité inversée, retest suivi ;
- **Structure line (zigzag)** : polyligne causale des swings alternés ;
- **Cycle de marché** : phase advance/decline + cible projetée sur le canal ;
- helpers causaux `trendline_value_at`, `regression_channel_at`,
  `void_targets_above/below`, zone morte ±eq_tol×ATR sur les sweeps de swings.

Stratégie `smart_money` adaptée : cibles voids, confluence « tap de
trendline » (+0.05), setup `BREAKER_RETEST` (testé négatif sur BTC 4h :
−163 USDC / 220 trades → off par défaut, exploré par l'optimiseur).
**Validation BTC/USDC 4h 2018→2026 : 181 trades, WR 46,4 %, +40,5 %, PF 1.41,
Sharpe 6.5, DD −9,9 %** (PF par tiers 2.28/1.54/0.95). Page vérifiée par
capture navigateur (Playwright). 28 tests SMC — suite complète 405 OK.

### 🧠 Moteur d'analyse Smart Money Concepts + stratégie `smart_money`

Nouveau moteur `app/core/smc.py` (une passe causale O(n), sans lookahead) :
- **Structure de marché** : swings fractals HH/HL/LH/LL, cassures **BOS** et
  changements de caractère **CHoCH** (sur clôture) ;
- **Zones de liquidité** : equal highs/lows (buy-side/sell-side liquidity),
  cycle de vie active → swept, **sweeps** (stop hunts) avec détection du rejet ;
- **Offre/Demande** : **order blocks** par displacement (corps ≥ k×ATR),
  strength 2 si l'impulsion casse la structure, statut fresh/touché/invalidé ;
- **FVG** (imbalances) ouverts/mitigés/comblés ;
- **Premium/Discount** (équilibre 50 %, OTE 62-79 %) — version causale par barre ;
- **Tendances** : trendlines automatiques (2 derniers swings) + canal de
  régression linéaire ±2σ.

Nouvelle stratégie **`smart_money`** (`strategies/smart_money.yaml`) :
- setups **SWEEP_REVERSAL** (prise de liquidité rejetée) et **OB_RETEST**
  (premier retour dans l'order block) — uniquement avec la tendance, côté
  momentum du range, au-dessus/en-dessous de l'EMA200 ;
- TP posé devant la prochaine poche de liquidité opposée, bracket fixe ;
- ⚠ **filtre de gain : seules les positions au gain potentiel > 0,4 %**
  (`min_gain_pct`) et RR ≥ 1.2 sont retenues ;
- backtest O(1)/barre via `prepare_for_backtest` (une passe + cache).

Validation BTC/USDC 4h 2018→2026 : 179 trades, WR 45,2 %, **+33,8 %**, PF 1.35,
Sharpe 5.5, DD −10,6 % (PF par tiers : 2.33/1.40/0.87 — edge décroissant sur
2024-2026 ; TF < 4h négatifs avec les défauts, laissés à l'optimiseur).

Branchements : case **« SMC (Smart Money) »** sur le graphique du scanner
(zones, markers BOS/CHoCH/sweeps, trendlines, canal, signal courant avec
entrée/SL/TP/gain %) via `GET /api/scanner/smc` ; stratégie disponible dans le
replay, le backtest, l'optimiseur et le live. Documentation :
`docs/SMART_MONEY_CONCEPTS.md`. 22 tests unitaires (`tests/test_smc.py`).

---

## [12.10.0] - 2026-06-18

### ⚡ Performance backtest/optimisation : suppression d'un O(n²) et du « get_column storm » des stratégies ML

**Symptôme.** Backtests ML très lents et **ralentissant avec la taille** (279 → 185
→ 147 bars/s en cours de route) — catastrophique sur 50 000 barres.

**Causes (profil cProfile).**
1. **`_detect_timeframe(df)` appelé à chaque barre** sur la fenêtre **croissante**
   `df[:i+1]` : `times.diff().median()` est O(i) → backtest O(n²). Or le
   timeframe est **constant** sur tout le backtest.
2. **`_predict` (stratégies polars v7/v10_retrained/v11/v11_followsetup,
   opus_stat_retrained_v4) accédait aux ~440 colonnes de features une par une**
   (`row[c][0]`), soit ~880 `get_column` polars par barre (~58 % du temps de
   backtest).

**Correctif.**
- `_detect_timeframe` ne lit plus que les **derniers deltas** (`tail(64)`) → O(1)
  par appel, résultat identique (espacement uniforme). Appliqué à toutes les
  variantes (ML + `_no_ml`).
- `_predict` extrait la dernière ligne en **un seul appel** (`row(0, named=True)`
  → dict) puis lit les features depuis le dict → fini le `get_column` par feature.
- Résultats **numériquement identiques** (336 tests OK, dont parité scoring/ML).

**Gain mesuré** (v11, 1h) : **~5× plus rapide** (≈380 → ≈1840 bars/s à 4 000
barres) et **mise à l'échelle linéaire** restaurée (plus de ralentissement ; le
résidu provient des seuls ré-entraînements walk-forward périodiques).

**Audit O(n²) de TOUTES les stratégies** (40 fichiers) — 5 autres bugs corrigés,
même cause racine (indicateur pleine fenêtre recalculé par barre, sans cache ni
tail, pour ne lire que la dernière valeur) :
- `composite_score` : FFT+polyfit sur toute la fenêtre/barre (bornée à 1024
  barres) **et** stochastique recalculé sur tout le df (borné à k+d barres).
- `harmonic_regime` : `.to_numpy()` sur la colonne entière (ATR/close) pour
  n'utiliser que la queue → matérialisation bornée (`tail`), `_cycle` reçoit la
  queue + l'index absolu (cache stride préservé).
- `fear_momentum` : `volume.rolling_mean(20)[-1]` sur tout le df → `tail(20)`.
- `ml_dynamic_threshold` : `_detect_tf` (deltas pleine fenêtre) et `_adx(df,14)`
  recalculé par barre → bornés (`tail(64)` / `tail(300)`).

Mise à l'échelle vérifiée **constante** après correctif (ex. composite_score :
~1050 bars/s à 4 000 comme à 12 000 barres). 35 stratégies déjà saines (cache
`prepare_for_backtest` ou lectures `pre_val`/tails bornées). 336 tests OK.

### 🎯 Optimiseur : score monotone, données auto-dimensionnées, recherche TPE, parallélisme par défaut, réglage ML two-phase

Cinq améliorations ciblées de l'optimiseur et de ses performances :

- **#1 — Score composite monotone avec le PnL** (`opt_scoring.py`). Avant, la
  pénalité d'un résultat perdant était *multiplicative et jamais négative*
  (`ret_sign ∈ {1.0, 0.3}`) : une stratégie **nette perdante** au win-rate/Sharpe
  corrects obtenait un score **positif**, était sélectionnée par l'optimiseur et
  passait le gate live (`MIN_VIABLE_SCORE`). Désormais : `PnL > 0` → bundle
  qualité (échelle **inchangée**, rétro-compatible avec les scores déjà
  persistés) ; `PnL ≤ 0` → score = rendement normalisé (**négatif** et monotone
  avec la perte). Impact live : les paramétrages nets perdants sont correctement
  exclus de `get_active_strategies_per_tf` (effet uniquement après ré-optimisation
  — les scores déjà sauvegardés restent figés).
- **#2 — Limite de bougies auto-dimensionnée** (`optimizer.auto_fetch_limit`,
  utilisée par `optimize_runner.py` et la route web). Corrige le décalage
  `RECOMMENDED_LIMIT[1h]=1500 < 2229` requis par les omnibus ML, qui faisait
  **ignorer silencieusement** ces stratégies. La limite par défaut (`--limit 0`,
  `limit=0` côté UI) dérive désormais du besoin réel (`ceil(min_bars/0.35)`). Les
  jobs ignorés sont en plus **remontés visiblement** (`⊘ … ignoré : …`) au lieu
  d'être noyés dans les logs.
- **#4 — Recherche bayésienne TPE via Optuna** (`optimizer.bayesian_search`).
  Remplace l'heuristique « random + perturbation locale » par une vraie recherche
  informée (TPE). Parallèle sur un **ProcessPool persistant** (cache de features /
  d'entraînement réutilisé entre lots) ou séquentielle (cache in-process chaud).
  **Repli automatique** sur l'ancienne heuristique si Optuna est absent (dépendance
  optionnelle ajoutée à `requirements.txt`).
- **#5 — Parallélisme par défaut** (`optimize_runner.py --jobs 0 = auto cpu-1`).
  Les threads BLAS/LightGBM restent épinglés à 1 et le portillon mémoire borne la
  concurrence → discret mais bien plus rapide.
- **#6 — Réglage ML two-phase** (`optimizer.optimize_two_phase`, opt-in via
  `--ml-tune` / case UI). Grille externe sur les hyperparamètres d'entraînement
  (`learning_rate`, `n_estimators`) × recherche interne sur les seuils de décision.
  Chaque combo segmente le cache d'entraînement (coût ~linéaire). Les HP retenus
  sont persistés dans `best_params` et réutilisés au ré-entraînement du modèle final.

---

## [12.9.0] - 2026-06-16

### 🛡️ Optimiseur ML : portillon mémoire anti-OOM (corrige l'arrêt silencieux du bot pendant une optimisation multi-jobs)

**Problème.** Lancer une optimisation ML sur plusieurs stratégies × timeframes
(ex. 9 jobs) depuis l'UI faisait **s'arrêter le bot sans aucune traceback**
(retour sec au prompt). Cause : `start_async` ouvre un thread par job et en
laisse tourner jusqu'à `cpu-1` **en parallèle** ; avec le défaut `n_jobs=1`,
chaque job évalue ses trials **dans le process** (pas de ProcessPool) — soit
`cpu-1` backtests ML walk-forward simultanés, chacun **réentraînant LightGBM en
boucle** sur de larges matrices de features. Sur de gros jeux de données (50k
bougies), le pic mémoire cumulé épuise la RAM → `std::bad_alloc` LightGBM / OOM →
le process entier est tué (sur Windows : pas d'OOM-killer noyau, pas de
traceback). Le garde-fou `mem_aware_max_workers` existant ne couvrait que le
ProcessPool d'un job (chemin `n_jobs>1`), **jamais** la concurrence inter-jobs,
et était de surcroît **désactivé sur Windows** (`available_memory_bytes()`
dépendait de `psutil` — non installé — ou de `/proc/meminfo` — Linux only).

**Correctif.**
- **Portillon d'admission mémoire** (`auto_optimizer.py`) : un job n'entre en
  exécution que si son empreinte estimée tient dans le budget restant (70 % de
  la RAM dispo, mesurée par lot). La mémoire **cumulée** des jobs actifs est
  bornée → sur machine contrainte, les jobs ML se sérialisent au lieu d'OOM.
  Règle anti-blocage : un job seul est toujours admis (au pire, un par un).
  L'estimation est échelonnée sur la taille réelle des données (la variable qui
  provoque l'OOM) et le type de stratégie (ML vs non-ML).
- **`available_memory_bytes()` multi-plateforme** (`opt_workers.py`) : ajout du
  fallback Windows `GlobalMemoryStatusEx` (ctypes) → le cap mémoire fonctionne
  enfin sur Windows même sans `psutil`.
- `psutil` ajouté aux dépendances (chemin préféré ; fallbacks `/proc/meminfo` et
  `GlobalMemoryStatusEx` conservés s'il est absent).
- `optimize_runner.py --ml-only` : pendant symétrique de `--no-ml-only`, optimise
  toutes les stratégies ML (détection structurelle `BaseStrategyML`). Ex. :
  `python optimize_runner.py --ml-only --limit 50000 --apply` (séquentiel, sûr).
- Couverture : `tests/test_opt_mem_gate.py` (admission, mise en attente,
  anti-blocage, annulation, libération, estimation).

### ⚡ Optimiseur ML : hyperparamètres d'entraînement figés + fenêtres alignées (≈ heures → minutes sur 50k bougies)

**Problème.** Impossible de mener à terme les optimisations des stratégies ML
sur 50 000 bougies (plusieurs heures par job, souvent interrompu) :

1. Les `param_space` des stratégies ML « retrained » échantillonnaient les
   hyperparamètres **d'entraînement** (`retrain_every`, `warmup_bars`,
   `n_estimators`, `num_leaves`, `learning_rate`, `amp_top_pct`, et pour V11
   `label_horizons`/`calibrate`/`prune_features`). Chaque trial avait donc une
   clé de cache d'entraînement différente → le cache process-wide
   (`train_cache.py`) ne servait à rien et **chaque trial repayait l'intégralité
   des retrains LightGBM walk-forward** (~30-100 entraînements × 2 modèles ×
   ~462 features par trial).
2. Même à hyperparamètres identiques, le déclenchement des retrains dépend du
   compteur d'appels `score()` (fonction des trades du trial) : les fenêtres
   d'entraînement divergeaient de quelques barres entre trials → fingerprints
   différents → cache contourné.

**Correctif.**
- Les hyperparamètres d'entraînement sortent de `param_space` (déplacés en
  `fixed_params`, valeurs = `_DEFAULTS`, surchargables via le YAML stratégie)
  pour : `opus_omnibus_v7`, `opus_stat_retrained_v4`, `opus_omnibus_v10_retrained`,
  `opus_omnibus_v11`, `opus_omnibus_v11_followsetup`, `opus_omnibus_v12`
  (`mldyn_lookahead`/`mldyn_vol_multiplier`), `scoring_statistique_opus_v3/v4/v5`.
  L'optimiseur se concentre sur les paramètres de **décision** (seuils, SL/TP…),
  ceux qui déterminent réellement le ratio gain/risque par timeframe.
- Nouveau `aligned_train_window()` (`app/core/train_cache.py`) : la fin de la
  fenêtre d'entraînement est alignée sur la grille `retrain_every` → fenêtres
  identiques entre trials → hits de cache déterministes. Staleness bornée à
  `retrain_every` barres, identique à la cadence de déclenchement existante.
- **Bugfix walk-forward** : dans `_train_impl`, le fast-path features prenait
  `self._bt_features.head(len(train_df))` — soit les **premières** lignes des
  features pré-calculées alors que `train_df` est une tranche de **fin** de
  fenêtre. Les modèles inline s'entraînaient donc sur les plus *vieilles*
  données au lieu des plus récentes. Corrigé via un offset (`_bt_train_offset`)
  posé par `score()` avant `_train`.

Vérifié sur données synthétiques (3 000 barres 1h, `opus_omnibus_v7`) : 2ᵉ run
= 100 % de hits du cache d'entraînement, 0 réentraînement, trades identiques.

---

## [12.8.0] - 2026-06-08

### 🗑️ Suppression des stratégies ML `_1`

`opus_omnibus_v7_1`, `v8_1`, `v9_1`, `v10_1` (variantes « score additif » des
omnibus ML) sont supprimées (`.py` + `strategies/*.yaml`). Aucune référence
restante dans `config.yaml`, les tests ou l'UI.

### 🛠️ Optimiseur : score réaligné sur le PnL et garde-fou d'application durci

**Problème.** L'optimiseur pouvait retenir/appliquer un paramétrage au PnL OOS
nettement inférieur (ex. `+33` sur 3 trades) face au paramétrage courant
(`+96` sur 15 trades). Deux causes :

1. **Score composite dominé par un Sharpe brut non plafonné** (`_composite_score`,
   `optimizer.py`). Le terme `sharpe * 0.28` n'étant pas borné, un Sharpe absurde
   de petite fenêtre (>50, voire 120 sur 3 trades) écrasait tous les autres
   termes (échelle 0–1) — et le **montant** du PnL n'entrait pas du tout dans le
   score (seul son *signe* via `pnl_sign`). Le score gonflait jusqu'à ~34 alors
   que l'UI attend une échelle 0–1 (seuils verts `>0.4`, viabilité `-0.05`).
2. **Garde-fou d'application « 2 critères sur 3 »** (`_beats_baseline`,
   `auto_optimizer.py`) : un meilleur Win Rate **et** Sharpe suffisait à
   appliquer un PnL pourtant inférieur au baseline.

**Correctif.**
- Sharpe **normalisé** dans `[-1, 1]` (saturation à `|Sharpe| ≥ 10`) pour qu'il
  pèse comme les autres métriques ; ajout d'un terme de **montant du PnL**
  normalisé (poids 0.20) ; léger renfort du poids du nombre de trades
  (`0.08 → 0.10`). Le score retrouve l'échelle 0–1 attendue par l'UI.
- `_beats_baseline` rend l'**amélioration du PnL OOS obligatoire** (plus jamais
  outvotée par WR/Sharpe), en plus d'au moins un gain de qualité (WR ou Sharpe).

### 🛠️ Optimiseur : « non appliqué = non utilisé » + `params:` jamais écrasé

**Problème.** Un résultat d'optimisation **non appliqué** (refusé par le
garde-fou en auto-apply, ou simplement en attente du bouton « Appliquer »)
était quand même persisté dans `optimizer_results[tf]` via
`save_optimizer_results`. Or `resolve_strategy_params` donne **précédence** à
`optimizer_results` sur `params:`, et `load_config` recharge ce store : le
paramétrage « refusé » devenait donc **immédiatement actif** pour le
backtest/comparatif/live (et pouvait même activer une stratégie en live). C'est
pourquoi un comparatif après optimisation reproduisait les chiffres « Après
optimisation » alors que « rien n'était appliqué ». De plus, le panneau
« Avant optimisation » était calculé sans timeframe → sans l'overlay
`optimizer_results`, donc à partir du seul bloc `params:`, ce qui ne reflétait
pas le paramétrage réellement actif.

**Correctif.**
- **Non appliqué = non utilisé** : les chemins non appliqués tracent désormais
  le résultat dans le changelog (audit) via la nouvelle fonction
  `record_optimizer_audit`, **sans** écrire dans `optimizer_results`. Le
  paramétrage en place reste actif tant qu'il n'est pas explicitement appliqué.
- **`params:` jamais écrasé** : `apply_best_params` écrit désormais
  **uniquement** dans `optimizer_results[tf]` (le store actif, par précédence) et
  laisse intact le bloc `params:` = configuration par défaut réglée à la main.
  Un timeframe est requis (sans lui, aucun emplacement à activer).
- **Baseline réaligné** : `_run_baseline` reçoit le timeframe et applique
  l'overlay `optimizer_results` → le panneau « Avant optimisation » reflète le
  paramétrage réellement actif (comme le live et le comparatif).

### 🐛 Optimiseur : Alpha OOS manquant dans le panneau « Après optimisation »

Le chemin d'évaluation **non parallèle** (`Optimizer._eval`) n'incluait pas
`oos_alpha` dans son dict de résultat, contrairement au worker parallèle. En
mode `n_jobs=1`, `best_oos_alpha` revenait donc `None` et l'UI masquait la ligne
Alpha. Ajout de `oos_alpha` à `_eval` pour aligner les deux chemins.

---

## [12.7.0] - 2026-06-06

### ✨ Indicateurs du catalogue V4 ajoutés à `indicators.py` + runner d'optimisation

**Indicateurs repris du catalogue de features V4 (~462 colonnes).** Quatre
primitives génériques, réutilisables et jusque-là absentes de
`app/core/indicators.py`, y sont ajoutées (avec tests) :
- `roc(close, n)` — Rate of Change en % (momentum fondamental, étonnamment absent) ;
- `green_ratio(df, n)` — proportion de bougies haussières sur `n` (breadth locale) ;
- `rsi_divergence(df, period, lookback)` — divergence RSI/prix signée {−1, 0, +1}
  (fusion des features `bull_div`/`bear_div`) ;
- `trend_duration(df, n, adx_threshold)` — barres consécutives en tendance forte
  (persistance de régime).

Les autres features V4 utiles étaient déjà couvertes : `precompute_df` expose en
O(1) RSI/ATR/ADX/±DI/MACD/SMA/EMA, les ratios de volatilité normalisés 100b
(`_pre_atr_pct_r`…), `_pre_range_pos20`, `_pre_rsi_vel6`, structure de bougie, etc.
— c'est cette base que consomment les jumeaux `_no_ml`.

**Runner d'optimisation en ligne de commande — `optimize_runner.py`.** Lance
l'optimisation des stratégies **une à une** sans passer par l'interface web
(même moteur `AutoOptimizer` : baseline → recherche → sauvegarde
`strategies/<nom>.yaml`, ré-entraînement/persistance du modèle pour les ML) :
- **séquentiel** (un seul job à la fois) via `AutoOptimizer.optimize_sequential` ;
- **anti-veille** multi-plateforme (macOS `caffeinate` / Windows
  `SetThreadExecutionState` / Linux `systemd-inhibit`), best-effort ;
- **thread-safe** : verrou fichier exclusif (une seule instance) en plus des
  verrous internes de l'optimiseur (YAML, registre de jobs) ;
- **tâche de fond discrète** : priorité processus abaissée (`nice`/IDLE) et threads
  de calcul bornés (`--jobs`, défaut 1 ; env `OMP/MKL/…_NUM_THREADS` plafonnés).

Exemples : `python optimize_runner.py --no-ml-only --apply`,
`python optimize_runner.py --strategies opus_omnibus_v11_no_ml --tfs 1h --trials 30`.

### 🔧 Fichiers

| Fichier | Changement |
|---------|------------|
| `app/core/indicators.py` | + `roc`, `green_ratio`, `rsi_divergence`, `trend_duration` |
| `app/engine/auto_optimizer.py` | + `AutoOptimizer.optimize_sequential` (exécution une-à-une) |
| `optimize_runner.py` | Script CLI : optimisation séquentielle, anti-veille, verrou, priorité basse (nouveau) |
| `tests/test_indicators.py` | + tests des 4 nouveaux indicateurs |

---

## [12.6.0] - 2026-06-06

### ✨ Jumeaux « sans ML » des stratégies Opus Omnibus + seuil dynamique

Les stratégies ML (`opus_omnibus_v8/v10/v11/v11_followsetup`, `ml_dynamic_threshold`)
sont coûteuses à entraîner et à maintenir (modèles LightGBM/sklearn, pkl,
ré-entraînement périodique). Cette version ajoute pour chacune un **équivalent
purement à base d'indicateurs**, suffixé `_no_ml`, qui réplique le routing de la
stratégie d'origine et ne remplace **que** les deux sorties ML.

**Chaque jumeau est autonome.** Aucun module proxy partagé, aucun import croisé
entre stratégies : tout le routing (régime, setups, sélection, sorties
anticipées) est embarqué dans le fichier, et tous les indicateurs proviennent de
`app/core/indicators.py`.

**Performance.** Les proxys lisent les indicateurs en **O(1)** depuis les
colonnes `_pre_*` déjà calculées par `precompute_df` (appliqué une fois par le
backtest et le live ; repli idempotent sinon). Aucun DataFrame de features lourd
(~462 colonnes) n'est reconstruit par bougie → backtest ~0.5 s (contre plusieurs
dizaines de secondes auparavant), coût live négligeable.

**Proxys déterministes (inline dans chaque fichier) :**
- `p_up` (direction) — sigmoïde d'une moyenne pondérée de signaux directionnels :
  DI_diff, RSI, MACD/ATR, ROC, distance SMA50, vélocité RSI, position dans la
  range 20, direction du corps de bougie.
- `p_event` (amplitude) — sigmoïde recentrée de signaux d'amplitude déjà
  normalisés par leur moyenne 100 barres (TF-indépendants) : ATR%, range,
  écart-type des log-returns, ratio de volume, ADX, corps absolu.
- Coefficients (`p_up_gain`, `p_event_gain`, `p_event_center`) paramétrables/optimisables.

**Variantes ajoutées :** `opus_omnibus_v8_no_ml`, `opus_omnibus_v10_no_ml`,
`opus_omnibus_v11_no_ml` (régime enrichi DI/pente conservé),
`opus_omnibus_v11_followsetup_no_ml` (sortie sur flip de setup conservée),
`ml_dynamic_threshold_no_ml` (filtre ADX + seuils proba + porte de volatilité
reproduisant l'esprit « seuil dynamique »).

### 🔧 Fichiers

| Fichier | Changement |
|---------|------------|
| `app/strategies/opus_omnibus_v8_no_ml.py` | Jumeau autonome sans ML de V8 (nouveau) |
| `app/strategies/opus_omnibus_v10_no_ml.py` | Jumeau autonome sans ML de V10 (nouveau) |
| `app/strategies/opus_omnibus_v11_no_ml.py` | Jumeau autonome sans ML de V11 (nouveau) |
| `app/strategies/opus_omnibus_v11_followsetup_no_ml.py` | Jumeau autonome sans ML de V11-FollowSetup (nouveau) |
| `app/strategies/ml_dynamic_threshold_no_ml.py` | Jumeau autonome sans ML du seuil dynamique (nouveau) |
| `strategies/*_no_ml.yaml` | Configs (params + coefficients de proxy) des 5 jumeaux |

---

## [12.5.0] - 2026-06-03

### ✨ Dérivés « au fil de l'eau » + stratégie `funding_flow` (100 % dérivés)

Suite de l'intégration dérivés (V12.4) : accumulation automatique dans la boucle
live + stratégie directionnelle théorique exploitant funding/OI/LSR/taker.

**Accumulation au fil de l'eau (comme l'OHLCV) :**
- `DerivativesStore.refresh()` — fetch incrémental throttlé, merge dans
  `data/derivatives/*.parquet` (même logique que CandleStore pour l'OHLCV).
- Branché dans `OHLCVCache.get()` derrière le flag `derivatives.enabled`
  (opt-in, **comportement inchangé si désactivé**) : à chaque nouvelle bougie,
  accumulation + injection des colonnes `funding_z`/`oi_change_pct`/`lsr_z`/
  `taker_z` dans le df de scoring. **Gracieux** : réseau KO → df OHLCV inchangé.
- `research/accumulate_derivatives.py` — accumulation hors-bot (cron/backfill).
- Config : section `derivatives` (`enabled`, `period`, `refresh_interval`, `z_window`).

**Stratégie `funding_flow` (rule-based, théorique) :** fade des extrêmes de
positionnement — pression de foule = somme pondérée `funding_z`/`lsr_z`/`taker_z`
(contrarian), conviction renforcée par l'OI, garde-fou tendance. Pression positive
extrême (foule longue) → SHORT ; négative → LONG. Sans dérivés → abstention.
⚠️ Théorique (historique gratuit OI/LSR/taker ≈ 30 j) : à calibrer/valider en live.

### 🔧 Fichiers

| Fichier | Changement |
|---------|------------|
| `app/core/derivatives.py` | + `refresh()` throttlé (accumulation au fil de l'eau) |
| `app/live/ohlcv_cache.py` | + enrichissement dérivés dans `get()` (opt-in, gracieux) |
| `app/core/config.py`, `config.yaml` | + section `derivatives` |
| `app/strategies/funding_flow.py` | Stratégie directionnelle 100 % dérivés |
| `strategies/funding_flow.yaml` | Paramètres |
| `research/accumulate_derivatives.py` | Script d'accumulation/backfill autonome |
| `tests/test_funding_flow.py` | Tests stratégie + hook OHLCVCache (réseau mocké) |

---

## [12.4.0] - 2026-06-03

### ✨ Intégration de données de dérivés (gratuites) + edge directionnel

Réponse à la question « peut-on prédire la direction ? ». Démarche en deux temps.

**1. Chasse à l'edge directionnel** (`research/directional_hunt.py`) — mesure
honnête sur OHLCV (P(up|condition), z-scores binomiaux, AUC logistique OOS) :
- La direction non-conditionnelle est ~martingale (AUC OOS combiné ≈ **0.52**).
- **Seul edge robuste : la mean-reversion sur la position-dans-le-range** —
  bas de range → biais UP (P(up)≈**57 %**, **z=7.6**), haut → DOWN (z=-7.3),
  cohérent 1h/4h/1d. Renforcé par : reversal de streaks, fade d'euphorie, rebond
  de capitulation (volume), pullback en tendance.
- Saisonnalité (heures funding, jour de semaine) : **démentie** (z≈0).

**2. Pourquoi il faut les dérivés** — le cœur OHLCV mean-reversion est ≈ breakeven
(`derivatives_reversion` backtest 4h ≈ -1.8 %) : l'edge directionnel est réel
(win 59 %) mais le payoff est asymétrique (cassures de range → besoin de 71 % de
win). **Filtrer les fausses reversions exige funding/OI/sentiment** — là vit
l'alpha directionnel crypto.

**Module `app/core/derivatives.py` — `DerivativesStore` (gratuit, sans clé API) :**
- funding_rate (ccxt, historique long), open_interest (ccxt), long_short_ratio &
  taker_buy_sell_ratio (Binance futures-data REST). Cache Parquet, thread-safe,
  **dégradation gracieuse** (aucune exception si réseau KO).
- `align_to_ohlcv()` : enrichit l'OHLCV (join_asof causal) avec funding_z, oi_change,
  lsr_z, taker_z. Câblage live en 1 ligne (cf. research/DERIVATIVES_integration.md).

**Stratégie `derivatives_reversion` (rule-based, zéro ML) :** fade des extrêmes de
range, **veto/boost par funding & sentiment** quand les colonnes sont présentes ;
fallback OHLCV pur sinon.

### 🔧 Fichiers ajoutés

| Fichier | Rôle |
|---------|------|
| `app/core/derivatives.py` | DerivativesStore (funding/OI/LS/taker, cache, alignement) |
| `app/strategies/derivatives_reversion.py` | Stratégie directionnelle mean-reversion + dérivés |
| `strategies/derivatives_reversion.yaml` | Paramètres |
| `tests/test_derivatives.py` | Tests (réseau mocké) du store + de la stratégie |
| `research/directional_hunt.py` | Chasse à l'edge directionnel (P(up), AUC OOS) |
| `research/DERIVATIVES_integration.md` | Doc : sources gratuites, limites, câblage |
| `research/backtest_reversion.py` | Harnais backtest du cœur OHLCV |

---

## [12.3.0] - 2026-06-03

### ✨ Nouvelle stratégie — `volatility_squeeze` (RULE-BASED, antithèse de l'Omnibus)

Issue d'une **remise en question des hypothèses** de la lignée `opus_omnibus`
V7/V8/V10/V11 (cf. `research/CRITIQUE_omnibus_v7-v11.md`).

**Critique de l'Omnibus :**
- Hypothèse fondatrice fausse — la lignée prédit la DIRECTION par ML alors qu'elle
  **admet elle-même un AUC_dir ≈ 0.53** (quasi-aléatoire, docstring V10). Toute la
  machinerie de routing `p_up` filtre donc du bruit.
- Sous-exploite le seul edge réel — l'AMPLITUDE/volatilité (AUC ≈ 0.7, clustering
  ACF|r| 0.15-0.28).
- Sur-apprentissage — 17 à 23 paramètres + seuils par setup, **tunés sur des
  backtests in-sample de 12-122 trades**, `oos_score: null` (aucune validation OOS).
- Mauvais timeframes — 15m/30m/1h, **sous le mur des frais** (mesuré).
- Complexité fragile — LightGBM inline, path-dépendant, non déterministe.

**Réponse — `volatility_squeeze` :** trader la VOLATILITÉ (prévisible), pas la
direction (aléatoire). On attend une **compression** (squeeze = largeur Bollinger
dans son percentile bas) puis sa **détente alignée sur la tendance établie**
(jamais prédite) ; abstention en chop. Règle pure : **~8 paramètres, déterministe,
ZÉRO ML, zéro réentraînement**.

**Backtest 4h, 7.5 ans (frais/spread/borrow réalistes) :**
- long-only strict : **+68.7 %** · Sharpe 13.7 · maxDD **-5.8 %** · **PF 2.49** · win 51 %.
- Walk-forward OOS : **consistance 80 %** (meilleure des stratégies du repo).
- BEAR 2022 **-1.2 % vs B&H -53 %** ; BULL 2023-24 +12.0 % (PF 3.9) ; CHOP -1.2 %.
- ⚠️ 1h backtesté **-41.6 %** → confirme que l'orientation bas-TF de l'Omnibus est
  sous le mur des frais.

> 8 paramètres déterministes battent 23 paramètres + ML inline non-validé OOS :
> la discipline (ne trader que l'edge réel) bat la complexité.

### 🔧 Fichiers ajoutés

| Fichier | Rôle |
|---------|------|
| `app/strategies/volatility_squeeze.py` | Stratégie rule-based (`BaseStrategy`, zéro ML) |
| `strategies/volatility_squeeze.yaml` | Params + `optimizer_results` (4h, 1d) |
| `tests/test_volatility_squeeze.py` | Tests unitaires + intégration |
| `research/CRITIQUE_omnibus_v7-v11.md` | Critique structurée de la lignée Omnibus |
| `research/backtest_squeeze.py` | Harnais backtest/walk-forward/split |

---

## [12.2.0] - 2026-06-03

### ✨ Nouvelle stratégie — `momentum_blitz` (AGRESSIVE, plein capital)

Pendant **agressif** de `harmonic_regime` : vise le rendement absolu maximal en
assumant un drawdown élevé. Issue de `research/analysis_aggressive.py` +
`research/STRATEGIE_momentum_blitz.md` (nouveaux TF 15m/30m analysés).

**Edges (mesurés, nets de frais) :** ignition = breakout Donchian + surge de
volume + expansion d'ATR + alignement HTF (net-positif seulement ≥ 4h ;
15m/30m/1h perdent : frais > edge). Asymétrie MFE/MAE≈1.24, queue droite +6 %.

**Mécanique d'agression :** déploiement **plein capital** (`size_factor` 1.0→2.0
selon conviction), exits **asymétriques** (stop serré 1.3×ATR + trailing LARGE
3×ATR → laisse courir), seuil de qualité bas mais gate ignition. Long-biais
(shorts net-négatifs désactivés).

**Backtest 4h, 7.5 ans (frais/spread/borrow réalistes) :**
- full1x (réaliste) : **+58.2 %** · Sharpe **5.73** · maxDD **-11.6 %** · PF 1.55.
- lev2x (agressif) : **+113.7 %** (×2.14) · Sharpe **6.95** · maxDD -12.3 % · PF 1.74.
- Positif dans tous les régimes : BEAR 2022 flat (vs B&H -53 %), BULL +31.6 %,
  CHOP +6.4 %. Walk-forward OOS : PnL moyen +87, consistance 60 %.
- ⚠️ TF = 4h uniquement (1h/30m/15m backtestés négatifs).

> Leçon : *agressif ≠ plus de trades* (plus de frais, edge dilué). La
> sélectivité (ignition-only) maximise l'edge par trade, que le plein capital amplifie.

### 🔧 Fichiers ajoutés

| Fichier | Rôle |
|---------|------|
| `app/strategies/momentum_blitz.py` | Stratégie agressive (`BaseStrategy`) |
| `strategies/momentum_blitz.yaml` | Params + `optimizer_results` (4h) |
| `tests/test_momentum_blitz.py` | Tests unitaires + intégration |
| `research/analysis_aggressive.py` | Analyse edges de gros mouvement nets de frais |
| `research/backtest_blitz.py` | Harnais backtest (déploiement/levier, Monte-Carlo) |
| `research/STRATEGIE_momentum_blitz.md` | Rapport analyse → conception → validation |

---

## [12.1.0] - 2026-06-03

### ✨ Nouvelle stratégie — `harmonic_regime` (confluence régime-adaptative)

Stratégie de swing **data-driven** issue d'une analyse quantitative exhaustive de
BTC 1h/4h/1d (`research/analysis_btc.py`, `research/STRATEGIE_harmonic_regime.md`).

**Edges retenus (mesurés, significatifs) :**
- LONG trend-momentum (close>EMA50>EMA200 + ADX + breakout) — t≈7-8, multi-TF.
- Clustering de volatilité (ACF|r|≈0.15-0.28) — timing d'entrée + sizing ATR.
- SHORT **défensif** en macro-bear CONFIRMÉ uniquement (propre sur 1d).
- Mean-reversion long douce en range (RSI survente). Cycle FFT + Fibonacci en
  confirmation/zones à faible poids (non significatifs comme edges autonomes).

**Posture :** longs en tendance + **FLAT en bear** (protège du DD -72 % du
Buy & Hold) + shorts opportunistes filtrés. Sizing par risque 1 %/trade, stop
ATR, trailing multi-phase (`TrailingStopManager`), max-hold.

**Backtest (7.5 ans, frais/spread/borrow réalistes) :**
- 4h : **+33.4 %**, Sharpe **5.29**, max DD **-7.3 %**, PF 1.41 ; walk-forward OOS
  consistance 60 %. BEAR 2022 : **-1.1 % vs B&H -53 %** (alpha +52 pt).
- 1d : **+11.5 %**, Sharpe **2.90**, max DD **-4.7 %**, PF 1.56 ; walk-forward OOS
  consistance **100 %**.
- ⚠️ 1h **non recommandé** : edge directionnel < coût round-trip → non rentable.

### 🔧 Fichiers ajoutés

| Fichier | Rôle |
|---------|------|
| `app/strategies/harmonic_regime.py` | Stratégie (`BaseStrategy`, score de confluence) |
| `strategies/harmonic_regime.yaml` | Params + `optimizer_results` validés (4h, 1d) |
| `tests/test_harmonic_regime.py` | Tests unitaires + intégration backtest |
| `research/analysis_btc.py` | Analyse quantitative reproductible (9 sections) |
| `research/backtest_harmonic.py` | Harnais backtest/walk-forward/split bull-bear |
| `research/STRATEGIE_harmonic_regime.md` | Rapport analyse → conception → validation |

---

## [12.0.0] - 2026-03-25

### ✨ Nouvelles fonctionnalités

#### Paper mode réaliste — slippage, capital settled, persistence

Amélioration majeure du mode simulation pour des résultats plus proches du trading réel.

**Slippage adverse :**
- Nouveau paramètre `paper_slippage` (défaut `0.001` = 0,1 %) dans `config.yaml` et l'API
- Chaque fill applique un slippage défavorable : les achats se font plus cher, les ventes moins cher
- Configurable via l'interface web (section *Paramètres de trading*)

**Suivi capital settled (`_paper_base`) :**
- Le capital settled (equity réalisée) est tracé séparément du `capital_display`
- Le PnL non réalisé des positions ouvertes est exclu du sizing du risque
- `capital_display = settled + PnL non réalisé` (synchronisé à chaque cycle paper)

**Persistence entre sessions :**
- `_restore_paper_base()` restaure le capital settled depuis la dernière `DailyStats.equity_close` en BDD
- Pas de remise à zéro du capital entre redémarrages en paper mode

**Protection capital insuffisant :**
- `_pre_execution_check()` en paper mode bloque une entrée si le capital disponible
  (`settled − notionals verrouillés`) est inférieur au notional demandé

### 🔧 Fichiers modifiés

| Fichier | Changement |
|---------|------------|
| `app/core/config.py` | `paper_slippage: 0.001` ajouté aux defaults |
| `app/live/live_trader.py` | `_paper_base`, `_restore_paper_base()`, `_sync_paper_balance()`, `_pre_execution_check()` |
| `app/live/position_mixin.py` | Slippage appliqué aux fills paper |
| `app/api/routes/config.py` | `paper_slippage` exposé dans l'API de configuration |
| `app/web/templates/config.html` | Champ *Paper slippage %* dans l'interface |

### 🗄️ Structure V12

```
app/
└── live/
    ├── live_trader.py     ← _paper_base, _restore_paper_base, _sync_paper_balance
    └── position_mixin.py  ← slippage adverse sur fills paper
```

---

## [11.0.0] - 2026-03-18

### ✨ Nouvelles fonctionnalités

#### CandleStore — Stockage Parquet persistant des bougies OHLCV

Nouveau module `app/core/candle_store.py` qui centralise tous les accès aux données OHLCV.

**Architecture :**
```
data/
└── ohlcv/
    ├── BTC_USDC/
    │   ├── 1h.parquet    (~80 KB pour 2 000 bougies)
    │   ├── 4h.parquet
    │   └── 1d.parquet
    ├── ETH_USDC/
    │   └── ...
    └── ...
```

**Principe de fetch :**
```
1er démarrage   → fetch complet depuis l'exchange (paginé si > 1 000 bougies)
                  → persistence Parquet (compression zstd)

Cycles suivants → lecture Parquet locale (< 5 ms)
                  → fetch incrémental : uniquement les nouvelles bougies
                  → merge + déduplication + persistence
```

**Couverture complète — tous les callers :**

| Module | Avant | Après |
|--------|-------|-------|
| `MarketScanner.fetch_ohlcv()` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| `engine.Scanner._scan_pair()` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| `engine.Scanner.get_ohlcv_df()` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| `API /api/backtest` | `fetch_ohlcv_paged()` | `CandleStore.fetch()` |
| `API /api/optimize/start` | `fetch_ohlcv_paged()` | `CandleStore.fetch()` |
| `API /api/ml/train` | `fetch_ohlcv_paged()` | `CandleStore.fetch()` |
| `CLI --backtest` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| `CLI --optimize` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| LiveTrader (tous les cas) | via `scanner.fetch_ohlcv` | via `MarketScanner` → store |

**Bénéfices :**
- Indépendance exchange : backtest, optimizer, ML training utilisent le cache local
- Historique croissant automatiquement à chaque cycle live
- Aucune nouvelle dépendance (`polars` supporte Parquet nativement via PyArrow)
- Thread-safe : verrou par fichier (live trader multi-thread)
- Nouveau endpoint `GET /api/candles/stats` pour inspecter le cache

#### Découverte automatique des stratégies (`app/strategies/registry.py`)

Chaque stratégie porte ses propres métadonnées d'optimisation en attributs de classe.
L'optimiseur les découvre automatiquement — aucun fichier central à modifier
pour ajouter une nouvelle stratégie.

### 🏗️ Refactorisation (optimizer.py)

- `STRATEGY_TIMEFRAMES`, `PARAM_SPACES`, `FIXED_PARAMS` ne sont plus codés en dur
  dans `optimizer.py`. Ces dicts sont construits dynamiquement par le registre.
- Chaque `Strategy` déclare maintenant directement :
  - `timeframes`   : `List[str]` — TFs recommandés pour l'optimisation
  - `param_space`  : `Dict[str, List]` — espace de recherche des hyperparamètres
  - `fixed_params` : `Dict[str, Any]` — paramètres fixes (non optimisables)
- `BaseStrategy` expose ces attributs avec des valeurs par défaut vides.
- `RECOMMENDED_LIMIT` (config globale par TF) reste dans `optimizer.py`.
- Rétrocompatibilité totale : tous les imports existants fonctionnent.

### 🔧 Impact pour ajouter une nouvelle stratégie

**Avant (V10)** : 4 fichiers à modifier (stratégie + optimizer.py + config.yaml + doc).

**Après (V11)** : 1 seul fichier :
```python
# app/strategies/ma_nouvelle_strategie.py
class Strategy(BaseStrategy):
    name         = "ma_nouvelle_strategie"
    timeframes   = ["1h", "4h"]
    param_space  = {"period": [10, 20, 30], "rr_min": [1.3, 1.5, 2.0]}
    fixed_params = {}
    # ... min_bars_required(), score() ...
```
L'optimiseur, l'API et le live trader la détectent automatiquement.

### 🗄️ Structure V11

```
app/
└── core/
    ├── candle_store.py    ← NOUVEAU — stockage Parquet OHLCV
    ├── indicators.py
    ├── database.py
    └── exchange.py

data/
└── ohlcv/                 ← NOUVEAU — données Parquet (gitignore)
    └── {SYMBOL}/{TF}.parquet
```

---

## [10.0.0] - 2026-03-18

### ✨ Nouvelles fonctionnalités

- **Fichier indicateurs unifié** : `app/strategies/indicators.py` est **supprimé**.
  `app/core/indicators.py` est le seul et unique module d'indicateurs. Toutes les stratégies,
  le moteur et le live trader importent directement depuis `app.core.indicators`.
- **`__version__ = "10.0.0"`** dans `app/core/indicators.py` pour traçabilité programmatique.

### ⚡ Performance — Portage maximum vers Polars

Toutes les fonctions d'indicateurs sont désormais en Polars pur ; NumPy est limité à la seule
boucle séquentielle du SuperTrend (dépendance `upper[i] = f(upper[i-1])` incontournable).

| Fonction | Avant (v9) | Après (v10) |
|---|---|---|
| `_true_range` | `np.maximum` + 3 × `to_numpy()` | `pl.max_horizontal` dans DataFrame temporaire |
| `rsi` | `to_numpy()` + `np.where` + `pl.Series(arr)` | `.clip(lower_bound=1e-10)` pur Polars |
| `adx` | 6 × round-trip numpy, `np.where`, `pl.Series(arr)` | Multiplication booléenne `(up > dn).cast(Float64)` + `.clip()` |
| `supertrend` | TR/ATR calculés en numpy + boucle | TR/ATR via `_true_range()` Polars ; boucle seule en numpy |
| `precompute_df` | `np.maximum` + `pl.when(Series)` mélangé | Entièrement Polars Series + `.clip()` |

### 🐛 Corrections de bugs

- **`precompute_df`** : `pl.when(Series)` retournait un `Expr` mélangé à des `Series`, source
  d'ambiguïtés lors de l'évaluation dans `with_columns`. Remplacé par des opérations Series pures.
- **`rsi`** (standalone) : La conversion numpy masquait les `None` initiaux ; la version Polars
  les propage correctement.

### 📚 Documentation

- **`app/core/indicators.py`** : En-tête de module avec changelog détaillé des changements v10.
- **`CHANGELOG.md`** : Ce fichier — entrée v10.
- **`README.md`** : Référence mise à jour vers V10.

### 🗄️ Structure

```
app/
└── core/
    └── indicators.py    ← SOURCE UNIQUE — v10.0.0 (tous indicateurs ici)
                           app/strategies/indicators.py SUPPRIMÉ
```

### ⚡ Migration depuis V9

```python
# Ancien code (V9) — importait depuis deux modules selon le contexte :
from app.core.indicators import detect_regime, adx_val, volume_ratio
from app.strategies.indicators import rsi, atr, adx, pre_val

# Nouveau code (V10) — un seul module source :
from app.core.indicators import detect_regime, adx_val, volume_ratio, rsi, atr_val, pre_val

# app/strategies/indicators.py est supprimé — importer directement depuis app.core.indicators
# Exemple de mapping des alias courants :
#   atr_val as calc_atr     (remplace : atr as calc_atr du shim)
#   adx_val as calc_adx     (remplace : adx as calc_adx du shim)
```

---

## [9.0.0] - 2026-03-16

### ✨ Nouvelles fonctionnalités

- **Unification versioning** : V7/V8/V9 consolidée (v9.0.0)
- **Arguments CLI nettoyés** : Suppression de `--web` et `--live`
- **Caching stratégies** : TTL 300s pour `/api/backtest/settings`
- **Health check endpoint** : `GET /health` pour monitoring
- **Pagination trades** : Support offset/skip dans `/api/trades`
- **Structured logging** : Format JSON en production

### 🐛 Corrections de bugs

- **Bug #1** : Incohérence versioning (V7 vs V8 vs V9)
- **Bug #2** : Arguments CLI obsolètes `--web` et `--live` supprimés
- **Bug #3** : Argum CLI non documentés dans README (tous documentés maintenant)
- **Bug #5** : Exception silencieuse LiveTrader → maintenant logged et tracé
- **Bug #6** : Fuseau horaire non géré (UTC standardisé)
- **Bug #11** : `/api/status` sans auth → documention clarifiée

### 🔒 Sécurité

- CORS restreint en production (voir ARCHITECTURE.md)
- Validation stratégies whitelist renforcée
- API Key en header, pas en query params

### 📊 Performance

- Index DB créés : `idx_trades_symbol_strategy`, `idx_trades_time`
- Gain : -300ms sur `/api/trades`
- Cache stratégies : -40% requêtes répétées
- Polars optimisé pour backtest multiples

### 🎨 UX/UI

- Toast d'erreur API failure
- Loading spinner sur startBot/stopBot
- Modal confirmation avant actions dangereuses (exportCSV)
- Responsive design amélioré (mobile, tablette)
- Accessibilité : aria-label, lang attribute
- Dark theme supporté

### 📚 Documentation

- **README.md** : Complète, arguments CLI, OS setup, API endpoints
- **ARCHITECTURE.md** : Design patterns, threading, sécurité, performance
- **CHANGELOG.md** : Ce fichier
- **docs/SETUP.md** : Installation détaillée par OS
- **docs/API.md** : Référence API complète (TODO)
- **docs/STRATEGIES.md** : Écrire une stratégie (TODO)

### 🗄️ Structure

```
crypto_bot_v9/
├── ARCHITECTURE.md          ← NEW
├── CHANGELOG.md             ← NEW
├── CONTRIBUTING.md          ← NEW
├── docs/                    ← NEW
│   ├── SETUP.md
│   ├── API.md
│   ├── STRATEGIES.md
│   └── TROUBLESHOOTING.md
└── ... (resto inchangé)
```

### ⚡ Migration depuis V8

```bash
# 1. Remplacer la branche
git checkout main
git pull origin main

# 2. Maj config.yaml (aucun changement requis)

# 3. Redémarrer
python cli.py

# CLI anciens arguments ? Ils sont supprimés :
python cli.py --web      ❌ Erreur (avant: web-only)
python cli.py --live     ❌ Erreur (argument inexistant)

# Nouveau comportement :
python cli.py            ✅ Démarrer bot + web (live ou paper selon config)
python cli.py --paper    ✅ Forcer paper trading
```

---

## [8.0.0] - 2025-Q4

### ✨ Nouvelles fonctionnalités

- Multi-timeframe support (`/api/config/timeframes`)
- Scanner v2 avec opportunities detection
- Optimizer résultats par (strategy, timeframe)
- Server-Sent Events pour progression optimizer
- Configuration dynamique stratégies

### 🐛 Corrections

- Gestion marge trading
- Margin level warnings
- Timeout CCXT mieux géré

### 📊 Performance

- Concurrent backtest (ThreadPoolExecutor, max_workers=4)
- Validation OHLCV gaps
- Rate limiting exchanges

### 📚 Documentation

- README.md mise à jour pour V8
- Pages web améliorées

---

## [7.0.0] - 2025-Q3

### ✨ Fondations

- Architecture multi-stratégies
- Interface web (dashboard, backtest, optimizer)
- API REST FastAPI
- Backtester avec Walk-Forward et Monte-Carlo
- 5 stratégies natives (trend, pullback_trend, supertrend_macd, breakout, ml_dynamic_threshold)
- Gestion risque + circuit breaker
- Trailing stop
- Notifications (Telegram, WhatsApp)
- Optimiseur (Grid, Random, Bayesian)

### Base de données

- SQLAlchemy ORM
- Trades tracking
- Daily stats aggregation

### Exchanges

- CCXT support (Binance, Kraken, Bybit, etc.)
- Paper trading mode
- Live trading avec gestion clés API

---

## Roadmap V10+

### Prévu

- [ ] Machine Learning integration améliorée (Random Forest, LSTM)
- [ ] Backtester distribué (Celery)
- [ ] WebSocket live streaming (vs polling)
- [ ] Multi-account management
- [ ] Risk management avancé (VaR, Corr)
- [ ] Backtester GPU-accelerated (Numba)
- [ ] Mobile app (React Native)

---

## Notes importantes

### V9 est LTS (Long Term Support)

- Support 12 mois
- Backports security fixes
- Rétrocompatibilité config

### Migration V9 → V10

- Pas de breaking changes prévues
- Config YAML rétrocompatible

---

**Crypto Bot Changelog** — Suivi transparent des évolutions 📊