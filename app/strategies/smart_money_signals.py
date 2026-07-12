"""Construction des signaux de la stratégie ``smart_money``
(V4-L / ARCH-14) : ``_signal_at`` (détection des setups SWEEP_REVERSAL /
OB_RETEST / BREAKER_RETEST + confluences) et ``_build_trade`` (SL/TP sur les
poches de liquidité, filtres min_gain/min_rr).

Fonctions au protocole *méthode* : premier argument ``self`` = l'instance
``Strategy`` (rattachées à la classe dans smart_money.py via
``_signal_at = smart_money_signals._signal_at`` — descripteur de fonction,
donc de vraies méthodes). Extraites telles quelles, aucune logique modifiée.
"""
import logging
from typing import Any, Dict, List, Optional

import numpy as np

from app.core import ict, smc

logger = logging.getLogger(__name__)


def _signal_at(self, res: dict, i: int, open_: np.ndarray, high: np.ndarray,
               low: np.ndarray, close: np.ndarray, aux: Dict[str, Any],
               p: Dict[str, Any]) -> Optional[dict]:
    """Construit le meilleur signal SMC à la barre ``i`` (causale : seules
    les entités formées avant ``i`` sont utilisées). Retourne None si aucun
    setup ne passe les filtres (dont le gain minimal ``min_gain_pct``)."""
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
    candidates: List[dict] = []

    # ── Filtres/bonus transverses des enrichissements ─────────────────────
    # Killzones : filtre dur optionnel + bonus (sessions LDN/NY = fenêtres
    # où les desks génèrent l'essentiel des sweeps).
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
    # Biais multi-timeframe : la structure du timeframe supérieur commande.
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
    # AMD : la barre courante sweepe après une phase de compression
    # (accumulation) → manipulation probable, expansion à suivre.
    amd_here = bool(aux["comp"][i]) if aux["comp"] is not None else False
    amd_add = 0.10 if (bool(p.get("amd_bonus", False)) and amd_here) else 0.0
    # Volume profile : HVN = acceptation (support volumétrique de la zone).
    vp = None
    if bool(p.get("vp_confluence", False)) or bool(p.get("vp_targets", False)):
        vp = smc.volume_profile(aux["h"], aux["l"], aux["c"], aux["v"], i,
                                lookback=int(p["vp_lookback"]),
                                n_bins=int(p["vp_bins"]))

    def _vp_add(zone_lo: float, zone_hi: float) -> float:
        if vp is None or not bool(p.get("vp_confluence", False)):
            return 0.0
        pad = 0.25 * atr
        return 0.05 if any(zone_lo - pad <= lv <= zone_hi + pad
                           for lv in vp["hvns"]) else 0.0

    # 4c — inversion de rôle des FVG (off par défaut) : bonus si un FVG de
    # sens OPPOSÉ, déjà mitigé, chevauche la zone d'entrée.
    def _inv_fvg_add(zone_lo: float, zone_hi: float, side: str) -> float:
        if not bool(p.get("inv_fvg_bonus", False)):
            return 0.0
        return 0.05 if ict.inverted_fvg_overlap(res["_all_fvgs"], i, side,
                                                zone_lo, zone_hi) else 0.0

    # Confirmation bougie (off par défaut) : +0.05 si pin bar / engulfing
    # dans le sens du setup à la barre i (qualité par trade élevée).
    pin_a, eng_a = aux.get("pin"), aux.get("eng")

    def _candle_add(side: str) -> float:
        if pin_a is None:
            return 0.0
        sgn = 1 if side == "long" else -1
        return 0.05 if (int(pin_a[i]) == sgn or int(eng_a[i]) == sgn) else 0.0

    vp_above = sorted([lv for lv in ([vp["poc"]] + vp["hvns"])
                       if lv > c]) if (vp and p.get("vp_targets")) else []
    vp_below = sorted([lv for lv in ([vp["poc"]] + vp["hvns"])
                       if lv < c], reverse=True) \
        if (vp and p.get("vp_targets")) else []

    # SMC-03 — liquidité calendaire (off par défaut) : niveaux PDH/PDL/PWH/PWL
    # du jour/semaine UTC clôturés. Deux usages indépendants :
    #   mode "targets"/True : cibles de TP additionnelles (cal_tp) ;
    #   mode "sweeps"/True  : déclencheur SWEEP_REVERSAL (bloc dédié plus bas).
    cal_mode = p.get("use_calendar_liquidity", False)
    cal = aux.get("cal") if cal_mode else None
    cal_tp: List[float] = []
    if cal is not None and cal_mode in (True, "targets"):
        cal_tp = [float(cal[k][i]) for k in ("pdh", "pdl", "pwh", "pwl")
                  if not np.isnan(cal[k][i])]

    # SMC-05 — grille de TP en écarts-types ICT du dealing range courant
    # (−1/−2/−2.5/−4 SD au-delà du range premium/discount). Off par défaut.
    sd_up: List[float] = []
    sd_dn: List[float] = []

    # Garde CHoCH : pas d'entrée contre un changement de caractère récent.
    # Indices CHoCH pré-triés et mémoïsés sur ``res`` → lookup O(log n) par
    # barre au lieu d'un scan O(événements) (évite le O(événements²) de la
    # passe prepare_for_backtest). Résultat strictement identique.
    guard = int(p["choch_guard_bars"])
    cd, cu = self._choch_index_arrays(res)
    recent_choch_down = bool(
        np.searchsorted(cd, i, "right") - np.searchsorted(cd, i - guard, "left"))
    recent_choch_up = bool(
        np.searchsorted(cu, i, "right") - np.searchsorted(cu, i - guard, "left"))

    # Premium/discount CAUSAL à la barre i (pas l'état final de l'analyse).
    pd_zone = smc.premium_discount_at(res, high, low, close, i) or {}
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

    # Biais EMA (filtre institutionnel simple) : long au-dessus, short en
    # dessous. ema is None quand ema_filter_len=0 → filtre désactivé.
    long_ema_ok  = ema is None or c > float(ema[i])
    short_ema_ok = ema is None or c < float(ema[i])

    # Tap de trendline automatique (causal) : la mèche touche la ligne à
    # tl_tol_atr×ATR près et la clôture tient du bon côté.
    tl_tol = float(p["tl_tol_atr"]) * atr
    tl_sup = smc.trendline_value_at(res, i, "support")
    tl_res = smc.trendline_value_at(res, i, "resistance")
    tl_tap_long = (tl_sup is not None
                   and low[i] <= tl_sup + tl_tol and c > tl_sup)
    tl_tap_short = (tl_res is not None
                    and high[i] >= tl_res - tl_tol and c < tl_res)

    # ── A. Sweep reversal ────────────────────────────────────────────────
    # Filtres durs (validés sur BTC/USDC 1h→4h 2019-2026) :
    #   - AVEC la tendance uniquement : un sweep sell-side ne s'achète que
    #     dans une structure haussière (pullback qui prend la liquidité),
    #     jamais en contre-tendance (les deux pires buckets historiques).
    #   - Côté momentum du range : pas de long en zone discount ni de short
    #     en zone premium — sur crypto, la force appelle la force ; le
    #     « deep discount » d'une tendance haussière est le plus souvent
    #     une structure en train de casser.
    allow_ct = bool(p.get("allow_counter_trend", False))
    # SMC-04 — Judas swing (off par défaut) : bonus/filtre appliqué aux seuls
    # SWEEP_REVERSAL (le Judas EST un sweep d'ouverture de session).
    jd = aux.get("judas")
    jd_s = int(jd[i]) if jd is not None else 0
    jd_bonus = bool(p.get("judas_bonus", False))
    jd_filter = bool(p.get("judas_filter", False))
    for ev in res["_all_sweeps"]:
        if ev["index"] != i or not ev["rejected"]:
            continue
        if ev["kind"] == "sell_side" and not recent_choch_down:
            if (trend != 1 and not allow_ct) or zone == "discount" \
                    or not long_ema_ok or not long_htf_ok:
                continue
            if jd_filter and jd_s == -1:
                continue              # SMC-04 : Judas contredit le long
            sc = 0.50 + kz_add + amd_add
            sc += 0.05 if (jd_bonus and jd_s == 1) else 0.0
            sc += 0.10 if trend == 1 else 0.0
            sc += 0.10 if ev["source"] == "pool" else 0.0
            sc += 0.10 if zone == "premium" else 0.0
            sc += 0.05 if vol_ok else 0.0
            sc += 0.05 if close[i] > open_[i] else 0.0
            sc += 0.05 if tl_tap_long else 0.0
            sc += _vp_add(float(ev["level"]) - 0.5 * atr,
                          float(ev["level"]) + 0.5 * atr)
            sc += _inv_fvg_add(float(ev["level"]) - 0.5 * atr,
                               float(ev["level"]) + 0.5 * atr, "long")
            sc += _candle_add("long")
            sl = min(float(low[i]), float(ev["level"])) - \
                float(p["sl_buffer_atr"]) * atr
            cand = self._build_trade(res, i, "long", c, sl, atr, p,
                                     setup="SWEEP_REVERSAL", score=sc,
                                     detail=f"sweep {ev['source']} "
                                            f"{ev['level']:.6g}",
                                     trend=trend, zone=zone,
                                     extra_targets=vp_above,
                                     cal_targets=cal_tp,
                                     sd_targets=sd_up)
            if cand:
                candidates.append(cand)
        elif ev["kind"] == "buy_side" and not recent_choch_up:
            if (trend != -1 and not allow_ct) or zone == "premium" \
                    or not short_ema_ok or not short_htf_ok:
                continue
            if jd_filter and jd_s == 1:
                continue              # SMC-04 : Judas contredit le short
            sc = 0.50 + kz_add + amd_add
            sc += 0.05 if (jd_bonus and jd_s == -1) else 0.0
            sc += 0.10 if trend == -1 else 0.0
            sc += 0.10 if ev["source"] == "pool" else 0.0
            sc += 0.10 if zone == "discount" else 0.0
            sc += 0.05 if vol_ok else 0.0
            sc += 0.05 if close[i] < open_[i] else 0.0
            sc += 0.05 if tl_tap_short else 0.0
            sc += _vp_add(float(ev["level"]) - 0.5 * atr,
                          float(ev["level"]) + 0.5 * atr)
            sc += _inv_fvg_add(float(ev["level"]) - 0.5 * atr,
                               float(ev["level"]) + 0.5 * atr, "short")
            sc += _candle_add("short")
            sl = max(float(high[i]), float(ev["level"])) + \
                float(p["sl_buffer_atr"]) * atr
            cand = self._build_trade(res, i, "short", c, sl, atr, p,
                                     setup="SWEEP_REVERSAL", score=sc,
                                     detail=f"sweep {ev['source']} "
                                            f"{ev['level']:.6g}",
                                     trend=trend, zone=zone,
                                     extra_targets=vp_below,
                                     cal_targets=cal_tp,
                                     sd_targets=sd_dn)
            if cand:
                candidates.append(cand)

    # SMC-03 — sweep d'un niveau calendaire (mode "sweeps"/True) : la mèche
    # perce PDL/PWL (long) ou PDH/PWH (short) puis la clôture revient du bon
    # côté — première barre de perce uniquement (low/high[i−1] du bon côté).
    # Mêmes filtres durs et même grille de score que les sweeps du moteur ;
    # +0.10 « pool » : un niveau calendaire est une poche majeure par nature.
    if cal is not None and cal_mode in (True, "sweeps"):
        if not recent_choch_down and (trend == 1 or allow_ct) \
                and zone != "discount" and long_ema_ok and long_htf_ok \
                and not (jd_filter and jd_s == -1):
            for kname in ("pdl", "pwl"):
                lv = float(cal[kname][i])
                if np.isnan(lv) or not (float(low[i]) <= lv < float(low[i - 1])) \
                        or c <= lv:
                    continue
                sc = 0.50 + kz_add + amd_add
                sc += 0.10 if trend == 1 else 0.0
                sc += 0.10                    # niveau calendaire = pool majeur
                sc += 0.10 if zone == "premium" else 0.0
                sc += 0.05 if vol_ok else 0.0
                sc += 0.05 if close[i] > open_[i] else 0.0
                sc += 0.05 if tl_tap_long else 0.0
                sc += _vp_add(lv - 0.5 * atr, lv + 0.5 * atr)
                sc += _inv_fvg_add(lv - 0.5 * atr, lv + 0.5 * atr, "long")
                sc += _candle_add("long")
                sl = min(float(low[i]), lv) - float(p["sl_buffer_atr"]) * atr
                cand = self._build_trade(res, i, "long", c, sl, atr, p,
                                         setup="SWEEP_REVERSAL", score=sc,
                                         detail=f"sweep {kname.upper()} {lv:.6g}",
                                         trend=trend, zone=zone,
                                         extra_targets=vp_above,
                                         cal_targets=cal_tp,
                                         sd_targets=sd_up)
                if cand:
                    candidates.append(cand)
        if not recent_choch_up and (trend == -1 or allow_ct) \
                and zone != "premium" and short_ema_ok and short_htf_ok \
                and not (jd_filter and jd_s == 1):
            for kname in ("pdh", "pwh"):
                lv = float(cal[kname][i])
                if np.isnan(lv) or not (float(high[i]) >= lv > float(high[i - 1])) \
                        or c >= lv:
                    continue
                sc = 0.50 + kz_add + amd_add
                sc += 0.10 if trend == -1 else 0.0
                sc += 0.10                    # niveau calendaire = pool majeur
                sc += 0.10 if zone == "discount" else 0.0
                sc += 0.05 if vol_ok else 0.0
                sc += 0.05 if close[i] < open_[i] else 0.0
                sc += 0.05 if tl_tap_short else 0.0
                sc += _vp_add(lv - 0.5 * atr, lv + 0.5 * atr)
                sc += _inv_fvg_add(lv - 0.5 * atr, lv + 0.5 * atr, "short")
                sc += _candle_add("short")
                sl = max(float(high[i]), lv) + float(p["sl_buffer_atr"]) * atr
                cand = self._build_trade(res, i, "short", c, sl, atr, p,
                                         setup="SWEEP_REVERSAL", score=sc,
                                         detail=f"sweep {kname.upper()} {lv:.6g}",
                                         trend=trend, zone=zone,
                                         extra_targets=vp_below,
                                         cal_targets=cal_tp,
                                         sd_targets=sd_dn)
                if cand:
                    candidates.append(cand)

    # ── B. Retest d'order block / rejection block ─────────────────────────
    # Les rejection blocks (mèches de swing) partagent la même mécanique de
    # retest que les OB : zone d'offre/demande née d'un rejet violent.
    max_ob_age = int(p["ob_max_age"])
    # SMC-01 : si smt_at_origin, les candidats de retest portent la barre de
    # l'impulsion d'origine (created_at) — le SMT y sera évalué au lieu de i.
    smt_origin = bool(p.get("smt_at_origin", False)) and aux.get("smt") is not None
    # SMC-11 — inducement (off par défaut) : sweep rejeté opposé requis dans
    # les inducement_lookback barres avant l'origine de la zone (crédibilité).
    req_inducement = bool(p.get("require_inducement", False))
    ind_lb = int(p.get("inducement_lookback", 12))
    zone_sources = [("OB_RETEST", res["_all_obs"])]
    if bool(p.get("use_rejection_blocks", False)):
        zone_sources.append(("REJECTION_RETEST", res["_all_rejections"]))
    for setup_name, zone_list in zone_sources:
        for ob in zone_list:
            if ob["touched_at"] != i or i - ob["created_at"] > max_ob_age:
                continue
            if ob["invalidated_at"] is not None and ob["invalidated_at"] <= i:
                continue
            strength2 = ob.get("strength", 1) >= 2
            if ob["kind"] == "bullish" and trend == 1 and not recent_choch_down:
                if c < ob["bottom"]:
                    continue          # zone déjà transpercée sur clôture
                if zone == "discount" or not long_ema_ok or not long_htf_ok:
                    continue          # côté momentum uniquement (cf. sweeps)
                if req_inducement and not smc.recent_sweep(
                        res, int(ob["created_at"]), "sell_side", ind_lb):
                    continue          # SMC-11 : pas de prise de liquidité avant
                sc = 0.50 + 0.10 + kz_add   # structure alignée par construction
                sc += 0.10 if strength2 else 0.0
                sc += 0.10 if zone == "premium" else 0.0
                sc += 0.05 if ict.fvg_overlap(res["_all_fvgs"], i, "bullish",
                                              ob["bottom"], ob["top"]) else 0.0
                sc += 0.05 if vol_ok else 0.0
                sc += 0.05 if close[i] > open_[i] else 0.0
                sc += 0.05 if tl_tap_long else 0.0
                sc += _vp_add(ob["bottom"], ob["top"])
                sc += _inv_fvg_add(ob["bottom"], ob["top"], "long")
                sc += _candle_add("long")
                sl = float(ob["bottom"]) - float(p["sl_buffer_atr"]) * atr
                cand = self._build_trade(res, i, "long", c, sl, atr, p,
                                         setup=setup_name, score=sc,
                                         detail=f"demande [{ob['bottom']:.6g}"
                                                f"–{ob['top']:.6g}]",
                                         trend=trend, zone=zone,
                                         extra_targets=vp_above,
                                         cal_targets=cal_tp,
                                         sd_targets=sd_up)
                if cand:
                    if smt_origin:
                        cand["_smt_ref_bar"] = int(ob["created_at"])
                    candidates.append(cand)
            elif ob["kind"] == "bearish" and trend == -1 and not recent_choch_up:
                if c > ob["top"]:
                    continue
                if zone == "premium" or not short_ema_ok or not short_htf_ok:
                    continue          # côté momentum uniquement (cf. sweeps)
                if req_inducement and not smc.recent_sweep(
                        res, int(ob["created_at"]), "buy_side", ind_lb):
                    continue          # SMC-11 : pas de prise de liquidité avant
                sc = 0.50 + 0.10 + kz_add
                sc += 0.10 if strength2 else 0.0
                sc += 0.10 if zone == "discount" else 0.0
                sc += 0.05 if ict.fvg_overlap(res["_all_fvgs"], i, "bearish",
                                              ob["bottom"], ob["top"]) else 0.0
                sc += 0.05 if vol_ok else 0.0
                sc += 0.05 if close[i] < open_[i] else 0.0
                sc += 0.05 if tl_tap_short else 0.0
                sc += _vp_add(ob["bottom"], ob["top"])
                sc += _inv_fvg_add(ob["bottom"], ob["top"], "short")
                sc += _candle_add("short")
                sl = float(ob["top"]) + float(p["sl_buffer_atr"]) * atr
                cand = self._build_trade(res, i, "short", c, sl, atr, p,
                                         setup=setup_name, score=sc,
                                         detail=f"offre [{ob['bottom']:.6g}"
                                                f"–{ob['top']:.6g}]",
                                         trend=trend, zone=zone,
                                         extra_targets=vp_below,
                                         cal_targets=cal_tp,
                                         sd_targets=sd_dn)
                if cand:
                    if smt_origin:
                        cand["_smt_ref_bar"] = int(ob["created_at"])
                    candidates.append(cand)

    # ── C. Retest de breaker block (OB invalidé → polarité inversée) ────
    # Le transpercement d'un OB accompagne le plus souvent un CHoCH : le
    # premier retest de la zone dans le NOUVEAU sens attrape les stops
    # piégés. Mêmes filtres durs que les autres setups.
    if bool(p.get("use_breakers", True)):
        for brk in res["_all_breakers"]:
            if brk["touched_at"] != i or i - brk["created_at"] > max_ob_age:
                continue
            if brk["invalidated_at"] is not None and brk["invalidated_at"] <= i:
                continue
            if brk["kind"] == "bullish" and trend == 1 \
                    and not recent_choch_down:
                if c < brk["bottom"] or zone == "discount" \
                        or not long_ema_ok or not long_htf_ok:
                    continue
                if req_inducement and not smc.recent_sweep(
                        res, int(brk["created_at"]), "sell_side", ind_lb):
                    continue          # SMC-11
                sc = 0.50 + 0.10 + kz_add
                sc += 0.10 if zone == "premium" else 0.0
                sc += 0.05 if vol_ok else 0.0
                sc += 0.05 if close[i] > open_[i] else 0.0
                sc += 0.05 if tl_tap_long else 0.0
                sc += _vp_add(brk["bottom"], brk["top"])
                sc += _inv_fvg_add(brk["bottom"], brk["top"], "long")
                sc += _candle_add("long")
                sl = float(brk["bottom"]) - float(p["sl_buffer_atr"]) * atr
                cand = self._build_trade(res, i, "long", c, sl, atr, p,
                                         setup="BREAKER_RETEST", score=sc,
                                         detail=f"breaker [{brk['bottom']:.6g}"
                                                f"–{brk['top']:.6g}]",
                                         trend=trend, zone=zone,
                                         extra_targets=vp_above,
                                         cal_targets=cal_tp,
                                         sd_targets=sd_up)
                if cand:
                    if smt_origin:
                        cand["_smt_ref_bar"] = int(brk["created_at"])
                    candidates.append(cand)
            elif brk["kind"] == "bearish" and trend == -1 \
                    and not recent_choch_up:
                if c > brk["top"] or zone == "premium" \
                        or not short_ema_ok or not short_htf_ok:
                    continue
                if req_inducement and not smc.recent_sweep(
                        res, int(brk["created_at"]), "buy_side", ind_lb):
                    continue          # SMC-11
                sc = 0.50 + 0.10 + kz_add
                sc += 0.10 if zone == "discount" else 0.0
                sc += 0.05 if vol_ok else 0.0
                sc += 0.05 if close[i] < open_[i] else 0.0
                sc += 0.05 if tl_tap_short else 0.0
                sc += _vp_add(brk["bottom"], brk["top"])
                sc += _inv_fvg_add(brk["bottom"], brk["top"], "short")
                sc += _candle_add("short")
                sl = float(brk["top"]) + float(p["sl_buffer_atr"]) * atr
                cand = self._build_trade(res, i, "short", c, sl, atr, p,
                                         setup="BREAKER_RETEST", score=sc,
                                         detail=f"breaker [{brk['bottom']:.6g}"
                                                f"–{brk['top']:.6g}]",
                                         trend=trend, zone=zone,
                                         extra_targets=vp_below,
                                         cal_targets=cal_tp,
                                         sd_targets=sd_dn)
                if cand:
                    if smt_origin:
                        cand["_smt_ref_bar"] = int(brk["created_at"])
                    candidates.append(cand)

    # ── D. BPR_REVERSAL (SMC-06, off par défaut) ────────────────────────
    # Balanced Price Range : FVG haussier ∩ FVG baissier ouverts = offre et
    # demande sur la même zone → forte réaction. Entrée disciplinée au CE
    # (50 % de la zone) touché par la mèche, clôture du bon côté, tendance
    # alignée — SL de l'autre côté de la zone.
    if bool(p.get("use_bpr", False)):
        for z in ict.balanced_price_ranges(res["_all_fvgs"], i):
            ce = float(z["ce"])
            if trend == 1 and not recent_choch_down:
                if not (float(low[i]) <= ce <= float(high[i])) or c < ce:
                    continue
                if zone == "discount" or not long_ema_ok or not long_htf_ok:
                    continue
                sc = 0.50 + 0.10 + kz_add + amd_add   # structure alignée
                sc += 0.10 if zone == "premium" else 0.0
                sc += 0.05 if vol_ok else 0.0
                sc += 0.05 if close[i] > open_[i] else 0.0
                sc += 0.05 if tl_tap_long else 0.0
                sc += _vp_add(float(z["bottom"]), float(z["top"]))
                sc += _candle_add("long")
                sl = float(z["bottom"]) - float(p["sl_buffer_atr"]) * atr
                cand = self._build_trade(res, i, "long", c, sl, atr, p,
                                         setup="BPR_REVERSAL", score=sc,
                                         detail=f"BPR CE {ce:.6g} "
                                                f"[{z['bottom']:.6g}–{z['top']:.6g}]",
                                         trend=trend, zone=zone,
                                         extra_targets=vp_above,
                                         cal_targets=cal_tp,
                                         sd_targets=sd_up)
                if cand:
                    candidates.append(cand)
            elif trend == -1 and not recent_choch_up:
                if not (float(low[i]) <= ce <= float(high[i])) or c > ce:
                    continue
                if zone == "premium" or not short_ema_ok or not short_htf_ok:
                    continue
                sc = 0.50 + 0.10 + kz_add + amd_add
                sc += 0.10 if zone == "discount" else 0.0
                sc += 0.05 if vol_ok else 0.0
                sc += 0.05 if close[i] < open_[i] else 0.0
                sc += 0.05 if tl_tap_short else 0.0
                sc += _vp_add(float(z["bottom"]), float(z["top"]))
                sc += _candle_add("short")
                sl = float(z["top"]) + float(p["sl_buffer_atr"]) * atr
                cand = self._build_trade(res, i, "short", c, sl, atr, p,
                                         setup="BPR_REVERSAL", score=sc,
                                         detail=f"BPR CE {ce:.6g} "
                                                f"[{z['bottom']:.6g}–{z['top']:.6g}]",
                                         trend=trend, zone=zone,
                                         extra_targets=vp_below,
                                         cal_targets=cal_tp,
                                         sd_targets=sd_dn)
                if cand:
                    candidates.append(cand)

    if not candidates:
        return None
    # SMT divergence (off par défaut) : filtre les setups contredits par une
    # divergence de sens opposé, bonifie ceux qu'elle confirme. Appliqué une
    # seule fois ici, par candidat (côté connu) → point d'injection unique.
    smt_a = aux.get("smt")
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
    return best


