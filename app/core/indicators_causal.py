"""Réutilisation causale de séries pré-calculées (fenêtre ↔ df complet).

Extrait de ``indicators.py`` (découpage V13) : relit la valeur d'une série
causale calculée une fois sur le df complet du backtest à la position de la
fenêtre courante (SuperTrend, MACD, EMA) — évite le recalcul O(n) par barre.
"""
import logging

import polars as pl

from app.core.indicators_core import macd, supertrend

_log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  Réutilisation des pré-calculs causaux (accélération backtest/optimiseur)
# ══════════════════════════════════════════════════════════════════════════════
#  SuperTrend et MACD sont CAUSAUX : leur valeur à la barre i ne dépend que des
#  barres [0..i]. Donc ``indicator(full_df)[i] == indicator(full_df[:i+1])[-1]``.
#  En backtest la stratégie est appelée sur des fenêtres croissantes ``df[:i+1]`` ;
#  recalculer l'indicateur sur toute la fenêtre à chaque barre coûte O(n) → O(n²)
#  sur l'ensemble du backtest (la boucle Python de SuperTrend domine le profil :
#  ~95 % du temps de supertrend_macd). Ces helpers calculent la série COMPLÈTE une
#  seule fois — mémoïsée par jeu de paramètres dans un cache détenu par l'appelant
#  (l'instance stratégie) — puis indexent la barre courante en O(1).
#
#  Le cache ne retient qu'UN jeu de paramètres : indispensable pour l'optimiseur
#  qui fait varier (period, mult) / (fast, slow, signal) entre trials. Comme chaque
#  trial utilise une instance fraîche, il n'y a aucune contamination inter-trial.

def _causal_prefix_index(window: pl.DataFrame, full_df: pl.DataFrame):
    """Indice de la dernière barre de ``window`` dans ``full_df`` si ``window`` en
    est un préfixe causal (hauteur n ⇒ barres 0..n-1), sinon ``None``.

    Vérification O(1) via le dernier ``close`` : les fenêtres de backtest sont des
    slices ``df[:i+1]`` du df complet, donc le close de la dernière barre suffit à
    confirmer l'alignement. En live (pas de ``full_df`` cohérent) → ``None`` →
    calcul direct sur la fenêtre.
    """
    if full_df is None or window is None:
        return None
    n = window.height
    if n < 2 or n > full_df.height:
        return None
    try:
        if float(window["close"][-1]) == float(full_df["close"][n - 1]):
            return n - 1
    except Exception:
        return None
    return None


def close_prefix_index(close: pl.Series, full_df: pl.DataFrame):
    """Variante de :func:`_causal_prefix_index` pour un appelant qui ne reçoit
    que la série ``close`` (et non la fenêtre entière) — même vérification O(1)
    par la dernière valeur."""
    if full_df is None or close is None:
        return None
    n = len(close)
    if n < 2 or n > full_df.height:
        return None
    try:
        if float(close[-1]) == float(full_df["close"][n - 1]):
            return n - 1
    except Exception:
        return None
    return None


def bb_squeeze_series(full_df: pl.DataFrame, lookback: int = 15,
                      bb_period: int = 20, quantile: float = 0.30):
    """Série de décisions ``bb_squeeze`` (bool) pour CHAQUE barre de ``full_df``.

    ``out[i]`` reproduit exactement ``bb_squeeze(full_df["close"][:i+1],
    lookback, bb_period, quantile)``. La largeur de bande est une fonction
    rolling causale du close, et la comparaison au quantile ne porte que sur les
    ``lookback`` largeurs qui PRÉCÈDENT la barre : le tout se ramène à un
    ``rolling_quantile`` décalé d'un cran.

    ``interpolation="nearest"`` est le défaut de ``Series.quantile`` — l'écrire
    explicitement plutôt que d'en dépendre : les deux appels doivent
    interpoler pareil, sinon la série diverge du calcul barre à barre sur les
    seules barres où le quantile tombe entre deux échantillons.
    """
    import numpy as np

    n = full_df.height
    out = np.zeros(n, dtype=bool)
    lookback = int(lookback)
    bb_period = int(bb_period)
    if n == 0 or lookback < 5:
        # ``len(past) >= 5`` est infaisable sous 5 barres d'historique de
        # largeur : la fonction d'origine retourne False partout.
        return out
    try:
        close = full_df["close"]
        sma = close.rolling_mean(bb_period)
        std = close.rolling_std(bb_period)
        width = 4 * std / sma.clip(lower_bound=1e-9)
        seuil = width.rolling_quantile(
            quantile=quantile, window_size=lookback, interpolation="nearest",
        ).shift(1)
        w = width.to_numpy()
        s = seuil.to_numpy()
    except Exception:
        return None

    valide = np.zeros(n, dtype=bool)
    debut = bb_period + lookback - 1          # garde ``len(close) < bb_period + lookback``
    if debut < n:
        valide[debut:] = True
    fini = np.isfinite(w) & np.isfinite(s)
    out[valide & fini] = (w <= s)[valide & fini]
    return out


