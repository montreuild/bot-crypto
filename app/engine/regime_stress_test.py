"""S3-09 : Stress tests par régimes de marché.

L'audit V2 notait que le backtest expose `max_drawdown` global mais ne
détaille pas la performance par régime (bull / bear / range). Ce module
découpe un historique OHLCV en sous-périodes par régime et calcule les
métriques pour chaque segment.

Objectif : identifier les stratégies qui :
    - Performe en bull mais s'effondre en bear (edge asymétrique)
    - Performe en range mais perd en tendance (et inversement)
    - Ont un edge seulement sur un régime spécifique (fragile)

Détection des régimes (3 méthodes configurables) :
    - `trend` : EMA200 slope + prix au-dessus/dessous (bull/bear/range)
    - `volatility` : ATR rolling percentile (high/medium/low vol)
    - `combined` : intersection (bull-trending / bear-trending / range-volatile...)

Référence :
    Walk-Forward Analysis + Regime Detection — López de Prado (2018),
    "Advances in Financial Machine Learning", ch. 8 (Cross-Validation
    in Finance).
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import polars as pl

logger = logging.getLogger(__name__)


@dataclass
class RegimeSegment:
    """Segment de marché identifié par un régime."""
    regime: str          # 'bull' | 'bear' | 'range' | 'high_vol' | 'low_vol' | 'combined'
    start_time: str      # ISO date
    end_time: str
    n_bars: int
    metrics: dict        # {sharpe, return_pct, max_dd, win_rate, ...}


def detect_trend_regime(df: pl.DataFrame, ema_period: int = 200,
                         slope_window: int = 20) -> List[int]:
    """Étiquette chaque barre avec un régime de tendance.

    Bull : prix au-dessus de EMA200 + pente EMA20 > 0
    Bear : prix en-dessous de EMA200 + pente EMA20 < 0
    Range : sinon (prix proche de EMA200 ou pente EMA20 plate)

    Returns
    -------
    list of int
        Régime par barre : 1=bull, -1=bear, 0=range.
    """
    closes = df["close"].to_list()
    n = len(closes)
    if n < ema_period + slope_window:
        return [0] * n

    # EMA simple (poids exponentiel)
    def ema(values: list, period: int) -> list:
        alpha = 2.0 / (period + 1)
        ema_vals = [values[0]] * len(values)
        for i in range(1, len(values)):
            ema_vals[i] = alpha * values[i] + (1 - alpha) * ema_vals[i - 1]
        return ema_vals

    ema200 = ema(closes, ema_period)
    ema_short = ema(closes, slope_window)

    regimes = []
    for i in range(n):
        if i < ema_period:
            regimes.append(0)
            continue
        above_ema = closes[i] > ema200[i]
        slope = ema_short[i] - ema_short[i - slope_window] if i >= slope_window else 0
        slope_threshold = ema_short[i] * 0.001  # 0.1% de pente sur la fenêtre
        if above_ema and slope > slope_threshold:
            regimes.append(1)
        elif (not above_ema) and slope < -slope_threshold:
            regimes.append(-1)
        else:
            regimes.append(0)
    return regimes


def detect_volatility_regime(df: pl.DataFrame, atr_period: int = 14,
                               window: int = 100,
                               high_pct: float = 0.75,
                               low_pct: float = 0.25) -> List[int]:
    """Étiquette chaque barre par volatilité relative.

    High vol : ATR percentile > 75% sur la fenêtre glissante
    Low vol : ATR percentile < 25%
    Medium vol : sinon

    Returns
    -------
    list of int
        2=high, 1=medium, 0=low
    """
    highs = df["high"].to_list()
    lows = df["low"].to_list()
    closes = df["close"].to_list()
    n = len(closes)

    # ATR simple
    tr = []
    for i in range(1, n):
        h, l, c_prev = highs[i], lows[i], closes[i - 1]
        tr.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
    tr = [0.0] + tr

    # EMA ATR
    alpha = 2.0 / (atr_period + 1)
    atr = [tr[0]] * n
    for i in range(1, n):
        atr[i] = alpha * tr[i] + (1 - alpha) * atr[i - 1]

    regimes = [0] * n
    for i in range(n):
        if i < window:
            continue
        window_atr = atr[i - window: i]
        if not window_atr:
            continue
        # Percentile
        sorted_atr = sorted(window_atr)
        idx_high = int(len(sorted_atr) * high_pct)
        idx_low = int(len(sorted_atr) * low_pct)
        current_atr = atr[i]
        if current_atr >= sorted_atr[min(idx_high, len(sorted_atr) - 1)]:
            regimes[i] = 2
        elif current_atr <= sorted_atr[idx_low]:
            regimes[i] = 0
        else:
            regimes[i] = 1
    return regimes


def segment_by_regime(regimes: List[int], df: pl.DataFrame) -> List[Tuple[int, int, int]]:
    """Découpe le df en segments contigus du même régime.

    Returns
    -------
    list of (start_idx, end_idx, regime_value)
    """
    if not regimes:
        return []
    segments = []
    start = 0
    current = regimes[0]
    for i in range(1, len(regimes)):
        if regimes[i] != current:
            segments.append((start, i - 1, current))
            start = i
            current = regimes[i]
    segments.append((start, len(regimes) - 1, current))
    # Filtrer segments trop courts (< 30 bars)
    return [(s, e, r) for s, e, r in segments if e - s >= 30]


def compute_segment_metrics(df: pl.DataFrame, start: int, end: int,
                              initial_capital: float = 1000.0) -> dict:
    """Calcule les métriques d'un sous-segment du df.

    Métriques simplifiées (Buy & Hold sur le segment) — pour backtest
    complet par segment, il faut appeler Backtester.run(df[start:end]).
    """
    if end - start < 2:
        return {}
    closes = df["close"].to_list()[start:end + 1]
    first, last = closes[0], closes[-1]
    if first <= 0:
        return {}
    return_pct = (last - first) / first * 100.0
    # Sharpe daily (approximation)
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1]
                for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(returns) < 2:
        sharpe = 0.0
    else:
        mean_r = sum(returns) / len(returns)
        var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = math.sqrt(var_r) if var_r > 0 else 0
        sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0
    # Max DD
    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        peak = max(peak, c)
        if peak > 0:
            dd = (peak - c) / peak * 100
            max_dd = max(max_dd, dd)
    return {
        'n_bars': end - start + 1,
        'return_pct': round(return_pct, 3),
        'sharpe_annualized': round(sharpe, 3),
        'max_drawdown_pct': round(max_dd, 2),
        'first_close': round(first, 6),
        'last_close': round(last, 6),
    }


def stress_test_by_regime(df: pl.DataFrame,
                            regime_type: str = 'trend',
                            min_segment_bars: int = 30) -> List[RegimeSegment]:
    """Découpe le df en segments par régime et calcule les métriques.

    Parameters
    ----------
    df : pl.DataFrame
        OHLCV DataFrame avec colonne 'time' + 'close' (minimum).
    regime_type : str
        'trend' (défaut) | 'volatility' | 'combined'
    min_segment_bars : int
        Segments plus courts que ça sont filtrés.

    Returns
    -------
    list of RegimeSegment
        Un segment par régime identifié, trié par ordre chronologique.
    """
    if df is None or df.height < 200:
        return []

    if regime_type == 'trend':
        regimes = detect_trend_regime(df)
        label_map = {1: 'bull', -1: 'bear', 0: 'range'}
    elif regime_type == 'volatility':
        regimes = detect_volatility_regime(df)
        label_map = {2: 'high_vol', 1: 'medium_vol', 0: 'low_vol'}
    elif regime_type == 'combined':
        # Combiner trend + vol en labels plus riches
        trend = detect_trend_regime(df)
        vol = detect_volatility_regime(df)
        regimes = []
        for t, v in zip(trend, vol):
            label_idx = t * 10 + v  # -12, -11, ..., 12
            regimes.append(label_idx)
        label_map = {
            12: 'bull_high_vol', 11: 'bull_med_vol', 10: 'bull_low_vol',
            2: 'range_high_vol', 1: 'range_med_vol', 0: 'range_low_vol',
            -8: 'bear_high_vol', -9: 'bear_med_vol', -10: 'bear_low_vol',
        }
    else:
        raise ValueError(f"regime_type inconnu : {regime_type}")

    segments = segment_by_regime(regimes, df)
    segments = [(s, e, r) for s, e, r in segments if e - s >= min_segment_bars]

    times = df["time"].to_list() if "time" in df.columns else list(range(df.height))
    results = []
    for start, end, regime_val in segments:
        regime_name = label_map.get(regime_val, f'regime_{regime_val}')
        metrics = compute_segment_metrics(df, start, end)
        if not metrics:
            continue
        seg = RegimeSegment(
            regime=regime_name,
            start_time=str(times[start])[:10],
            end_time=str(times[end])[:10],
            n_bars=metrics['n_bars'],
            metrics=metrics,
        )
        results.append(seg)

    return results


def regime_summary(segments: List[RegimeSegment]) -> dict:
    """Agrège les métriques par régime.

    Returns
    -------
    dict
        {regime_name: {n_segments, total_bars, avg_return_pct, avg_sharpe,
                       worst_max_dd, best_return_pct}}
    """
    by_regime = {}
    for seg in segments:
        if seg.regime not in by_regime:
            by_regime[seg.regime] = {
                'n_segments': 0,
                'total_bars': 0,
                'returns': [],
                'sharpes': [],
                'max_dds': [],
            }
        agg = by_regime[seg.regime]
        agg['n_segments'] += 1
        agg['total_bars'] += seg.n_bars
        agg['returns'].append(seg.metrics.get('return_pct', 0))
        agg['sharpes'].append(seg.metrics.get('sharpe_annualized', 0))
        agg['max_dds'].append(seg.metrics.get('max_drawdown_pct', 0))

    summary = {}
    for regime, agg in by_regime.items():
        summary[regime] = {
            'n_segments': agg['n_segments'],
            'total_bars': agg['total_bars'],
            'avg_return_pct': round(sum(agg['returns']) / len(agg['returns']), 3),
            'best_return_pct': round(max(agg['returns']), 3),
            'worst_return_pct': round(min(agg['returns']), 3),
            'avg_sharpe': round(sum(agg['sharpes']) / len(agg['sharpes']), 3),
            'worst_max_dd': round(max(agg['max_dds']), 2),
        }
    return summary
