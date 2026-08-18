# 12 — Données : OHLCV, feature store, intégrité

Périmètre : `app/core/candle_store.py` (831 l), `app/core/feature_store.py` (526 l),
`app/core/ohlcv_gaps.py`, `app/core/market_calendar.py`, `app/core/providers.py`,
`app/core/provider_router.py`, `app/core/yfinance_provider.py`, `app/core/derivatives.py`,
`app/live/ohlcv_cache.py`, et les données réelles de `data/` (831 Mo).

**Inventaire réel** (`C:\…\bot-crypto\data`) :

| Élément | Volume |
|---|---|
| Total | **831 Mo** |
| `ohlcv/` | ~600 répertoires — 4 paires crypto (BTC, ETH, SOL, XRP `_USDC`) et ~590 actions européennes (`.PA`, `.AS`, `.F`, `.DE`, `.L`) |
| Timeframes par paire crypto | `1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `1d` — 8 fichiers Parquet |
| `features/` | un seul symbole peuplé : `BTC_USDC` |
| `derivatives/`, `universe/` | présents |
| `backtest_history.json`, `oos_tracker.json` | **3 octets chacun** (`{}`) — purgés avant merge (`8edf54a`) |

**Jugement d'ensemble.** La couche données est la mieux construite du dépôt sur le plan de
la robustesse : écriture Parquet **atomique**, verrous par fichier, détection de trous
consciente du calendrier de marché, suppression de la bougie en formation. Les points
d'attention portent sur la **couverture** (features quasi vides, actions non backfillées)
plutôt que sur la correction du code.

---

## DATA-01 — Le feature store n'est peuplé que pour un symbole sur ~600

**Sévérité P2 · CONFIRMÉ (inventaire)**

```
data/features/
└── BTC_USDC/          ← seul répertoire
```

`app/core/feature_store.py` (526 lignes) implémente un cache de features avec empreinte
OHLCV (`_with_ohlcv_hash`, vérification de recouvrement `overlap_mask`) — mécanique
correcte et testée (`test_feature_store.py`, `test_feature_store_integration.py`).

Mais en pratique il ne sert qu'à BTC. Toute stratégie ML sur ETH, SOL, XRP ou sur les
~590 actions recalcule ses features à chaque backtest et à chaque essai d'optimisation.

Deux conséquences :

1. **Performance** — c'est le poste de calcul dominant d'une campagne d'optimisation ML
   multi-symboles, et il est intégralement répété.
2. **Reproductibilité** — un cache dont la couverture dépend de ce qui a été lancé par le
   passé rend deux exécutions du même job non comparables en durée, et masque les
   régressions de performance.

**À trancher, pas à corriger aveuglément** : soit le feature store est un cache
opportuniste et il faudrait le dire (et ne pas s'y fier pour les mesures), soit c'est un
artefact à construire, et un job de pré-calcul par symbole/TF s'impose.

---

## DATA-02 — 590 répertoires d'actions pour un moteur qui ne les exécute pas

**Sévérité P2 · Observation, à arbitrer**

`data/ohlcv/` contient les répertoires de ~590 actions européennes (SBF 120 et
au-delà : `AC.PA`, `BNP.PA`, `APAM.AS`, `ARRD.F`…). Le code correspondant existe et est
soigné : calendrier XPAR, taxe de transaction française, `lot_size`, `tick_size`,
`fractional=False`, `close_at_session_end`.

Mais `Venue.can_execute = False` pour ces venues (G2) : le bot **calcule** le trade et
**notifie** au lieu de passer un ordre — G3 n'est pas livré
(`app/core/yfinance_provider.py:602`).

Ce n'est pas un défaut, c'est un état de projet. Le signaler parce qu'il a un coût mesuré :
600 répertoires de données à maintenir, à backfiller (`/api/data/backfill-equities`) et à
sauvegarder, pour une fonctionnalité qui ne peut pas encore trader. Le rapport
volume/valeur mérite une décision explicite — pas une accumulation par défaut.

---

## DATA-03 — La détection de trous parcourt les timestamps en boucle Python

**Sévérité P3 · CONFIRMÉ (lecture)**

`app/core/ohlcv_gaps.py` :

```python
times = df["time"]
gaps = []
for i in range(1, len(times)):
    delta = times[i] - times[i - 1]
