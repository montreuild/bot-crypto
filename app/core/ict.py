"""Détecteurs ICT (Inner Circle Trader) automatisables — briques réutilisables,
pures et CAUSALES, en complément du moteur smart_money (``app/core/smc.py``).

Ces fonctions ne sont câblées à aucune stratégie : ce sont des primitives que
l'on branchera et MESURERA ensuite (discipline du projet : activer seulement ce
qu'une mesure justifie). Deux familles :

  • sur entités SMC (FVG / breakers) : Consequent Encroachment, Balanced Price
    Range, Unicorn model, projections en écarts-types ;
  • sur OHLCV + temps : Silver Bullet windows, Judas Swing, SMT divergence
    (nécessite un actif corrélé aligné).

Toutes causales : un signal à la barre ``i`` n'utilise que des données ≤ ``i``.
"""
from typing import List, Optional

import numpy as np
import polars as pl

# ══════════════════════════════════════════════════════════════════════════════
#  Sur entités SMC (FVG / breakers)
# ══════════════════════════════════════════════════════════════════════════════

def consequent_encroachment(top: float, bottom: float) -> float:
    """Consequent Encroachment (CE) : niveau 50 % d'un FVG — l'entrée « propre »
    ICT (on veut voir le prix atteindre le CE, pas juste effleurer le gap)."""
    return 0.5 * (float(top) + float(bottom))


def _open_fvg(fv: dict, i: int) -> bool:
    """FVG formé strictement avant i et non encore comblé (causal)."""
    if fv["index"] >= i:
        return False
    return not (fv["filled_at"] is not None and fv["filled_at"] <= i)


def balanced_price_ranges(fvgs: List[dict], i: int) -> List[dict]:
    """Balanced Price Range (BPR) : chevauchement d'un FVG haussier ET d'un FVG
    baissier (offre + demande sur la même zone de prix) → zone d'inversion à
    forte réaction. Retourne les zones actives à la barre ``i`` (causal)."""
    bulls = [f for f in fvgs if f["kind"] == "bullish" and _open_fvg(f, i)]
    bears = [f for f in fvgs if f["kind"] == "bearish" and _open_fvg(f, i)]
    out = []
    for a in bulls:
        for b in bears:
            lo = max(float(a["bottom"]), float(b["bottom"]))
            hi = min(float(a["top"]), float(b["top"]))
            if hi > lo:                       # chevauchement réel
                out.append({"bottom": lo, "top": hi,
                            "ce": consequent_encroachment(hi, lo),
                            "bull_index": a["index"], "bear_index": b["index"]})
    return out


def unicorn_zones(breakers: List[dict], fvgs: List[dict], i: int) -> List[dict]:
    """Unicorn model : un Breaker block chevauché par un FVG de MÊME polarité
    (breaker haussier + FVG haussier) → confluence ICT haute probabilité.
    Zones actives (breaker non invalidé, FVG ouvert) à la barre ``i`` (causal)."""
    out = []
    for brk in breakers:
        if brk.get("created_at", 0) >= i:
            continue
        if brk.get("invalidated_at") is not None and brk["invalidated_at"] <= i:
            continue
        for f in fvgs:
            if f["kind"] != brk["kind"] or not _open_fvg(f, i):
                continue
            lo = max(float(brk["bottom"]), float(f["bottom"]))
            hi = min(float(brk["top"]), float(f["top"]))
            if hi > lo:
                out.append({"kind": brk["kind"], "bottom": lo, "top": hi,
                            "ce": consequent_encroachment(hi, lo)})
    return out


def fvg_overlap(fvgs: List[dict], i: int, kind: str,
                zone_lo: float, zone_hi: float) -> bool:
    """True si un FVG OUVERT de même direction (``kind``) chevauche [lo, hi] à la
    barre ``i`` (formé avant i, non comblé). Causal."""
    for fv in fvgs:
        if fv["kind"] != kind or fv["index"] >= i:
            continue
        if fv["filled_at"] is not None and fv["filled_at"] <= i:
            continue
        if fv["bottom"] <= zone_hi and fv["top"] >= zone_lo:
            return True
    return False


