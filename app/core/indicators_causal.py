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


def supertrend_last(window: pl.DataFrame, period: int, mult: float,
                    full_df: pl.DataFrame = None, cache: dict = None):
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
                    full_df: pl.DataFrame = None, cache: dict = None):
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
               full_df: pl.DataFrame = None, cache: dict = None) -> pl.Series:
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


