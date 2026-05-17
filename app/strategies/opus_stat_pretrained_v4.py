"""Stratégie Opus Stat Pretrained V4 — utilise les modèles LightGBM V4 déjà entraînés.

Contrairement aux stratégies `scoring_statistique_opus_v*`, **aucun entraînement
n'est effectué** (ni inline dans l'optimiseur, ni périodique en live). Les six
modèles LightGBM (15m/30m/1h × amplitude/direction) du pack V4 sont chargés une
fois pour toutes depuis le PKL embarqué dans
``app/strategies/opus_stat_pretrained_v4_data/``.

Pipeline :

  1. Détection du timeframe (15m / 30m / 1h) à partir des deltas de ``df['time']``
  2. Construction des 40 features V4 attendues par chaque modèle, via une
     reproduction fidèle du ``FeatureBuilder`` du bot autonome V4 (pandas).
  3. Détection du régime ADX + alignement SMA (Range / Trend Up / Trend Down /
     Choppy). Trend Up → pas de signal (AUC ≈ 0.50).
  4. P(événement) et P(hausse) prédits par les modèles pré-entraînés
     (les médianes du set d'entraînement servent à imputer les NaN).
  5. Décision : seuils plus stricts hors Trend Down (cf. rapport V4 §6.4).
  6. Sortie : stop initial = 1.5×ATR (SL) + trailing manager (TP par trailing).

Comme la stratégie est entièrement statique, ``managed_externally`` est forcé à
``True`` : le ``MLStrategyTrainer`` ne tentera jamais de la réentraîner et
l'optimiseur ne fera que varier les seuils de décision.
"""

import json
import logging
import os
import pickle
import threading
import warnings
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import polars as pl

from app.engine.engine import BaseStrategyML
from app.core.indicators import pre_val

