"""Stratégie ``vizion`` — application STRICTE du modèle ICT présenté par
@Vizion-fr (entrée sur Order Block validée par le timeframe alignment).

Checklist (LONG ; miroir SHORT), à la barre où un Order Block est retesté :
  1. OB AVEC la tendance, en zone DISCOUNT (sous l'equilibrium).
  2. « Crédibilité » : une prise de liquidité (sweep rejeté) a eu lieu autour de
     l'origine de l'OB (``require_sweep``).
  3. Déclencheur : rebalance d'un FVG (BISI) de même sens chevauchant l'OB
     (``require_fvg``).
  4. TIMEFRAME ALIGNMENT : l'OB LTF est IMBRIQUÉ dans un OB HTF de même sens
     (``require_htf_ob`` — cœur de la méthode, via ``smc.htf_analysis``).
  5. Confirmation SMT (``use_smt``) : un actif corrélé (``smt_correlate_path``,
     GÉNÉRIQUE — crypto, action, ETF, forex) ne prend pas la liquidité
     équivalente. Dégradation gracieuse si les données du corrélé sont absentes.
  6. Confluences bonus (score) : Unicorn (OB+breaker), propulsion block.
  SL sous l'OB ; TP sur la prochaine liquidité opposée (bracket, comme la vidéo).

⚠ enabled: false — modèle discrétionnaire à edge NON prouvé (exemples des vidéos
cherry-pické). À mesurer avant toute activation. Les setups LTF (15 s/1 min) des
vidéos ne survivront pas aux frais spot crypto (cf. campagne LTF) mais peuvent
tenir sur futures ou en 4h.
"""
import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategy
from app.core import smc
from app.core import ict
from app.core.indicators_core import atr_wilder
from app.live.utils import _HTF_MAP

logger = logging.getLogger(__name__)

_TF_SEC = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
           "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
           "1d": 86400}
_HTF_SEC_MAP = {_TF_SEC[k]: _TF_SEC[v] for k, v in _HTF_MAP.items()
                if k in _TF_SEC and v in _TF_SEC}


