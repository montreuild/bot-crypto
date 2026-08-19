"""Checkers de setups SMC (X-03). Rattachés comme méthodes via ``_SETUP_CHECKERS``."""
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from app.core import ict, smc


def _at_bar(res: dict, index_key: str, items: list, field: str, i: int):
    """Entités de la barre ``i``. Index PERF-05 si présent, sinon scan."""
    idx = res.get(index_key)
    if isinstance(idx, dict):
        return idx.get(i, ())
    return [e for e in items if e.get(field) == i]


@dataclass
class _SignalCtx:
    res: dict
    i: int
    open_: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    aux: Dict[str, Any]
    p: Dict[str, Any]
    atr: float
    trend: int
    c: float
    zone: str
    kz_add: float
    amd_add: float
    in_sb: bool
    long_ema_ok: bool
    short_ema_ok: bool
    long_htf_ok: bool
    short_htf_ok: bool
    vol_ok: bool
    tl_tap_long: bool
    tl_tap_short: bool
    recent_choch_down: bool
    recent_choch_up: bool
    vp: Optional[dict]
    vp_above: List[float]
    vp_below: List[float]
    cal: Optional[dict]
    cal_mode: Any
    cal_tp: List[Tuple[float, str]]
    sd_up: List[float]
    sd_dn: List[float]
    allow_ct: bool
    jd_s: int
    jd_bonus: bool
    jd_filter: bool
    smt_origin: bool
    req_inducement: bool
    ind_lb: int
    max_ob_age: int
    pin_a: Optional[Any]
    eng_a: Optional[Any]
    struct_state: str
    struct_seq: str
    protected_high: Optional[float]
    protected_low: Optional[float]

def _vp_add(ctx: _SignalCtx, zone_lo: float, zone_hi: float) -> float:
    if ctx.vp is None or not bool(ctx.p.get("vp_confluence", False)):
        return 0.0
    pad = 0.25 * ctx.atr
    return 0.05 if any(zone_lo - pad <= lv <= zone_hi + pad
                       for lv in ctx.vp["hvns"]) else 0.0


def _inv_fvg_add(ctx: _SignalCtx, zone_lo: float, zone_hi: float,
                 side: str) -> float:
    # 4c — inversion de rôle des FVG (off par défaut) : bonus si un FVG de
    # sens OPPOSÉ, déjà mitigé, chevauche la zone d'entrée.
    if not bool(ctx.p.get("inv_fvg_bonus", False)):
        return 0.0
    return 0.05 if ict.inverted_fvg_overlap(ctx.res["_all_fvgs"], ctx.i, side,
                                            zone_lo, zone_hi) else 0.0


def _candle_add(ctx: _SignalCtx, side: str) -> float:
    if ctx.pin_a is None:
        return 0.0
    sgn = 1 if side == "long" else -1
    return 0.05 if (int(ctx.pin_a[ctx.i]) == sgn or int(ctx.eng_a[ctx.i]) == sgn) else 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  Checkers par setup — chacun retourne une Liste[dict] de candidates
#  (un même setup peut produire plusieurs candidates à la même barre).
# ─────────────────────────────────────────────────────────────────────────────

