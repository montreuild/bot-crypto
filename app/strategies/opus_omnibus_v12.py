"""Stratégie Opus Omnibus V12 — fusion V11 + ml_dynamic_threshold.

V12 combine deux moteurs de décision indépendants :

  * **V11** (``opus_omnibus_v11``) — routing par setups sur modèles LightGBM
    multi-horizon calibrés. Décide du *setup*, du *side* et de la gestion du
    risque (SL/TP/size).
  * **ml_dynamic_threshold** — RandomForest/LogReg à seuil de volatilité
    adaptatif, entraîné par timeframe. Fournit une probabilité de hausse
    indépendante (features différentes : VWAP, micro-structure, volatilité
    réalisée…).

La fusion utilise ml_dynamic_threshold comme **filtre de confirmation** sur les
signaux V11 :

  - **accord** (la proba ml_dyn confirme le side V11) → le signal est conservé
    et son score légèrement renforcé ;
  - **désaccord marqué** (proba ml_dyn opposée au-delà de ``veto_margin``) →
    le signal est **rejeté** (veto), évitant les trades où les deux familles de
    modèles se contredisent ;
  - **neutre** (proba ml_dyn indécise) → signal conservé mais taille réduite
    (``unconfirmed_size_factor``) si ``require_confirmation`` ;
  - **ml_dyn indisponible** (modèle pas encore entraîné) → repli sur V11 seul.

V12 hérite de tout l'appareil d'entraînement/routing de V11 et embarque une
instance privée de ml_dynamic_threshold dont il pilote le cycle de vie
(reset/prepare/managed_externally/cancel) en parallèle.
"""

import logging
from typing import Any, Dict, List, Optional

import polars as pl

from app.strategies.opus_omnibus_v11 import Strategy as _V11Strategy
from app.strategies.ml_dynamic_threshold import Strategy as _MLDynStrategy

logger = logging.getLogger(__name__)


