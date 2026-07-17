"""Stratégie Funding Flow — directionnelle 100 % DÉRIVÉS (théorique).

Exploite directement funding / open interest / long-short ratio / taker order-flow
(colonnes injectées par app.core.derivatives.DerivativesStore.align_to_ohlcv,
accumulées au fil de l'eau quand ``derivatives.enabled: true``). L'OHLCV ne sert
qu'au prix et à l'ATR (stops / sizing).

⚠️ THÉORIQUE : non backtestable sur années (historique gratuit OI/LSR/taker ≈ 30 j).
Fondé sur la littérature de microstructure crypto + l'edge mean-reversion mesuré
(research/directional_hunt.py). À calibrer/valider en live (cf.
research/DERIVATIVES_integration.md). Sans colonnes dérivées → abstention.

Thèses (toutes CONTRARIAN aux extrêmes de positionnement) :
  • FUNDING (principal) — funding très positif = longs surchargés qui *paient* →
    pression de reversion baissière (et inverse). C'est le signal directionnel
    crypto le mieux documenté (funding mean-reversion / basis trade).
  • LONG-SHORT RATIO — foule massivement longue/short = sur-positionnement → fade.
  • TAKER buy/sell — climax d'achats agressifs (sur funding haut) = épuisement →
    short ; vague de ventes agressives (funding bas) = capitulation → long.
  • OPEN INTEREST — OI en hausse pendant l'extrême = positionnement qui se construit
    → conviction renforcée (carburant du squeeze de reversion).

Décision : pression de foule = somme pondérée des z-scores. Extrême positif → SHORT
(fade des longs surchargés), extrême négatif → LONG. Conviction ∝ |pression| × OI.
Garde-fou : taille réduite si on fade une tendance forte (knife-catch).
Règle, pas ML.
"""

import logging
import math
from typing import Any, Dict, List, Optional

import polars as pl

from app.engine.engine import BaseStrategy
from app.core.indicators import precompute_df, pre_val

logger = logging.getLogger(__name__)


def _safe(v, fb=0.0) -> float:
    try:
        f = float(v)
        return fb if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return fb


def _col_last(df: pl.DataFrame, name: str) -> Optional[float]:
    if name not in df.columns or len(df) == 0:
        return None
    v = df[name][-1]
    if v is None:
        return None
    f = float(v)
    return None if (math.isnan(f) or math.isinf(f)) else f


