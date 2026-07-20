"""Stratégie Opus Omnibus V8 — SIGNAL_UP : rebond après excès baissier.

V8 = V7 intégral + SIGNAL_UP comme setup long prioritaire.

Règle de priorité :
  - Quand bearish_excess=True ET p_dir>0.60 → SIGNAL_UP gagne (priorité -1, la plus haute).
    Cela "upgrade" une entrée LONG_CHOPPY/LONG_EXIT_TD/LONG_RANGE_STRICT avec confirmation
    d'excès baissier. En régime Trend Up, SIGNAL_UP ouvre des trades que V7 n'ouvrait pas.
  - Quand bearish_excess=False → les setups V7 (LONG_CHOPPY, LONG_EXIT_TD…) fonctionnent
    exactement comme en V7. Le compte de trades V8 ≥ V7.

  - SIGNAL_UP confluence : chaque fois que SIGNAL_UP se déclenche (bearish_excess=True
    + p_dir>0.60, tout régime) → size_factor×1.5.

Réutilise les modèles V4 pré-entraînés (même pkl + médianes que
``opus_stat_pretrained_v4``).

Setups V8 (par priorité) :
  Priority -1  SIGNAL_UP           tout régime,     p_amp≥0.50, p_dir>0.60, excès baissier requis
  Priority  0  SHORT_TD_HIGH       reg=Trend Down,  p_amp≥0.60, p_dir<0.30  size×1.5
  Priority  1  SHORT_TD            reg=Trend Down,  p_amp≥0.50, p_dir<0.40
  Priority  2  LONG_CHOPPY         reg=Choppy,      p_amp≥0.50, p_dir>0.58  (identique V7)
  Priority  2  SHORT_CHOPPY        reg=Choppy,      p_amp≥0.50, p_dir<0.42
  Priority  3  LONG_EXIT_TD        exit_td_window,  reg≠TD,     p_amp≥0.40  (identique V7)
  Priority  4  LONG_RANGE_STRICT   reg=Range,       p_amp≥0.60, p_dir>0.60
"""

import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import polars as pl

from app.core.indicators import pre_val
from app.core.indicators import safe_num as _safe_num
from app.engine.engine import BaseStrategyML
from app.strategies.opus_stat_pretrained_v4 import (
    REGIME_CHOPPY,
    REGIME_LABELS,
    REGIME_RANGE,
    REGIME_TREND_DN,
    REGIME_TREND_UP,
    _detect_timeframe,
    _FeatureBuilder,
    _last_bar_hour_dow,
    _load_pretrained,
    _prepare_row,
    _to_pandas_window,
)

logger = logging.getLogger(__name__)

_SUPPORTED_TFS = ("15m", "30m", "1h", "4h", "1d")
_EXIT_TD_WINDOW_BARS = 3   # fenêtre LONG_EXIT_TD (bougies)


