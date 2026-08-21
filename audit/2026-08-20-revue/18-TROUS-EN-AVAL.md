# 18 — Ce que le moteur fait des trous et des fermetures

Ouvert le 2026-08-21, en marge du travail sur la détection de trous.

La question posée était : « une bougie manquante — maintenance, suspension,
volume nul — comment est-elle gérée par l'optimiseur, le backtest, les
modèles ? »

**Réponse mesurée : elle ne l'est nulle part.** Aucun consommateur en aval ne
sait qu'une série est trouée, et le moteur convertit partout un *nombre de
barres* en *durée* comme si le marché ne fermait jamais.

Les quatre constats ci-dessous n'ont **pas** été introduits par le travail sur
les trous — ils préexistent. Ce travail les a rendus visibles.

---

## DOWN-01 — L'annualisation suppose un marché 24/7, y compris sur les actions (P1, CONFIRMÉ)

**Fichiers** : `app/core/timeframes.py:42-49`,
`app/engine/backtest_result.py:105-110`, `:347-357`.

```python
def bars_per_year(tf: str) -> float:
    minutes = TF_MINUTES.get(tf, 60)
    return 365 * 24 * 60 / minutes
```

C'est une convention crypto — la docstring le dit — appliquée **sans
distinction** aux actions. Or `_years()` en dépend :

```python
return self._n_bars / bars_per_year(self._timeframe)
```

Une action ne cote que ~8,5 h sur 24, 5 jours sur 7 : le compte de barres et le
temps écoulé n'ont pas le même rapport que sur un marché continu.

### Mesure sur les données réelles

| Symbole | TF | Années calculées | Années réelles | CAGR | Sharpe annualisé |
|---|---|---:|---:|---:|---:|
| BNP.PA | 15m | 0,06 | 0,23 | **×3,9** | **×2,0** |
| BNP.PA | 1h | 0,54 | 2,06 | ×3,8 | ×2,0 |
| AC.PA | 1d | 18,75 | 26,63 | ×1,4 | ×1,2 |
| BTC_USDC | 1h | 5,98 | 6,43 | ×1,1 | ×1,0 |

Le crypto est juste — la convention lui correspond. **Sur une action en 15 m, le
Sharpe annualisé est surestimé d'un facteur 2 et le CAGR d'un facteur 3,9.**

### Scénario d'échec

Le facteur étant identique pour le candidat et pour le baseline, la comparaison
`oos_sharpe > b_sharpe` de `beats_baseline` reste valide **à symbole et
timeframe constants**. Trois usages, en revanche, sont faussés :

1. **Le gate Deflated Sharpe** (`opt_scoring.deflated_sharpe_ratio`,
   `min_deflated_sharpe` par défaut 0,5) compare un Sharpe **absolu** à un
   seuil. Sur une action, un Sharpe réel de 0,3 en affiche 0,6 et franchit le
   seuil.
2. **Toute comparaison entre timeframes ou entre classes d'actifs** : un
   backtest action 15 m (×2,0) contre un crypto 1 h (×1,0) n'est pas
   comparable. C'est pourtant ce que fait le tableau de comparaison des
   stratégies.
3. **Le CAGR affiché** — ×3,9 sur une action en 15 m.

### Vérification

**CONFIRMÉ** — `bars_per_year` lu, `_years()` lu, et le rapport mesuré sur
quatre séries réelles en comparant `n_bars / bars_per_year(tf)` à
`(dernier − premier) / 365,25 j`.

### Correctif proposé

`bars_per_year` doit dépendre de la **place**, pas du seul timeframe. Le
calendrier sait déjà répondre : nombre de séances par an × durée de séance /
timeframe. `app.core.market_calendar` expose `expected_bars_between` — un an
glissant donne directement le facteur.

Repli sur la convention actuelle quand aucun calendrier n'est connu (crypto),
où elle est juste.

**Effort** : 3 h, plus la revalidation des métriques actions déjà publiées.