def fvg_overlap_ce(fvgs: List[dict], i: int, kind: str,
                   zone_lo: float, zone_hi: float):
    """CE (niveau 50 %) du premier FVG OUVERT de même direction chevauchant
    [lo, hi] à la barre ``i`` — None si aucun. Variante de ``fvg_overlap``
    exposant le niveau d'entrée « propre » ICT (SMC-06)."""
    for fv in fvgs:
        if fv["kind"] != kind or fv["index"] >= i:
            continue
        if fv["filled_at"] is not None and fv["filled_at"] <= i:
            continue
        if fv["bottom"] <= zone_hi and fv["top"] >= zone_lo:
            return consequent_encroachment(fv["top"], fv["bottom"])
    return None


def inverted_fvg_overlap(fvgs: List[dict], i: int, side: str,
                         zone_lo: float, zone_hi: float) -> bool:
    """Inversion de rôle (IFVG) : un FVG de sens OPPOSÉ, DÉJÀ mitigé avant i, qui
    chevauche [lo, hi] agit en support (long) / résistance (short) inversé.
    Causal (``mitigated_at`` < i)."""
    want = "bearish" if side == "long" else "bullish"
    for fv in fvgs:
        if fv["kind"] != want:
            continue
        m = fv["mitigated_at"]
        if m is None or m >= i:
            continue
        if fv["bottom"] <= zone_hi and fv["top"] >= zone_lo:
            return True
    return False


def measured_move_target(swings: List[dict], i: int, side: str,
                         entry: float) -> Optional[float]:
    """Projection par symétrie de jambe (measured move) : amplitude de la
    dernière jambe de structure COMPLÈTE avant i (deux derniers swings
    confirmés), projetée depuis l'entrée. Causal. None si indisponible."""
    sw = [s for s in swings if s["confirmed_at"] < i]
    if len(sw) < 2:
        return None
    sw = sorted(sw, key=lambda s: s["index"])[-2:]
    amp = abs(float(sw[-1]["price"]) - float(sw[-2]["price"]))
    if amp <= 0:
        return None
    lvl = entry + amp if side == "long" else entry - amp
    ok = (lvl > entry) if side == "long" else (lvl < entry)
    return lvl if ok else None


def propulsion_block(obs: List[dict], i: int) -> List[dict]:
    """Propulsion block : un Order Block qui se forme À L'INTÉRIEUR d'un OB
    ANTÉRIEUR de même polarité (OB sur OB). Retesté, il « propulse » le prix.
    Zones actives à la barre ``i`` (les deux OB formés avant i). Causal."""
    def created(o):
        return int(o.get("created_at", o.get("index", 0)))
    active = [o for o in obs if created(o) < i
              and not (o.get("invalidated_at") is not None and o["invalidated_at"] <= i)]
    out = []
    for ob in active:
        for prev in active:
            if prev is ob or prev["kind"] != ob["kind"]:
                continue
            if created(prev) >= created(ob):
                continue
            lo = max(float(ob["bottom"]), float(prev["bottom"]))
            hi = min(float(ob["top"]), float(prev["top"]))
            if hi > lo:                       # OB imbriqué dans un OB antérieur
                out.append({"kind": ob["kind"], "bottom": float(ob["bottom"]),
                            "top": float(ob["top"]),
                            "ce": consequent_encroachment(ob["top"], ob["bottom"])})
                break
    return out


def nested_order_block(htf_obs: List[dict], kind: str,
                       zone_lo: float, zone_hi: float) -> bool:
    """Imbrication multi-timeframe (« timeframe alignment » ICT) : True si un OB
    HTF de même polarité (``kind``) chevauche la zone LTF [lo, hi]. L'appelant
    ne fournit que les OB HTF ACTIFS à l'instant de la barre LTF (causal)."""
    for ob in htf_obs:
        if ob["kind"] != kind:
            continue
        if float(ob["bottom"]) <= zone_hi and float(ob["top"]) >= zone_lo:
            return True
    return False


def align_series(time_a: np.ndarray, time_b: np.ndarray,
                 close_b: np.ndarray) -> np.ndarray:
    """Aligne CAUSALEMENT la série B sur les timestamps de A (générique : crypto,
    action, ETF, forex — n'importe quel couple corrélé). Pour chaque temps de A,
    prend la DERNIÈRE clôture de B connue à cet instant (≤). NaN avant le début
    de B. Sert à préparer une SMT divergence entre deux actifs quelconques."""
    ta = np.asarray(time_a, np.int64)
    tb = np.asarray(time_b, np.int64)
    cb = np.asarray(close_b, float)
    idx = np.searchsorted(tb, ta, side="right") - 1
    out = np.full(len(ta), np.nan)
    valid = idx >= 0
    out[valid] = cb[idx[valid]]
    return out