# ── A. Sweep reversal ───────────────────────────────────────────────────────
# Filtres durs (validés sur BTC/USDC 1h→4h 2019-2026) :
#   - AVEC la tendance uniquement : un sweep sell-side ne s'achète que
#     dans une structure haussière (pullback qui prend la liquidité),
#     jamais en contre-tendance.
#   - Côté momentum du range : pas de long en zone discount ni de short
#     en zone premium — sur crypto, la force appelle la force.
def _check_sweep_reversal(self, ctx: _SignalCtx) -> List[dict]:
    """Setup A : Sweep rejeté (sell_side → long, buy_side → short)."""
    p, res, i = ctx.p, ctx.res, ctx.i
    out: List[dict] = []
    for ev in _at_bar(res, "_sweeps_at", res["_all_sweeps"], "index", i):
        if not ev["rejected"]:
            continue
        if ev["kind"] == "sell_side" and not ctx.recent_choch_down:
            if (ctx.trend != 1 and not ctx.allow_ct) or ctx.zone == "discount" \
                    or not ctx.long_ema_ok or not ctx.long_htf_ok:
                continue
            if ctx.jd_filter and ctx.jd_s == -1:
                continue              # SMC-04 : Judas contredit le long
            sc = 0.50 + ctx.kz_add + ctx.amd_add
            sc += 0.05 if (ctx.jd_bonus and ctx.jd_s == 1) else 0.0
            sc += 0.10 if ctx.trend == 1 else 0.0
            sc += 0.10 if ev["source"] == "pool" else 0.0
            sc += 0.10 if ctx.zone == "premium" else 0.0
            sc += 0.05 if ctx.vol_ok else 0.0
            sc += 0.05 if ctx.close[i] > ctx.open_[i] else 0.0
            sc += 0.05 if ctx.tl_tap_long else 0.0
            sc += _vp_add(ctx, float(ev["level"]) - 0.5 * ctx.atr,
                          float(ev["level"]) + 0.5 * ctx.atr)
            sc += _inv_fvg_add(ctx, float(ev["level"]) - 0.5 * ctx.atr,
                               float(ev["level"]) + 0.5 * ctx.atr, "long")
            sc += _candle_add(ctx, "long")
            sl = min(float(ctx.low[i]), float(ev["level"])) - \
                float(p["sl_buffer_atr"]) * ctx.atr
            cand = self._build_trade(res, i, "long", ctx.c, sl, ctx.atr, p,
                                     setup="SWEEP_REVERSAL", score=sc,
                                     detail=f"sweep {ev['source']} "
                                            f"{ev['level']:.6g}",
                                     trend=ctx.trend, zone=ctx.zone,
                                     extra_targets=ctx.vp_above,
                                     cal_targets=ctx.cal_tp,
                                     sd_targets=ctx.sd_up)
            if cand:
                out.append(cand)
        elif ev["kind"] == "buy_side" and not ctx.recent_choch_up:
            if (ctx.trend != -1 and not ctx.allow_ct) or ctx.zone == "premium" \
                    or not ctx.short_ema_ok or not ctx.short_htf_ok:
                continue
            if ctx.jd_filter and ctx.jd_s == 1:
                continue              # SMC-04 : Judas contredit le short
            sc = 0.50 + ctx.kz_add + ctx.amd_add
            sc += 0.05 if (ctx.jd_bonus and ctx.jd_s == -1) else 0.0
            sc += 0.10 if ctx.trend == -1 else 0.0
            sc += 0.10 if ev["source"] == "pool" else 0.0
            sc += 0.10 if ctx.zone == "discount" else 0.0
            sc += 0.05 if ctx.vol_ok else 0.0
            sc += 0.05 if ctx.close[i] < ctx.open_[i] else 0.0
            sc += 0.05 if ctx.tl_tap_short else 0.0
            sc += _vp_add(ctx, float(ev["level"]) - 0.5 * ctx.atr,
                          float(ev["level"]) + 0.5 * ctx.atr)
            sc += _inv_fvg_add(ctx, float(ev["level"]) - 0.5 * ctx.atr,
                               float(ev["level"]) + 0.5 * ctx.atr, "short")
            sc += _candle_add(ctx, "short")
            sl = max(float(ctx.high[i]), float(ev["level"])) + \
                float(p["sl_buffer_atr"]) * ctx.atr
            cand = self._build_trade(res, i, "short", ctx.c, sl, ctx.atr, p,
                                     setup="SWEEP_REVERSAL", score=sc,
                                     detail=f"sweep {ev['source']} "
                                            f"{ev['level']:.6g}",
                                     trend=ctx.trend, zone=ctx.zone,
                                     extra_targets=ctx.vp_below,
                                     cal_targets=ctx.cal_tp,
                                     sd_targets=ctx.sd_dn)
            if cand:
                out.append(cand)
    return out