warnings.filterwarnings("ignore", category=UserWarning, module="lightgbm")

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Chemins des artefacts embarqués (pkl + médianes d'imputation)
# ─────────────────────────────────────────────────────────────────────────────
_DATA_DIR     = os.path.join(os.path.dirname(__file__), "opus_stat_pretrained_v4_data")
_MODELS_PATH  = os.path.join(_DATA_DIR, "v4_models.pkl")
_MEDIANS_PATH = os.path.join(_DATA_DIR, "v4_medians.json")

# Régimes — alignés sur les stratégies opus existantes
REGIME_RANGE    = 0
REGIME_TREND_UP = 1
REGIME_TREND_DN = 2
REGIME_CHOPPY   = 3
REGIME_LABELS = {
    REGIME_RANGE:    "Range",
    REGIME_TREND_UP: "Trend Up",
    REGIME_TREND_DN: "Trend Down",
    REGIME_CHOPPY:   "Choppy",
}

_SUPPORTED_TFS = ("15m", "30m", "1h")


# ─────────────────────────────────────────────────────────────────────────────
# Détection de timeframe à partir des deltas de la colonne ``time``
# ─────────────────────────────────────────────────────────────────────────────
def _detect_timeframe(df: pl.DataFrame) -> Optional[str]:
    """Renvoie '15m' / '30m' / '1h' selon la médiane des deltas de ``time``."""
    if "time" not in df.columns or len(df) < 5:
        return None
    times = df["time"]
    try:
        # Polars Datetime : différence retourne Duration (en µs)
        deltas = times.diff().drop_nulls()
        if len(deltas) == 0:
            return None
        # Convert to seconds — try several access patterns selon la version Polars
        try:
            med_ns = deltas.dt.total_microseconds().median()
            med_s  = float(med_ns) / 1_000_000.0
        except Exception:
            med_s = float(deltas.median().total_seconds())
    except Exception:
        # Fallback : timestamps numériques (epoch s ou ms)
        arr = times.to_numpy()
        try:
            diffs = np.diff(arr.astype("float64"))
            med_s = float(np.median(diffs))
            if med_s > 1e6:  # epoch ms
                med_s /= 1000.0
        except Exception:
            return None
    if med_s <= 0:
        return None
    if abs(med_s - 900)  < 60:
        return "15m"
    if abs(med_s - 1800) < 120:
        return "30m"
    if abs(med_s - 3600) < 240:
        return "1h"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Reproduction du FeatureBuilder V4 (pipeline 11_v4_datasets.py — pandas)
# ─────────────────────────────────────────────────────────────────────────────
class _FeatureBuilder:
    """Construit, sur un DataFrame pandas OHLCV, l'intégralité des features V4
    plus leurs lags 1/3/6/12 — strictement le même pipeline que celui qui a
    servi à entraîner les modèles, sans quoi les prédictions seraient invalides.
    """

    @staticmethod
    def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
        delta = close.diff()
        gain  = delta.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
        rs    = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _macd(close: pd.Series, fast=12, slow=26, signal=9):
        ef   = close.ewm(span=fast, adjust=False).mean()
        es   = close.ewm(span=slow, adjust=False).mean()
        line = ef - es
        sig  = line.ewm(span=signal, adjust=False).mean()
        return line, sig, line - sig

    @staticmethod
    def _bollinger(close: pd.Series, n=20, k=2):
        ma = close.rolling(n).mean()
        sd = close.rolling(n).std()
        return ma, ma + k * sd, ma - k * sd

    @staticmethod
    def _atr(h: pd.Series, l: pd.Series, c: pd.Series, n=14) -> pd.Series:
        tr = pd.concat(
            [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
        ).max(axis=1)
        return tr.ewm(alpha=1/n, adjust=False).mean()

    @staticmethod
    def _adx(h: pd.Series, l: pd.Series, c: pd.Series, n=14):
        up = h.diff();  dn = -l.diff()
        plus_dm  = ((up > dn) & (up > 0)) * up
        minus_dm = ((dn > up) & (dn > 0)) * dn
        tr = pd.concat(
            [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
        ).max(axis=1)
        atr_ = tr.ewm(alpha=1/n, adjust=False).mean()
        pdi  = 100 * plus_dm.ewm(alpha=1/n, adjust=False).mean() / atr_
        mdi  = 100 * minus_dm.ewm(alpha=1/n, adjust=False).mean() / atr_
        dx   = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
        return dx.ewm(alpha=1/n, adjust=False).mean(), pdi, mdi

    @staticmethod
    def _slope(series: pd.Series, n: int) -> pd.Series:
        x = np.arange(n)
        def _s(y):
            if np.any(np.isnan(y)):
                return np.nan
            return np.polyfit(x, y, 1)[0]
        return series.rolling(n).apply(_s, raw=True)

    @staticmethod
    def _hurst_rs(arr: np.ndarray, max_lag: int = 20) -> float:
        arr = arr[~np.isnan(arr)]
        if len(arr) < max_lag + 5:
            return np.nan
        tau = []
        for lag in range(2, max_lag):
            diff = arr[lag:] - arr[:-lag]
            if len(diff) < 2 or np.std(diff) == 0:
                continue
            tau.append(np.sqrt(np.std(diff)))
        if len(tau) < 5:
            return np.nan
        try:
            poly = np.polyfit(np.log(np.arange(2, 2 + len(tau))), np.log(tau), 1)
            return poly[0] * 2.0
        except Exception:
            return np.nan

    @staticmethod
    def _bars_since_cross(sf: pd.Series, ss: pd.Series) -> pd.Series:
        above = (sf > ss).astype(int)
        cross = above.diff().fillna(0)
        result = np.full(len(above), np.nan)
        last_idx, last_dir = -1, 0
        for i in range(len(above)):
            if cross.iloc[i] != 0:
                last_idx, last_dir = i, int(cross.iloc[i])
            if last_idx >= 0:
                result[i] = last_dir * (i - last_idx)
        return pd.Series(result, index=above.index)

    def build(self, raw_df: pd.DataFrame) -> Optional[pd.DataFrame]:
        if len(raw_df) < 210:
            return None

        # Tri stable + reset d'index pour les opérations rolling
        if "time" in raw_df.columns:
            d = raw_df.sort_values("time").reset_index(drop=True)
        else:
            d = raw_df.reset_index(drop=True)
        c, h, l, v, o = d["close"], d["high"], d["low"], d["volume"], d["open"]

        # Rendements
        d["ret"]       = c.pct_change()
        d["ret_intra"] = (c - o) / o
        d["log_ret"]   = np.log(c / c.shift(1))

        # MM
        for n in (20, 50, 100, 200):
            d[f"SMA_{n}"]      = c.rolling(n).mean()
            d[f"EMA_{n}"]      = c.ewm(span=n, adjust=False).mean()
            d[f"dist_SMA{n}"]  = (c - d[f"SMA_{n}"]) / d[f"SMA_{n}"]
            d[f"dist_EMA{n}"]  = (c - d[f"EMA_{n}"]) / d[f"EMA_{n}"]
            d[f"slope_SMA{n}"] = self._slope(d[f"SMA_{n}"], min(n, 20)) / c

        d["MM_bullish_align"] = (
            (d["SMA_20"]  > d["SMA_50"])  &
            (d["SMA_50"]  > d["SMA_100"]) &
            (d["SMA_100"] > d["SMA_200"])
        ).astype(int)
        d["MM_bearish_align"] = (
            (d["SMA_20"]  < d["SMA_50"])  &
            (d["SMA_50"]  < d["SMA_100"]) &
            (d["SMA_100"] < d["SMA_200"])
        ).astype(int)
        d["EMA_9"]  = c.ewm(span=9,  adjust=False).mean()
        d["EMA_21"] = c.ewm(span=21, adjust=False).mean()

        # Croisements
        d["cross_9_21"]   = self._bars_since_cross(d["EMA_9"],  d["EMA_21"])
        d["cross_20_50"]  = self._bars_since_cross(d["SMA_20"], d["SMA_50"])
        d["cross_50_100"] = self._bars_since_cross(d["SMA_50"], d["SMA_100"])
        d["cross_50_200"] = self._bars_since_cross(d["SMA_50"], d["SMA_200"])

        # Momentum
        for n in (7, 14, 21):
            d[f"RSI_{n}"] = self._rsi(c, n)
            d[f"ROC_{n}"] = c.pct_change(n) * 100

        d["RSI_14_d1"]    = d["RSI_14"].diff()
        d["RSI_14_d3"]    = d["RSI_14"].diff(3)
        d["RSI_14_accel"] = d["RSI_14_d1"].diff()
        d["RSI_oversold"]   = (d["RSI_14"] < 30).astype(int)
        d["RSI_overbought"] = (d["RSI_14"] > 70).astype(int)

        rhp = c.rolling(14).max();  rhr = d["RSI_14"].rolling(14).max()
        d["bear_div"] = ((c == rhp) & (d["RSI_14"] < rhr * 0.97)).astype(int)
        rlp = c.rolling(14).min();  rlr = d["RSI_14"].rolling(14).min()
        d["bull_div"] = ((c == rlp) & (d["RSI_14"] > rlr * 1.03)).astype(int)

        green = (c > o).astype(int)
        d["green_ratio_10"] = green.rolling(10).mean()
        d["green_ratio_20"] = green.rolling(20).mean()
        d["accel_5"]        = d["ret"].rolling(5).mean().diff(5)

        # MACD
        m, s, hist = self._macd(c)
        d["MACD"] = m
        d["MACD_signal"] = s
        d["MACD_hist"] = hist
        d["MACD_hist_d1"]      = hist.diff()
        d["MACD_hist_d3"]      = hist.diff(3)
        d["MACD_above_signal"] = (m > s).astype(int)
        d["MACD_zero_cross"]   = (np.sign(m) - np.sign(m.shift(1))).fillna(0)

        # Breakout
        for n in (20, 50, 100):
            d[f"high_{n}"]       = h.rolling(n).max().shift(1)
            d[f"low_{n}"]        = l.rolling(n).min().shift(1)
            d[f"break_high_{n}"] = (c > d[f"high_{n}"]).astype(int)
            d[f"break_low_{n}"]  = (c < d[f"low_{n}"]).astype(int)
            d[f"dist_high_{n}"]  = (c - d[f"high_{n}"]) / d[f"high_{n}"]
            d[f"dist_low_{n}"]   = (c - d[f"low_{n}"]) / d[f"low_{n}"]
            rng = d[f"high_{n}"] - d[f"low_{n}"]
            d[f"range_pos_{n}"]  = (c - d[f"low_{n}"]) / rng.replace(0, np.nan)

        d["false_break_high_20"] = (
            (h.rolling(3).max().shift(1) > d["high_20"].shift(2)) & (c < d["high_20"])
        ).astype(int)
        d["false_break_low_20"] = (
            (l.rolling(3).min().shift(1) < d["low_20"].shift(2)) & (c > d["low_20"])
        ).astype(int)

        # Bollinger
        bb_ma, bb_up, bb_lo = self._bollinger(c)
        d["BB_width"]         = (bb_up - bb_lo) / bb_ma
        d["BB_pos"]           = (c - bb_lo) / (bb_up - bb_lo).replace(0, np.nan)
        d["BB_width_rank100"] = d["BB_width"].rolling(100).rank(pct=True)
        d["BB_squeeze"]       = (d["BB_width_rank100"] < 0.2).astype(int)
        d["BB_expansion"]     = (d["BB_width"] > d["BB_width"].shift(5) * 1.2).astype(int)

        # Pullback
        d["pullback_to_sma20"]    = (d["SMA_20"] - c) / c
        big = d["ret"].rolling(5).sum()
        d["pullback_after_rally"] = ((big.shift(3) > 0.01)  & (d["ret"].rolling(3).sum() < 0)).astype(int)
        d["bounce_after_drop"]    = ((big.shift(3) < -0.01) & (d["ret"].rolling(3).sum() > 0)).astype(int)
        sh = h.rolling(50).max(); sl_s = l.rolling(50).min()
        d["fib_pos"] = (c - sl_s) / (sh - sl_s).replace(0, np.nan)

        # ADX / régime
        adxv, pdi, mdi = self._adx(h, l, c)
        d["ADX"] = adxv
        d["DI_plus"] = pdi
        d["DI_minus"] = mdi
        d["DI_diff"]           = pdi - mdi
        d["trend_strong"]      = (adxv > 25).astype(int)
        d["trend_very_strong"] = (adxv > 40).astype(int)
        strong = (adxv > 25).astype(int)
        grp = (strong != strong.shift()).cumsum()
        d["trend_duration"] = strong.groupby(grp).cumsum()
        d["hurst_100"] = d["log_ret"].rolling(100).apply(
            lambda x: self._hurst_rs(np.asarray(x)), raw=True
        )

        # Volatilité / volume
        d["ATR_14"]       = self._atr(h, l, c, 14)
        d["ATR_pct"]      = d["ATR_14"] / c
        d["vol_std_20"]   = d["ret"].rolling(20).std()
        d["vol_ratio"]    = v / v.rolling(20).mean()
        d["vol_ratio_50"] = v / v.rolling(50).mean()
        d["OBV"]          = (np.sign(c.diff()).fillna(0) * v).cumsum()
        d["OBV_slope"]    = self._slope(d["OBV"], 10) / v.rolling(10).mean()

        # Bougie
        d["body"]        = (c - o) / o
        d["body_abs"]    = d["body"].abs()
        d["upper_wick"]  = (h - d[["open", "close"]].max(axis=1)) / o
        d["lower_wick"]  = (d[["open", "close"]].min(axis=1) - l) / o
        d["range_size"]  = (h - l) / o
        d["doji"]        = (d["body_abs"] < 0.001).astype(int)
        d["three_green"] = ((c > o) & (c.shift() > o.shift()) & (c.shift(2) > o.shift(2))).astype(int)
        d["three_red"]   = ((c < o) & (c.shift() < o.shift()) & (c.shift(2) < o.shift(2))).astype(int)

        # Interactions
        d["RSI_x_ADX"]   = d["RSI_14"] * d["ADX"]
        d["BBpos_x_ADX"] = d["BB_pos"] * d["ADX"]
        d["vol_x_break"] = d["vol_ratio"] * (d["break_high_20"] + d["break_low_20"])

        # Lags 1/3/6/12 — reproduit exactement ``add_lags()`` du pipeline V4
        continuous_feats = [
            "dist_SMA20", "dist_SMA50", "dist_SMA100", "dist_SMA200",
            "dist_EMA20", "dist_EMA50", "slope_SMA20", "slope_SMA50", "slope_SMA100",
            "cross_9_21", "cross_20_50", "cross_50_100", "cross_50_200",
            "RSI_7", "RSI_14", "RSI_21", "RSI_14_d1", "RSI_14_d3", "RSI_14_accel",
            "ROC_7", "ROC_14", "ROC_21", "green_ratio_10", "green_ratio_20", "accel_5",
            "MACD", "MACD_signal", "MACD_hist", "MACD_hist_d1", "MACD_hist_d3",
            "dist_high_20", "dist_low_20", "dist_high_50", "dist_low_50",
            "range_pos_20", "range_pos_50", "range_pos_100",
            "BB_width", "BB_pos", "BB_width_rank100", "pullback_to_sma20", "fib_pos",
            "ADX", "DI_plus", "DI_minus", "DI_diff", "trend_duration", "hurst_100",
            "ATR_pct", "vol_std_20", "vol_ratio", "vol_ratio_50", "OBV_slope",
            "body", "body_abs", "upper_wick", "lower_wick", "range_size",
            "RSI_x_ADX", "BBpos_x_ADX",
        ]
        binary_feats = [
            "MM_bullish_align", "MM_bearish_align", "RSI_oversold", "RSI_overbought",
            "bear_div", "bull_div", "MACD_above_signal",
            "break_high_20", "break_low_20", "break_high_50", "break_low_50",
            "break_high_100", "break_low_100",
            "false_break_high_20", "false_break_low_20", "BB_squeeze", "BB_expansion",
            "pullback_after_rally", "bounce_after_drop", "trend_strong", "trend_very_strong",
            "doji", "three_green", "three_red", "MACD_zero_cross", "vol_x_break",
        ]
        new_cols = {}
        for feat in continuous_feats + binary_feats:
            if feat not in d.columns:
                continue
            for lag in (1, 3, 6, 12):
                new_cols[f"{feat}_lag{lag}"] = d[feat].shift(lag)
        d = pd.concat([d, pd.DataFrame(new_cols, index=d.index)], axis=1)
        # Défragmentation finale — évite les PerformanceWarning de pandas.
        return d.copy()


# ─────────────────────────────────────────────────────────────────────────────
# Chargeur des modèles V4 (singleton process-wide pour éviter de relire le pkl)
# ─────────────────────────────────────────────────────────────────────────────
_PRETRAINED_CACHE: dict = {"models": None, "medians": None}
_PRETRAINED_LOCK = threading.Lock()


def _load_pretrained() -> tuple:
    """Charge (une seule fois par process) le pkl + le JSON de médianes."""
    with _PRETRAINED_LOCK:
        if _PRETRAINED_CACHE["models"] is None:
            if not os.path.exists(_MODELS_PATH) or not os.path.exists(_MEDIANS_PATH):
                raise FileNotFoundError(
                    f"Artefacts V4 introuvables — vérifiez {_DATA_DIR}"
                )
            with open(_MODELS_PATH, "rb") as f:
                models = pickle.load(f)
            with open(_MEDIANS_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            medians: Dict[tuple, Dict[str, float]] = {}
            for k, v in raw.items():
                # clés stockées comme "('15m', 'amp')" — on les normalise
                k_clean = k.strip("()").replace("'", "").replace(" ", "")
                parts = k_clean.split(",")
                if len(parts) == 2:
                    medians[(parts[0], parts[1])] = v
            _PRETRAINED_CACHE["models"]  = models
            _PRETRAINED_CACHE["medians"] = medians
            logger.info(
                "[OpusV4-PT] Modèles V4 pré-entraînés chargés "
                f"({len(models)} entrées, {len(medians)} sets de médianes)"
            )
    return _PRETRAINED_CACHE["models"], _PRETRAINED_CACHE["medians"]


def _prepare_row(features_df: pd.DataFrame,
                 feat_names: List[str],
                 medians: dict) -> pd.DataFrame:
    """Extrait la dernière ligne ; impute NaN/inf via les médianes du train.

    Renvoie un DataFrame (et non un ndarray) pour que LightGBM retrouve les
    noms de colonnes attendus, évitant le warning sklearn ``X does not have
    valid feature names``.
    """
    last = features_df.iloc[-1]
    row = {}
    for f in feat_names:
        val = last.get(f, np.nan)
        if pd.isna(val) or np.isinf(val):
            val = medians.get(f, 0.0)
        row[f] = float(val)
    return pd.DataFrame([row], columns=feat_names)


# ─────────────────────────────────────────────────────────────────────────────
# Conversion polars → pandas pour le sous-ensemble utile au FeatureBuilder
# ─────────────────────────────────────────────────────────────────────────────
def _last_bar_hour_dow(df: pl.DataFrame) -> tuple:
    """Renvoie ``(hour_utc, weekday)`` de la dernière barre, ou ``(None, None)``."""
    if "time" not in df.columns or len(df) == 0:
        return None, None
    ts = df["time"][-1]
    try:
        if hasattr(ts, "hour") and hasattr(ts, "weekday"):
            return int(ts.hour), int(ts.weekday())
    except Exception:
        pass
    # Fallback : timestamp numérique (epoch s ou ms)
    try:
        import datetime as _dt
        raw = float(ts)
        if raw > 1e12:
            raw /= 1000.0
        d = _dt.datetime.utcfromtimestamp(raw)
        return d.hour, d.weekday()
    except Exception:
        return None, None


def _to_pandas_window(df: pl.DataFrame, n: int = 260) -> pd.DataFrame:
    """Retourne les `n` dernières lignes en pandas avec les seules colonnes OHLCV+time."""
    cols = [c for c in ("time", "open", "high", "low", "close", "volume") if c in df.columns]
    window = df.select(cols).tail(n)
    pdf = window.to_pandas()
    # Garantie : colonnes float
    for c in ("open", "high", "low", "close", "volume"):
        if c in pdf.columns:
            pdf[c] = pdf[c].astype(float)
    if "time" not in pdf.columns:
        pdf["time"] = pd.RangeIndex(len(pdf))
    return pdf


# ─────────────────────────────────────────────────────────────────────────────
# Stratégie
# ─────────────────────────────────────────────────────────────────────────────
class Strategy(BaseStrategyML):
    """Stratégie ML utilisant directement les modèles V4 pré-entraînés (pkl)."""

    name      = "opus_stat_pretrained_v4"
    # Le dossier embarque les artefacts ; l'engine ne tentera pas d'écrire dedans.
    model_dir = _DATA_DIR

    timeframes: List[str] = list(_SUPPORTED_TFS)

    # Seuls les seuils de décision sont optimisables — les modèles sont figés.
    # SL/TP per régime : reproduit risk.py (tp_mults / sl_mults) du bot V4.
    param_space: Dict[str, Any] = {
        "thresh_amp_td":    [0.40, 0.45, 0.50, 0.55, 0.60],
        "thresh_dir_td":    [0.05, 0.08, 0.10, 0.12, 0.15],
        "thresh_amp_other": [0.50, 0.55, 0.60, 0.65, 0.70],
        "thresh_dir_other": [0.10, 0.13, 0.15, 0.18, 0.20],
        "sl_atr_mult_td":    [1.5, 1.75, 1.8, 2.0, 2.25],
        "sl_atr_mult_other": [1.0, 1.25, 1.5, 1.75],
        "tp_atr_mult_td":    [1.0, 1.2, 1.4, 1.6],
        "tp_atr_mult_other": [0.8, 1.0, 1.2, 1.4],
        "max_hold_bars":     [1, 2, 4, 6, 8],
    }
    fixed_params: Dict[str, Any] = {}

    # Valeurs par défaut des flags de comportement V4 — surchargeables via YAML.
    # Multiplicateurs SL/TP par régime alignés sur risk.py du bot V4 :
    #   Trend Down : SL=1.8×ATR, TP=1.2×ATR (mouvements plus violents)
    #   Range/Choppy : SL=1.5×ATR, TP=1.0×ATR
    _DEFAULTS = {
        "enable_hour_filter":  True,
        "active_hours_utc":    list(range(13, 21)),   # 13h-20h UTC (session US)
        "active_days":         [0, 1, 2, 3, 4],       # Lun-Ven
        "use_fixed_tp":        True,                  # TP fixe = tp_atr_mult × ATR
        "disable_trailing":    True,                  # SL fixe, pas de trailing
        "use_exit_after_bars": False,                 # pas de sortie temporelle
        # SL/TP par régime (risk.py V4)
        "sl_atr_mult_td":      1.8,
        "sl_atr_mult_other":   1.5,
        "tp_atr_mult_td":      1.2,
        "tp_atr_mult_other":   1.0,
        # Demi-Kelly via confidence (rapport V4 §6.6)
        "use_kelly_sizing":    True,
        "kelly_size_other":    0.5,                   # Range/Choppy : taille ×0.5
        "min_confidence":      0.2,                   # plancher pour éviter taille nulle
    }

    # Intervalle de réentraînement énorme — la stratégie ne se réentraîne jamais
    # mais on laisse le MLStrategyTrainer planifier un cycle factice par sécurité.
    retrain_interval_h: int = 24 * 365  # 1 an : effectivement jamais

    _FEATURE_BUILDER = _FeatureBuilder()

    def __init__(self):
        self._lock = threading.Lock()
        self._models:  Dict[tuple, Any]            = {}
        self._medians: Dict[tuple, Dict[str, float]] = {}
        self._loaded = False
        # Compatibilité avec l'API ML
        self._managed_externally  = True
        self._best_auc            = 0.0
        self._best_auc_per_tf:   Dict[str, float] = {}
        self._train_meta:        Dict[str, dict]  = {}
        self._ensure_loaded()

    # ── Cycle de vie ML ────────────────────────────────────────────────────
    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return True
        try:
            models, medians = _load_pretrained()
            with self._lock:
                self._models  = dict(models)
                self._medians = dict(medians)
                self._loaded  = True
                # AUC OOS documentées dans le README V4 — informationnel
                self._best_auc_per_tf = {"15m": 0.626, "30m": 0.597, "1h": 0.603}
                self._best_auc        = max(self._best_auc_per_tf.values())
                for tf in _SUPPORTED_TFS:
                    self._train_meta[tf] = {
                        "auc_amp": {"15m": 0.749, "30m": 0.690, "1h": 0.676}.get(tf, 0.0),
                        "auc_dir": {"15m": 0.503, "30m": 0.504, "1h": 0.530}.get(tf, 0.0),
                        "source":  "v4_models.pkl (pré-entraîné, AUC OOS rapport V4)",
                    }
            return True
        except Exception as e:
            logger.error(f"[OpusV4-PT] Chargement des modèles KO : {e}")
            return False

    @property
    def is_trained(self) -> bool:
        return self._loaded

    @property
    def managed_externally(self) -> bool:
        # Toujours True : aucun cycle d'entraînement n'a de sens ici.
        return True

    @managed_externally.setter
    def managed_externally(self, _value: bool):
        # Ignoré — la stratégie reste managed externally en toutes circonstances.
        return

    def min_bars_required(self, params: dict = None) -> int:
        # FeatureBuilder a besoin de 210 barres ; on garde une marge pour les lags.
        return 230

    def reset_model(self) -> None:
        # Les modèles sont figés — on ne les efface jamais.
        return

    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        # Pas d'entraînement : on s'assure simplement que le pkl est chargé.
        self._ensure_loaded()

    def save_model(self, path: str) -> None:
        # Modèles embarqués — pas de persistance par TF.
        return

    def load_model(self, path: str = None) -> bool:
        # Ignore ``path`` : on recharge toujours le pkl embarqué.
        return self._ensure_loaded()

    # ── Prédictions ────────────────────────────────────────────────────────
    # Reproduction de l'API ModelEngine du bot V4 standalone : on expose deux
    # méthodes publiques explicites (predict_amplitude / predict_direction) qui
    # délèguent au cœur générique _predict(target). Les NaN/Inf sont imputés
    # via les médianes du set d'entraînement (cf. v4_medians.json).
    def _predict(self, features_df: pd.DataFrame, tf: str,
                 target: str) -> Optional[float]:
        key = (tf, target, "single")
        entry = self._models.get(key)
        if entry is None:
            return None
        try:
            feat_names = entry["features"]
            medians    = self._medians.get((tf, target), {})
            X          = _prepare_row(features_df, feat_names, medians)
            return float(entry["model"].predict_proba(X)[0, 1])
        except Exception as e:
            logger.warning(f"[OpusV4-PT] Prédiction {key} KO : {e}")
            return None

    def predict_amplitude(self, features_df: pd.DataFrame, tf: str) -> Optional[float]:
        """P(événement |return_{t+1}| > seuil) pour le timeframe donné."""
        return self._predict(features_df, tf, "amp")

    def predict_direction(self, features_df: pd.DataFrame, tf: str) -> Optional[float]:
        """P(hausse à t+1) pour le timeframe donné."""
        return self._predict(features_df, tf, "dir")

    # ── Cœur du signal ─────────────────────────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if not self._ensure_loaded():
            return self._none("Modèles V4 indisponibles")

        if df is None or len(df) < self.min_bars_required():
            return self._none(f"Données insuffisantes ({len(df) if df is not None else 0})")

        p = (params or {}).get(self.name, {})
        thresh_amp_td    = float(p.get("thresh_amp_td",    0.50))
        thresh_dir_td    = float(p.get("thresh_dir_td",    0.10))
        thresh_amp_other = float(p.get("thresh_amp_other", 0.55))
        thresh_dir_other = float(p.get("thresh_dir_other", 0.13))
        adx_threshold    = float(p.get("adx_threshold",    20.0))
        max_hold_bars    = int(p.get("max_hold_bars",      4))

        # Multiplicateurs SL/TP par régime (reproduit risk.py V4) ────────────
        sl_atr_mult_td    = float(p.get("sl_atr_mult_td",    self._DEFAULTS["sl_atr_mult_td"]))
        sl_atr_mult_other = float(p.get("sl_atr_mult_other", self._DEFAULTS["sl_atr_mult_other"]))
        tp_atr_mult_td    = float(p.get("tp_atr_mult_td",    self._DEFAULTS["tp_atr_mult_td"]))
        tp_atr_mult_other = float(p.get("tp_atr_mult_other", self._DEFAULTS["tp_atr_mult_other"]))
        # Rétro-compat avec l'ancienne API mono-régime
        if "sl_atr_mult" in p:
            sl_atr_mult_td = sl_atr_mult_other = float(p["sl_atr_mult"])
        if "tp_atr_mult" in p:
            tp_atr_mult_td = tp_atr_mult_other = float(p["tp_atr_mult"])

        # Demi-Kelly (reproduit risk.py V4 : size = capital × risk × confidence)
        use_kelly_sizing = bool(p.get("use_kelly_sizing", self._DEFAULTS["use_kelly_sizing"]))
        kelly_size_other = float(p.get("kelly_size_other", self._DEFAULTS["kelly_size_other"]))
        min_confidence   = float(p.get("min_confidence",   self._DEFAULTS["min_confidence"]))

        # Flags de comportement V4 (défauts dans _DEFAULTS, surchargés par YAML)
        enable_hour_filter  = bool(p.get("enable_hour_filter",  self._DEFAULTS["enable_hour_filter"]))
        active_hours_utc    = list(p.get("active_hours_utc",    self._DEFAULTS["active_hours_utc"]))
        active_days         = list(p.get("active_days",         self._DEFAULTS["active_days"]))
        use_fixed_tp        = bool(p.get("use_fixed_tp",        self._DEFAULTS["use_fixed_tp"]))
        disable_trailing    = bool(p.get("disable_trailing",    self._DEFAULTS["disable_trailing"]))
        use_exit_after_bars = bool(p.get("use_exit_after_bars", self._DEFAULTS["use_exit_after_bars"]))

        # 0. Filtre temporel (session US par défaut, désactivable via YAML).
        if enable_hour_filter:
            hour, dow = _last_bar_hour_dow(df)
            if hour is not None and dow is not None:
                if dow not in active_days:
                    return self._none(
                        f"Hors jours actifs (weekday={dow}, autorisés={active_days})"
                    )
                if hour not in active_hours_utc:
                    return self._none(
                        f"Hors session ({hour}h UTC, autorisées={active_hours_utc})"
                    )

        # 1. Détection du TF — indispensable pour choisir le bon modèle.
        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return self._none(
                f"Timeframe non supporté (détecté={tf}, attendus={_SUPPORTED_TFS})"
            )

        # 2. Conversion polars → pandas + construction des features V4.
        pdf      = _to_pandas_window(df, n=max(260, self.min_bars_required() + 20))
        features = self._FEATURE_BUILDER.build(pdf)
        if features is None or len(features) == 0:
            return self._none("Construction des features V4 impossible")

        last = features.iloc[-1]
        atr_v = float(last.get("ATR_14", 0.0) or 0.0)
        if not np.isfinite(atr_v) or atr_v <= 0:
            # Fallback : ATR depuis le précompute du backtester si disponible
            atr_v = float(pre_val(df, "_pre_atr14") or 0.0)
        c_now = float(df["close"][-1] or 0.0)
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        # 3. Régime : ADX + alignement SMA (réplique de la logique V4).
        adx_v = float(last.get("ADX", 0.0) or 0.0)
        bull  = int(last.get("MM_bullish_align", 0) or 0)
        bear  = int(last.get("MM_bearish_align", 0) or 0)
        if adx_v < adx_threshold:
            regime = REGIME_RANGE
        elif bull == 1:
            regime = REGIME_TREND_UP
        elif bear == 1:
            regime = REGIME_TREND_DN
        else:
            regime = REGIME_CHOPPY
        regime_lbl = REGIME_LABELS[regime]

        # 4. Trend Up : AUC ≈ 0.50 → pas d'edge, on s'abstient (rapport §6.4).
        if regime == REGIME_TREND_UP:
            return self._none(
                f"Trend Up : aucun edge (AUC dir ≈ 0.50)",
                regime=regime,
            )

        # 5. Prédictions LightGBM (pré-entraînées).
        p_event = self._predict(features, tf, "amp")
        p_up    = self._predict(features, tf, "dir")
        if p_event is None or p_up is None:
            return self._none(f"Modèle {tf} indisponible")
        dir_dist = abs(p_up - 0.5)

        # 6. Règle de décision (réplique exacte du bot V4) — seuils + mults SL/TP
        # + facteur de taille de base par régime.
        if regime == REGIME_TREND_DN:
            amp_thresh, dir_thresh = thresh_amp_td, thresh_dir_td
            sl_atr_mult, tp_atr_mult = sl_atr_mult_td, tp_atr_mult_td
            regime_size_fac = 1.0
        else:  # Range ou Choppy
            amp_thresh, dir_thresh = thresh_amp_other, thresh_dir_other
            sl_atr_mult, tp_atr_mult = sl_atr_mult_other, tp_atr_mult_other
            regime_size_fac = kelly_size_other

        if p_event < amp_thresh:
            return self._none(
                f"P(event)={p_event:.2f} < {amp_thresh:.2f} | {regime_lbl}",
                p_event=p_event, p_up=p_up, regime=regime,
            )
        if dir_dist < dir_thresh:
            return self._none(
                f"|P(up)-0.5|={dir_dist:.2f} < {dir_thresh:.2f} | {regime_lbl}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        side = "long" if p_up > 0.5 else "short"

        # 7. Stop loss / take-profit — on envoie les MULTIPLICATEURS d'ATR (pas
        # les prix absolus) pour que l'engine les calcule à partir de exec_price
        # (le prix réellement exécuté à la barre suivante). Évite les inversions
        # de direction sur les gaps close→open.
        confidence = dir_dist * 2.0
        score_val  = round(min(0.55 + p_event * confidence * 0.39, 0.94), 3)
        meta       = self._train_meta.get(tf, {})

        # ── Sizing demi-Kelly (reproduit risk.py V4) ──────────────────────────
        # V4 standalone : size = capital × risk_pct × max(confidence, min_conf)
        # On expose un facteur ∈ [0, 1] que l'engine multiplie sur le sizing
        # standard (basé sur stop_dist en backtest ou ATR en live).
        if use_kelly_sizing:
            size_factor = regime_size_fac * max(confidence, min_confidence)
            size_factor = min(1.0, max(0.0, size_factor))
        else:
            size_factor = regime_size_fac

        # Construction du signal — payload conditionnel selon les flags V4.
        sig: Dict[str, Any] = {
            "score":            score_val,
            "side":             side,
            "name":             self.name,
            "atr":              atr_v,
            "sl_atr_mult":      sl_atr_mult,
            "size_factor":      round(size_factor, 4),
            "disable_trailing": disable_trailing,
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "tf_detected":      tf,
        }
        # TP fixe (V4 standalone) — optionnel
        if use_fixed_tp:
            sig["tp_atr_mult"] = tp_atr_mult
        # Sortie temporelle (filet de sécurité) — optionnel
        if use_exit_after_bars:
            sig["exit_after_bars"] = max_hold_bars
        # Trailing override : injecté seulement si trailing actif.
        if not disable_trailing:
            sig["trail_override"] = {
                "trail_wide":  max(1.0, sl_atr_mult),
                "trail_tight": max(0.5, tp_atr_mult * 0.5),
                "breakeven_r": 0.8,
                "lock_r":      max(1.0, tp_atr_mult),
                "tight_r":     max(1.5, tp_atr_mult * 1.5),
                "grace_bars":  1,
            }

        # Lignes de diagnostic — décrivent la configuration effective des sorties.
        exit_desc = []
        exit_desc.append(f"SL fixe = entry ∓ {sl_atr_mult:.2f}×ATR")
        if use_fixed_tp:
            exit_desc.append(f"TP fixe = entry ± {tp_atr_mult:.2f}×ATR")
        if not disable_trailing:
            exit_desc.append("trailing actif")
        else:
            exit_desc.append("trailing désactivé")
        if use_exit_after_bars:
            exit_desc.append(f"sortie après {max_hold_bars} barres max")

        sig["indicators"] = {
            "adx":              round(adx_v, 1),
            "rsi":              round(float(last.get("RSI_14", 50.0) or 50.0), 1),
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "dir_dist":         round(dir_dist, 4),
            "confidence":       round(confidence, 4),
            "size_factor":      round(size_factor, 4),
            "regime_size_fac":  regime_size_fac,
            "sl_mult":          sl_atr_mult,
            "tp_mult":          tp_atr_mult if use_fixed_tp else None,
            "use_fixed_tp":     use_fixed_tp,
            "use_kelly_sizing": use_kelly_sizing,
            "disable_trailing": disable_trailing,
            "auc_amp":          meta.get("auc_amp", 0.0),
            "auc_dir":          meta.get("auc_dir", 0.0),
        }
        sig["conditions"] = [
            f"Modèle pré-entraîné V4 / {tf} (aucun ré-entraînement)",
            f"Régime : {regime_lbl} (ADX={adx_v:.0f}) — autorisé ✓",
            f"P(événement)={p_event:.2f} ≥ {amp_thresh:.2f} ✓",
            f"P(hausse)={p_up:.2f} → |dist|={dir_dist:.2f} ≥ {dir_thresh:.2f} ✓",
            f"Risque : SL {sl_atr_mult:.2f}×ATR | TP {tp_atr_mult:.2f}×ATR (régime {regime_lbl})",
            f"Sizing : régime ×{regime_size_fac:.2f} × confidence {confidence:.2f} "
            f"= size_factor {size_factor:.2f}" if use_kelly_sizing
            else f"Sizing : ×{regime_size_fac:.2f} (Kelly désactivé)",
            f"Sortie : {' + '.join(exit_desc)}",
            f"AUC OOS V4 : amp={meta.get('auc_amp', 0):.2f} / dir={meta.get('auc_dir', 0):.2f}",
        ]
        sig["reason"] = (
            f"OpusV4-PT {side.upper()} | {regime_lbl} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f}"
        )
        return sig

    # ── Helpers ────────────────────────────────────────────────────────────
    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        return self.score(df, params)

    def _none(self, reason: str = "", p_event: float = 0.0, p_up: float = 0.5,
              regime: int = -1) -> dict:
        return {
            "score":      0,
            "side":       "none",
            "name":       self.name,
            "reason":     reason,
            "p_event":    round(p_event, 4),
            "p_up":       round(p_up, 4),
            "regime":     regime,
            "regime_lbl": REGIME_LABELS.get(regime, "?"),
        }
