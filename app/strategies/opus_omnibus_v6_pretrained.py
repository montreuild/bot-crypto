"""Stratégie Opus Omnibus V6 (pré-entraîné) — système 5 setups sur modèle V4.

Reproduit le pipeline V6.1 du fichier `23_v6_1_final.py` :

  - Réutilise les modèles V4 pré-entraînés (mêmes pkl + médianes que
    ``opus_stat_pretrained_v4``).
  - Au lieu d'une règle unique (V4) ou de seuils par régime, V6.1 combine
    **5 setups complémentaires** avec priorités :

      Priority 1  SHORT_TD            reg=Trend Down,    p_amp≥0.50, p_dir<0.40
      Priority 2  SHORT_CHOPPY        reg=Choppy,        p_amp≥0.50, p_dir<0.45
      Priority 2  LONG_CHOPPY         reg=Choppy,        p_amp≥0.50, p_dir>0.55
      Priority 3  LONG_EXIT_TD        exit_td_window,    reg≠TD,     p_amp≥0.40
      Priority 4  LONG_RANGE_STRICT   reg=Range,         p_amp≥0.60, p_dir>0.60

    Le setup retenu pour une bougie donnée est celui de plus petite priorité
    parmi ceux dont les conditions sont satisfaites.

  - Chaque setup a son propre couple ``tp_mult`` / ``sl_mult`` (multiplicateurs
    ATR appliqués au prix d'exécution) et son ``max_bars`` (sortie temporelle).

  - ``exit_td_window`` : fenêtre de 3 bougies à partir du moment où le régime
    Trend Down se termine — permet de capter le rebond technique.

  - Filtre horaire : 13h-20h UTC (session US), comme V4.

Limitations vs V6.1 source :
  - **Sorties anticipées** (``should_exit_early`` : changement de régime,
    inversion p_dir pendant la position) ne sont pas portées. L'engine
    bot-crypto ne fournit pas de hook pour clore une position ouverte depuis
    la stratégie. Le filet de sécurité ``max_bars`` reste actif via
    ``exit_after_bars``, et le SL fixe limite la perte.
  - **Cooldown / loss streak / daily limit** : déjà gérés au niveau du
    ``RiskManager`` du bot (clés ``consecutive_loss_limit``,
    ``reentry_cooldown_bars``, etc.).
"""

import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import polars as pl

from app.engine.engine import BaseStrategyML
from app.core.indicators import pre_val
from app.strategies.opus_stat_pretrained_v4 import (
    _FeatureBuilder,
    _detect_timeframe,
    _last_bar_hour_dow,
    _to_pandas_window,
    _load_pretrained,
    _prepare_row,
    REGIME_RANGE, REGIME_TREND_UP, REGIME_TREND_DN, REGIME_CHOPPY,
    REGIME_LABELS,
)

logger = logging.getLogger(__name__)

_SUPPORTED_TFS = ("15m", "30m", "1h")
_EXIT_TD_WINDOW_BARS = 3   # fenêtre LONG_EXIT_TD (bougies)


