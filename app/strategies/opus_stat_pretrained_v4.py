"""Stratégie Opus Stat Pretrained V4 — utilise les modèles LightGBM V4 déjà entraînés.

Contrairement aux stratégies `scoring_statistique_opus_v*`, **aucun entraînement
n'est effectué** (ni inline dans l'optimiseur, ni périodique en live). Les six
modèles LightGBM (15m/30m/1h × amplitude/direction) du pack V4 sont chargés une
fois pour toutes depuis les fichiers natifs `.lgb` + `.json` dans
``app/strategies/opus_stat_pretrained_v4_data/``.

Pipeline :

  1. Détection du timeframe (15m / 30m / 1h) à partir des deltas de ``df['time']``
  2. Construction des ~462 features V4 via ``MLBackend.build_features`` (Polars)
     — équivalent byte-à-byte du ``_FeatureBuilder`` pandas originel (vérifié
     à 3.19e-07 près, cf. ``scripts/check_pandas_polars_equivalence.py``).
     Les features restent en Polars de bout en bout (``booster.predict``
     reçoit un ``np.ndarray``).
  3. Détection du régime ADX + alignement SMA (Range / Trend Up / Trend Down /
     Choppy). Trend Up → pas de signal (AUC ≈ 0.50).
  4. P(événement) et P(hausse) prédits par les boosters natifs (sans wrapper
     sklearn — élimine le risque RCE du pkl original).
  5. Décision : seuils plus stricts hors Trend Down (cf. rapport V4 §6.4).
  6. Sortie : stop initial = 1.5×ATR (SL) + trailing manager (TP par trailing).

Comme la stratégie est entièrement statique, ``managed_externally`` est forcé à
``True`` : le ``MLStrategyTrainer`` ne tentera jamais de la réentraîner et
l'optimiseur ne fera que varier les seuils de décision.

Migration ARCH-012 + SEC-020 :
  - Le pkl ``v4_models.pkl`` (8,8 Mo, RCE-vulnérable via ``pickle.load``) a été
    converti en 8 fichiers ``.lgb`` + 8 fichiers ``.json`` via
    ``scripts/convert_v4_pretrained.py``.
  - Le ``_FeatureBuilder`` pandas (~290 L) a été supprimé, remplacé par
    ``MLBackend.build_features`` (Polars) — source unique.

Migration phase6-pandas-removal :
  - Suppression de toute dépendance à ``pandas`` : les features V4 restent en
    Polars de bout en bout, ``_prepare_row`` renvoie un ``np.ndarray`` direct
    (LightGBM accepte nativement un ndarray), et le catalogue FeatureStore
    passe de ``opus_v4_pandas`` à ``opus_v4_polars`` (version ``1``) pour
    invalider le cache disque pandas résiduel.
"""

import json
import logging
import os
import threading
import warnings
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from app.core.indicators import pre_val
from app.core.indicators import safe_num as _safe_num
from app.engine.engine import BaseStrategyML
from app.ml.backend import (
    REGIME_CHOPPY,
    REGIME_LABELS,
    REGIME_RANGE,
    REGIME_TREND_DN,
    REGIME_TREND_UP,
    SUPPORTED_TFS,
    MLBackend,
)
from app.ml.backend import (
    build_features as _build_features_polars,
)
from app.ml.backend import (
    detect_timeframe as _detect_timeframe,
)
from app.ml.backend import (
    last_bar_hour_dow as _last_bar_hour_dow,
)
from app.ml.backend import (
    window_polars as _window_polars,
)

warnings.filterwarnings("ignore", category=UserWarning, module="lightgbm")

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Chemins des artefacts embarqués (format natif LGB + JSON, plus de pkl)
# ─────────────────────────────────────────────────────────────────────────────
_DATA_DIR     = os.path.join(os.path.dirname(__file__), "opus_stat_pretrained_v4_data")
_MEDIANS_PATH = os.path.join(_DATA_DIR, "v4_medians.json")

# Alias de régimes (compat — consommateurs historiques importent ces symboles)
_SUPPORTED_TFS = SUPPORTED_TFS