**Délégation IA** —
> `app/core/timeframes.py::bars_per_year` renvoie `365×24×60/minutes` — une
> convention 24/7 — et sert de facteur d'annualisation à `BacktestResult`
> (Sharpe, CAGR) comme au Sharpe live (`app/live/health_mixin.py`). Sur une
> action, elle surestime le Sharpe d'un facteur 2 et le CAGR d'un facteur 3,9
> (mesuré sur BNP.PA 15 m).
> La faire dépendre du symbole : dériver le nombre de barres par an du
> calendrier de la place (`app.core.ohlcv_gaps.calendar_for_symbol` puis
> `expected_bars_between` sur un an glissant), avec repli sur la formule
> actuelle si le calendrier est 24/7.
> Attention : `bars_per_year` est la **source unique** partagée par le Sharpe
> backtest et le Sharpe live — ils doivent rester comparables. Test : le
> facteur d'une action en 15 m doit valoir ~1/4 de celui du crypto au même
> timeframe.

---

## DOWN-02 — La complétude n'est consommée par personne (P2, CONFIRMÉ)

**Fichier** : `app/core/ohlcv_gaps.py::completeness_from_gaps`.

```
grep -rn "completeness_from_gaps" app/   →  app/core/candle_store.py uniquement
```

L'indicateur est calculé, journalisé, écrit dans `<tf>.gaps.json` et affiché.
**Aucun consommateur en aval** : ni `app/engine`, ni `app/ml`, ni les services.

**Scénario d'échec** — un backtest tourne sur `XRP/USDC` 15 m à 55 % de
complétude, ou sur une action à 26 %, sans que rien ne le signale. Le résultat
est produit, publié, et peut franchir le gate d'auto-apply.

**Vérification** — **CONFIRMÉ** par recherche exhaustive.

**Correctif proposé** — un seuil à l'entrée du backtest et de l'optimiseur.
Refuser sous un plancher, avertir entre deux bornes, et **porter la complétude
dans le résultat** pour qu'elle accompagne les métriques plutôt que de vivre à
côté.

**Effort** : 2 h.

---

## DOWN-03 — La durée d'une position vient du compte de barres (P2, CONFIRMÉ)

**Fichiers** : `app/engine/position_lifecycle.py:44`, `:156`.

```python
hours_held = bars_held * _bar_to_days(ctx.timeframe) * 24.0
```

Sur une série trouée, le temps réellement écoulé est supérieur. `hours_held`
alimente `borrow_cost` et `_funding_cost`.

**Portée réelle, vérifiée** — `venue_borrow_rate` force le coût d'emprunt à **0
sur une venue spot**, et les actions du dépôt sont déclarées
`market_type: spot` (`config/venues.yaml:58`). **L'écart n'a donc aucun effet
financier sur les actions.** Il n'en a un que sur les positions à effet de
levier ou les perpétuels, où l'écart mesuré est de −7 % (BTC 1 h) à −45 %
(XRP 15 m) du temps écoulé — donc autant de coût de portage et de funding
sous-estimés.

**Vérification** — **CONFIRMÉ** ; la portée limitée au margin/perp a été
vérifiée dans `execution.venue_borrow_rate` et `config/venues.yaml`, pas
supposée.

**Correctif proposé** — dériver `hours_held` des horodatages réels
(`df["time"][i] − df["time"][entry_bar]`). Une ligne, mais elle touche le PnL :
à livrer avec les invariants comptables de `tests/test_partial_exits.py`.

**Effort** : 1 h.

---

## DOWN-04 — Rien ne vérifie la continuité temporelle, et les indicateurs raisonnent en positions (P2, CONFIRMÉ)

**Fichiers** : `app/engine/backtest.py` (boucle principale),
`app/core/indicators_core.py:44-70`.

Le backtest itère sur les lignes du DataFrame ; aucune vérification d'écart
entre deux barres consécutives (`grep` sur `diff()`, `expected_bars`,
`detect_ohlcv_gaps` dans `backtest.py` et `position_lifecycle.py` : **0
occurrence**).

Les indicateurs sont positionnels — `shift(1)`, `rolling_mean(n)`,
`ewm_mean(span=n)`. Sur une série trouée, une moyenne mobile 14 mélange des
barres qui ne couvrent pas la même durée.

**Scénario d'échec** — `RCO.PA` en 15 m n'a une bougie qu'environ deux fois sur
trois. Un RSI 14 y porte sur ~5 h de marché au lieu de 3,5 h, et le stop
`bars_held`-dépendant se déclenche à un horizon variable.

**Vérification** — **CONFIRMÉ** pour l'absence de contrôle et le caractère
positionnel des indicateurs. **Non quantifié** : je n'ai pas mesuré l'écart de
signal induit, ce qui demanderait de rejouer une stratégie sur une série
trouée et sur la même série interpolée.