# ─────────────────────────────────────────────────────────────────────────────
# Définition des 5 setups OMNIBUS V6.1 — valeurs par défaut, surchargeables YAML
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_SETUPS: Tuple[Dict[str, Any], ...] = (
    {
        "name": "SHORT_TD",     "priority": 1, "direction": -1, "enabled": True,
        "regime": REGIME_TREND_DN, "needs_exit_td_window": False,
        "amp_min": 0.50, "dir_max": 0.40, "dir_min": None,
        "tp_mult": 1.2,  "sl_mult": 1.6,  "max_bars": 8,
    },
    {
        "name": "SHORT_CHOPPY", "priority": 2, "direction": -1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_exit_td_window": False,
        "amp_min": 0.50, "dir_max": 0.45, "dir_min": None,
        "tp_mult": 1.0,  "sl_mult": 1.4,  "max_bars": 6,
    },
    {
        "name": "LONG_CHOPPY",  "priority": 2, "direction":  1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_exit_td_window": False,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.55,
        "tp_mult": 1.0,  "sl_mult": 1.4,  "max_bars": 6,
    },
    {
        "name": "LONG_EXIT_TD", "priority": 3, "direction":  1, "enabled": True,
        "regime": None,  "needs_exit_td_window": True,
        # Particularité : régime ≠ Trend Down (la fenêtre s'ouvre quand on sort de TD)
        "amp_min": 0.40, "dir_max": None, "dir_min": None,
        "tp_mult": 1.2,  "sl_mult": 1.5,  "max_bars": 8,
    },
    {
        "name": "LONG_RANGE_STRICT", "priority": 4, "direction":  1, "enabled": True,
        "regime": REGIME_RANGE, "needs_exit_td_window": False,
        "amp_min": 0.60, "dir_max": None, "dir_min": 0.60,
        "tp_mult": 0.8,  "sl_mult": 1.2,  "max_bars": 6,
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
        adx_v = float(row.get("ADX", 0.0) or 0.0)
        bull  = int(row.get("MM_bullish_align", 0) or 0)
        bear  = int(row.get("MM_bearish_align", 0) or 0)
        out.append(_classify_regime(adx_v, bull, bear, adx_threshold))
    return out


def _exit_td_window_active(regimes: List[int],
                           window_bars: int = _EXIT_TD_WINDOW_BARS) -> bool:
    """True si le régime est sorti d'un Trend Down au cours des `window_bars`
    dernières bougies (transition 2 → non-2)."""
    n = len(regimes)
    if n < 2:
        return False
    # On cherche une transition à l'index k tel que (n-1-k) < window_bars,
    # i.e., k ∈ [n-window_bars, n-1].
    start = max(1, n - window_bars)
    for k in range(start, n):
        if regimes[k] != REGIME_TREND_DN and regimes[k - 1] == REGIME_TREND_DN:
            return True
    return False


def _apply_setup_overrides(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Construit la liste effective des setups en superposant les surcharges YAML.

    Convention de clés YAML : ``setup_<name_lower>_<field>``
    (ex. ``setup_short_td_amp_min``, ``setup_long_choppy_enabled``).
    """
    setups: List[Dict[str, Any]] = []
    for src in _DEFAULT_SETUPS:
        s = dict(src)
        prefix = f"setup_{s['name'].lower()}_"
        for field in ("priority", "direction", "amp_min", "dir_max", "dir_min",
                      "tp_mult", "sl_mult", "max_bars", "enabled"):
            key = prefix + field
            if key in p and p[key] is not None:
                s[field] = p[key]
        setups.append(s)
    return setups


def _evaluate_setup(setup: Dict[str, Any],
                    regime: int, p_event: float, p_up: float,
                    exit_td_active: bool) -> bool:
    """Renvoie True si toutes les conditions du setup sont satisfaites."""
    if not setup.get("enabled", True):
        return False
    # Régime requis (None → ignore le filtre régime)
    if setup["regime"] is not None and regime != setup["regime"]:
        return False
    # Cas spécial LONG_EXIT_TD : exige la fenêtre + régime ≠ Trend Down
    if setup["needs_exit_td_window"]:
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
    return True


def _select_setup(setups: List[Dict[str, Any]],
                  regime: int, p_event: float, p_up: float,
                  exit_td_active: bool) -> Optional[Dict[str, Any]]:
    cands = [s for s in setups
             if _evaluate_setup(s, regime, p_event, p_up, exit_td_active)]
    if not cands:
        return None
    return min(cands, key=lambda s: s["priority"])


# ─────────────────────────────────────────────────────────────────────────────
# Stratégie
# ─────────────────────────────────────────────────────────────────────────────
class Strategy(BaseStrategyML):
    """V6.1 OMNIBUS — 5 setups avec routing par priorité, sur modèles V4 pkl."""

    name      = "opus_omnibus_v6_pretrained"
    # Dossier de la pkl V4 — pas d'écriture car les modèles sont figés.
    model_dir = os.path.join(os.path.dirname(__file__), "opus_stat_pretrained_v4_data")

    timeframes: List[str] = list(_SUPPORTED_TFS)

    # Seuils optimisables — sous-ensemble des paramètres setup les plus impactants
    param_space: Dict[str, Any] = {
        "setup_short_td_amp_min":         [0.45, 0.50, 0.55],
        "setup_short_td_dir_max":         [0.35, 0.40, 0.45],
        "setup_short_td_tp_mult":         [1.0, 1.2, 1.4],
        "setup_short_td_sl_mult":         [1.4, 1.6, 1.8],
        "setup_short_choppy_amp_min":     [0.45, 0.50, 0.55],
        "setup_short_choppy_dir_max":     [0.40, 0.45, 0.50],
        "setup_long_choppy_amp_min":      [0.45, 0.50, 0.55],
        "setup_long_choppy_dir_min":      [0.50, 0.55, 0.60],
        "setup_long_exit_td_amp_min":     [0.35, 0.40, 0.45],
        "setup_long_exit_td_max_bars":    [4, 6, 8, 10],
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
        "disable_trailing":    True,   # V6.1 utilise SL fixe (pas de trailing)
        "use_fixed_tp":        True,
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
            logger.error(f"[OmnibusV6-PT] Chargement modèles V4 KO : {e}")
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
        return  # figé

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
            logger.warning(f"[OmnibusV6-PT] Prédiction {key} KO : {e}")
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

        # 3. Features V4
        pdf      = _to_pandas_window(df, n=max(260, self.min_bars_required() + 20))
        features = self._FEATURE_BUILDER.build(pdf)
        if features is None or len(features) == 0:
            return self._none("Construction des features V4 impossible")

        last_row = features.iloc[-1]
        atr_v    = float(last_row.get("ATR_14", 0.0) or 0.0)
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

        # 6. Sélection du setup (priorité ascendante)
        setups = _apply_setup_overrides(p)
        setup  = _select_setup(setups, regime, p_event, p_up, exit_td_active)
        if setup is None:
            return self._none(
                f"Aucun setup actif | regime={regime_lbl} p_event={p_event:.2f} "
                f"p_up={p_up:.2f} exit_td={exit_td_active}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        # 7. Construction du signal — mults TP/SL/max_bars du setup retenu
        side = "long" if setup["direction"] == 1 else "short"
        sl_atr_mult = float(setup["sl_mult"])
        tp_atr_mult = float(setup["tp_mult"])
        max_bars    = int(setup["max_bars"])

        # Score = note de confiance (utilisé par l'engine pour ordonner les signaux)
        # On donne plus de poids aux priorités basses (= meilleures).
        priority_bonus = (5 - int(setup["priority"])) * 0.04
        confidence     = abs(p_up - 0.5) * 2.0
        score_val      = round(min(0.55 + p_event * confidence * 0.30 + priority_bonus, 0.94), 3)

        meta = self._train_meta.get(tf, {})

        sig: Dict[str, Any] = {
            "score":            score_val,
            "side":             side,
            "name":             self.name,
            "atr":              atr_v,
            "sl_atr_mult":      sl_atr_mult,
            "disable_trailing": disable_trailing,
            "size_factor":      1.0,    # V6.1 = taille fixe % capital, pas de Kelly
            "exit_after_bars":  max_bars,  # max_bars du setup = filet de sécurité temporel
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "tf_detected":      tf,
            "setup":            setup["name"],
            "setup_priority":   int(setup["priority"]),
            "exit_td_active":   bool(exit_td_active),
        }
        if use_fixed_tp:
            sig["tp_atr_mult"] = tp_atr_mult

        sig["indicators"] = {
            "adx":              round(float(last_row.get("ADX", 0.0) or 0.0), 1),
            "rsi":              round(float(last_row.get("RSI_14", 50.0) or 50.0), 1),
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
        sig["conditions"] = [
            f"Setup V6.1 retenu : {setup['name']} (priorité {setup['priority']})",
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
        sig["reason"] = (
            f"OmnibusV6-PT {setup['name']} {side.upper()} | {regime_lbl} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f}"
        )
        return sig

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
