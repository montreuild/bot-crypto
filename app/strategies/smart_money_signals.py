"""Construction des signaux de la stratégie ``smart_money``
(V4-L / ARCH-14 / ARCH-008) : ``_signal_at`` orchestrateur mince qui
dispatche vers des checkers par setup SMC (SWEEP_REVERSAL, calendar sweep,
OB_RETEST, BREAKER_RETEST, BPR_REVERSAL) via le dict ``_SETUP_CHECKERS``.
Le score final (filtre SMT + max + seuil min_score) est extrait dans
``_score_setup`` pour rendre la fonction testable par branche.

ARCH-008 — la fonction ``_signal_at`` historique (542 lignes) est découpée
en :
  * ``_build_ctx``     : calcule une fois par barre tous les filtres/bonus
                         transverses (killzones, HTF, CHoCH, premium/discount,
                         EMA, trendline, VP, calendar, SD).
  * 5 ``_check_<setup>``: checkers indépendants (testables par branche).
  * ``_score_setup``   : consolidation SMT + max(score) + seuil min_score.
Aucune logique métier modifiée ; signatures publiques préservées.

Fonctions au protocole *méthode* : premier argument ``self`` = l'instance
``Strategy`` (rattachées à la classe dans smart_money.py via
``_signal_at = smart_money_signals._signal_at`` — descripteur de fonction,
donc de vraies méthodes).
"""
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from app.core import ict, smc
from app.core.smc_quality import (
    classe_liquidite,
    meilleure_cible,
    qualite_balayage,
    qualite_displacement,
)
from app.core.smc_state import sequence_label, state_label

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Contexte transverse (filtres + bonus) calculé une fois par barre
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _SignalCtx:
    """Toutes les données dont un checker a besoin à la barre ``i``.

    Calculé une fois par barre par ``_build_ctx`` (évite la recomputation
    des filtres killzones/HTF/CHoCH/premium-discount/VP par setup). Tous
    les champs dérivés reproduisent exactement le calcul précédemment
    inliné dans le monolithe ``_signal_at``.
    """
    # Entrée brute
    res: dict
    i: int
    open_: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    aux: Dict[str, Any]
    p: Dict[str, Any]
    # Dérivés de base
    atr: float
    trend: int
    c: float
    zone: str
    # Bonus cumulatifs transverses (kz_add contient déjà le bonus Silver Bullet)
    kz_add: float
    amd_add: float
    #: Barre dans une fenêtre Silver Bullet — sert au découpage §65/§31, pas à
    #: la décision (le bonus est déjà fondu dans `kz_add`).
    in_sb: bool
    # Gates directionnels
    long_ema_ok: bool
    short_ema_ok: bool
    long_htf_ok: bool
    short_htf_ok: bool
    vol_ok: bool
    tl_tap_long: bool
    tl_tap_short: bool
    recent_choch_down: bool
    recent_choch_up: bool
    # Volume profile
    vp: Optional[dict]
    vp_above: List[float]
    vp_below: List[float]
    # Calendar liquidity (SMC-03)
    cal: Optional[dict]
    cal_mode: Any
    #: L4 — ``(niveau, clé)`` : la clé (pdh/pwl/…) porte la classe de liquidité.
    cal_tp: List[Tuple[float, str]]
    # SD projections (SMC-05)
    sd_up: List[float]
    sd_dn: List[float]
    # Setup-level flags
    allow_ct: bool
    jd_s: int
    jd_bonus: bool
    jd_filter: bool
    smt_origin: bool
    req_inducement: bool
    ind_lb: int
    max_ob_age: int
    # Confirmation bougie
    pin_a: Optional[Any]
    eng_a: Optional[Any]
    # L3 — état de structure séquentiel à la barre i (§60) et niveaux protégés
    # (§64). `struct_state` vaut "UNKNOWN" quand le module est désactivé.
    struct_state: str
    struct_seq: str
    protected_high: Optional[float]
    protected_low: Optional[float]