def htf_trend_ema_series(full_df: pl.DataFrame, ema_period: int = 50,
                         mult: int = 4):
    """Série ``htf_trend`` (+1/−1/0) pour CHAQUE barre de ``full_df``.

    ``out[i]`` reproduit **exactement** ``htf_trend(None, df_ltf=full_df[:i+1],
    ema_period=…, mult=…)``. L'égalité n'est pas une approximation, elle
    découle de trois propriétés du repli barre à barre :

    1. les buckets HTF sont des bornes d'horloge (``epoch // htf_sec``) : ceux
       d'un préfixe sont ceux du df complet, tronqués ;
    2. ``htf_trend`` ne lit que les buckets ENTIÈREMENT clôturés
       (``htf_df.head(idx[-1] + 1)``), dont l'agrégation OHLC est donc
       identique dans le préfixe et dans le df complet — le bucket partiel de
       fin, seul à différer, est justement celui qu'il exclut ;
    3. ``ewm_mean(adjust=False)`` est une récurrence : sa valeur à l'indice j
       ne dépend que des indices ≤ j, donc l'EMA d'un préfixe est le préfixe
       de l'EMA.

    Retourne ``None`` — l'appelant retombe alors sur le calcul barre à barre —
    quand l'exactitude n'est pas démontrable : ``ltf_sec`` est déduit de la
    MÉDIANE des 64 derniers écarts temporels, qui peut varier d'une barre à
    l'autre si la grille est irrégulière (bougies manquantes, séances
    boursières). Sur une grille régulière — le cas des séries OHLCV crypto de
    ``CandleStore`` — cette médiane est constante par construction.
    """
    import numpy as np

    n = full_df.height
    if n < 40 or "time" not in full_df.columns:
        return None
    try:
        epoch = full_df["time"].dt.epoch(time_unit="s").to_numpy().astype("int64")
    except Exception:
        return None
    d = np.diff(epoch)
    if d.size == 0 or d[0] <= 0 or not bool(np.all(d == d[0])):
        return None                     # grille irrégulière → repli exact

    from app.core.smc_sessions import _htf_buckets
    htf_df, idx, htf_sec, _ = _htf_buckets(full_df, None, mult)
    out = np.zeros(n, dtype=np.int8)
    if htf_df is None or htf_sec <= 0:
        # Moins de 12 buckets sur TOUT le df ⇒ a fortiori sur chaque préfixe.
        return out

    hc = htf_df["close"]
    ema_line = hc.ewm_mean(span=int(ema_period), adjust=False).to_numpy()
    hcv = hc.to_numpy()
    nb = len(hcv)
    trend_b = np.zeros(nb, dtype=np.int8)
    if nb >= 4:
        above = hcv[3:] > ema_line[3:]
        slope = ema_line[3:] > ema_line[:-3]
        trend_b[3:] = np.where(above & slope, 1,
                               np.where(~above & ~slope, -1, 0)).astype(np.int8)

    # Index du bucket CONTENANT la barre i : c'est lui qui donne le nombre de
    # buckets vus par le préfixe, donc la garde « moins de 12 buckets » de
    # ``_htf_buckets`` (qui compte aussi le bucket en cours, pas seulement les
    # clôturés).
    bucket = epoch // int(htf_sec)
    bidx = np.cumsum(np.diff(bucket, prepend=bucket[0] - 1) != 0) - 1

    j = np.asarray(idx)
    ok = ((np.arange(n) >= 39)              # _htf_buckets refuse n < 40
          & (bidx + 1 >= 12)                # … et moins de 12 buckets
          & (j >= 0)                        # aucun bucket encore clôturé
          & (j + 1 >= int(ema_period) + 3))  # htf_trend : len(df_htf) trop court
    out[ok] = trend_b[np.clip(j, 0, nb - 1)[ok]]
    return out