**Correctif proposé** — c'est le constat le plus lourd et le moins mécanique.
Deux voies, à trancher :

- **restreindre** — n'exposer au scan et au backtest que les couples
  symbole/timeframe dont la complétude dépasse un plancher. Simple, immédiat,
  et cohérent avec `DOWN-02` ;
- **assumer** — garder les séries trouées mais rendre les indicateurs
  temporels (fenêtres en durée, pas en nombre de barres). Beaucoup plus lourd,
  et cela changerait tous les résultats existants.

La première voie me paraît la bonne pour ce dépôt : une valeur qui n'a une
bougie que deux fois sur trois en 15 m n'a pas grand sens à ce timeframe, et
réparer les indicateurs ne rendrait pas le signal meilleur.

**Effort** : 2 h (voie 1) ; plusieurs jours (voie 2).

---

## Lecture d'ensemble

Les quatre constats ont une racine commune : **le moteur confond « nombre de
barres » et « durée »**. C'est exact sur un marché continu sans trou — le cas
pour lequel il a été écrit — et faux partout ailleurs.

Le travail sur la détection de trous a rendu la mesure fiable ; il n'a rien
changé à ce que le moteur en fait. `DOWN-01` est le plus rentable : il fausse
une métrique qui alimente un gate de promotion, et le calendrier sait déjà
donner la bonne réponse.

---

## Le registre des créneaux absents n'est pas à réinitialiser (2026-08-22)

Question posée à l'occasion du correctif `/api/data/backfill-equities` : ce
backfill inerte a-t-il pollué la détection de trous ?

**Non, sur les deux axes.**

*Le backfill n'a jamais atteint le store.* La boucle levait un `AttributeError`
sur `provider.fetch_bars` avant tout appel au `CandleStore`. Or le store est le
seul écrivain de la détection de trous et du registre `<tf>.absent.json`. Un
job qui n'a rien écrit n'a rien pu corrompre.

*Le registre existant se répare seul.* Mesuré sur `data/ohlcv` (378 fichiers,
46 087 créneaux) :

| Format | Fichiers | Créneaux | Contenu |
|---|---|---|---|
| 1 (pré-calendaire) | 366 | 37 235 | actions — **62 à 77 % de week-ends et de nuits** |
| 2 (post-calendaire) | 12 | 8 852 | crypto uniquement (BTC/ETH/XRP) — 24/7, créneaux légitimes |

`ohlcv_absents._FORMAT = 2` fait déjà le travail : `charger()` ignore tout
fichier d'un format antérieur et reconstruit. Les 366 registres d'actions sont
inertes, et la première mémorisation réécrit le fichier en format 2. **Aucun
symbole action n'a encore de registre format 2.**

Le détail qui rend ces 37 235 créneaux illisibles : en heure de Paris, les
registres 1 h portent exactement 396 créneaux à **chaque** heure de la journée,
02 h et 04 h comprises, pour un marché ouvert de 09 h à 17 h 30 — la signature
d'une énumération faite sur l'horloge, pas sur le calendrier.

*Le modèle actuel ne les reproduit pas.* Vérifié sur les Parquet réels :

| Symbole / TF | Barres | Trous | Créneaux énumérés | Hors séance |
|---|---|---|---|---|
| AC.PA 1 h | 4 701 | 1 | 2 (lundi) | **0** |
| AC.PA 1 d | 6 843 | 5 | 0 | **0** |
| BNP.PA 15 m | 2 073 | 0 | 0 | **0** |
| BNP.PA 1 h | 4 737 | 1 | 2 (lundi) | **0** |

Supprimer les 366 fichiers format 1 est possible mais sans effet fonctionnel :
ils ne sont ni lus ni comptés.

### Une réserve, antérieure et non introduite ici

`_history_exhausted` (mémoire vive, 6 h) empêche un backfill profond de rejouer
une demande que le provider a déjà déclarée épuisée, tant que la borne basse du
cache n'a pas bougé. Un backfill lancé dans les 6 h suivant un passage du live
ou du scanner sautera donc ces symboles, en log `DEBUG`. Ni `refetch` ni
`backfill-equities` ne remettent ce marqueur à zéro — seule l'arrivée de
nouvelles bougies le fait (`candle_store.py:321`). Le marqueur ne survit pas au
redémarrage du processus.