# ─────────────────────────────────────────────────────────────────────────────
# Chargeur des modèles V4 natifs (singleton process-wide)
# ─────────────────────────────────────────────────────────────────────────────
_PRETRAINED_CACHE: dict = {"models": None, "medians": None}
_PRETRAINED_LOCK = threading.Lock()


def _load_pretrained() -> tuple:
    """Charge (une seule fois par process) les boosters natifs + médianes JSON.

    Remplace l'ancien ``pickle.load(v4_models.pkl)`` (RCE-vulnérable) par un
    chargement de fichiers LightGBM natifs (``.lgb``) + JSON (``.json``) —
    sans exécution de code arbitraire à la désérialisation.

    Retourne ``(models, medians)`` où :
      - ``models`` : ``Dict[Tuple[tf, target, config], dict]`` avec
        ``{"model": lgb.Booster, "features": List[str], "split_idx": int, ...}``
      - ``medians`` : ``Dict[Tuple[tf, target], Dict[str, float]]``
    """
    with _PRETRAINED_LOCK:
        if _PRETRAINED_CACHE["models"] is not None:
            return _PRETRAINED_CACHE["models"], _PRETRAINED_CACHE["medians"]

        # 1. Charger les 8 fichiers natifs (.lgb + .json).
        # Ignorer v4_medians.json (ce n'est pas un modèle, juste les médianes).
        try:
            import lightgbm as lgb
        except ImportError:
            raise RuntimeError("lightgbm requis pour opus_stat_pretrained_v4")

        models: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for fname in sorted(os.listdir(_DATA_DIR)):
            # Format attendu : v4_{tf}_{target}_{config}.json
            # (ex: v4_15m_amp_single.json) — on exclut v4_medians.json.
            if not fname.endswith(".json") or not fname.startswith("v4_"):
                continue
            if fname == "v4_medians.json":
                continue
            meta_path = os.path.join(_DATA_DIR, fname)
            lgb_path  = meta_path[:-5] + ".lgb"  # remplace .json par .lgb
            if not os.path.exists(lgb_path):
                logger.warning(f"[OpusV4-PT] {fname} sans .lgb correspondant — skip")
                continue
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                booster = lgb.Booster(model_file=lgb_path)
                tf      = meta["tf"]
                target  = meta["target"]
                config  = meta["config"]
                models[(tf, target, config)] = {
                    "model":      booster,
                    "features":   list(meta.get("features") or []),
                    "split_idx":  int(meta.get("split_idx", 0)),
                    "tf":         tf,
                    "target":     target,
                    "config":     config,
                    "n_features_in": int(meta.get("n_features_in", 0)),
                    "n_classes":  int(meta.get("n_classes", 2)),
                    "classes_":   list(meta.get("classes_", [0, 1])),
                }
            except Exception as e:
                logger.warning(f"[OpusV4-PT] Chargement {fname} KO : {e}")

        if not models:
            raise FileNotFoundError(
                f"Aucun modèle natif V4 trouvé dans {_DATA_DIR} — exécutez "
                f"scripts/convert_v4_pretrained.py pour migrer le pkl legacy."
            )

        # 2. Charger les médianes (déjà JSON).
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
            "[OpusV4-PT] Modèles V4 natifs chargés "
            f"({len(models)} entrées, {len(medians)} sets de médianes) — "
            "format RCE-safe (.lgb + .json)"
        )
    return _PRETRAINED_CACHE["models"], _PRETRAINED_CACHE["medians"]


def _prepare_row(features_df: pl.DataFrame,
                 feat_names: List[str],
                 medians: dict) -> np.ndarray:
    """Extrait la dernière ligne ; impute NaN/inf via les médianes du train.

    Renvoie un ``np.ndarray`` 2D ``(1, n_features)`` directement consommable
    par ``lightgbm.Booster.predict`` (natif — pas de wrapper sklearn). Les
    valeurs manquantes sont remplacées par les médianes du set d'entraînement
    (cf. ``v4_medians.json``).
    """
    last = features_df.row(-1, named=True)  # dict col → valeur (None si absent)
    row = np.empty(len(feat_names), dtype=np.float64)
    for i, f in enumerate(feat_names):
        val = last.get(f)
        if val is None:
            val = medians.get(f, 0.0)
        try:
            v = float(val)
        except (TypeError, ValueError):
            v = float(medians.get(f, 0.0))
        if not np.isfinite(v):
            v = float(medians.get(f, 0.0))
        row[i] = v
    return row.reshape(1, -1)