# ── A.bis — sweep d'un niveau calendaire (SMC-03, mode "sweeps") ────────────
# La mèche perce PDL/PWL (long) ou PDH/PWH (short) puis la clôture revient
# du bon côté — première barre de perce uniquement (low/high[i−1] du bon
# côté). Mêmes filtres durs et même grille de score que les sweeps du
# moteur ; +0.10 « pool » : un niveau calendaire est une poche majeure.
def _check_calendar_sweep(self, ctx: _SignalCtx) -> List[dict]:
    """Sweep d'un niveau PDH/PDL/PWH/PWL (cal_mode in (True, "sweeps"))."""
    p, res, i = ctx.p, ctx.res, ctx.i
    out: List[dict] = []
    if ctx.cal is None or ctx.cal_mode not in (True, "sweeps"):
        return out
    cal = ctx.cal
    if not ctx.recent_choch_down and (ctx.trend == 1 or ctx.allow_ct) \
            and ctx.zone != "discount" and ctx.long_ema_ok and ctx.long_htf_ok \
            and not (ctx.jd_filter and ctx.jd_s == -1):
        for kname in ("pdl", "pwl"):
            lv = float(cal[kname][i])
            if np.isnan(lv) or not (float(ctx.low[i]) <= lv < float(ctx.low[i - 1])) \
                    or ctx.c <= lv:
                continue
            sc = 0.50 + ctx.kz_add + ctx.amd_add
            sc += 0.10 if ctx.trend == 1 else 0.0
            sc += 0.10                    # niveau calendaire = pool majeur
            sc += 0.10 if ctx.zone == "premium" else 0.0
            sc += 0.05 if ctx.vol_ok else 0.0
            sc += 0.05 if ctx.close[i] > ctx.open_[i] else 0.0
            sc += 0.05 if ctx.tl_tap_long else 0.0
            sc += _vp_add(ctx, lv - 0.5 * ctx.atr, lv + 0.5 * ctx.atr)
            sc += _inv_fvg_add(ctx, lv - 0.5 * ctx.atr, lv + 0.5 * ctx.atr, "long")
            sc += _candle_add(ctx, "long")
            sl = min(float(ctx.low[i]), lv) - float(p["sl_buffer_atr"]) * ctx.atr
            cand = self._build_trade(res, i, "long", ctx.c, sl, ctx.atr, p,
                                     setup="SWEEP_REVERSAL", score=sc,
                                     detail=f"sweep {kname.upper()} {lv:.6g}",
                                     trend=ctx.trend, zone=ctx.zone,
                                     extra_targets=ctx.vp_above,
                                     cal_targets=ctx.cal_tp,
                                     sd_targets=ctx.sd_up)
            if cand:
                out.append(cand)
    if not ctx.recent_choch_up and (ctx.trend == -1 or ctx.allow_ct) \
            and ctx.zone != "premium" and ctx.short_ema_ok and ctx.short_htf_ok \
            and not (ctx.jd_filter and ctx.jd_s == 1):
        for kname in ("pdh", "pwh"):
            lv = float(cal[kname][i])
            if np.isnan(lv) or not (float(ctx.high[i]) >= lv > float(ctx.high[i - 1])) \
                    or ctx.c >= lv:
                continue
            sc = 0.50 + ctx.kz_add + ctx.amd_add
            sc += 0.10 if ctx.trend == -1 else 0.0
            sc += 0.10                    # niveau calendaire = pool majeur
            sc += 0.10 if ctx.zone == "discount" else 0.0
            sc += 0.05 if ctx.vol_ok else 0.0
            sc += 0.05 if ctx.close[i] < ctx.open_[i] else 0.0
            sc += 0.05 if ctx.tl_tap_short else 0.0
            sc += _vp_add(ctx, lv - 0.5 * ctx.atr, lv + 0.5 * ctx.atr)
            sc += _inv_fvg_add(ctx, lv - 0.5 * ctx.atr, lv + 0.5 * ctx.atr, "short")
            sc += _candle_add(ctx, "short")
            sl = max(float(ctx.high[i]), lv) + float(p["sl_buffer_atr"]) * ctx.atr
            cand = self._build_trade(res, i, "short", ctx.c, sl, ctx.atr, p,
                                     setup="SWEEP_REVERSAL", score=sc,
                                     detail=f"sweep {kname.upper()} {lv:.6g}",
                                     trend=ctx.trend, zone=ctx.zone,
                                     extra_targets=ctx.vp_below,
                                     cal_targets=ctx.cal_tp,
                                     sd_targets=ctx.sd_dn)
            if cand:
                out.append(cand)
    return out