# ─────────────────────────────────────────────────────────────────────────────
# Définition des 5 setups OMNIBUS V8 — valeurs par défaut, surchargeables YAML
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_SETUPS: Tuple[Dict[str, Any], ...] = (
    # V8 : SIGNAL_UP — priorité -1 → gagne sur tout quand excès baissier confirmé.
    # bearish_excess=True + p_dir>0.60 garantis → size_factor×1.5 automatique.
    {
        "name": "SIGNAL_UP", "priority": -1, "direction": 1, "enabled": True,
        "regime": None,  # tout régime
        "needs_exit_td_window": False,
        "needs_bearish_excess": True,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.60,
        "tp_mult": 1.0, "sl_mult": 1.3, "max_bars": 6, "size_factor": 1.0,
    },
    # V7 setups — inchangés, identiques à opus_omnibus_v7_pretrained
    {
        "name": "SHORT_TD_HIGH", "priority": 0, "direction": -1, "enabled": True,
        "regime": REGIME_TREND_DN, "needs_exit_td_window": False,
        "needs_bearish_excess": False,
        "amp_min": 0.60, "dir_max": 0.30, "dir_min": None,
        "tp_mult": 1.4, "sl_mult": 1.6, "max_bars": 8, "size_factor": 1.5,
    },
    {
        "name": "SHORT_TD", "priority": 1, "direction": -1, "enabled": True,
        "regime": REGIME_TREND_DN, "needs_exit_td_window": False,
        "needs_bearish_excess": False,
        "amp_min": 0.50, "dir_max": 0.40, "dir_min": None,
        "tp_mult": 1.2, "sl_mult": 1.6, "max_bars": 8, "size_factor": 1.0,
    },
    {
        "name": "LONG_CHOPPY", "priority": 2, "direction": 1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_exit_td_window": False,
        "needs_bearish_excess": False,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.58,
        "tp_mult": 0.9, "sl_mult": 1.2, "max_bars": 10, "size_factor": 1.0,
    },
    {
        "name": "SHORT_CHOPPY", "priority": 2, "direction": -1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_exit_td_window": False,
        "needs_bearish_excess": False,
        "amp_min": 0.50, "dir_max": 0.42, "dir_min": None,
        "tp_mult": 1.2, "sl_mult": 1.4, "max_bars": 6, "size_factor": 1.0,
    },
    {
        "name": "LONG_EXIT_TD", "priority": 3, "direction": 1, "enabled": True,
        "regime": None, "needs_exit_td_window": True,
        "needs_bearish_excess": False,
        "amp_min": 0.40, "dir_max": None, "dir_min": None,
        "tp_mult": 1.2, "sl_mult": 1.5, "max_bars": 8, "size_factor": 1.0,
    },
    {
        "name": "LONG_RANGE_STRICT", "priority": 4, "direction": 1, "enabled": True,
        "regime": REGIME_RANGE, "needs_exit_td_window": False,
        "needs_bearish_excess": False,
        "amp_min": 0.60, "dir_max": None, "dir_min": 0.60,
        "tp_mult": 0.8, "sl_mult": 1.2, "max_bars": 6, "size_factor": 1.0,
    },
)
_SETUP_NAMES = tuple(s["name"] for s in _DEFAULT_SETUPS)


def _classify_regime(adx_val: float, bull: int, bear: int,
                     adx_threshold: float = 20.0) -> int:
    if adx_val < adx_threshold:
        return REGIME_RANGE
    if bull == 1:
        return REGIME_TREND_UP
    if bear == 1:
        return REGIME_TREND_DN
    return REGIME_CHOPPY


def _regime_history_from_features(features_df: pd.DataFrame, n_last: int = 5,
                                  adx_threshold: float = 20.0) -> List[int]:
    """Calcule la séquence des régimes sur les `n_last` dernières bougies."""
    sub = features_df.iloc[-n_last:]
    out: List[int] = []
    for _, row in sub.iterrows():
        adx_v = _safe_num(row.get("ADX", 0.0), 0.0)
        bull  = int(_safe_num(row.get("MM_bullish_align", 0), 0.0))
        bear  = int(_safe_num(row.get("MM_bearish_align", 0), 0.0))
        out.append(_classify_regime(adx_v, bull, bear, adx_threshold))
    return out


def _exit_td_window_active(regimes: List[int],
                           window_bars: int = _EXIT_TD_WINDOW_BARS) -> bool:
    """True si le régime est sorti d'un Trend Down au cours des `window_bars`
    dernières bougies (transition 2 → non-2)."""
    n = len(regimes)
    if n < 2:
        return False
    start = max(1, n - window_bars)
    for k in range(start, n):
        if regimes[k] != REGIME_TREND_DN and regimes[k - 1] == REGIME_TREND_DN:
            return True
    return False


