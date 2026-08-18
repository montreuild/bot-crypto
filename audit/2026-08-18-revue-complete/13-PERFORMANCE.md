# 13 — Performance

Toutes les mesures de ce rapport ont été **exécutées** sur les données réelles du dépôt
(`data/ohlcv/BTC_USDC`), pas estimées. Machine : Windows 11, Python 3.14.6, venv du projet.

---

## Mesures de référence

### Inventaire des séries utilisées

| Symbole | TF | Barres | Période |
|---|---|---:|---|
| BTC_USDC | 1h | 51 909 | 2020-03-17 → 2026-08-07 |
| BTC_USDC | 4h | 15 769 | 2018-12-15 → 2026-08-07 |
| BTC_USDC | 1d | 2 630 | 2018-12-15 → 2026-08-07 |
| ETH_USDC | 4h | 15 769 | 2018-12-15 → 2026-08-07 |

### Pré-calcul vectorisé — excellent

```
precompute_df, 51 909 barres : 34-38 ms   (~1,4 M barres/s)   28 colonnes _pre_*
2e appel (cache mémoïsé)     : 0,05-0,09 ms   → gain ×400 à ×700
```

Rien à redire : 28 indicateurs sur 52 000 barres en 35 ms, et un cache qui fonctionne.

### Débit du backtest — un écart de 45× entre deux stratégies

```
trend                4h   15 769 barres : 272,70 s  (   58 barres/s)  13 trades
volatility_squeeze   4h   15 769 barres :   5,98 s  ( 2 637 barres/s)  10 trades
```

**Mêmes données, même moteur, même machine. Facteur 45.**

---

## PERF-01 — Cinq horodatages irréguliers sur 15 768 désactivent une optimisation ×120 sur tout le jeu de données

**Sévérité P1 · CONFIRMÉ (profilage + diagnostic ciblé)**

### Profil

`cProfile` sur `trend`, 2 500 barres, 12,68 s au total :

| Fonction | Temps cumulé | Part |
|---|---:|---:|
| `Backtester.run` | 12,68 s | 100 % |
| `engine.passing_signals` | 12,50 s | 99 % |
| `trend.score` | 12,46 s | 98 % |
| **`indicators_market.htf_trend`** | **11,53 s** | **91 %** |
| **`smc_sessions._htf_buckets`** | **10,82 s** | **85 %** |

`_htf_buckets` est appelé **2 280 fois** — une fois par barre — pour un total de
**2 334 264** réductions numpy (`ufunc.reduce`) et 777 322 appels chacun de `min`, `max`
et `sum`. C'est la signature exacte d'un O(n²).

### Mécanisme

Le commit `bfc330e` de la fenêtre auditée s'intitule *« perf(backtest) : htf_trend coûtait
O(n²) — 8 stratégies, jusqu'à ×120 »*. La mémoïsation qu'il introduit
(`indicators_market.py:76-97`) est correctement écrite :

```python
pos = _causal_prefix_index(df_ltf, full_df)
if pos is not None:
    key = (id(full_df), full_df.height, int(ema_period), int(mult))
    if key in cache:
        arr = cache[key]
    else:
        arr = htf_trend_ema_series(full_df, ema_period, mult)
        cache[key] = arr           # ← None est mémoïsé aussi
    if arr is not None:
        return int(arr[pos])       # ← chemin rapide
from app.core.smc_sessions import _htf_buckets
htf_df, idx, _, _ = _htf_buckets(df_ltf, None, mult)   # ← repli, NON mémoïsé
```

Diagnostic exécuté sur les données réelles :

```
_causal_prefix_index(préfixe, complet) = 3999          ← le préfixe causal est reconnu
htf_trend_ema_series(données réelles)  = None          ← la série vectorisée est REFUSÉE
htf_trend_ema_series(grille régulière) = array(4000)   ← disponible sur grille parfaite
```

`indicators_causal.py:153` porte la condition en clair :

```python
return None                     # grille irrégulière → repli exact
```

Et l'irrégularité des données réelles :

```
pas != 4 h : 5 sur 15 768   (0,03 %)
pas observés : 14400 s ×15 763 · 28800 s ×3 · 43200 s ×1 · 14 184 000 s ×1
```