# ── B. Retest d'order block / rejection block ────────────────────────────────
# Les rejection blocks (mèches de swing) partagent la même mécanique de
# retest que les OB : zone d'offre/demande née d'un rejet violent.
def _check_ob_retest(self, ctx: _SignalCtx) -> List[dict]:
    """Setup B : Retest d'order block (OB_RETEST) ou rejection block
    (REJECTION_RETEST). Mêmes filtres durs et même mécanique de retest."""
    p, res, i = ctx.p, ctx.res, ctx.i
    out: List[dict] = []
    # SMC-13 — Mitigation Blocks : zones dont l'impulsion n'a pas cassé la
    # structure (subtype "mitigation") — exclues ou pénalisées si demandé.
    mit_mode = str(p.get("mitigation_mode", "off"))
    zone_sources = []
    if bool(p.get("use_ob_retest", True)):
        zone_sources.append(("OB_RETEST", res["_all_obs"]))
    if bool(p.get("use_rejection_blocks", False)):
        zone_sources.append(("REJECTION_RETEST", res["_all_rejections"]))
    if not zone_sources:
        return out
    for setup_name, zone_list in zone_sources:
        idx_key = "_obs_at" if setup_name == "OB_RETEST" else "_rejections_at"
        field = "touched_at"
        for ob in _at_bar(res, idx_key, zone_list, field, i):
            if i - ob["created_at"] > ctx.max_ob_age:
                continue
            if ob["invalidated_at"] is not None and ob["invalidated_at"] <= i:
                continue
            strength2 = ob.get("strength", 1) >= 2
            is_mitigation = ob.get("subtype", "ob") == "mitigation"
            if mit_mode == "exclude" and is_mitigation \
                    and setup_name == "OB_RETEST":
                continue              # SMC-13 : zone faible exclue
            mit_pen = 0.05 if (mit_mode == "penalize" and is_mitigation
                               and setup_name == "OB_RETEST") else 0.0
            if ob["kind"] == "bullish" and ctx.trend == 1 and not ctx.recent_choch_down:
                if ctx.c < ob["bottom"]:
                    continue          # zone déjà transpercée sur clôture
                if ctx.zone == "discount" or not ctx.long_ema_ok or not ctx.long_htf_ok:
                    continue          # côté momentum uniquement (cf. sweeps)
                if ctx.req_inducement and not smc.recent_sweep(
                        res, int(ob["created_at"]), "sell_side", ctx.ind_lb):
                    continue          # SMC-11 : pas de prise de liquidité avant
                sc = 0.50 + 0.10 + ctx.kz_add - mit_pen   # structure alignée
                sc += 0.10 if strength2 else 0.0
                sc += 0.10 if ctx.zone == "premium" else 0.0
                sc += 0.05 if ict.fvg_overlap(res["_all_fvgs"], i, "bullish",
                                              ob["bottom"], ob["top"]) else 0.0
                sc += 0.05 if ctx.vol_ok else 0.0
                sc += 0.05 if ctx.close[i] > ctx.open_[i] else 0.0
                sc += 0.05 if ctx.tl_tap_long else 0.0
                sc += _vp_add(ctx, ob["bottom"], ob["top"])
                sc += _inv_fvg_add(ctx, ob["bottom"], ob["top"], "long")
                sc += _candle_add(ctx, "long")
                sl = float(ob["bottom"]) - float(p["sl_buffer_atr"]) * ctx.atr
                cand = self._build_trade(res, i, "long", ctx.c, sl, ctx.atr, p,
                                         setup=setup_name, score=sc,
                                         detail=f"demande [{ob['bottom']:.6g}"
                                                f"–{ob['top']:.6g}]",
                                         trend=ctx.trend, zone=ctx.zone,
                                         extra_targets=ctx.vp_above,
                                         cal_targets=ctx.cal_tp,
                                         sd_targets=ctx.sd_up)
                if cand:
                    if ctx.smt_origin:
                        cand["_smt_ref_bar"] = int(ob["created_at"])
                    out.append(cand)
            elif ob["kind"] == "bearish" and ctx.trend == -1 and not ctx.recent_choch_up:
                if ctx.c > ob["top"]:
                    continue
                if ctx.zone == "premium" or not ctx.short_ema_ok or not ctx.short_htf_ok:
                    continue          # côté momentum uniquement (cf. sweeps)
                if ctx.req_inducement and not smc.recent_sweep(
                        res, int(ob["created_at"]), "buy_side", ctx.ind_lb):
                    continue          # SMC-11 : pas de prise de liquidité avant
                sc = 0.50 + 0.10 + ctx.kz_add - mit_pen
                sc += 0.10 if strength2 else 0.0
                sc += 0.10 if ctx.zone == "discount" else 0.0
                sc += 0.05 if ict.fvg_overlap(res["_all_fvgs"], i, "bearish",
                                              ob["bottom"], ob["top"]) else 0.0
                sc += 0.05 if ctx.vol_ok else 0.0
                sc += 0.05 if ctx.close[i] < ctx.open_[i] else 0.0
                sc += 0.05 if ctx.tl_tap_short else 0.0
                sc += _vp_add(ctx, ob["bottom"], ob["top"])
                sc += _inv_fvg_add(ctx, ob["bottom"], ob["top"], "short")
                sc += _candle_add(ctx, "short")
                sl = float(ob["top"]) + float(p["sl_buffer_atr"]) * ctx.atr
                cand = self._build_trade(res, i, "short", ctx.c, sl, ctx.atr, p,
                                         setup=setup_name, score=sc,
                                         detail=f"offre [{ob['bottom']:.6g}"
                                                f"–{ob['top']:.6g}]",
                                         trend=ctx.trend, zone=ctx.zone,
                                         extra_targets=ctx.vp_below,
                                         cal_targets=ctx.cal_tp,
                                         sd_targets=ctx.sd_dn)
                if cand:
                    if ctx.smt_origin:
                        cand["_smt_ref_bar"] = int(ob["created_at"])
                    out.append(cand)
    return out