# ── Construction du trade : ciblage liquidité + filtre 0.4 % ────────────
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
    tp = None
    tp_src = ""
    if side == "long":
        liq = smc.liquidity_targets_above(res, i, entry, max_age=max_age)
        vds = smc.void_targets_above(res, i, entry, max_age=max_age) \
            if use_voids else []
        vps = [lv for lv in (extra_targets or []) if lv > entry]
        cals = [lv for lv in (cal_targets or []) if lv > entry]
        sds = [lv for lv in (sd_targets or []) if lv > entry]
        targets = sorted({(lv, "liquidité") for lv in liq} |
                         {(lv, "void") for lv in vds} |
                         {(lv, "volume") for lv in vps} |
                         {(lv, "calendaire") for lv in cals} |
                         {(lv, "std_dev") for lv in sds} |
                         ({(mm, "measured")} if mm else set()))
        for level, src in targets:
            cand = level - front
            gain_pct = (cand - entry) / entry * 100.0
            if gain_pct <= 0:
                continue
            if gain_pct > min_gain and (cand - entry) / risk >= min_rr:
                tp, tp_src = cand, f"{src} {level:.6g}"
                break
    else:
        liq = smc.liquidity_targets_below(res, i, entry, max_age=max_age)
        vds = smc.void_targets_below(res, i, entry, max_age=max_age) \
            if use_voids else []
        vps = [lv for lv in (extra_targets or []) if lv < entry]
        cals = [lv for lv in (cal_targets or []) if lv < entry]
        sds = [lv for lv in (sd_targets or []) if lv < entry]
        targets = sorted({(lv, "liquidité") for lv in liq} |
                         {(lv, "void") for lv in vds} |
                         {(lv, "volume") for lv in vps} |
                         {(lv, "calendaire") for lv in cals} |
                         {(lv, "std_dev") for lv in sds} |
                         ({(mm, "measured")} if mm else set()), reverse=True)
        for level, src in targets:
            cand = level + front
            gain_pct = (entry - cand) / entry * 100.0
            if gain_pct <= 0:
                continue
            if gain_pct > min_gain and (entry - cand) / risk >= min_rr:
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
        "stop_hint": round(sl, 8),
        "tp_hint":   tp_out,
        "exit_after_bars": exit_after,
        "disable_trailing": disable_trailing,
        "trail_override": trail_override,
        "size_factor": size_factor,
        "indicators": {
            "bias":     bias_label,
            "pd_zone":  zone or None,
            "gain_pct": round(gain_pct, 3),
            "rr":       round(rr_final, 2),
            "tp_source": tp_src,
            "tp_target": round(tp, 8),      # cible affichée (info) même en trailing
            "_risk_pct": round(risk_pct, 6),  # pour le time-stop conditionnel
        },
        "reason": (f"{arrow} {setup} : {detail} — {exit_txt} "
                   f"(gain {gain_pct:.2f}% > {min_gain:g}%, RR {rr_final:.2f}), "
                   f"bias {bias_label}"),
    }
