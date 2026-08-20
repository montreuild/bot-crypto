# 13 — Performance

Toutes les mesures de ce rapport sont des **temps mesurés sur les données
réelles** (`bot-crypto/data/`, 645 parquet, 128 symboles), pas des estimations.
Chaque régression est comparée en A/B contre la version d'avant le delta,
extraite par `git show a6659e1:<fichier>`.

---

## PERF-01 — La détection de trous est 1,75× plus lente, jusqu'à 5,5× sur certains fichiers (P1, CONFIRMÉ)

**Fichier** : `app/core/ohlcv_gaps.py:52-79`.
**Introduit par** : `ccb53a8` (dans le delta).

### Mesure A/B sur données réelles

`detect_ohlcv_gaps`, même DataFrame, même calendrier, avant et après le delta :

| Symbole | TF | Barres | Avant | Après | Ratio |
|---|---|---:|---:|---:|---:|
| BTC_USDC | 1h | 52 364 | 118,9 ms | 172,3 ms | ×1,45 |
| BTC_USDC | 15m | 59 473 | 134,3 ms | 185,9 ms | ×1,38 |
| AC.PA | 1h | 4 689 | 285,8 ms | 660,2 ms | ×2,31 |
| **AC.PA** | **15m** | **1 994** | **118,8 ms** | **657,3 ms** | **×5,53** |
| AC.PA | 1d | 6 841 | 831,7 ms | 917,2 ms | ×1,10 |
| BNP.PA | 1h | 4 725 | 555,9 ms | 1 505,2 ms | ×2,71 |
| AIR.PA | 15m | 2 028 | 276,6 ms | 769,0 ms | ×2,78 |
| BNP.PA | 1d | 8 461 | 1 260,0 ms | 1 413,1 ms | ×1,12 |
| **Total** | | **140 575** | **3 582 ms** | **6 280 ms** | **×1,75** |

### Coût unitaire par barre

| Marché | µs / barre |
|---|---:|
| Crypto (`AlwaysOpenCalendar`) | 3,2 |
| Actions (`XPAR`) | 77 à 373, moyenne mesurée **212** |

**Le chemin calendaire coûte 66× le chemin simple.**

### Cause

Le garde-fou censé éviter ce coût ne se déclenche jamais
(`app/core/ohlcv_gaps.py:52`) :

```python
if delta_secs <= simple_allowed and cal is None:
    continue
```

`cal` n'est jamais `None` en production — `calendar_for_symbol` renvoie
`ALWAYS_OPEN` pour le crypto, et les quatre appels de `candle_store.py`
passent explicitement un calendrier (détail dans `09-DONNEES.md`, DAT-03).

Chaque barre paie donc : `_calendar_closed_span` (jusqu'à 7 sondes `is_open`),
`session_end`, `next_open`, `max_gap_seconds` — pour un résultat acquis
d'avance, puisque `allowed` n'est calculé que par `max(...)` et ne peut donc
que croître au-dessus d'un écart déjà conforme.

### Impact sur le parc

| Grandeur | Valeur |
|---|---:|
| Barres actions dans `data/ohlcv` | 1 848 671 |
| Coût d'un scan complet au tarif actuel (212 µs/barre) | **392 s** |
| Coût au tarif du chemin simple (3,2 µs/barre) | 5,9 s |
| **Surcoût du chemin calendaire** | **386 s (6 min 26)** |

Ce coût n'est pas payé une fois. `detect_ohlcv_gaps` est appelé depuis
`CandleStore._warn_write_gaps`, lui-même appelé par `_save`
(`app/core/candle_store.py:1019`) avec `log_gaps=True` par défaut :
**chaque sauvegarde de bougies rejoue un scan sur tout l'historique du
fichier**, pas seulement sur les barres ajoutées. Sur BNP.PA 1h, une bougie
incrémentale coûte 1,5 s de détection de trous.

### Vérification

**CONFIRMÉ** — A/B exécuté sur les parquet réels, avec la version d'avant le
delta chargée en parallèle par `importlib`. Comptage des barres du parc par
`pl.scan_parquet(...).select(pl.len())`.

### Correctif proposé

Une ligne — retirer la garde morte :

```python
if delta_secs <= simple_allowed:
    continue
```

Cela ramène le coût des écarts normaux (la quasi-totalité des barres) au tarif
du chemin simple, sans changer aucun résultat : un écart déjà sous
`simple_allowed` ne peut pas devenir un trou, `allowed` ne faisant que croître.

Gain attendu sur le parc actions : **de 392 s à quelques secondes**.

**Effort** : 5 min correctif + 30 min pour mesurer le gain.

### Délégation IA

> Dans `app/core/ohlcv_gaps.py::detect_ohlcv_gaps`, la garde
> `if delta_secs <= simple_allowed and cal is None: continue` ne se déclenche
> jamais : `cal` n'est jamais `None` (`calendar_for_symbol` renvoie
> `ALWAYS_OPEN` pour le crypto, et `candle_store.py` passe toujours un
> calendrier). Retirer la condition `and cal is None`. C'est correct : `allowed`
> n'est construit que par des `max(...)`, donc un écart déjà inférieur à
> `simple_allowed` ne peut pas devenir un trou.
> Mesurer avant/après sur `data/ohlcv/BNP.PA/1h.parquet` et
> `data/ohlcv/BTC_USDC/1h.parquet`, et vérifier que la LISTE des trous détectés
> est identique. Livrer ce correctif avec DAT-01 (même fonction).