# ── C. Retest de breaker block (OB invalidé → polarité inversée) ────────────
# Le transpercement d'un OB accompagne le plus souvent un CHoCH : le
# premier retest de la zone dans le NOUVEAU sens attrape les stops piégés.
def _check_breaker_retest(self, ctx: _SignalCtx) -> List[dict]:
    """Setup C : Retest de breaker (OB invalidé accompagnant un CHoCH)."""
    p, res, i = ctx.p, ctx.res, ctx.i
    out: List[dict] = []
    # Défaut aligné FIXED_PARAMS (false) — l'ancien défaut True réactivait
    # le setup même hors YAML.
    if not bool(p.get("use_breakers", False)):
        return out
    for brk in _at_bar(res, "_breakers_at", res["_all_breakers"], "touched_at", i):
        if i - brk["created_at"] > ctx.max_ob_age:
            continue
        if brk["invalidated_at"] is not None and brk["invalidated_at"] <= i:
            continue
        if brk["kind"] == "bullish" and ctx.trend == 1 \
                and not ctx.recent_choch_down:
            if ctx.c < brk["bottom"] or ctx.zone == "discount" \
                    or not ctx.long_ema_ok or not ctx.long_htf_ok:
                continue
            if ctx.req_inducement and not smc.recent_sweep(
                    res, int(brk["created_at"]), "sell_side", ctx.ind_lb):
                continue          # SMC-11
            sc = 0.50 + 0.10 + ctx.kz_add
            sc += 0.10 if ctx.zone == "premium" else 0.0
            sc += 0.05 if ctx.vol_ok else 0.0
            sc += 0.05 if ctx.close[i] > ctx.open_[i] else 0.0
            sc += 0.05 if ctx.tl_tap_long else 0.0
            sc += _vp_add(ctx, brk["bottom"], brk["top"])
            sc += _inv_fvg_add(ctx, brk["bottom"], brk["top"], "long")
            sc += _candle_add(ctx, "long")
            sl = float(brk["bottom"]) - float(p["sl_buffer_atr"]) * ctx.atr
            cand = self._build_trade(res, i, "long", ctx.c, sl, ctx.atr, p,
                                     setup="BREAKER_RETEST", score=sc,
                                     detail=f"breaker [{brk['bottom']:.6g}"
                                            f"–{brk['top']:.6g}]",
                                     trend=ctx.trend, zone=ctx.zone,
                                     extra_targets=ctx.vp_above,
                                     cal_targets=ctx.cal_tp,
                                     sd_targets=ctx.sd_up)
            if cand:
                if ctx.smt_origin:
                    cand["_smt_ref_bar"] = int(brk["created_at"])
                out.append(cand)
        elif brk["kind"] == "bearish" and ctx.trend == -1 \
                and not ctx.recent_choch_up:
            if ctx.c > brk["top"] or ctx.zone == "premium" \
                    or not ctx.short_ema_ok or not ctx.short_htf_ok:
                continue
            if ctx.req_inducement and not smc.recent_sweep(
                    res, int(brk["created_at"]), "buy_side", ctx.ind_lb):
                continue          # SMC-11
            sc = 0.50 + 0.10 + ctx.kz_add
            sc += 0.10 if ctx.zone == "discount" else 0.0
            sc += 0.05 if ctx.vol_ok else 0.0
            sc += 0.05 if ctx.close[i] < ctx.open_[i] else 0.0
            sc += 0.05 if ctx.tl_tap_short else 0.0
            sc += _vp_add(ctx, brk["bottom"], brk["top"])
            sc += _inv_fvg_add(ctx, brk["bottom"], brk["top"], "short")
            sc += _candle_add(ctx, "short")
            sl = float(brk["top"]) + float(p["sl_buffer_atr"]) * ctx.atr
            cand = self._build_trade(res, i, "short", ctx.c, sl, ctx.atr, p,
                                     setup="BREAKER_RETEST", score=sc,
                                     detail=f"breaker [{brk['bottom']:.6g}"
                                            f"–{brk['top']:.6g}]",
                                     trend=ctx.trend, zone=ctx.zone,
                                     extra_targets=ctx.vp_below,
                                     cal_targets=ctx.cal_tp,
                                     sd_targets=ctx.sd_dn)
            if cand:
                if ctx.smt_origin:
                    cand["_smt_ref_bar"] = int(brk["created_at"])
                out.append(cand)
    return out