def std_dev_projections(low: float, high: float, side: str,
                        mults=(1.0, 2.0, 2.5, 4.0)) -> List[dict]:
    """Projections ICT en « écarts-types » d'une jambe (dealing range) : cibles
    au-delà du range à ``mult`` × amplitude, dans le sens du mouvement. Sert de
    grille de Take-Profit (−1/−2/−2.5/−4 SD chez ICT)."""
    rng = float(high) - float(low)
    if rng <= 0:
        return []
    out = []
    for m in mults:
        lvl = float(high) + m * rng if side == "up" else float(low) - m * rng
        out.append({"mult": float(m), "level": round(lvl, 8)})
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  Sur OHLCV + temps
# ══════════════════════════════════════════════════════════════════════════════

# Fenêtres Silver Bullet ICT en heures UTC (New York ET ≈ UTC−5 hors DST) :
# 03:00 ET → 08 UTC, 10:00 ET → 15 UTC, 14:00 ET → 19 UTC. Chaque fenêtre = 1 h.
SILVER_BULLET_UTC = (8, 15, 19)


def silver_bullet_flags(times_epoch: np.ndarray,
                        windows=SILVER_BULLET_UTC) -> np.ndarray:
    """Booléen par barre : la barre tombe dans une fenêtre Silver Bullet (1 h).
    ⚠ Fenêtres en UTC fixe (pas de gestion DST) — ajuster ``windows`` au besoin."""
    ep = np.asarray(times_epoch, dtype=np.int64)
    hours = (ep // 3600) % 24
    flag = np.zeros(len(ep), dtype=bool)
    for w in windows:
        flag |= (hours == int(w))
    return flag


def judas_swing(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                times_epoch: np.ndarray, open_hour: int = 8,
                window: int = 3, lookback: int = 12) -> np.ndarray:
    """Judas Swing {+1, 0, −1} : dans les ``window`` premières barres d'une
    session (heure d'ouverture ``open_hour`` UTC), un faux mouvement prend la
    liquidité du range précédent (``lookback`` barres) puis referme à l'intérieur :
      −1  balaie le plus-HAUT récent et clôture en dessous → faux move haussier
          (biais SHORT) ;
      +1  balaie le plus-BAS récent et clôture au-dessus → biais LONG. Causal."""
    h = np.asarray(high, float)
    lo = np.asarray(low, float)
    c = np.asarray(close, float)
    ep = np.asarray(times_epoch, np.int64)
    hours = (ep // 3600) % 24
    hh = pl.Series(h).rolling_max(lookback).shift(1).to_numpy()
    ll = pl.Series(lo).rolling_min(lookback).shift(1).to_numpy()
    out = np.zeros(len(c), dtype=np.int8)
    in_win = (hours >= open_hour) & (hours < open_hour + window)
    for i in range(len(c)):
        if not in_win[i] or np.isnan(hh[i]) or np.isnan(ll[i]):
            continue
        if h[i] > hh[i] and c[i] < hh[i]:
            out[i] = -1
        elif lo[i] < ll[i] and c[i] > ll[i]:
            out[i] = 1
    return out


def smt_divergence(close_a: np.ndarray, close_b: np.ndarray,
                   lookback: int = 20) -> np.ndarray:
    """SMT divergence {+1, 0, −1} sur le référentiel de l'actif A, entre deux
    actifs CORRÉLÉS alignés (mêmes barres) :
      −1  A inscrit un nouveau plus-HAUT sur ``lookback`` mais B NON (le smart
          money distribue) → biais baissier ;
      +1  A inscrit un nouveau plus-BAS mais B NON → biais haussier.
    ⚠ ``close_a`` et ``close_b`` doivent être alignés temporellement (même index)."""
    a = np.asarray(close_a, float)
    b = np.asarray(close_b, float)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    amax = pl.Series(a).rolling_max(lookback).to_numpy()
    bmax = pl.Series(b).rolling_max(lookback).to_numpy()
    amin = pl.Series(a).rolling_min(lookback).to_numpy()
    bmin = pl.Series(b).rolling_min(lookback).to_numpy()
    out = np.zeros(n, dtype=np.int8)
    tol = 1e-9
    new_high_a = a >= amax - tol
    new_high_b = b >= bmax - tol
    new_low_a = a <= amin + tol
    new_low_b = b <= bmin + tol
    out[new_high_a & ~new_high_b] = -1
    out[new_low_a & ~new_low_b] = 1
    return out
