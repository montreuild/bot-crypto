# 09 — Données

Tout ce rapport a été rejoué sur les données réelles du dépôt principal
(`bot-crypto/data/`, 456 Mo) : **645 fichiers parquet, 128 symboles,
1 848 671 barres actions et 610 249 barres crypto**.

Le delta touche deux modules de ce domaine :
`app/core/ohlcv_gaps.py` (+80/−12) et `app/core/candle_store.py` (+277/−21).

L'intention du changement était juste : un week-end sur une action n'est pas
un trou de données. Le résultat sur les actions est excellent. Mais le même
commit a introduit une seconde modification qui **masque des trous réels sur le
crypto**.

---

## DAT-01 — Le seuil de détection des trous passe de 1,5×tf à 3×tf sur les marchés 24/7 (P1, CONFIRMÉ)

**Fichier** : `app/core/ohlcv_gaps.py:71-74`.
**Introduit par** : `ccb53a8` (dans le delta).

### Le code

```python
try:
    allowed = max(allowed, float(cal.max_gap_seconds(ts, expected_secs)))
except Exception:
    pass
```

`max_gap_seconds` mesure **l'ancienneté tolérée d'une donnée live** (« est-ce
que mon cache est périmé ? »), pas la taille d'un trou historique acceptable.
Pour `AlwaysOpenCalendar` — le calendrier du crypto — elle vaut
`tf_seconds × _STALE_TF_MULTIPLIER`, avec
`_STALE_TF_MULTIPLIER = 3` (`app/core/market_calendar.py:52`,
`:96-97`).

Le seuil effectif devient donc `max(1,5×tf, 3×tf) = 3×tf`. Tout trou de 1 ou
2 barres cesse d'être détecté.

Le code retiré par ce même commit portait précisément l'avertissement :
`max_gap_seconds mesure le stale live, pas un trou historique`.

### Scénario d'échec

`data/ohlcv/BTC_USDC/1h.parquet`, 52 364 barres réelles :

| | Avant le delta | HEAD |
|---|---:|---:|
| Trous détectés | 20 | **5** |
| Complétude annoncée | 92,94 % | 92,98 % |

Les 15 trous perdus, tous réels (extrait) :

```
2020-03-17 18:00 -> 2020-03-17 20:00   durée 2:00:00   1 barre manquante
2020-04-25 01:00 -> 2020-04-25 04:00   durée 3:00:00   2 barres manquantes
2020-11-30 05:00 -> 2020-11-30 07:00   durée 2:00:00   1 barre manquante
2020-12-25 01:00 -> 2020-12-25 03:00   durée 2:00:00   1 barre manquante
2021-02-11 02:00 -> 2021-02-11 05:00   durée 3:00:00   2 barres manquantes
2021-03-06 01:00 -> 2021-03-06 03:00   durée 2:00:00   1 barre manquante
2021-04-20 01:00 -> 2021-04-20 04:00   durée 3:00:00   2 barres manquantes
2021-05-08 15:00 -> 2021-05-08 18:00   durée 3:00:00   2 barres manquantes
… + 7 autres
```

`BTC_USDC/4h` : **5 trous → 1**, dont un de 12 h (2 barres).

### Conséquences

1. `CandleStore._backfill_gaps` (`app/core/candle_store.py:576`) ne voit plus
   ces trous : **il ne tentera plus jamais de les combler.** Le trou devient
   permanent.
2. `completeness_from_gaps` remonte une complétude légèrement supérieure à la
   réalité (92,98 % au lieu de 92,94 %) : l'indicateur de qualité de données
   affiché à l'utilisateur ment dans le sens rassurant.
3. Un backtest sur une fenêtre contenant un de ces trous enjambe 1 à 2 barres
   sans le signaler. Sur un trailing stop ou une sortie intrabar, une barre
   manquante change le résultat du trade.

### Vérification

**CONFIRMÉ** — comparaison A/B de `detect_ohlcv_gaps` entre `a6659e1` (extrait
par `git show`) et HEAD, sur les fichiers parquet réels, avec énumération
nominative des trous perdus.

### Correctif proposé

Retirer les 4 lignes `allowed = max(allowed, cal.max_gap_seconds(...))`. La
correction week-end/férié, elle, doit être conservée : c'est
`_calendar_closed_span` qui la porte, et elle fonctionne (voir DAT-02).

**Effort** : 15 min correctif + 30 min de test.

### Délégation IA

> Dans `app/core/ohlcv_gaps.py::detect_ohlcv_gaps`, supprimer le bloc
> `allowed = max(allowed, float(cal.max_gap_seconds(ts, expected_secs)))`.
> `max_gap_seconds` est un seuil de fraîcheur live, pas un seuil de trou
> historique : l'utiliser ici remonte le seuil de 1,5×tf à 3×tf et masque tout
> trou de 1 à 2 barres. Conserver `_calendar_closed_span`, qui porte la
> correction week-end/férié.
> Ajouter un test : une série 1h à laquelle on retire 1 barre, avec le
> calendrier `ALWAYS_OPEN`, doit produire exactement 1 trou. Ce test doit
> échouer sur le code actuel.

---