# ── D. BPR_REVERSAL (SMC-06, off par défaut) ─────────────────────────────────
# Balanced Price Range : FVG haussier ∩ FVG baissier ouverts = offre et
# demande sur la même zone → forte réaction. Entrée disciplinée au CE
# (50 % de la zone) touché par la mèche, clôture du bon côté, tendance
# alignée — SL de l'autre côté de la zone.
def _check_bpr_reversal(self, ctx: _SignalCtx) -> List[dict]:
    """Setup D : Balanced Price Range (FVG haussier ∩ baissier ouverts)."""
    p, res, i = ctx.p, ctx.res, ctx.i
    out: List[dict] = []
    if not bool(p.get("use_bpr", False)):
        return out
    for z in ict.balanced_price_ranges(res["_all_fvgs"], i):
        ce = float(z["ce"])
        if ctx.trend == 1 and not ctx.recent_choch_down:
            if not (float(ctx.low[i]) <= ce <= float(ctx.high[i])) or ctx.c < ce:
                continue
            if ctx.zone == "discount" or not ctx.long_ema_ok or not ctx.long_htf_ok:
                continue
            sc = 0.50 + 0.10 + ctx.kz_add + ctx.amd_add   # structure alignée
            sc += 0.10 if ctx.zone == "premium" else 0.0
            sc += 0.05 if ctx.vol_ok else 0.0
            sc += 0.05 if ctx.close[i] > ctx.open_[i] else 0.0
            sc += 0.05 if ctx.tl_tap_long else 0.0
            sc += _vp_add(ctx, float(z["bottom"]), float(z["top"]))
            sc += _candle_add(ctx, "long")
            sl = float(z["bottom"]) - float(p["sl_buffer_atr"]) * ctx.atr
            cand = self._build_trade(res, i, "long", ctx.c, sl, ctx.atr, p,
                                     setup="BPR_REVERSAL", score=sc,
                                     detail=f"BPR CE {ce:.6g} "
                                            f"[{z['bottom']:.6g}–{z['top']:.6g}]",
                                     trend=ctx.trend, zone=ctx.zone,
                                     extra_targets=ctx.vp_above,
                                     cal_targets=ctx.cal_tp,
                                     sd_targets=ctx.sd_up)
            if cand:
                out.append(cand)
        elif ctx.trend == -1 and not ctx.recent_choch_up:
            if not (float(ctx.low[i]) <= ce <= float(ctx.high[i])) or ctx.c > ce:
                continue
            if ctx.zone == "premium" or not ctx.short_ema_ok or not ctx.short_htf_ok:
                continue
            sc = 0.50 + 0.10 + ctx.kz_add + ctx.amd_add
            sc += 0.10 if ctx.zone == "discount" else 0.0
            sc += 0.05 if ctx.vol_ok else 0.0
            sc += 0.05 if ctx.close[i] < ctx.open_[i] else 0.0
            sc += 0.05 if ctx.tl_tap_short else 0.0
            sc += _vp_add(ctx, float(z["bottom"]), float(z["top"]))
            sc += _candle_add(ctx, "short")
            sl = float(z["top"]) + float(p["sl_buffer_atr"]) * ctx.atr
            cand = self._build_trade(res, i, "short", ctx.c, sl, ctx.atr, p,
                                     setup="BPR_REVERSAL", score=sc,
                                     detail=f"BPR CE {ce:.6g} "
                                            f"[{z['bottom']:.6g}–{z['top']:.6g}]",
                                     trend=ctx.trend, zone=ctx.zone,
                                     extra_targets=ctx.vp_below,
                                     cal_targets=ctx.cal_tp,
                                     sd_targets=ctx.sd_dn)
            if cand:
                out.append(cand)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