Cinq intervalles anormaux : trois de 8 h, un de 12 h, et **un de 14 184 000 s = 164 jours**
(un trou d'historique). C'est tout ce qu'il faut.

### Ce qui rend le défaut invisible

Trois choses se combinent, et c'est ce qui explique qu'il ait survécu à un correctif dédié :

1. **La mémoïsation protège la vérification, pas le calcul.** Le `None` est mis en cache
   pour ne pas repayer l'analyse de la grille — le commentaire le dit explicitement. Mais
   le **repli**, qui est la partie coûteuse, est recalculé intégralement à chaque barre.
   Le cache économise le test et laisse passer les 10,8 s.
2. **Les tests utilisent des grilles synthétiques régulières.** J'ai reproduit : sur une
   grille parfaite de 4 000 barres, `htf_trend_ema_series` rend bien un tableau et le
   chemin rapide s'active. Un test de non-régression de performance construit sur des
   données générées valide donc une optimisation qui ne s'appliquera jamais en production.
3. **Aucune trace.** Le repli n'émet ni log ni compteur. Un backtest 45× plus lent que
   prévu ne dit pas pourquoi.

### Portée

**Huit stratégies** appellent `htf_trend` :

```
breakout · breakout_filtreHor · fear_momentum · gemini_trend_follow
multi_tf_sr · pullback_trend · supertrend_macd · trend
```

Mesure de confirmation sur trois d'entre elles, 4 000 barres :

```
trend            :  20,33 s  (197 b/s)
pullback_trend   :  20,44 s  (196 b/s)
fear_momentum    :  20,72 s  (193 b/s)
```

Toutes les trois au même palier — c'est bien `htf_trend` qui domine, pas la logique propre
de chaque stratégie.

### Coût réel, chiffré

`config/lifecycle.yaml:45` fixe `max_trials: 400`. Chaque essai lance **deux** backtests
(IS puis OOS, `optimizer_search.py:315-316`), soit environ une fenêtre complète par essai.

| Scénario | Durée pour 400 essais, 1 stratégie × 1 symbole × 1 TF |
|---|---|
| **Aujourd'hui** (repli O(n²)) | 400 × 273 s ≈ **30 heures** |
| Avec le chemin rapide | 400 × 6 s ≈ **40 minutes** |

Sur une campagne multi-symboles (4 paires) et multi-TF (3), l'écart passe de la demi-heure
à plusieurs semaines. C'est le facteur limitant de tout le pipeline d'optimisation, et il
tient à cinq horodatages.

### Corrections

Trois, par ordre de rendement :

1. **Mémoïser le repli** — c'est la correction qui vaut, et elle est petite. `_htf_buckets`
   ne dépend que de `(full_df, mult)` : le calculer une fois sur `full_df` et indexer en
   O(1) par `pos`, exactement comme le chemin rapide. Cela rend l'optimisation
   **indépendante de la régularité de la grille**.
2. **Nettoyer les données** — un trou de 164 jours dans une série 4 h est un défaut en soi
   (cf. `12-DONNEES.md`). `detect_ohlcv_gaps` sait déjà les trouver ; rien ne les corrige.
   Utile, mais insuffisant : une seule bougie manquante suffirait à re-désactiver le chemin
   rapide.
3. **Rendre le repli visible** — un `log_throttled` au premier repli, et un compteur dans
   `diagnostics`. Sans quoi le prochain défaut de ce type prendra le même temps à trouver.

**Effort** : ~30 lignes pour le point 1, qui suffit à récupérer le facteur 45.

---

## PERF-02 — Le même motif de repli existe pour `bb_squeeze_series`

**Sévérité P1 · PLAUSIBLE (structure identique, non mesuré)**

`app/core/indicators_causal.py` expose deux séries vectorisées causales avec la même
structure et les mêmes conditions de refus :

```
ligne  70 : def bb_squeeze_series(full_df, lookback=15, …)   → 4 chemins `return None`
ligne 117 : def htf_trend_ema_series(full_df, ema_period=50, mult=4, …)  → 3 chemins `return None`
```

Le commit `4830ef9` (*« perf(backtest) : bb_squeeze en série causale — breakout ×80 au
total »*) est le jumeau de `bfc330e`. Si son repli est également non mémoïsé — la structure
du code le suggère fortement — le gain ×80 annoncé pour `breakout` est inerte dans les
mêmes conditions.

Je ne l'ai pas mesuré : `breakout` combine `htf_trend` **et** `bb_squeeze`, donc isoler la
contribution de chacun demande un profilage dédié. À faire dans le même passage que
PERF-01, puisque la correction est la même.

---

## PERF-03 — Aucun test ne garde le débit du backtest

**Sévérité P2 · CONFIRMÉ**

`requirements-dev.txt` fournit `pytest-benchmark` (le binaire est présent dans le venv), et
la CI a un job `slow.yml`. Aucun des deux ne mesure le débit du backtest.

Or les commits `bfc330e`, `4830ef9`, `229cb4c`, `a68e364`, `b791e40` de la seule fenêtre
auditée annoncent des gains de ×3 à ×120. Ces gains ne sont vérifiés par rien : PERF-01
démontre qu'un d'entre eux est inerte sur les données réelles depuis son introduction.

**Correction** : un test `@pytest.mark.slow` qui lance un backtest de référence sur une
série réelle tronquée (pas synthétique — c'est tout l'enseignement de PERF-01) et échoue
si le débit tombe sous un seuil. ~30 lignes, et il aurait attrapé PERF-01.

---

## PERF-04 — Un unique `precompute_df` mémoïsé, une seule bonne clé

**Sévérité — rien à corriger, à conserver**

Point positif à protéger : la clé de cache
`(hauteur, largeur, borne basse, borne haute, dernier close, convention de lissage)`
(`indicators_precompute.py:109-110`) est assez discriminante pour être sûre et assez bon
marché pour être gratuite. Le gain mesuré est de ×400 à ×700 sur le second appel, ce qui
compte dans une boucle d'optimisation où chaque essai repasse sur la même fenêtre.

Le garde-fou mémoire (`_PRECOMPUTE_MAXSIZE`, éviction LRU) est présent.

---

## Ce qui a été vérifié et tenu

- **`precompute_df`** : 1,4 M barres/s, 28 indicateurs, cache ×400-700. Excellent.
- **`compute_pool`** — `ProcessPoolExecutor` en `spawn` pour sortir le calcul lourd du
  thread API, avec des workers `pickleables` isolés dans `compute_jobs.py` (A-02).
- **`ml/threads.py`** — nouveau module de la fenêtre, dimensionne les threads LightGBM
  selon le contexte (×3,1 mesuré en entraînement autonome). Bon endroit pour ce réglage.
- **Prédiction par lot** (`229cb4c`) sur v4/v5/`ml_dynamic_threshold`, ADX pré-calculé.
- **Slice zéro-copie** — `ctx.window = df.slice(0, i + 1)` (`backtest.py:488`) et non
  `df[:i+1]`, avec le commentaire qui le dit (P-05).
- **Peu de boucles Python sur DataFrame** — 10 occurrences de
  `for i in range(len(…))` / `.to_list()` / `iter_rows` dans `app/strategies` et
  `app/core` confondus. Le code est majoritairement vectorisé ; les exceptions sont
  bornées (`market_structure` ne regarde que les `n_pivots × window` dernières barres,
  donc O(1) — vérifié, ce n'est pas un point chaud).
- **Annulation coopérative** — `_cancel_event` testé toutes les 100 barres, avec log de
  progression et ETA toutes les 500.

---

## Récapitulatif

| ID | Sévérité | Preuve | Constat | Effort |
|---|---|---|---|---|
| PERF-01 | **P1** | CONFIRMÉ | Repli `htf_trend` non mémoïsé : ×45 sur 8 stratégies, 30 h par campagne | 30 lignes |
| PERF-02 | **P1** | PLAUSIBLE | Même motif pour `bb_squeeze_series` (×80 annoncé) | avec PERF-01 |
| PERF-03 | P2 | CONFIRMÉ | Aucun garde de débit, alors que 5 gains ont été annoncés | 1 h |