---

## PERF-02 — Chaque sauvegarde de bougies rescanne tout l'historique (P2, CONFIRMÉ)

**Fichier** : `app/core/candle_store.py:1008-1026`.

```python
def _save(self, path, df, *, log_gaps: bool = True) -> None:
    …
    self._warn_write_gaps(path, df, log=log_gaps)
```

`_warn_write_gaps` appelle `detect_ohlcv_gaps` sur le DataFrame **complet**,
puis réécrit `<tf>.gaps.json`. Le coût est donc proportionnel à la taille de
l'historique, alors que l'écriture est incrémentale.

**Scénario d'échec** — pas de sortie fausse : un ralentissement. En collecte
live sur 128 symboles, chaque nouvelle bougie déclenche un scan complet. Avec
les chiffres de PERF-01 (212 µs/barre en actions), un symbole à 8 000 barres
paie 1,7 s par bougie sauvegardée.

**Vérification** — **CONFIRMÉ** pour la structure d'appel (lecture de
`candle_store.py:1008-1026` et de ses appelants) et pour le coût unitaire
(mesure PERF-01). **Non reproduit de bout en bout** : je n'ai pas instrumenté
une session de collecte live complète.

Le correctif PERF-01 réduit ce constat d'environ deux ordres de grandeur et
peut suffire. Si ce n'est pas le cas, la piste est de ne rescanner que la
fenêtre modifiée, ou de mémoïser le résultat sur le hash `(nb_barres,
dernier_temps)`.

**Effort** : 5 min si PERF-01 suffit ; 3 h pour un scan incrémental.

---

## PERF-03 — Le pool de calcul ne partage plus d'`Event` (CONFIRMÉ — amélioration)

**Fichiers** : `app/engine/compute_pool.py:69`, `app/engine/compute_jobs.py`,
commit `54e8a8c`.

Le `ProcessPoolExecutor` était instancié avec un `Event` de synchronisation
partagé. Sous Windows — la plateforme de développement ici — le démarrage
`spawn` exige que tout argument soit picklable, ce que `threading.Event` n'est
pas. La suppression retire une source de plantage à l'initialisation du pool.

`max_workers` est par ailleurs borné par `len(jobs)`
(`compute_pool.py:102`) : plus de processus créés pour rien.

Aucun défaut relevé.

---

## Ce qui a été mesuré sans rien trouver

- **Suite de tests** — `pytest -q` complet : **241 s** pour 2 142 tests, soit
  113 ms par test en moyenne. Pas de test pathologiquement lent qui
  ressortirait du lot.
- **Suite frontend** — `vitest run` : 17,6 s pour 190 tests / 20 fichiers.
  Le poste dominant est l'environnement jsdom (141 s cumulés, parallélisés),
  pas les tests eux-mêmes (4,2 s).
- **`tsc --noEmit`** — passe sans erreur sur les 34 115 lignes TS/TSX, y
  compris les 1 254 lignes de types nouvellement éclatées.
- **Lecture des parquet** — `pl.read_parquet` sur `BTC_USDC/1h` (52 364 barres,
  6,1 Mo) n'apparaît pas dans les temps mesurés : le coût observé est bien
  celui de la détection de trous, pas celui des entrées-sorties.