def supertrend_last(window: pl.DataFrame, period: int, mult: float,
                    full_df: pl.DataFrame | None = None, cache: dict | None = None):
    """``(last_dir, prev_dir, last_line)`` pour la dernière barre de ``window``.

    Réutilise la série SuperTrend pré-calculée de ``full_df`` (O(1)/barre) quand
    ``window`` en est un préfixe causal ; sinon calcule sur ``window`` (live).
    """
    if cache is not None and full_df is not None:
        idx = _causal_prefix_index(window, full_df)
        if idx is not None:
            key = (id(full_df), full_df.height, int(period), float(mult))
            hit = cache.get(key)
            if hit is None:
                cache.clear()  # un seul jeu de params à la fois (optimiseur)
                hit = supertrend(full_df, int(period), float(mult))
                cache[key] = hit
            d, line = hit
            return int(d[idx]), int(d[idx - 1]), float(line[idx])
    d, line = supertrend(window, int(period), float(mult))
    return int(d[-1]), int(d[-2]), float(line[-1])


def macd_hist_last3(window: pl.DataFrame, fast: int, slow: int, signal: int,
                    full_df: pl.DataFrame | None = None, cache: dict | None = None):
    """``(h[-1], h[-2], h[-3])`` de l'histogramme MACD à la dernière barre.

    Même réutilisation causale que :func:`supertrend_last`. Pour les paramètres
    par défaut (12/26/9) la colonne pré-calculée ``_pre_macd_hist`` reste la voie
    la plus rapide ; ce helper sert quand l'optimiseur fait varier fast/slow/signal.
    """
    if cache is not None and full_df is not None:
        idx = _causal_prefix_index(window, full_df)
        if idx is not None and idx >= 2:
            key = (id(full_df), full_df.height, int(fast), int(slow), int(signal))
            h = cache.get(key)
            if h is None:
                cache.clear()
                _, _, h = macd(full_df["close"], int(fast), int(slow), int(signal))
                cache[key] = h
            return float(h[idx]), float(h[idx - 1]), float(h[idx - 2])
    _, _, h = macd(window["close"], int(fast), int(slow), int(signal))
    return float(h[-1]), float(h[-2]), float(h[-3])


def ema_window(window: pl.DataFrame, span: int,
               full_df: pl.DataFrame | None = None, cache: dict | None = None) -> pl.Series:
    """Série EMA(span) du ``close`` alignée sur ``window``.

    L'EMA est causale (récurrence depuis la barre 0). Quand ``window`` est un
    préfixe causal de ``full_df``, on calcule l'EMA complète UNE fois par span
    (mémoïsée) puis on renvoie la tranche ``[:n]`` — l'appelant continue d'indexer
    ``[-1]``, ``[-4]``, ``[-1-k]`` exactement comme avant. Évite un recalcul O(n)
    de ``ewm_mean`` par barre (O(n²) sur le backtest) quand le span n'est pas
    pré-calculé (ex: ema_fast 13/21/34, ema_slow 80 hors colonnes _pre_ema*).

    Le cache retient plusieurs spans simultanément (ema_fast/slow/trend d'un même
    trial coexistent) ; il est borné par sécurité. En live (pas de ``full_df``)
    l'EMA est calculée directement sur ``window``.
    """
    if cache is not None and full_df is not None:
        idx = _causal_prefix_index(window, full_df)
        if idx is not None:
            key = (id(full_df), full_df.height, int(span))
            s = cache.get(key)
            if s is None:
                if len(cache) > 16:  # garde-fou mémoire (jamais atteint en backtest)
                    cache.clear()
                s = full_df["close"].ewm_mean(span=int(span), adjust=False)
                cache[key] = s
            return s[: idx + 1]
    return window["close"].ewm_mean(span=int(span), adjust=False)