def _build_features(raw_df: pl.DataFrame) -> Optional[pl.DataFrame]:
    """Construit les ~462 features V4 en Polars via ``MLBackend.build_features``.

    ARCH-012 + phase6-pandas-removal : le builder Polars de ``MLBackend`` est la
    source unique. Plus de conversion finale en pandas — les features restent
    en Polars de bout en bout (``_prepare_row`` en extrait la dernière ligne
    et renvoie un ``np.ndarray`` pour ``booster.predict``).
    """
    if len(raw_df) < 210:
        return None
    return _build_features_polars(raw_df)


# Alias maintenu pour compat (scanner_service importait _to_pandas_window).
# Retourne un pl.DataFrame OHLCV+time (équivalent Polars de l'ancienne version).
def _to_pandas_window(df: pl.DataFrame, n: int = 260) -> pl.DataFrame:
    """Déprécié — alias retro-compat de ``MLBackend.window_polars``.

    Retourne les ``n`` dernières lignes en Polars avec les colonnes OHLCV+time.
    Conservé temporairement pour ``app.api.services.scanner_service`` qui
    consommait l'ancienne version pandas ; migrera vers ``MLBackend.window_polars``
    au prochain passage.
    """
    return _window_polars(df, n)


class _FeatureBuilder:
    """Compatibilité ARCH-012 — wrapper autour de ``_build_features``.

    Historiquement, cette classe était un builder pandas de ~290 L dupliqué
    dans ``opus_stat_pretrained_v4.py``. Le code est désormais factorisé dans
    ``app.ml.backend.features.build_features`` (Polars). Depuis phase6, elle
    préserve l'API ``.build(raw_df: pl.DataFrame) -> Optional[pl.DataFrame]``
    attendue par les stratégies V8/V9/V10/V7_pretrained.

    Équivalence byte-à-byte vérifiée à 3.19e-07 près (< 1e-6) — cf.
    ``scripts/check_pandas_polars_equivalence.py``.
    """

    def build(self, raw_df: pl.DataFrame) -> Optional[pl.DataFrame]:
        """Construit les ~462 features V4 (Polars) via MLBackend."""
        return _build_features(raw_df)