class Strategy(_V11Strategy):
    """OMNIBUS V12 — V11 confirmé/veto par ml_dynamic_threshold."""

    name      = "opus_omnibus_v12"
    model_dir = "models"

    # Espace de recherche = celui de V11 + paramètres de fusion.
    param_space: Dict[str, Any] = {
        **_V11Strategy.param_space,
        "fusion_mode":             ["confirm", "veto", "blend"],
        "require_confirmation":    [True, False],
        "veto_margin":             [0.10, 0.15, 0.20],
        "blend_weight":            [0.6, 0.7, 0.8],
        "unconfirmed_size_factor": [0.5, 0.7, 1.0],
        "mldyn_vol_multiplier":    [0.4, 0.6, 0.8],
        "mldyn_lookahead":         [2, 3, 5],
        # Knob d'optim : laisse l'optimiseur choisir d'éteindre mldyn (et
        # économiser son entraînement, ~40-60% du coût par trial v12).
        "mldyn_enabled":           [True, False],
    }

    _DEFAULTS = {
        **_V11Strategy._DEFAULTS,
        # ── Fusion ──
        "mldyn_enabled":           True,
        "fusion_mode":             "confirm",
        "require_confirmation":    True,
        "confirm_margin":          0.0,
        "veto_margin":             0.15,
        "blend_weight":            0.7,
        "unconfirmed_size_factor": 0.7,
        # ── Modèle ml_dynamic_threshold embarqué ──
        "mldyn_model_type":   "random_forest",
        "mldyn_lookahead":    3,
        "mldyn_vol_multiplier": 0.6,
        "mldyn_adx_min":      10.0,
        "mldyn_n_trials":     8,
        "mldyn_min_train":    150,
        "mldyn_retrain_every": 200,
    }

    def __init__(self):
        super().__init__()
        self._mldyn = _MLDynStrategy()

    # ── Cycle de vie : propage vers le modèle embarqué ────────────────────────
    @property
    def managed_externally(self) -> bool:
        return self._managed_externally

    @managed_externally.setter
    def managed_externally(self, v: bool) -> None:
        self._managed_externally = bool(v)
        if getattr(self, "_mldyn", None) is not None:
            self._mldyn.managed_externally = bool(v)

    def reset_model(self) -> None:
        super().reset_model()
        if getattr(self, "_mldyn", None) is not None:
            self._mldyn.reset_model()

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        super().prepare_for_backtest(df)
        try:
            self._mldyn.prepare_for_backtest(df)
        except Exception as e:
            logger.warning(f"[OmnibusV12] prepare_for_backtest ml_dyn KO : {e}")

    def _mldyn_params(self, p: dict) -> dict:
        return {"ml_dynamic_threshold": {
            "model_type":     p.get("mldyn_model_type",   self._DEFAULTS["mldyn_model_type"]),
            "lookahead":      p.get("mldyn_lookahead",     self._DEFAULTS["mldyn_lookahead"]),
            "vol_multiplier": p.get("mldyn_vol_multiplier", self._DEFAULTS["mldyn_vol_multiplier"]),
            "adx_min":        p.get("mldyn_adx_min",       self._DEFAULTS["mldyn_adx_min"]),
            # Seuils neutres : on veut la proba brute, la décision est dans la fusion.
            "proba_long":     0.5,
            "proba_short":    0.5,
            "n_trials":       p.get("mldyn_n_trials",      self._DEFAULTS["mldyn_n_trials"]),
            "min_train":      p.get("mldyn_min_train",     self._DEFAULTS["mldyn_min_train"]),
            "retrain_every":  p.get("mldyn_retrain_every", self._DEFAULTS["mldyn_retrain_every"]),
        }}

    # ── Score V12 ─────────────────────────────────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        sig = super().score(df, params, df_htf=df_htf, symbol=symbol)
        if not sig or sig.get("side") == "none":
            return sig

        p = (params or {}).get(self.name, {})
        mldyn_enabled  = bool(p.get("mldyn_enabled",       self._DEFAULTS["mldyn_enabled"]))
        fusion_mode    = str(p.get("fusion_mode",          self._DEFAULTS["fusion_mode"]))
        require_conf   = bool(p.get("require_confirmation", self._DEFAULTS["require_confirmation"]))
        confirm_margin = float(p.get("confirm_margin",     self._DEFAULTS["confirm_margin"]))
        veto_margin    = float(p.get("veto_margin",        self._DEFAULTS["veto_margin"]))
        blend_weight   = float(p.get("blend_weight",       self._DEFAULTS["blend_weight"]))
        unconf_size    = float(p.get("unconfirmed_size_factor", self._DEFAULTS["unconfirmed_size_factor"]))

        # ── Skip mldyn quand son effet est négligeable ───────────────────────
        # Cas où mldyn ne change rien au signal V11 et où son coût (random
        # search interne de ~8-15 trials par fit) est gaspillé :
        #   - ``mldyn_enabled=False`` (knob d'optim explicite), OU
        #   - ``require_conf=False`` ET ``fusion_mode=="veto"`` (le veto et
        #     l'ajustement de taille sont gated par require_conf ; en mode
        #     veto pur sans confirmation requise, mldyn n'a aucun effet)
        if (not mldyn_enabled) or (
            not require_conf and fusion_mode == "veto"
        ):
            sig["fusion"] = "skipped"
            sig["mldyn_proba_up"] = None
            sig["fusion_mode"]    = fusion_mode
            sig.setdefault("conditions", []).append(
                "Fusion : ml_dyn skip (mldyn_enabled=False ou veto+require_conf=False)"
            )
            return sig

        # ── Interroge le modèle ml_dynamic_threshold embarqué ────────────────
        self._mldyn._cancel_event = self._cancel_event
        self._mldyn.managed_externally = self._managed_externally
        md_proba = None
        try:
            md = self._mldyn.score(df, self._mldyn_params(p))
            md_proba = (md.get("indicators") or {}).get("proba_up")
        except Exception as e:
            logger.debug(f"[OmnibusV12] ml_dyn score KO : {e}")

        side = sig["side"]
        sig["mldyn_proba_up"]  = round(md_proba, 4) if md_proba is not None else None
        sig["fusion_mode"]     = fusion_mode

        # ml_dyn indisponible : repli sur V11 seul.
        if md_proba is None:
            sig["fusion"] = "unconfirmed_no_model"
            sig.setdefault("conditions", []).append(
                "Fusion : ml_dynamic_threshold indisponible → V11 seul"
            )
            return sig

        # Signe de l'avis ml_dyn par rapport au side V11.
        if side == "long":
            agree    = md_proba >= 0.5 + confirm_margin
            disagree = md_proba <  0.5 - veto_margin
        else:  # short
            agree    = md_proba <= 0.5 - confirm_margin
            disagree = md_proba >  0.5 + veto_margin

        md_conf = abs(md_proba - 0.5) * 2.0  # ∈ [0, 1]

        # Veto : désaccord marqué (modes confirm/veto).
        if disagree and fusion_mode in ("confirm", "veto") and require_conf:
            return self._none(
                f"Veto ml_dyn : proba_up={md_proba:.2f} contredit {side.upper()} "
                f"(setup V11 {sig.get('setup')})",
                p_event=float(sig.get("p_event", 0.0)),
                p_up=float(sig.get("p_up", 0.5)),
                regime=int(sig.get("regime", -1)),
            )

        if agree:
            # Accord : renforce le score (blend V11 ↔ confiance ml_dyn).
            boosted = blend_weight * float(sig["score"]) + (1.0 - blend_weight) * (0.5 + 0.5 * md_conf)
            sig["score"] = round(min(boosted, 0.97), 3)
            sig["fusion"] = "confirmed"
            sig.setdefault("conditions", []).append(
                f"Fusion : ml_dyn confirme {side.upper()} (proba_up={md_proba:.2f}, "
                f"conf={md_conf:.2f}) → score {sig['score']}"
            )
        else:
            # Neutre : conservé, taille réduite si confirmation requise.
            if require_conf:
                sig["size_factor"] = round(float(sig.get("size_factor", 1.0)) * unconf_size, 4)
            sig["fusion"] = "neutral"
            sig.setdefault("conditions", []).append(
                f"Fusion : ml_dyn neutre (proba_up={md_proba:.2f}) → "
                f"{'taille ×' + format(unconf_size, '.2f') if require_conf else 'inchangé'}"
            )

        if isinstance(sig.get("indicators"), dict):
            sig["indicators"]["mldyn_proba_up"] = round(md_proba, 4)
            sig["indicators"]["fusion"]         = sig.get("fusion")
        sig["reason"] = sig.get("reason", "") + f" | fusion={sig.get('fusion')} mldyn={md_proba:.2f}"
        return sig