# Mapping nom → checker. Chaque checker retourne une Liste[dict] de
# candidates (un même setup peut produire plusieurs candidates à la même
# barre : ex. sweep sell-side ET buy_side, ou PDL + PWL). L'orchestrateur
# ``_signal_at`` retient la candidate au meilleur score après filtrage SMT.
#
# Activation par param (FIXED_PARAMS / optimizer_results) — le moteur SMC
# produit toujours les structures ; seuls les setups cochés génèrent des
# ordres. trade_plans / Smart Graph ne sont pas affectés.
_SETUP_CHECKERS: Dict[str, Callable[..., List[dict]]] = {
    "SWEEP_REVERSAL":  _check_sweep_reversal,
    "CALENDAR_SWEEP":  _check_calendar_sweep,
    "OB_RETEST":       _check_ob_retest,
    "BREAKER_RETEST":  _check_breaker_retest,
    "BPR_REVERSAL":    _check_bpr_reversal,
}

# Flag param → setup(s). Calendar est un mode de use_calendar_liquidity.
_SETUP_ENABLE_FLAGS: Dict[str, str] = {
    "SWEEP_REVERSAL": "use_sweep_reversal",
    "OB_RETEST": "use_ob_retest",
    "BREAKER_RETEST": "use_breakers",
    "BPR_REVERSAL": "use_bpr",
    # CALENDAR_SWEEP : géré dans le checker via use_calendar_liquidity
}


#: Module SMC/ICT de chaque setup — §65 de la spécification, qui demande des
#: statistiques SÉPARÉES par module plutôt qu'un chiffre global.
#:
#: La découpe suit la spec : SMC Core = le moteur nu (biais HTF, liquidité,
#: sweep, displacement, MSS, FVG/OB, cible) ; ICT Session = ce qui dépend du
#: CALENDRIER et des séances ; ICT Advanced = les raffinements (breaker, BPR,
#: IFVG, SMT, OTE) dont la spec dit explicitement de ne pas mélanger les
#: statistiques avec le cœur.
#:
#: Ce n'est pas cosmétique : le YAML de `smart_money` documente déjà que
#: `BREAKER_RETEST` coûtait −163 USDC sur 220 trades en 4 h et que `BPR` n'a
#: pas d'edge stable — deux setups d'ICT Advanced. Sans découpage, ce genre de
#: constat demande une ablation manuelle à chaque fois.
SETUP_MODULES: Dict[str, str] = {
    "SWEEP_REVERSAL": "SMC_CORE",
    "OB_RETEST":      "SMC_CORE",
    "CALENDAR_SWEEP": "ICT_SESSION",
    "BREAKER_RETEST": "ICT_ADVANCED",
    "BPR_REVERSAL":   "ICT_ADVANCED",
}

#: Module des trades pris dans une fenêtre Silver Bullet quand ce module est
#: activé (§31). Reclasse le trade hors de SMC Core pour que les statistiques
#: des deux ne se mélangent pas.
MODULE_SILVER_BULLET = "ICT_SILVER_BULLET"

#: Module servi quand un setup inconnu apparaît — préférable à `None`, qui le
#: ferait disparaître des statistiques sans que rien ne le signale.
MODULE_INCONNU = "AUTRE"