```

Boucle Python sur une colonne Polars, avec indexation élément par élément — le motif le
plus coûteux possible sur un DataFrame. Sur une série 1 m d'un an (~525 000 barres), c'est
un demi-million d'accès indexés là où `df["time"].diff()` fait le calcul en une passe
vectorisée.

Ce n'est un P3 que parce que la fonction est appelée à la lecture d'un fichier, pas dans
une boucle de backtest. Elle l'est cependant depuis `/api/backtest` et `/api/replay`
(`helpers.detect_ohlcv_gaps`), donc sur le chemin de chaque requête d'interface.

**Correction** : `df["time"].diff().dt.total_seconds()` puis filtrage vectorisé. ~10 lignes.

---

## DATA-04 — `data/` versionné, avec 10 629 lignes purgées dans la fenêtre

**Sévérité P3 · Observation**

Le diff des 3 jours montre `data` : **1 fichier, +1 / −10 629**, correspondant au commit
`8edf54a` *« chore(data) : purge oos_tracker et backtest_history pre-merge »*.

Purger avant merge est le bon réflexe. Reste que ces fichiers d'état d'exécution
(`oos_tracker.json`, `backtest_history.json`, `heartbeat.json`,
`smc_signals_recent.json` — 54 Ko) sont **suivis par git** et grossissent à chaque
exécution. Le dépôt porte aussi `optimizer_changelog.json` (194 Ko) à la racine, et le
commit `78581bf` de la fenêtre est un *« revert : optimizer_changelog.json — état de
production sali par mes essais »* : le problème s'est déjà manifesté.

**Correction** : sortir les fichiers d'état d'exécution du suivi git (`.gitignore` +
`git rm --cached`), en conservant un fichier d'exemple. Cela supprime une classe entière
de conflits et de pollutions accidentelles.

---

## Ce qui a été vérifié et tenu

- **Écriture Parquet atomique** — `candle_store.py:764-779` : écriture dans un `.tmp` puis
  `os.replace` (renommage atomique sur le même système de fichiers), avec nettoyage du
  temporaire en cas d'échec. Le commentaire donne la raison exacte : *« un lecteur
  concurrent ne voit jamais un fichier partiel »*. C'est la bonne construction, et elle
  est rarement faite.
- **Verrous par fichier** — `_get_file_lock(path)` (`candle_store.py:168`) : deux écritures
  concurrentes sur des symboles différents ne se bloquent pas mutuellement.
- **Bougie en formation supprimée** — `drop_forming_candle` (`candle_store.py:129`). C'est
  la fuite la plus banale d'un bot de trading (utiliser la bougie courante incomplète), et
  elle est traitée à la source, dans le magasin, pas dans chaque consommateur.
- **Détection de trous consciente du calendrier** — `ohlcv_gaps.py` : le seuil `1,5 × Δ`
  est élargi à `calendar.max_gap_seconds` pour les marchés à séance. Un week-end XPAR
  n'est plus un « trou ». L'heuristique par suffixe (`.PA`, `.AS`, `.F`, `.DE`, `.L` →
  XPAR, sinon 24/7) est simple mais explicite.
- **Lecture par plage** — `load_range`, `fetch_range` (`candle_store.py:325-368`) : le
  correctif `A-03` a supprimé le chargement complet pour une fenêtre partielle.
- **Validation des barres** — `_valid_bars` filtre à la lecture ; `_warn_write_gaps`
  signale les trous au moment de l'écriture, donc au plus près de la source.
- **`OHLCVCache` verrouillé** (`app/live/ohlcv_cache.py`, `RLock`, 10 sections critiques).
- **Empreinte OHLCV dans le feature store** — le cache est invalidé quand les bougies
  sous-jacentes changent, pas seulement quand la plage change.

---

## Récapitulatif

| ID | Sévérité | Preuve | Constat | Effort |
|---|---|---|---|---|
| DATA-01 | P2 | CONFIRMÉ | Feature store peuplé pour 1 symbole sur ~600 | à arbitrer |
| DATA-02 | P2 | CONFIRMÉ | 590 répertoires d'actions non exécutables (G3 absent) | décision |
| DATA-03 | P3 | CONFIRMÉ | Détection de trous en boucle Python | 30 min |
| DATA-04 | P3 | CONFIRMÉ | Fichiers d'état d'exécution suivis par git | 1 h |