class Strategy(BaseStrategy):
    name = "vizion"
    timeframes: List[str] = ["4h", "1h"]

    param_space: Dict[str, List] = {
        "require_sweep":   [True, False],
        "require_fvg":     [True, False],
        "require_htf_ob":  [True, False],
        "use_smt":         [True, False],
        "min_rr":          [2.0, 3.0, 4.0],
        "long_only":       [True, False],
        "use_trailing":    [True, False],
    }

    fixed_params: Dict[str, Any] = {
        "swing_len":       3,
        "ob_max_age":      60,
        "pool_max_age":    120,
        "sl_buffer_atr":   0.5,
        "min_rr":          3.0,      # le modèle vise de gros ratios (vidéos : 6, 2.7)
        "min_gain_pct":    0.4,
        "tp_front_run_atr": 0.1,
        "liq_lookback":    12,       # fenêtre du sweep « crédibilité » avant l'OB
        # ── Cases de la checklist (chacune activable/désactivable → mesurable) ──
        "require_sweep":   True,
        "require_fvg":     True,
        "require_htf_ob":  True,
        "use_smt":         True,
        "smt_correlate_path": "",    # ex. data/ohlcv/ETH_USDC/4h.parquet (GÉNÉRIQUE)
        "smt_lookback":    20,
        # ── Sortie ──
        "use_trailing":    False,    # bracket TP=liquidité (fidèle vidéo) par défaut
        "trail_mult":      3.0,
        "long_only":       False,
        "min_score":       0.5,
        "max_window":      500,
    }

    def __init__(self):
        self._sig: Optional[Dict[int, dict]] = None
        self._close_ref: Optional[np.ndarray] = None

    def min_bars_required(self, params: dict = None) -> int:
        return 260

    def _p(self, params: dict = None) -> Dict[str, Any]:
        p = dict(self.fixed_params)
        for k, v in ((params or {}).get(self.name, {}) or {}).items():
            if k in p and v is not None:
                p[k] = v
        return p

    def _smc_params(self, p: Dict[str, Any]) -> Dict[str, Any]:
        return {"swing_left": int(p["swing_len"]), "swing_right": int(p["swing_len"])}

    # ── SMT générique : charge l'actif corrélé et l'aligne par timestamp ──────
    def _load_smt(self, df: pl.DataFrame, p: Dict[str, Any]) -> Optional[np.ndarray]:
        path = str(p.get("smt_correlate_path", "") or "")
        if not (bool(p.get("use_smt", False)) and path and os.path.exists(path)
                and "time" in df.columns):
            return None
        try:
            cdf = pl.read_parquet(path) if path.endswith((".parquet", ".pq")) \
                else pl.read_csv(path, try_parse_dates=True)
            ren = {c: c.strip().lower() for c in cdf.columns}
            cdf = cdf.rename(ren)
            tcol = "time" if "time" in cdf.columns else (
                "date" if "date" in cdf.columns else None)
            ccol = "close" if "close" in cdf.columns else (
                "adj close" if "adj close" in cdf.columns else None)
            if tcol is None or ccol is None:
                return None
            ct = cdf[tcol].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
            cc = cdf[ccol].cast(pl.Float64).to_numpy()
            t = df["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
            aligned = ict.align_series(t, ct, cc)
            close = df["close"].to_numpy().astype(float)
            return ict.smt_divergence(close, aligned, int(p.get("smt_lookback", 20)))
        except Exception as e:                      # pragma: no cover
            logger.warning(f"[vizion] SMT KO ({path}) : {e}")
            return None

    def _active_htf_obs(self, htf_res, hidx: int):
        if htf_res is None or hidx < 0:
            return []
        return [ob for ob in htf_res["_all_obs"]
                if ob["created_at"] <= hidx
                and (ob["invalidated_at"] is None or ob["invalidated_at"] > hidx)]

    def _recent_sweep(self, res: dict, created_at: int, want: str,
                      lookback: int) -> bool:
        """Un sweep rejeté de type ``want`` (buy_side/sell_side) dans la fenêtre
        [created_at − lookback, created_at]. Prise de liquidité = crédibilité."""
        for sw in res["_all_sweeps"]:
            if not sw["rejected"] or sw["kind"] != want:
                continue
            if created_at - lookback <= sw["index"] <= created_at:
                return True
        return False

    def _build(self, res, htf_res, hidx_arr, smt, i, ob, side, p,
               high, low, close, atr) -> Optional[dict]:
        a = float(atr[i])
        c = float(close[i])
        if a <= 0 or c <= 0:
            return None
        kind = "bullish" if side == "long" else "bearish"
        # 1. discount (long) / premium (short)
        zone = (smc.premium_discount_at(res, high, low, close, i) or {}).get("zone", "")
        if side == "long" and zone != "discount":
            return None
        if side == "short" and zone != "premium":
            return None
        # 2. crédibilité : sweep opposé pris autour de l'origine de l'OB
        if bool(p.get("require_sweep", True)):
            want = "sell_side" if side == "long" else "buy_side"
            if not self._recent_sweep(res, ob["created_at"], want, int(p["liq_lookback"])):
                return None
        # 3. déclencheur : rebalance d'un FVG de même sens chevauchant l'OB
        if bool(p.get("require_fvg", True)) and not ict.fvg_overlap(
                res["_all_fvgs"], i, kind, ob["bottom"], ob["top"]):
            return None
        # 4. timeframe alignment : imbrication dans un OB HTF de même sens
        if bool(p.get("require_htf_ob", True)):
            htf_obs = self._active_htf_obs(htf_res, int(hidx_arr[i]) if hidx_arr is not None else -1)
            if not ict.nested_order_block(htf_obs, kind, ob["bottom"], ob["top"]):
                return None
        # 5. confirmation SMT (bonus + option filtre)
        smt_ok = True
        if smt is not None:
            want_sign = 1 if side == "long" else -1
            smt_ok = int(smt[i]) == want_sign

        # ── Score / confluences ──
        sc = 0.6
        sc += 0.1 if (smt is not None and smt_ok) else 0.0
        if ict.unicorn_zones(res["_all_breakers"], res["_all_fvgs"], i):
            sc += 0.1
        if ict.propulsion_block(res["_all_obs"], i):
            sc += 0.1
        sc += 0.1 if (ob.get("strength", 1) >= 2) else 0.0
        sc = round(min(sc, 1.0), 3)
        if sc < float(p["min_score"]):
            return None

        # ── SL / TP ──
        buf = float(p["sl_buffer_atr"]) * a
        if side == "long":
            sl = float(ob["bottom"]) - buf
        else:
            sl = float(ob["top"]) + buf
        risk = (c - sl) if side == "long" else (sl - c)
        if risk <= 0:
            return None
        tp, tp_src = self._tp(res, i, c, risk, side, p, a)
        if tp is None:
            return None
        gain = abs(tp - c) / c * 100.0
        if gain <= float(p["min_gain_pct"]) or abs(tp - c) / risk < float(p["min_rr"]):
            return None

        use_trailing = bool(p.get("use_trailing", False))
        arrow = "LONG" if side == "long" else "SHORT"
        return {
            "score": sc, "side": side, "name": self.name, "atr": a,
            "stop_hint": round(sl, 8),
            "tp_hint": None if use_trailing else round(tp, 8),
            "disable_trailing": not use_trailing,
            "trail_override": {"trail_wide": float(p["trail_mult"]), "mode": "dynamic"}
            if use_trailing else None,
            "indicators": {"zone": zone, "tp_source": tp_src,
                           "smt": int(smt[i]) if smt is not None else 0},
            "reason": (f"{arrow} vizion OB {zone} — TFA{'✓' if p.get('require_htf_ob') else ''} "
                       f"SMT{'✓' if (smt is not None and smt_ok) else '·'} (score {sc:.2f})"),
        }

    def _tp(self, res, i, entry, risk, side, p, atr):
        front = float(p["tp_front_run_atr"]) * atr
        max_age = int(p["pool_max_age"])
        if side == "long":
            liq = smc.liquidity_targets_above(res, i, entry, max_age=max_age)
            for lv in sorted(liq):
                cand = lv - front
                if cand > entry and (cand - entry) / risk >= float(p["min_rr"]):
                    return cand, f"liquidité {lv:.6g}"
        else:
            liq = smc.liquidity_targets_below(res, i, entry, max_age=max_age)
            for lv in sorted(liq, reverse=True):
                cand = lv + front
                if cand < entry and (entry - cand) / risk >= float(p["min_rr"]):
                    return cand, f"liquidité {lv:.6g}"
        return None, ""

    def _signals(self, df: pl.DataFrame, p: Dict[str, Any]) -> Dict[int, dict]:
        res = smc.analyze(df, self._smc_params(p))
        htf_res, hidx = smc.htf_analysis(df, self._smc_params(p),
                                         htf_sec_map=_HTF_SEC_MAP)
        smt = self._load_smt(df, p)
        high = df["high"].to_numpy().astype(float)
        low = df["low"].to_numpy().astype(float)
        close = df["close"].to_numpy().astype(float)
        atr = atr_wilder(df, 14).fill_null(0.0).to_numpy()
        trend = res["_trend_arr"]
        long_only = bool(p.get("long_only", False))
        max_age = int(p["ob_max_age"])
        out: Dict[int, dict] = {}
        for ob in res["_all_obs"]:
            i = ob["touched_at"]
            if i is None or i - ob["created_at"] > max_age:
                continue
            if ob["invalidated_at"] is not None and ob["invalidated_at"] <= i:
                continue
            if ob["kind"] == "bullish" and int(trend[i]) == 1:
                sig = self._build(res, htf_res, hidx, smt, i, ob, "long", p,
                                  high, low, close, atr)
            elif ob["kind"] == "bearish" and int(trend[i]) == -1 and not long_only:
                sig = self._build(res, htf_res, hidx, smt, i, ob, "short", p,
                                  high, low, close, atr)
            else:
                continue
            if sig is not None and (i not in out or sig["score"] > out[i]["score"]):
                out[i] = sig
        return out

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        try:
            p = self._p(getattr(self, "_bt_params", None))
            self._sig = self._signals(df, p)
            self._close_ref = df["close"].to_numpy().astype(float)
        except Exception as e:            # pragma: no cover
            logger.warning(f"[vizion] prepare_for_backtest KO : {e}")
            self._sig = None
            self._close_ref = None

    def _cache_valid(self, df: pl.DataFrame) -> bool:
        if self._sig is None or self._close_ref is None:
            return False
        idx = df.height - 1
        ref = self._close_ref
        return 0 <= idx < len(ref) and abs(float(df["close"][-1]) - ref[idx]) < 1e-9

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = self._p(params)
        if len(df) < self.min_bars_required(params):
            return self._none("historique insuffisant")
        if self._cache_valid(df):
            sig = self._sig.get(df.height - 1)
            return dict(sig) if sig else self._none("pas de setup vizion")
        win = df[-int(p["max_window"]):] if len(df) > int(p["max_window"]) else df
        sig = self._signals(win, p).get(len(win) - 1)
        return dict(sig) if sig else self._none("pas de setup vizion")

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