# ─────────────────────────────────────────────────────────────────────────────
# Stratégie
# ─────────────────────────────────────────────────────────────────────────────
class Strategy(BaseStrategyML):
    """Stratégie ML utilisant directement les modèles V4 pré-entraînés (natifs)."""

    name      = "opus_stat_pretrained_v4"
    # Le dossier embarque les artefacts ; l'engine ne tentera pas d'écrire dedans.
    model_dir = _DATA_DIR

    timeframes: List[str] = list(_SUPPORTED_TFS)

    # Seuils de décision sont optimisables — les modèles sont figés.
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
        # Cache backtest : features V4 pré-calculées sur toute la fenêtre.
        # Évite de rebuild les ~462 colonnes à chaque barre du backtest
        # (gain x100 sur les backtests longs).
        self._bt_features: Optional[pl.DataFrame] = None
        self._bt_features_len: int = 0
        self._ensure_loaded()

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Pré-calcule les features V4 pour TOUTE la fenêtre du backtest.

        Appelé une fois par ``Backtester.run`` avant la boucle bar-par-bar.
        Sans ce cache, ``score()`` reconstruit ~462 features à chaque appel
        (≈ 50 ms × ~5000 barres = > 4 min par stratégie). Avec ce cache,
        coût constant : un seul ``_build_features`` sur toute la fenêtre,
        puis lookup ``head(i+1)`` dans la boucle.

        ARCH-012 + phase6 : utilise le FeatureStore (catalogue partagé
        ``opus_v4_polars`` version ``1``) — la construction est déléguée à
        ``MLBackend.build_features`` (Polars). Plus de conversion pandas :
        les features restent en Polars de bout en bout.
        """
        try:
            from app.core.feature_store import cached_strategy_features
            feats = cached_strategy_features(
                getattr(self, "_bt_symbol", None), getattr(self, "_bt_tf", None), df,
                name="opus_v4_polars", version="1",
                builder=lambda w: MLBackend.build_features(w),
                in_kind="polars", out_kind="polars")
            self._bt_features = feats
            self._bt_features_len = len(df) if feats is not None else 0
            n_cols = len(feats.columns) if feats is not None else 0
            logger.info(
                f"[OpusV4-PT] backtest : features pré-calculées sur "
                f"{self._bt_features_len} bougies ({n_cols} colonnes)"
            )
        except Exception as e:
            logger.warning(f"[OpusV4-PT] prepare_for_backtest KO : {e}")
            self._bt_features = None
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
                # AUC OOS documentées dans le README V4 — informationnel
                self._best_auc_per_tf = {"15m": 0.626, "30m": 0.597, "1h": 0.603}
                self._best_auc        = max(self._best_auc_per_tf.values())
                for tf in _SUPPORTED_TFS:
                    self._train_meta[tf] = {
                        "auc_amp": {"15m": 0.749, "30m": 0.690, "1h": 0.676}.get(tf, 0.0),
                        "auc_dir": {"15m": 0.503, "30m": 0.504, "1h": 0.530}.get(tf, 0.0),
                        "source":  "v4_native (.lgb + .json, converté depuis v4_models.pkl)",
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
        # Les modèles sont figés — on ne les efface jamais. En revanche on
        # vide le cache de features (réinitialisé entre deux backtests).
        self._bt_features = None
        self._bt_features_len = 0

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
    def _predict(self, features_df: pl.DataFrame, tf: str,
                 target: str) -> Optional[float]:
        key = (tf, target, "single")
        entry = self._models.get(key)
        if entry is None:
            return None
        try:
            feat_names = entry["features"]
            medians    = self._medians.get((tf, target), {})
            X          = _prepare_row(features_df, feat_names, medians)
            # ARCH-012 + SEC-020 : utilisation de booster.predict() (natif LightGBM)
            # au lieu de clf.predict_proba() (sklearn wrapper). Équivalence
            # byte-à-byte vérifiée (cf. scripts/check_v4_equivalence.py).
            # ``_prepare_row`` renvoie déjà un ``np.ndarray`` — pas de conversion.
            booster = entry["model"]
            return float(booster.predict(X)[0])
        except Exception as e:
            logger.warning(f"[OpusV4-PT] Prédiction {key} KO : {e}")
            return None

    def predict_amplitude(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        """P(événement |return_{t+1}| > seuil) pour le timeframe donné."""
        return self._predict(features_df, tf, "amp")

    def predict_direction(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
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

        # 2. Construction des features V4 — fast-path backtest si pré-calculé.
        features: Optional[pl.DataFrame] = None
        if self._bt_features is not None and len(df) <= self._bt_features_len:
            features = self._bt_features.head(len(df))
        else:
            window   = _window_polars(df, n=max(260, self.min_bars_required() + 20))
            features = _build_features(window)
        if features is None or len(features) == 0:
            return self._none("Construction des features V4 impossible")

        last = features.row(-1, named=True)
        atr_v = _safe_num(last.get("ATR_14"), 0.0)
        if not np.isfinite(atr_v) or atr_v <= 0:
            # Fallback : ATR depuis le précompute du backtester si disponible
            atr_v = float(pre_val(df, "_pre_atr14") or 0.0)
        c_now = float(df["close"][-1] or 0.0)
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        # 3. Régime : ADX + alignement SMA (réplique de la logique V4).
        adx_v = _safe_num(last.get("ADX"), 0.0)
        bull  = int(_safe_num(last.get("MM_bullish_align"), 0.0))
        bear  = int(_safe_num(last.get("MM_bearish_align"), 0.0))
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
                "Trend Up : aucun edge (AUC dir ≈ 0.50)",
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
            "rsi":              round(_safe_num(last.get("RSI_14"), 50.0), 1),
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