def _apply_setup_overrides(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Construit la liste effective des setups en superposant les surcharges YAML.

    Convention de clés YAML : ``setup_<name_lower>_<field>``
    (ex. ``setup_short_td_amp_min``, ``setup_signal_up_enabled``).
    """
    setups: List[Dict[str, Any]] = []
    for src in _DEFAULT_SETUPS:
        s = dict(src)
        prefix = f"setup_{s['name'].lower()}_"
        for field in ("priority", "direction", "amp_min", "dir_max", "dir_min",
                      "tp_mult", "sl_mult", "max_bars", "enabled", "size_factor",
                      "needs_bearish_excess"):
            key = prefix + field
            if key in p and p[key] is not None:
                s[field] = p[key]
        setups.append(s)
    return setups


def _evaluate_setup(setup: Dict[str, Any],
                    regime: int, p_event: float, p_up: float,
                    exit_td_active: bool,
                    bearish_excess: bool = False) -> bool:
    """Renvoie True si toutes les conditions du setup sont satisfaites."""
    if not setup.get("enabled", True):
        return False
    # Régime requis (None → ignore le filtre régime)
    if setup["regime"] is not None and regime != setup["regime"]:
        return False
    # Cas spécial LONG_EXIT_TD : exige la fenêtre + régime ≠ Trend Down
    if setup.get("needs_exit_td_window", False):
        if not exit_td_active:
            return False
        if regime == REGIME_TREND_DN:
            return False
    # Seuil amplitude
    if p_event < float(setup["amp_min"]):
        return False
    # Seuils directionnels
    if setup["dir_max"] is not None and p_up >= float(setup["dir_max"]):
        return False
    if setup["dir_min"] is not None and p_up <= float(setup["dir_min"]):
        return False
    # V8 : condition excès baissier (SIGNAL_UP)
    if setup.get("needs_bearish_excess", False) and not bearish_excess:
        return False
    return True


def _check_early_exit_v7(setup_name: str, regime: int, p_up: float,
                         dir_inv_short: float = 0.55,
                         dir_inv_long: float = 0.40,
                         dir_drop_range: float = 0.40) -> Optional[str]:
    """Sorties anticipées V8 — identique V7 + SIGNAL_UP.

    Conditions par setup :
      SIGNAL_UP (V8)      : p_dir < dir_inv_long    → 'p_dir_drop'
                            régime = TD             → 'to_TD'
      SHORT_TD_HIGH       : régime ≠ TD             → 'regime_exit_TD'
                            p_dir > dir_inv_short   → 'p_dir_inversion'
      SHORT_TD            : régime ≠ TD             → 'regime_exit_TD'
                            p_dir > dir_inv_short   → 'p_dir_inversion'
      LONG_CHOPPY (V7)    : p_dir < dir_inv_long    → 'p_dir_drop'
                            régime = TD             → 'to_TD'
      SHORT_CHOPPY        : régime ≠ Choppy         → 'regime_exit_choppy'
                            p_dir > 0.58            → 'p_dir_inversion'
      LONG_EXIT_TD (V7)   : régime = TD             → 'back_to_TD'
      LONG_RANGE_STRICT   : régime = TD             → 'regime_to_TD'
                            p_dir < dir_drop_range  → 'p_dir_drop'
    """
    if setup_name == "SIGNAL_UP":
        if p_up < dir_inv_long:
            return "p_dir_drop"
        if regime == REGIME_TREND_DN:
            return "to_TD"
    elif setup_name in ("SHORT_TD_HIGH", "SHORT_TD"):
        if regime != REGIME_TREND_DN:
            return "regime_exit_TD"
        if p_up > dir_inv_short:
            return "p_dir_inversion"
    elif setup_name == "LONG_CHOPPY":
        if p_up < dir_inv_long:
            return "p_dir_drop"
        if regime == REGIME_TREND_DN:
            return "to_TD"
    elif setup_name == "SHORT_CHOPPY":
        if regime != REGIME_CHOPPY:
            return "regime_exit_choppy"
        if p_up > 0.58:
            return "p_dir_inversion"
    elif setup_name == "LONG_EXIT_TD":
        if regime == REGIME_TREND_DN:
            return "back_to_TD"
    elif setup_name == "LONG_RANGE_STRICT":
        if regime == REGIME_TREND_DN:
            return "regime_to_TD"
        if p_up < dir_drop_range:
            return "p_dir_drop"
    return None


def _select_setup(setups: List[Dict[str, Any]],
                  regime: int, p_event: float, p_up: float,
                  exit_td_active: bool,
                  bearish_excess: bool = False) -> Optional[Dict[str, Any]]:
    cands = [s for s in setups
             if _evaluate_setup(s, regime, p_event, p_up, exit_td_active, bearish_excess)]
    if not cands:
        return None
    return min(cands, key=lambda s: s["priority"])


# ─────────────────────────────────────────────────────────────────────────────
# Stratégie
# ─────────────────────────────────────────────────────────────────────────────
class Strategy(BaseStrategyML):
    """V8 OMNIBUS — SIGNAL_UP rebond après excès baissier, sur modèles V4 pkl."""

    name      = "opus_omnibus_v8"
    # Dossier de la pkl V4 — pas d'écriture car les modèles sont figés.
    model_dir = os.path.join(os.path.dirname(__file__), "opus_stat_pretrained_v4_data")

    timeframes: List[str] = list(_SUPPORTED_TFS)

    # Seuils optimisables — sous-ensemble des paramètres setup les plus impactants
    param_space: Dict[str, Any] = {
        # SIGNAL_UP (V8 — priorité -1)
        "setup_signal_up_amp_min":        [0.45, 0.50, 0.55],
        "setup_signal_up_dir_min":        [0.55, 0.60, 0.65],
        "setup_signal_up_tp_mult":        [0.8, 1.0, 1.2],
        "setup_signal_up_sl_mult":        [1.1, 1.3, 1.5],
        # SHORT_TD_HIGH (V7)
        "setup_short_td_high_amp_min":    [0.55, 0.60, 0.65],
        "setup_short_td_high_dir_max":    [0.25, 0.30, 0.35],
        # SHORT_TD (V7)
        "setup_short_td_amp_min":         [0.45, 0.50, 0.55],
        "setup_short_td_dir_max":         [0.35, 0.40, 0.45],
        "setup_short_td_tp_mult":         [1.0, 1.2, 1.4],
        "setup_short_td_sl_mult":         [1.4, 1.6, 1.8],
        # LONG_CHOPPY (V7 — identique)
        "setup_long_choppy_amp_min":      [0.45, 0.50, 0.55],
        "setup_long_choppy_dir_min":      [0.55, 0.58, 0.62],
        # SHORT_CHOPPY (V7)
        "setup_short_choppy_amp_min":     [0.45, 0.50, 0.55],
        "setup_short_choppy_dir_max":     [0.38, 0.42, 0.46],
        # LONG_RANGE_STRICT (V7)
        "setup_long_range_strict_amp_min":[0.55, 0.60, 0.65],
        "setup_long_range_strict_dir_min":[0.55, 0.60, 0.65],
        "exit_td_window_bars":            [2, 3, 4],
    }
    fixed_params: Dict[str, Any] = {}

    _DEFAULTS = {
        "enable_hour_filter":  True,
        "active_hours_utc":    list(range(13, 21)),
        "active_days":         [0, 1, 2, 3, 4],
        "adx_threshold":       20.0,
        "exit_td_window_bars": _EXIT_TD_WINDOW_BARS,
        "disable_trailing":    True,
        "use_fixed_tp":        True,
        # V8 : seuils de détection de l'excès baissier (configurables YAML)
        "bearish_excess_rsi_threshold": 38.0,   # RSI < X (spec originale)
        "bearish_excess_sma_pct":        1.5,   # prix > X% sous SMA20 (spec originale)
    }

    _FEATURE_BUILDER = _FeatureBuilder()
    retrain_interval_h: int = 24 * 365  # jamais (modèles figés)

    def __init__(self):
        self._lock = threading.Lock()
        self._models:  Dict[tuple, Any] = {}
        self._medians: Dict[tuple, Dict[str, float]] = {}
        self._loaded = False
        self._managed_externally = True
        self._best_auc            = 0.0
        self._best_auc_per_tf: Dict[str, float] = {}
        self._train_meta:      Dict[str, dict]  = {}
        # Cache backtest : voir opus_stat_pretrained_v4 pour la motivation.
        self._bt_features_pdf = None
        self._bt_features_len = 0
        self._ensure_loaded()

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Pré-calcule les features V4 pour toute la fenêtre de backtest."""
        try:
            from app.core.feature_store import cached_strategy_features
            _ohlcv = ("time", "open", "high", "low", "close", "volume")
            feats = cached_strategy_features(
                getattr(self, "_bt_symbol", None), getattr(self, "_bt_tf", None), df,
                name="opus_v4_pandas", version="1",
                builder=lambda pdf: self._FEATURE_BUILDER.build(
                    pdf[[c for c in _ohlcv if c in pdf.columns]]),
                in_kind="pandas", out_kind="pandas")
            self._bt_features_pdf = feats
            self._bt_features_len = len(df) if feats is not None else 0
            logger.info(
                f"[OmnibusV8] backtest : features pré-calculées sur "
                f"{self._bt_features_len} bougies "
                f"({(feats.shape[1] if feats is not None else 0)} colonnes)"
            )
        except Exception as e:
            logger.warning(f"[OmnibusV8] prepare_for_backtest KO : {e}")
            self._bt_features_pdf = None
            self._bt_features_len = 0

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
                self._best_auc_per_tf = {"15m": 0.626, "30m": 0.597, "1h": 0.603}
                self._best_auc        = max(self._best_auc_per_tf.values())
                for tf in _SUPPORTED_TFS:
                    self._train_meta[tf] = {
                        "auc_amp": {"15m": 0.749, "30m": 0.690, "1h": 0.676}.get(tf, 0.0),
                        "auc_dir": {"15m": 0.503, "30m": 0.504, "1h": 0.530}.get(tf, 0.0),
                        "source":  "v4_models.pkl (pré-entraîné V4, partagé avec opus_stat_pretrained_v4)",
                    }
            return True
        except Exception as e:
            logger.error(f"[OmnibusV8] Chargement modèles V4 KO : {e}")
            return False

    @property
    def is_trained(self) -> bool:
        return self._loaded

    @property
    def managed_externally(self) -> bool:
        return True

    @managed_externally.setter
    def managed_externally(self, _v: bool) -> None:
        return

    def min_bars_required(self, params: dict = None) -> int:
        return 230  # FeatureBuilder a besoin de 210 + marge lags

    def reset_model(self) -> None:
        self._bt_features_pdf = None
        self._bt_features_len = 0

    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        self._ensure_loaded()

    def save_model(self, path: str) -> None:
        return

    def load_model(self, path: str = None) -> bool:
        return self._ensure_loaded()

    # ── Prédictions (API V4 publique) ──────────────────────────────────────
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
            logger.warning(f"[OmnibusV8] Prédiction {key} KO : {e}")
            return None

    def predict_amplitude(self, features_df: pd.DataFrame, tf: str) -> Optional[float]:
        return self._predict(features_df, tf, "amp")

    def predict_direction(self, features_df: pd.DataFrame, tf: str) -> Optional[float]:
        return self._predict(features_df, tf, "dir")

    # ── Cœur du signal ─────────────────────────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if not self._ensure_loaded():
            return self._none("Modèles V4 indisponibles")

        if df is None or len(df) < self.min_bars_required():
            return self._none(f"Données insuffisantes ({len(df) if df is not None else 0})")

        p = (params or {}).get(self.name, {})
        enable_hour_filter = bool(p.get("enable_hour_filter", self._DEFAULTS["enable_hour_filter"]))
        active_hours_utc   = list(p.get("active_hours_utc",   self._DEFAULTS["active_hours_utc"]))
        active_days        = list(p.get("active_days",        self._DEFAULTS["active_days"]))
        adx_threshold      = float(p.get("adx_threshold",     self._DEFAULTS["adx_threshold"]))
        exit_td_window_bars = int(p.get("exit_td_window_bars", self._DEFAULTS["exit_td_window_bars"]))
        disable_trailing   = bool(p.get("disable_trailing",   self._DEFAULTS["disable_trailing"]))
        use_fixed_tp       = bool(p.get("use_fixed_tp",       self._DEFAULTS["use_fixed_tp"]))

        # 1. Filtre temporel V4
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

        # 2. Détection TF
        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return self._none(
                f"Timeframe non supporté (détecté={tf}, attendus={_SUPPORTED_TFS})"
            )

        # 3. Features V4 — fast-path backtest si pré-calculé.
        if self._bt_features_pdf is not None and len(df) <= self._bt_features_len:
            features = self._bt_features_pdf.iloc[: len(df)]
        else:
            pdf      = _to_pandas_window(df, n=max(260, self.min_bars_required() + 20))
            features = self._FEATURE_BUILDER.build(pdf)
        if features is None or len(features) == 0:
            return self._none("Construction des features V4 impossible")

        last_row = features.iloc[-1]
        atr_v    = _safe_num(last_row.get("ATR_14", 0.0), 0.0)
        if not np.isfinite(atr_v) or atr_v <= 0:
            atr_v = float(pre_val(df, "_pre_atr14") or 0.0)
        c_now    = float(df["close"][-1] or 0.0)
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        # 4. Régime courant + historique pour exit_td_window
        regime_history = _regime_history_from_features(
            features, n_last=max(exit_td_window_bars + 2, 5),
            adx_threshold=adx_threshold,
        )
        regime         = regime_history[-1]
        regime_lbl     = REGIME_LABELS[regime]
        exit_td_active = _exit_td_window_active(regime_history, exit_td_window_bars)

        # 5. Prédictions V4
        p_event = self.predict_amplitude(features, tf)
        p_up    = self.predict_direction(features, tf)
        if p_event is None or p_up is None:
            return self._none(f"Modèle {tf} indisponible")

        # === V8 : Excès baissier (calcul O(1), pas de to_numpy global) ===
        be_rsi_thr = float(p.get("bearish_excess_rsi_threshold", 38.0))
        be_sma_pct = float(p.get("bearish_excess_sma_pct", 1.5))

        # 1. 2+ bougies rouges consécutives — indexation directe Polars
        if len(df) >= 2:
            consec_red = bool(
                float(df["close"][-1]) < float(df["open"][-1]) and
                float(df["close"][-2]) < float(df["open"][-2])
            )
        else:
            consec_red = False

        # 2. RSI(14) < seuil configurable — lit le RSI déjà calculé dans les features
        rsi_v = _safe_num(last_row.get("RSI_14", 50.0), 50.0)
        rsi_excess = rsi_v < be_rsi_thr

        # 3. Prix > X% sous SMA(20) — fallback on-demand si precompute absent
        sma20_v = float(pre_val(df, "_pre_sma20") or 0.0)
        if sma20_v <= 0 and len(df) >= 20:
            sma20_v = float(df["close"].rolling_mean(20)[-1] or 0.0)
        price_below_sma20 = (c_now < sma20_v * (1.0 - be_sma_pct / 100.0)) if sma20_v > 0 else False

        bearish_excess = consec_red or rsi_excess or price_below_sma20

        # 6. Sélection du setup (priorité ascendante)
        setups = _apply_setup_overrides(p)
        setup  = _select_setup(setups, regime, p_event, p_up, exit_td_active, bearish_excess)
        if setup is None:
            return self._none(
                f"Aucun setup actif | regime={regime_lbl} p_event={p_event:.2f} "
                f"p_up={p_up:.2f} exit_td={exit_td_active} bearish_excess={bearish_excess}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        # V8 : bonus SIGNAL_UP — bearish_excess=True ET p_dir>0.60 sont déjà
        # garantis par _evaluate_setup, donc le bonus s'applique sur tous les régimes.
        long_choppy_confluence = False
        if setup["name"] == "SIGNAL_UP":
            setup = dict(setup)  # mutable copy avant mutation
            setup["size_factor"] = float(setup.get("size_factor", 1.0)) * 1.5
            long_choppy_confluence = True

        # 7. Construction du signal — mults TP/SL/max_bars du setup retenu
        side = "long" if setup["direction"] == 1 else "short"
        sl_atr_mult = float(setup["sl_mult"])
        tp_atr_mult = float(setup["tp_mult"])
        max_bars    = int(setup["max_bars"])

        # Score = note de confiance (utilisé par l'engine pour ordonner les signaux)
        priority_bonus = max(0, 4 - int(setup["priority"])) * 0.04
        confidence     = abs(p_up - 0.5) * 2.0
        score_val      = round(min(0.55 + p_event * confidence * 0.30 + priority_bonus, 0.94), 3)

        meta = self._train_meta.get(tf, {})

        size_factor = float(setup.get("size_factor", 1.0))

        sig: Dict[str, Any] = {
            "score":            score_val,
            "side":             side,
            "name":             self.name,
            "atr":              atr_v,
            "sl_atr_mult":      sl_atr_mult,
            "disable_trailing": disable_trailing,
            "size_factor":      size_factor,
            "exit_after_bars":  max_bars,
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "tf_detected":      tf,
            "setup":            setup["name"],
            "setup_priority":   int(setup["priority"]),
            "exit_td_active":   bool(exit_td_active),
            "bearish_excess":   bool(bearish_excess),
            "long_choppy_confluence": bool(long_choppy_confluence),
            "consec_red":       bool(consec_red),
            "rsi_excess":       bool(rsi_excess),
            "price_below_sma20": bool(price_below_sma20),
        }
        if use_fixed_tp:
            sig["tp_atr_mult"] = tp_atr_mult

        sig["indicators"] = {
            "adx":              round(_safe_num(last_row.get("ADX", 0.0), 0.0), 1),
            "rsi":              round(rsi_v, 1),
            "sma20":            round(sma20_v, 4) if sma20_v > 0 else None,
            "bearish_excess":   bool(bearish_excess),
            "long_choppy_confluence": bool(long_choppy_confluence),
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "setup":            setup["name"],
            "setup_priority":   int(setup["priority"]),
            "exit_td_active":   bool(exit_td_active),
            "sl_mult":          sl_atr_mult,
            "tp_mult":          tp_atr_mult if use_fixed_tp else None,
            "max_bars":         max_bars,
            "auc_amp":          meta.get("auc_amp", 0.0),
            "auc_dir":          meta.get("auc_dir", 0.0),
        }

        conditions = [
            f"Setup V8 retenu : {setup['name']} (priorité {setup['priority']})",
            f"Régime : {regime_lbl} | exit_td_window={exit_td_active}",
            f"P(événement)={p_event:.2f} ≥ {setup['amp_min']:.2f} ✓",
            (f"P(hausse)={p_up:.2f} < {setup['dir_max']:.2f} ✓"
             if setup.get("dir_max") is not None else
             f"P(hausse)={p_up:.2f} > {setup['dir_min']:.2f} ✓"
             if setup.get("dir_min") is not None else
             f"P(hausse)={p_up:.2f} (pas de seuil dir)"),
            f"Risque : SL {sl_atr_mult:.2f}×ATR | TP {tp_atr_mult:.2f}×ATR | "
            f"max {max_bars} bougies",
            f"Modèles V4 pré-entraînés : AUC amp={meta.get('auc_amp', 0):.2f} / "
            f"dir={meta.get('auc_dir', 0):.2f}",
        ]
        if setup["name"] == "SIGNAL_UP":
            conditions.append(
                f"Excès baissier : rouge×2={consec_red} | RSI<38={rsi_excess} | Prix<SMA20-1.5%={price_below_sma20}"
            )

        sig["conditions"] = conditions
        sig["reason"] = (
            f"OmnibusV8 {setup['name']} {side.upper()} | {regime_lbl} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f} bearish_excess={bearish_excess}"
        )
        return sig

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        return self.score(df, params)

    # ── Sortie anticipée V8 ────────────────────────────────────────────────
    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        """Recalcule le régime + p_dir à la barre courante et détermine si le
        setup ayant ouvert la position est devenu invalide."""
        # 1. Identifier le setup d'origine
        setup_name = position.get("setup")
        if not setup_name:
            ind = position.get("indicators") or {}
            setup_name = ind.get("setup")
        if not setup_name:
            return None

        if not self._ensure_loaded():
            return None
        if df is None or len(df) < self.min_bars_required():
            return None

        p = (params or {}).get(self.name, {})
        adx_threshold = float(p.get("adx_threshold", self._DEFAULTS["adx_threshold"]))
        dir_inv_short  = float(p.get("early_exit_dir_inv_short",  0.55))
        dir_inv_long   = float(p.get("early_exit_dir_inv_long",   0.45))
        dir_drop_range = float(p.get("early_exit_dir_drop_range", 0.40))

        # 2. Détection TF
        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return None

        # 3. Recalculer features + régime + p_dir à la barre courante
        try:
            if self._bt_features_pdf is not None and len(df) <= self._bt_features_len:
                features = self._bt_features_pdf.iloc[: len(df)]
            else:
                pdf      = _to_pandas_window(df, n=max(260, self.min_bars_required() + 20))
                features = self._FEATURE_BUILDER.build(pdf)
            if features is None or len(features) == 0:
                return None
            last_row = features.iloc[-1]
            regime = _classify_regime(
                _safe_num(last_row.get("ADX", 0.0), 0.0),
                int(_safe_num(last_row.get("MM_bullish_align", 0), 0.0)),
                int(_safe_num(last_row.get("MM_bearish_align", 0), 0.0)),
                adx_threshold,
            )
            p_up = self.predict_direction(features, tf)
            if p_up is None:
                return None
        except Exception as e:
            logger.warning(f"[OmnibusV8] check_early_exit recompute KO : {e}")
            return None

        # 4. Décision basée sur le setup
        return _check_early_exit_v7(
            setup_name, regime, p_up,
            dir_inv_short=dir_inv_short,
            dir_inv_long=dir_inv_long,
            dir_drop_range=dir_drop_range,
        )

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