## DAT-02 — La correction des faux trous de week-end fonctionne (CONFIRMÉ — amélioration majeure)

**Fichier** : `app/core/ohlcv_gaps.py:88-110` (`_calendar_closed_span`,
`_interior_all_closed`).

Mesuré sur les données réelles, en journalier :

| Symbole | Barres | Trous avant | Trous après |
|---|---:|---:|---:|
| `AC.PA` 1d | 6 841 | 1 402 | **47** |
| `BNP.PA` 1d | 8 461 | 1 716 | **47** |

Le dépôt signalait donc un trou de données à **chaque week-end** de chaque
action, soit ~1 400 faux positifs par symbole. Ils sont éliminés. Les 47
restants sont des trous plausibles (fériés non couverts, suspensions).

C'est le gain le plus net du delta sur ce domaine. Il est cité ici pour que
`DAT-01` ne conduise pas à annuler le commit en bloc : **seules les 4 lignes de
`max_gap_seconds` sont à retirer.**

---

## DAT-03 — Le chemin rapide de `detect_ohlcv_gaps` est mort (P1 pour la performance, CONFIRMÉ)

**Fichier** : `app/core/ohlcv_gaps.py:52-53`.

```python
if delta_secs <= simple_allowed and cal is None:
    continue
```

`cal` n'est **jamais** `None` sur les chemins de production :

- `detect_ohlcv_gaps(df, tf, symbol=…)` fait
  `cal = calendar_for_symbol(symbol)` (`:41-42`), qui renvoie `ALWAYS_OPEN`
  pour le crypto (`ohlcv_gaps.py:24`) — un objet, pas `None` ;
- les 4 appels de `candle_store.py` (`:429`, `:576`, `:629`, `:1042`) passent
  explicitement `calendar=calendar_for_symbol(symbol)`.

Résultat : **chaque barre de chaque symbole** traverse le chemin calendaire
complet — 7 sondes `is_open`, plus `session_end`, `next_open`,
`max_gap_seconds` — alors que le résultat est acquis d'avance pour un écart
normal (`allowed` ne peut que croître, donc `delta_secs > allowed` reste faux).

Le détail chiffré et l'impact global sont dans `13-PERFORMANCE.md` (PERF-01).

**Vérification** — **CONFIRMÉ** par lecture du code et par la mesure : 3,2 µs
par barre en crypto contre 77 à 373 µs en actions, sur données réelles.

### Correctif proposé

```python
if delta_secs <= simple_allowed:
    continue
```

La garde `cal is None` est inutile : quand l'écart est déjà sous le seuil
simple, aucun calendrier ne peut le transformer en trou, puisque `allowed`
n'est calculé que par `max(...)`.

**Effort** : 5 min. Correctif d'une ligne, à livrer avec `DAT-01`.

---

## DAT-04 — `_delta_seconds` peut lever sur une valeur temporelle nulle (P3, PLAUSIBLE)

**Fichier** : `app/core/ohlcv_gaps.py:56`.

```python
delta_secs = float(delta_secs_arr[i]) if delta_secs_arr is not None else …
```

`delta_secs_arr` vient de `pl.col("time").diff().dt.total_seconds()`. Si la
colonne `time` contient un nul en position `i`, l'élément vaut `None` et
`float(None)` lève `TypeError`.

**Scénario d'échec** — un parquet dont une ligne a `time = null` fait remonter
une `TypeError` non typée jusqu'à l'appelant. Dans `candle_store._backfill_gaps`
(`:575-580`) l'exception est capturée et le rattrapage abandonné silencieusement ;
dans `stats()` (`:429`) elle n'est pas capturée et remonte à la route API.

**Vérification** — lecture du code. **Non reproduit** : aucun des 645 fichiers
parquet réels ne contient de `time` nul, donc le cas ne se produit pas
aujourd'hui sur ces données.

**Correctif** — `float(delta_secs_arr[i] or 0.0)`, ou filtrer les nuls en amont.

**Effort** : 10 min.

---

## Ce qui a été vérifié sans rien trouver

- **`_interior_all_closed`** — échantillonne 7 points intérieurs. Pour un écart
  normal en séance, la première sonde renvoie `is_open=True` et la fonction
  sort immédiatement : pas de faux négatif observé sur les 1,85 M de barres
  actions.
- **`_delta_seconds` et le type polars** — `dtype in (pl.Datetime, pl.Date)`
  fonctionne bien sur les dtypes concrets (`Datetime(time_unit='us')`) des
  fichiers réels : la voie vectorisée est effectivement empruntée, vérifié par
  la mesure crypto à 3,2 µs/barre (la voie scalaire serait bien plus lente).
- **`gap_duration`** — le passage de `str(delta)` à `str(times[i] - times[i-1])`
  produit exactement la même chaîne. Vérifié sur les trous réels listés en
  DAT-01.
- **Périmètre des données** — `data/features/` ne contient que `BTC_USDC`
  (413 Mo). Les 127 symboles actions n'ont pas de features précalculées. Ce
  n'est pas un défaut du delta, mais c'est une asymétrie à connaître avant
  d'interpréter toute mesure ML sur les actions.