class Strategy(BaseStrategy):
    name = "funding_flow"

    # S2-04 : funding/OI/long-short/taker n'existent que sur les perpetuals
    # crypto (app/core/derivatives.py) — sans équivalent sur actions.
    asset_classes = frozenset({"crypto"})

    timeframes: List[str] = ["1h", "4h"]

    warmup_bars = 230

    param_space: Dict[str, List] = {
        "w_funding":       [0.4, 0.5, 0.6],
        "w_lsr":           [0.2, 0.25, 0.3],
        "w_taker":         [0.15, 0.25, 0.35],
        "enter_pressure":  [1.0, 1.3, 1.6, 2.0],
        "conviction_scale": [2.0, 2.5, 3.0],
        "oi_boost":        [0.0, 0.15, 0.3],
        "sl_atr":          [1.5, 2.0, 2.5],
        "tp_atr":          [2.0, 3.0, 4.0],
        "max_hold":        [8, 12, 24],
        "min_quality":     [0.56, 0.60, 0.64],
        "enable_short":    [True, False],
        "trend_guard":     [True, False],
        "cooldown":        [1, 2, 3],
        "score_threshold": [0.55, 0.60],
    }

    fixed_params: Dict[str, Any] = {}

    _D = {
        "w_funding":        0.5,
        "w_lsr":            0.25,
        "w_taker":          0.25,
        "enter_pressure":   1.3,    # |pression| (≈ z-score pondéré) requise
        "conviction_scale": 2.5,
        "oi_boost":         0.15,   # renfort de conviction si OI confirme
        "sl_atr":           2.0,
        "tp_atr":           3.0,
        "max_hold":         12,
        "min_quality":      0.60,
        "enable_short":     True,
        "trend_guard":      True,   # réduit la taille si on fade une tendance forte
        "cooldown":         2,
    }

    def __init__(self):
        self._call: Dict[str, int] = {}
        self._last: Dict[str, int] = {}

    def min_bars_required(self, params: dict = None) -> int:
        return 220

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = {**self._D, **((params or {}).get(self.name, {}))}
        sym = symbol or "default"

        if "_pre_atr14" not in df.columns:
            df = precompute_df(df)
        if len(df) < self.min_bars_required(params):
            return self._none("Données insuffisantes")

        cnt = self._call.get(sym, 0) + 1
        self._call[sym] = cnt
        if cnt - self._last.get(sym, -10**9) < int(p["cooldown"]):
            return self._none("Cooldown")

        c = _safe(df["close"][-1])
        atr = _safe(pre_val(df, "_pre_atr14"))
        if c <= 0 or atr <= 0:
            return self._none("Prix/ATR invalide")

        # ── Lecture des dérivés (colonnes injectées par DerivativesStore) ─────
        funding_z = _col_last(df, "funding_z")
        lsr_z = _col_last(df, "lsr_z")
        taker_z = _col_last(df, "taker_z")
        oi_chg = _col_last(df, "oi_change_pct")
        if funding_z is None and lsr_z is None and taker_z is None:
            return self._none("Dérivés absents (activez derivatives.enabled)")

        # ── Pression de foule (positionnement) — somme pondérée des z dispo ───
        terms, wsum = 0.0, 0.0
        if funding_z is not None:
            terms += p["w_funding"] * funding_z; wsum += p["w_funding"]
        if lsr_z is not None:
            terms += p["w_lsr"] * lsr_z; wsum += p["w_lsr"]
        if taker_z is not None:
            terms += p["w_taker"] * taker_z; wsum += p["w_taker"]
        if wsum <= 0:
            return self._none("Pondération dérivés nulle")
        pressure = terms / wsum * (p["w_funding"] + p["w_lsr"] + p["w_taker"])

        ctx = {
            "pressure": round(pressure, 3),
            "funding_z": round(funding_z, 2) if funding_z is not None else None,
            "lsr_z": round(lsr_z, 2) if lsr_z is not None else None,
            "taker_z": round(taker_z, 2) if taker_z is not None else None,
            "oi_change_pct": round(oi_chg, 3) if oi_chg is not None else None,
        }

        if abs(pressure) < p["enter_pressure"]:
            return self._none(f"Pression {pressure:+.2f} < seuil {p['enter_pressure']:.2f}",
                              ctx=ctx)

        # Extrême positif (foule longue) → SHORT ; extrême négatif → LONG.
        side = "short" if pressure > 0 else "long"
        if side == "short" and not p["enable_short"]:
            return self._none("Short désactivé", ctx=ctx)

        # ── Conviction ────────────────────────────────────────────────────────
        conviction = min(abs(pressure) / max(p["conviction_scale"], 1e-9), 1.0)
        # OI confirme : positionnement qui se CONSTRUIT pendant l'extrême → renfort.
        if oi_chg is not None and oi_chg > 0:
            conviction = min(conviction + p["oi_boost"], 1.0)

        # ── Garde-fou tendance (éviter le knife-catch contre un trend fort) ───
        ema50 = _safe(pre_val(df, "_pre_ema50"), c)
        ema200 = _safe(pre_val(df, "_pre_ema200"), c)
        adx = _safe(pre_val(df, "_pre_adx14"))
        strong_up = c > ema50 > ema200 and adx >= 28
        strong_dn = c < ema50 < ema200 and adx >= 28
        size_guard = 1.0
        if p["trend_guard"]:
            if side == "short" and strong_up:
                size_guard = 0.5
            elif side == "long" and strong_dn:
                size_guard = 0.5

        score = round(min(0.55 + conviction * 0.40, 0.95), 3)
        if score < p["min_quality"]:
            return self._none(f"Conviction faible (score {score:.2f})", ctx=ctx)
        size_factor = round(max(0.3, min((0.7 + 0.6 * conviction) * size_guard, 1.5)), 3)

        return {
            "score": score, "side": side, "name": self.name, "atr": atr,
            "sl_atr_mult": float(p["sl_atr"]), "tp_atr_mult": float(p["tp_atr"]),
            "exit_after_bars": int(p["max_hold"]), "size_factor": size_factor,
            "setup": "FUNDING_REVERSION",
            "indicators": ctx,
            "reason": (f"FundingFlow {side.upper()} | pression foule={pressure:+.2f} "
                       f"(funding_z={ctx['funding_z']}, lsr_z={ctx['lsr_z']}, "
                       f"taker_z={ctx['taker_z']}) | conv={conviction:.2f} "
                       f"taille×{size_factor:.2f}"),
            "conditions": [
                f"Positionnement extrême : pression {pressure:+.2f} ≥ {p['enter_pressure']:.2f}",
                f"Reversion contrarian → {side.upper()} (fade de la foule)",
                (f"OI confirme (+{oi_chg:.2f}%)" if (oi_chg is not None and oi_chg > 0)
                 else "OI neutre/baissier"),
                (f"Garde-fou tendance actif (taille ×0.5)" if size_guard < 1.0
                 else "Pas de tendance forte adverse"),
                f"TP {p['tp_atr']:.1f}×ATR / SL {p['sl_atr']:.1f}×ATR / hold ≤ {p['max_hold']}",
            ],
        }

    def _none(self, reason: str = "", ctx: dict = None) -> dict:
        d = {"score": 0, "side": "none", "name": self.name, "reason": reason}
        if ctx is not None:
            d["indicators"] = ctx
        return d