def _build_ctx(self, res: dict, i: int, open_: np.ndarray, high: np.ndarray,
               low: np.ndarray, close: np.ndarray, aux: Dict[str, Any],
               p: Dict[str, Any]) -> Optional[_SignalCtx]:
    """Construit le contexte transverse de la barre ``i``. Retourne None si
    un filtre dur d'entrée le rejette (ATR≤0, i<1, choppiness, killzones
    strict, Silver Bullet strict). Tous les filtres/bonus transverses sont
    calculés ici une seule fois (vs N× par setup dans la version monolithe).
    """
    volr = aux["volr"]
    ema  = aux["ema"]
    trend_arr = res["_trend_arr"]
    atr = float(res["_atr_arr"][i])
    if atr <= 0 or i < 1:
        return None
    # Filtre Choppiness (off par défaut) : pas de signal en congestion
    # (indice ≥ seuil) — ne trader qu'en tendance. Mesuré gagnant BTC 4h.
    chop = aux.get("chop")
    chop_max = float(p.get("chop_filter_max", 0.0))
    if chop is not None and chop_max > 0 and float(chop[i]) >= chop_max:
        return None
    trend = int(trend_arr[i])
    c = float(close[i])

    # ── Killzones : filtre dur optionnel + bonus ──────────────────────────
    in_kz = bool(aux["kz"][i]) if aux["kz"] is not None else False
    if bool(p.get("kz_filter", False)) and aux["kz"] is not None and not in_kz:
        return None
    kz_add = 0.05 if (bool(p.get("kz_bonus", False)) and in_kz) else 0.0
    # SMC-07 — Silver Bullet (off par défaut) : fenêtres 08/15/19 UTC (1 h),
    # filtre dur optionnel + bonus, cumulé au bonus killzone (kz_add).
    sb = aux.get("sb")
    in_sb = bool(sb[i]) if sb is not None else False
    if bool(p.get("sb_filter", False)) and sb is not None and not in_sb:
        return None
    kz_add += 0.05 if (bool(p.get("sb_bonus", False)) and in_sb) else 0.0

    # ── Biais multi-timeframe ─────────────────────────────────────────────
    htf_mode = str(p.get("htf_filter", "off"))
    htf_t = int(aux["htf"][i]) if aux["htf"] is not None else 0
    long_htf_ok, short_htf_ok = self._htf_ok(htf_mode, htf_t)
    # 1d — filtre de structure externe (off par défaut) : se COMPOSE avec le
    # gate HTF (les deux doivent autoriser le sens). Neutre si ext_trend None.
    ext = aux.get("ext_trend")
    if ext is not None:
        et = int(ext[i])
        long_htf_ok = long_htf_ok and et >= 0
        short_htf_ok = short_htf_ok and et <= 0

    # ── AMD : barre courante sweepe après une phase de compression ────────
    amd_here = bool(aux["comp"][i]) if aux["comp"] is not None else False
    amd_add = 0.10 if (bool(p.get("amd_bonus", False)) and amd_here) else 0.0

    # ── Volume profile : HVN = acceptation (support volumétrique) ─────────
    vp = None
    if bool(p.get("vp_confluence", False)) or bool(p.get("vp_targets", False)):
        vp = smc.volume_profile(aux["h"], aux["l"], aux["c"], aux["v"], i,
                                lookback=int(p["vp_lookback"]),
                                n_bins=int(p["vp_bins"]))
    vp_above = sorted([lv for lv in ([vp["poc"]] + vp["hvns"])
                       if lv > c]) if (vp and p.get("vp_targets")) else []
    vp_below = sorted([lv for lv in ([vp["poc"]] + vp["hvns"])
                       if lv < c], reverse=True) \
        if (vp and p.get("vp_targets")) else []

    # ── SMC-03 — liquidité calendaire ────────────────────────────────────
    # mode "targets" : cibles de TP additionnelles (cal_tp) ;
    # mode "sweeps"  : déclencheur SWEEP_REVERSAL (cf. _check_calendar_sweep).
    cal_mode = p.get("use_calendar_liquidity", False)
    cal = aux.get("cal") if cal_mode else None
    # L4 (§77) — la CLÉ est conservée : « pdh » et « pwh » n'ont pas la même
    # importance dans la hiérarchie de liquidité, et un simple niveau les
    # rendait indiscernables.
    cal_tp: List[Tuple[float, str]] = []
    if cal is not None and cal_mode in (True, "targets"):
        cal_tp = [(float(cal[k][i]), k) for k in ("pdh", "pdl", "pwh", "pwl")
                  if not np.isnan(cal[k][i])]

    # ── SMC-05 — grille de TP en écarts-types ICT ────────────────────────
    sd_up: List[float] = []
    sd_dn: List[float] = []

    # ── Garde CHoCH : pas d'entrée contre un changement de caractère ─────
    # Indices pré-triés et mémoïsés sur ``res`` → lookup O(log n) par barre.
    guard = int(p["choch_guard_bars"])
    cd, cu = self._choch_index_arrays(res)
    recent_choch_down = bool(
        np.searchsorted(cd, i, "right") - np.searchsorted(cd, i - guard, "left"))
    recent_choch_up = bool(
        np.searchsorted(cu, i, "right") - np.searchsorted(cu, i - guard, "left"))

    # ── Premium/discount CAUSAL à la barre i ─────────────────────────────
    pd_zone = smc.premium_discount_at(
        res, high, low, close, i,
        mode=str(p.get("pd_mode", "swing")),
        ipda_lookback=int(p.get("ipda_lookback", 40))) or {}
    zone = pd_zone.get("zone", "")
    if bool(p.get("tp_std_dev", False)) and pd_zone:
        _rlo = float(pd_zone.get("range_low", 0.0) or 0.0)
        _rhi = float(pd_zone.get("range_high", 0.0) or 0.0)
        if _rhi > _rlo > 0:
            sd_up = [d["level"] for d in
                     ict.std_dev_projections(_rlo, _rhi, "up")]
            sd_dn = [d["level"] for d in
                     ict.std_dev_projections(_rlo, _rhi, "down")]
    vol_ok = bool(volr[i] > float(p["vol_confluence"])) if i < len(volr) else False

    # ── Biais EMA (filtre institutionnel simple) ─────────────────────────
    long_ema_ok  = ema is None or c > float(ema[i])
    short_ema_ok = ema is None or c < float(ema[i])

    # ── Tap de trendline automatique (causal) ────────────────────────────
    tl_tol = float(p["tl_tol_atr"]) * atr
    tl_sup = smc.trendline_value_at(res, i, "support")
    tl_res = smc.trendline_value_at(res, i, "resistance")
    tl_tap_long = (tl_sup is not None
                   and low[i] <= tl_sup + tl_tol and c > tl_sup)
    tl_tap_short = (tl_res is not None
                    and high[i] >= tl_res - tl_tol and c < tl_res)

    # ── Setup-level flags ────────────────────────────────────────────────
    allow_ct = bool(p.get("allow_counter_trend", False))
    jd = aux.get("judas")
    jd_s = int(jd[i]) if jd is not None else 0
    jd_bonus = bool(p.get("judas_bonus", False))
    jd_filter = bool(p.get("judas_filter", False))
    smt_origin = bool(p.get("smt_at_origin", False)) and aux.get("smt") is not None
    req_inducement = bool(p.get("require_inducement", False))
    ind_lb = int(p.get("inducement_lookback", 12))
    max_ob_age = int(p["ob_max_age"])

    # ── Confirmation bougie (off par défaut) : +0.05 si pin bar/engulfing ─
    pin_a, eng_a = aux.get("pin"), aux.get("eng")

    # L3 — état de structure séquentiel de la barre (§60/§64).
    _st = aux.get("struct")
    if _st is not None and i < len(_st["states"]):
        st_lbl = state_label(int(_st["states"][i]))
        st_seq = sequence_label(int(_st["sequences"][i]))
        _ph, _pl = float(_st["protected_high"][i]), float(_st["protected_low"][i])
        prot_h = None if _ph != _ph else _ph          # NaN → None
        prot_l = None if _pl != _pl else _pl
    else:
        st_lbl, st_seq, prot_h, prot_l = "UNKNOWN", "NONE", None, None

    return _SignalCtx(
        res=res, i=i, open_=open_, high=high, low=low, close=close,
        aux=aux, p=p, atr=atr, trend=trend, c=c, zone=zone,
        kz_add=kz_add, amd_add=amd_add, in_sb=in_sb,
        long_ema_ok=long_ema_ok, short_ema_ok=short_ema_ok,
        long_htf_ok=long_htf_ok, short_htf_ok=short_htf_ok,
        vol_ok=vol_ok, tl_tap_long=tl_tap_long, tl_tap_short=tl_tap_short,
        recent_choch_down=recent_choch_down, recent_choch_up=recent_choch_up,
        vp=vp, vp_above=vp_above, vp_below=vp_below,
        cal=cal, cal_mode=cal_mode, cal_tp=cal_tp,
        sd_up=sd_up, sd_dn=sd_dn,
        allow_ct=allow_ct, jd_s=jd_s, jd_bonus=jd_bonus, jd_filter=jd_filter,
        smt_origin=smt_origin, req_inducement=req_inducement,
        ind_lb=ind_lb, max_ob_age=max_ob_age,
        pin_a=pin_a, eng_a=eng_a,
        struct_state=st_lbl, struct_seq=st_seq,
        protected_high=prot_h, protected_low=prot_l,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers de bonus transverses (closure → fonction module-level sur ctx)
# ─────────────────────────────────────────────────────────────────────────────

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
    for ev in res["_all_sweeps"]:
        if ev["index"] != i or not ev["rejected"]:
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
        for ob in zone_list:
            if ob["touched_at"] != i or i - ob["created_at"] > ctx.max_ob_age:
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
    for brk in res["_all_breakers"]:
        if brk["touched_at"] != i or i - brk["created_at"] > ctx.max_ob_age:
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


def setup_module(setup: Optional[str]) -> Optional[str]:
    """Module d'un setup, ou ``None`` si le signal n'en porte pas."""
    if not setup:
        return None
    return SETUP_MODULES.get(str(setup), MODULE_INCONNU)


def _setup_enabled(name: str, p: Dict[str, Any]) -> bool:
    """True si le setup est autorisé pour la stratégie (défaut = on pour le cœur)."""
    if name == "CALENDAR_SWEEP":
        mode = p.get("use_calendar_liquidity", False)
        return mode in (True, "sweeps")
    if name == "OB_RETEST":
        # OB et rejection partagent le checker ; un des deux suffit.
        return (bool(p.get("use_ob_retest", True))
                or bool(p.get("use_rejection_blocks", False)))
    flag = _SETUP_ENABLE_FLAGS.get(name)
    if flag is None:
        return True
    # Cœur historique on par défaut si clé absente ; breakers/bpr off.
    default = flag in ("use_sweep_reversal", "use_ob_retest")
    return bool(p.get(flag, default))


# ─────────────────────────────────────────────────────────────────────────────
#  Sélection finale : filtre SMT + max(score) + seuil min_score
# ─────────────────────────────────────────────────────────────────────────────

def _score_setup(candidates: List[dict], smt_a: Optional[np.ndarray],
                 i: int, p: Dict[str, Any],
                 in_sb: bool = False) -> Optional[dict]:
    """Filtrage SMT (optionnel) puis max(score) arrondi à [0,1] et seuil
    interne ``min_score``. Retourne None si tous les candidats sont filtrés
    ou si le meilleur score reste sous le seuil.

    SMT divergence (off par défaut) : filtre les setups contredits par une
    divergence de sens opposé, bonifie ceux qu'elle confirme. Appliqué une
    seule fois ici, par candidat (côté connu) → point d'injection unique.
    """
    if not candidates:
        return None
    if smt_a is not None and 0 <= i < len(smt_a):
        use_filter = bool(p.get("smt_filter", False))
        use_bonus = bool(p.get("smt_bonus", False))
        kept: List[dict] = []
        for cand in candidates:
            # SMC-01 : barre de référence = origine de la zone si portée
            # (smt_at_origin), sinon barre courante (comportement historique).
            ref = cand.pop("_smt_ref_bar", i)
            s = int(smt_a[ref]) if 0 <= ref < len(smt_a) else 0
            sgn = 1 if cand["side"] == "long" else -1
            if use_filter and s == -sgn:
                continue
            if use_bonus and s == sgn:
                cand["score"] += float(p.get("smt_conf", 0.05))
            kept.append(cand)
        if not kept:
            return None
        candidates = kept
    best = max(candidates, key=lambda x: x["score"])
    best["score"] = round(min(best["score"], 1.0), 3)
    # Seuil interne par TF (surchargable par optimizer_results, contrairement
    # à score_threshold) : mêmes sémantiques que le seuil de l'engine.
    if best["score"] < float(p.get("min_score", 0.0)):
        return None

    # §31 — Silver Bullet : ne JAMAIS mélanger ses statistiques avec le SMC
    # Core. Le module existait déjà comme bonus/filtre transverse (`sb_bonus`,
    # `sb_filter`), donc ses trades étaient comptés avec les autres et son
    # apport propre restait invisible.
    #
    # Reclassement plutôt que duplication : un trade pris dans une fenêtre
    # Silver Bullet EST un trade Silver Bullet, quel que soit le setup qui l'a
    # déclenché. Et le reclassement n'a lieu que si l'opérateur a activé le
    # module — les deux drapeaux valent False par défaut, donc aucune analyse
    # existante ne change de découpage.
    if in_sb and (p.get("sb_bonus") or p.get("sb_filter")):
        best["module"] = MODULE_SILVER_BULLET
    return best


# ─────────────────────────────────────────────────────────────────────────────
#  Orchestrateur (ARCH-008 : 542 → ~25 lignes)
# ─────────────────────────────────────────────────────────────────────────────

def _signal_at(self, res: dict, i: int, open_: np.ndarray, high: np.ndarray,
               low: np.ndarray, close: np.ndarray, aux: Dict[str, Any],
               p: Dict[str, Any]) -> Optional[dict]:
    """Construit le meilleur signal SMC à la barre ``i`` (causale : seules
    les entités formées avant ``i`` sont utilisées). Retourne None si aucun
    setup ne passe les filtres (dont le gain minimal ``min_gain_pct``).

    ARCH-008 — la fonction historique de 542 lignes est désormais un
    orchestrateur en 3 temps :
      1. ``_build_ctx``      : calcule une fois les filtres/bonus transverses.
      2. ``_SETUP_CHECKERS`` : itère sur chaque checker, collecte les candidates.
      3. ``_score_setup``    : filtre SMT + max(score) + seuil min_score.
    Chaque branche (setup) est désormais testable indépendamment en
    injectant un ``_SignalCtx`` minimal.
    """
    ctx = _build_ctx(self, res, i, open_, high, low, close, aux, p)
    if ctx is None:
        return None
    candidates: List[dict] = []
    for name, checker in _SETUP_CHECKERS.items():
        if not _setup_enabled(name, p):
            continue
        candidates.extend(checker(self, ctx))
    if not candidates:
        return None
    best = _score_setup(candidates, aux.get("smt"), i, p, in_sb=ctx.in_sb)
    if best is None:
        return None
    # L3 — la porte de structure filtre, l'état est TOUJOURS journalisé : sans
    # le second, on ne pourrait pas mesurer si le premier vaut quelque chose.
    if not _structure_autorise(ctx.struct_state, best["side"],
                               p.get("structure_gate", False)):
        return None
    best["structure_state"] = ctx.struct_state
    best["sequence_type"] = ctx.struct_seq
    _stamp_l6(best, ctx, p)
    if best.get("tier") == "D" and bool(p.get("tier_gate", False)):
        return None
    return best


# ─────────────────────────────────────────────────────────────────────────────
#  L6 — décomposition du score, séquence, tier, identité d'événement
# ─────────────────────────────────────────────────────────────────────────────

#: §71 — facteur de risque par tier. Se branche sur le `size_factor` NATIF
#: (Backtester et live le bornent déjà à [0, 2]) : pas de second mécanisme.
FACTEUR_TIER = {"A": 1.0, "B": 0.85, "C": 0.5, "D": 0.0}


def _tier(sequence: str, score: float, p: Dict[str, Any]) -> str:
    """§71 — A continuation ou retournement confirmé, B retournement en cours,
    C retournement précoce (moitié du risque), D non confirmé.

    Le tier vient d'ABORD de la séquence, ensuite du score : un score élevé sur
    un retournement non confirmé reste un pari, et c'est précisément ce que la
    hiérarchie de tiers sert à dire.
    """
    seuil_a = float(p.get("tier_a_score", 0.85))
    if sequence in ("CONTINUATION", "REVERSAL"):
        return "A" if score >= seuil_a else "B"
    if sequence == "EARLY_REVERSAL":
        return "C"
    if sequence == "FAILED_REVERSAL":
        return "B"      # l'échec du retournement EST la continuation (§73)
    return "D"


def _stamp_l6(sig: dict, ctx: _SignalCtx, p: Dict[str, Any]) -> None:
    """Pose la décomposition du score, l'identité d'événement et le tier.

    ``sequence_id`` et ``market_event_id`` sont **déterministes**, pas des
    UUID comme le suggère §72 : dérivés de la barre d'origine, ils sont
    reproductibles d'un run à l'autre, ce qu'un UUID interdit — et c'est la
    reproductibilité qui rend une analyse par séquence exploitable.
    """
    i = ctx.i
    # §20 — décomposition sur les axes de la spécification, calculée à partir
    # des notes de qualité de L4. Elle N'EST PAS le score qui décide (celui-ci
    # reste l'additif historique, mesuré) : les deux sont publiés côte à côte
    # pour être comparés par ablation, comme §3.2 du plan l'exige.
    atr = ctx.atr or 1e-9
    disp = qualite_displacement(i, ctx.open_, ctx.high, ctx.low, ctx.close, atr,
                                casse_structure=ctx.struct_seq != "NONE")
    balayage = 0.0
    barre_evenement = i
    for ev in ctx.res.get("_all_sweeps") or ():
        if ev.get("index") == i:
            balayage = qualite_balayage(ev, ctx.high, ctx.low, ctx.close,
                                        ctx.open_, atr)
            barre_evenement = int(ev["index"])
            break
    zone_ok = 1.0 if ((sig["side"] == "long" and ctx.zone == "discount")
                      or (sig["side"] == "short" and ctx.zone == "premium")) else 0.0
    htf_ok = 1.0 if (ctx.long_htf_ok if sig["side"] == "long"
                     else ctx.short_htf_ok) else 0.0
    sig["score_breakdown"] = {
        "htf": round(htf_ok, 3),
        "sweep": round(balayage, 3),
        "displacement": round(disp, 3),
        "structure": 1.0 if ctx.struct_seq in ("CONTINUATION", "REVERSAL") else 0.0,
        "premium_discount": zone_ok,
        "timing": 1.0 if ctx.kz_add > 0 else 0.0,
    }
    sig["htf_bias"] = {1: "haussier", -1: "baissier", 0: "neutre"}[int(ctx.trend)]
    sig["session"] = (sig.get("indicators") or {}).get("session")
    sig["sequence_id"] = f"{ctx.struct_state}:{barre_evenement}"
    # §97 — un balayage et son displacement forment UN événement : tous les
    # setups qui en découlent le partagent, et un seul trade primaire en sort.
    sig["market_event_id"] = f"{sig['side']}:{barre_evenement}"
    sig["tier"] = _tier(ctx.struct_seq, float(sig.get("score", 0.0)), p)
    if bool(p.get("tier_sizing", False)):
        facteur = FACTEUR_TIER.get(sig["tier"], 1.0)
        sig["size_factor"] = round(
            float(sig.get("size_factor", 1.0)) * facteur, 4)


#: États dans lesquels une entrée est autorisée, par sens. Un `*_WARNING` est
#: un avertissement de retournement : y prendre le sens NAISSANT, c'est jouer
#: un retournement non confirmé (tier C de §71) — autorisé, mais mesurable à
#: part. Un `*_PULLBACK` reste dans le sens de la tendance mère (§82).
_ETATS_LONG = frozenset({
    "BULLISH", "BULLISH_PULLBACK", "BULLISH_CONFIRMED", "BULLISH_WARNING",
    "REVERSAL_BULLISH_PENDING", "FAILED_BEARISH_REVERSAL",
})
_ETATS_SHORT = frozenset({
    "BEARISH", "BEARISH_PULLBACK", "BEARISH_CONFIRMED", "BEARISH_WARNING",
    "REVERSAL_BEARISH_PENDING", "FAILED_BULLISH_REVERSAL",
})


def _structure_autorise(etat: str, side: str, mode) -> bool:
    """Trois modes, mesurés séparément (cf. docs/MOTEUR_STRUCTURE_SEQUENTIEL.md).

    ``direction``    l'entrée doit aller dans le sens de l'état ;
    ``no_pullback``  refuse les entrées prises en `*_PULLBACK` ;
    ``both``         les deux.

    ``no_pullback`` n'est pas dans la spécification : il vient de la mesure, qui
    a montré que les états de pullback sont le pire compartiment — alors que la
    spécification suppose que ce sont les avertissements.
    """
    if not mode or mode == "off" or etat in ("UNKNOWN", "RANGING"):
        return True     # pas d'information ⇒ pas de veto
    mode = "direction" if mode is True else str(mode)
    if mode in ("no_pullback", "both") and etat.endswith("_PULLBACK"):
        return False
    if mode in ("direction", "both"):
        return etat in (_ETATS_LONG if side == "long" else _ETATS_SHORT)
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  Construction du trade : ciblage liquidité + filtre 0.4 %
#  (inchangé — non concerné par ARCH-008)
# ─────────────────────────────────────────────────────────────────────────────
def _build_trade(self, res: dict, i: int, side: str, entry: float,
                 sl: float, atr: float, p: Dict[str, Any],
                 setup: str, score: float, detail: str,
                 trend: int = 0, zone: str = "",
                 extra_targets: Optional[List[float]] = None,
                 cal_targets: Optional[List[float]] = None,
                 sd_targets: Optional[List[float]] = None) -> Optional[dict]:
    risk = (entry - sl) if side == "long" else (sl - entry)
    if risk <= 0 or entry <= 0:
        return None
    # ── L4 (§32 §23) — anti-FOMO ─────────────────────────────────────────────
    # Le stop est posé sur le POI (zone, balayage) plus une marge : la distance
    # entrée→stop EST donc la distance au POI. Au-delà du plafond, le prix a
    # quitté sa zone et l'entrée court après le mouvement.
    max_stop = float(p.get("max_stop_atr", 0) or 0)
    if max_stop > 0 and atr > 0 and risk > max_stop * atr:
        return None
    min_gain = float(p["min_gain_pct"])
    min_rr   = float(p["min_rr"])
    front    = float(p["tp_front_run_atr"]) * atr
    max_age  = int(p["pool_max_age"])

    # Cibles opposées : poches de liquidité + bords des liquidity voids non
    # comblés (zones fines = aimants). Première cible satisfaisant à la
    # fois le gain minimal (0.4 % par défaut) ET le RR minimal.
    use_voids = bool(p.get("use_void_targets", True))
    # 4b — cible optionnelle par symétrie de jambe (off par défaut) : entre
    # en concurrence avec les cibles liquidité/void/volume.
    mm = (ict.measured_move_target(res["_all_swings"], i, side, entry)
          if bool(p.get("tp_measured_move", False)) else None)
    sens = 1 if side == "long" else -1
    if side == "long":
        liq = smc.liquidity_targets_above(res, i, entry, max_age=max_age)
        vds = smc.void_targets_above(res, i, entry, max_age=max_age) \
            if use_voids else []
    else:
        liq = smc.liquidity_targets_below(res, i, entry, max_age=max_age)
        vds = smc.void_targets_below(res, i, entry, max_age=max_age) \
            if use_voids else []

    def au_dela(lv: float) -> bool:
        return (lv > entry) if sens > 0 else (lv < entry)

    targets = sorted(
        {(lv, "pool") for lv in liq} |
        {(lv, "void") for lv in vds} |
        {(lv, "hvn") for lv in (extra_targets or []) if au_dela(lv)} |
        {(lv, cle) for lv, cle in (cal_targets or []) if au_dela(lv)} |
        {(lv, "std_dev") for lv in (sd_targets or []) if au_dela(lv)} |
        ({(mm, "measured")} if mm else set()),
        reverse=(sens < 0))

    # ── L4 (§78 §79) — choix de la cible ─────────────────────────────────────
    # Historiquement : la PREMIÈRE cible éligible, donc toujours la plus proche.
    # L0 a mesuré ce que ça donne — cible atteinte 28 à 36 % du temps. Le mode
    # `expected_value` classe par `poids de classe × gain`, ce qui privilégie
    # une poche hebdomadaire un peu plus loin à un swing local tout proche.
    # L'éligibilité (gain minimal, R/R) ne change pas : seul le CHOIX change.
    tp = None
    tp_src = ""
    if str(p.get("target_mode", "nearest")) == "expected_value":
        best_t = meilleure_cible(list(targets), entry, risk, side, min_rr,
                                 min_gain, front_run=front)
        if best_t:
            tp = best_t["price"]
            tp_src = f"{best_t['source']} {best_t['price'] + sens * front:.6g}"
    else:
        for level, src in targets:
            cand = level - sens * front
            gain_pct = (cand - entry) / entry * 100.0 * sens
            if gain_pct <= 0:
                continue
            if gain_pct > min_gain and (cand - entry) * sens / risk >= min_rr:
                tp, tp_src = cand, f"{src} {level:.6g}"
                break

    # Fallback : cible en multiple de R si aucune poche exploitable.
    if tp is None:
        rr = float(p["tp_rr_fallback"])
        tp = entry + rr * risk if side == "long" else entry - rr * risk
        tp_src = f"{rr:g}R"

    gain_pct = abs(tp - entry) / entry * 100.0
    rr_final = abs(tp - entry) / risk

    # ⚠ FILTRE : gain potentiel > min_gain_pct sinon position rejetée.
    if gain_pct <= min_gain or rr_final < min_rr:
        return None

    bias_label = {1: "haussier", -1: "baissier", 0: "neutre"}[int(trend)]
    arrow = "LONG" if side == "long" else "SHORT"
    ts_bars = int(p.get("time_stop_bars", 0) or 0)
    use_trailing = bool(p.get("use_trailing", False))
    risk_pct = risk / entry * 100.0

    if use_trailing:
        # Mode trailing : on laisse courir (pas de TP fixe → take_profit None),
        # le TrailingStopManager du Backtester gère la sortie. Le time-stop
        # devient CONDITIONNEL (via check_early_exit) : il ne coupe que les
        # trades stagnants (MFE < ts_profit_r×R), jamais un gagnant qui court.
        tp_out = None
        exit_after = None
        trail_override = {"trail_wide": float(p.get("trail_mult", 2.5)),
                          "mode": "dynamic"}
        disable_trailing = False
        exit_txt = f"trailing {p.get('trail_mult', 2.5):g}×ATR"
    else:
        tp_out = round(tp, 8)
        exit_after = ts_bars if ts_bars > 0 else None
        trail_override = None
        disable_trailing = True
        exit_txt = f"TP {tp_src}"

    # ── L1 (§29 §30) — sorties partielles TP1 / TP2 / runner ─────────────────
    # Off par défaut : les `optimizer_results` du YAML ont été mesurés en
    # tout-ou-rien, les rendre partiels en silence invaliderait ces réglages.
    # TP1 est un multiple de R (niveau mécanique), TP2 la poche de liquidité
    # visée (niveau structurel), le runner suit la structure.
    exits_spec = None
    if bool(p.get("use_partial_exits", False)):
        f1 = float(p.get("tp1_fraction", 0.25))
        f2 = float(p.get("tp2_fraction", 0.25))
        exits_spec = []
        if f1 > 0:
            exits_spec.append({"r": float(p.get("tp1_r", 1.0)),
                               "fraction": f1, "reason": "tp1"})
        if f2 > 0 and tp is not None:
            exits_spec.append({"price": round(tp, 8), "fraction": f2,
                               "reason": f"tp2_{tp_src.split()[0]}"})
        # Le runner a besoin du trailing : sans lui il n'aurait plus de sortie
        # une fois les jambes prises, et courrait jusqu'au stop initial.
        tp_out = None
        exit_after = None
        disable_trailing = False
        trail_override = {"trail_wide": float(p.get("trail_mult", 2.5)),
                          "mode": str(p.get("trail_mode", "structure")),
                          "struct_buffer_atr": float(p.get("sl_buffer_atr", 0.25))}
        exit_txt = (f"TP1 {f1:.0%}@{p.get('tp1_r', 1.0):g}R + TP2 {f2:.0%}@"
                    f"{tp_src} + runner {1 - f1 - f2:.0%} "
                    f"({trail_override['mode']})")

    # Sizing pondéré par confluence : on alloue plus aux setups à forte
    # confluence via le hook natif size_factor (borné [0.4, 1.7] ; le
    # Backtester/live re-bornent à [0, 2]). Centré sur size_conf_center ⇒
    # exposition globale ≈ inchangée. Absent (=1.0) si désactivé.
    size_factor = 1.0
    if bool(p.get("size_by_confluence", False)):
        slope = float(p.get("size_conf_slope", 3.0))
        center = float(p.get("size_conf_center", 0.83))
        size_factor = max(0.4, min(1.7, 1.0 + slope * (score - center)))

    return {
        "score": score, "side": side, "name": self.name, "atr": atr,
        "setup": setup,
        # §65 — le moteur agrège `by_module` à partir de ce champ.
        "module": setup_module(setup),
        "stop_hint": round(sl, 8),
        "tp_hint":   tp_out,
        "exit_after_bars": exit_after,
        "disable_trailing": disable_trailing,
        "trail_override": trail_override,
        "exits": exits_spec,
        "size_factor": size_factor,
        "indicators": {
            "bias":     bias_label,
            "pd_zone":  zone or None,
            "gain_pct": round(gain_pct, 3),
            "rr":       round(rr_final, 2),
            "tp_source": tp_src,
            # L4 (§77) — classe de la cible visée, pour `by_target_class`.
            "tp_class": classe_liquidite(tp_src.split()[0] if tp_src else ""),
            "tp_target": round(tp, 8),      # cible affichée (info) même en trailing
            "_risk_pct": round(risk_pct, 6),  # pour le time-stop conditionnel
        },
        "reason": (f"{arrow} {setup} : {detail} — {exit_txt} "
                   f"(gain {gain_pct:.2f}% > {min_gain:g}%, RR {rr_final:.2f}), "
                   f"bias {bias_label}"),
    }
