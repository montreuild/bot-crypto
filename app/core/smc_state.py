"""Moteur de structure SÉQUENTIEL (§60–§64, §73, §82, §108).

Le moteur SMC produit un biais ternaire — `res["_trend_arr"] ∈ {−1, 0, +1}` —
qui bascule dès la première clôture au-delà du dernier swing. Une mèche suivie
d'une clôture marginale suffit donc à retourner le biais, et rien ne distingue
un pullback dans une tendance d'un vrai retournement.

Ce module ajoute la MÉMOIRE : il consomme les entités d'``analyze`` sans les
modifier, et en dérive par barre un état parmi douze, les niveaux protégés, et
une qualification des cassures.

Trois conventions, fixées ici une fois pour toutes (§61) — le dépôt n'en a pas
d'autre :

    BOS   cassure en clôture au-delà du dernier swing DANS LE SENS de la
          structure, avec displacement suffisant → continuation.
    MSS   balayage de liquidité, PUIS displacement, PUIS cassure du dernier LH
          (haussier) ou HL (baissier) → changement INTERNE. Déclenche un
          avertissement, jamais une confirmation.
    CHoCH première cassure contraire sans displacement suffisant → signal
          précoce. Ne change pas l'état.

La conséquence pratique est §62/§63 : un premier MSS contraire ne retourne PAS
le biais externe. Il fait passer en `*_WARNING` ; la confirmation exige la
formation d'un nouveau LH/HL puis un BOS. Si le prix échoue à former ce LH/HL et
casse dans le sens initial, c'est un `FAILED_*_REVERSAL` (§73) — le cas que la
spécification désigne comme le plus intéressant à tester.

CAUSALITÉ. Un swing n'est utilisé qu'à partir de son ``confirmed_at``, jamais
de son ``index`` : le pivot n'existe qu'une fois ses barres de droite connues.
Vérifié par test de préfixe (``tests/test_smc_state.py``), au même titre que
``app/ml/features_smc.py``.
"""
import logging
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)

STATES = (
    "UNKNOWN",
    "RANGING",
    "BULLISH",
    "BULLISH_PULLBACK",
    "BULLISH_WARNING",
    "BEARISH",
    "BEARISH_PULLBACK",
    "BEARISH_WARNING",
    "REVERSAL_BULLISH_PENDING",
    "REVERSAL_BEARISH_PENDING",
    "BULLISH_CONFIRMED",
    "BEARISH_CONFIRMED",
    "FAILED_BULLISH_REVERSAL",
    "FAILED_BEARISH_REVERSAL",
)
CODE: Dict[str, int] = {s: i for i, s in enumerate(STATES)}

SEQUENCES = ("NONE", "CONTINUATION", "REVERSAL", "EARLY_REVERSAL", "FAILED_REVERSAL")
SEQ_CODE: Dict[str, int] = {s: i for i, s in enumerate(SEQUENCES)}

DEFAULTS: Dict[str, Any] = {
    # Displacement minimal d'une cassure VALIDE (§60.3) — en fraction d'ATR.
    # Volontairement plus bas que `disp_body_atr` du moteur (1.3) : celui-ci
    # qualifie une impulsion créatrice d'order block, celui-là écarte seulement
    # les clôtures marginales.
    "break_body_atr": 0.5,
    # Fenêtre pendant laquelle un balayage reste « récent » pour armer un MSS.
    "mss_sweep_lookback": 8,
    # Barres au-delà desquelles un avertissement non confirmé retombe.
    "warning_max_bars": 40,
}


def _params(p: Optional[dict]) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    if p:
        for k in DEFAULTS:
            if p.get(k) is not None:
                out[k] = p[k]
    return out


def state_label(code: int) -> str:
    return STATES[int(code)] if 0 <= int(code) < len(STATES) else "UNKNOWN"


def sequence_label(code: int) -> str:
    return SEQUENCES[int(code)] if 0 <= int(code) < len(SEQUENCES) else "NONE"


def _par_barre(entites: List[dict], cle: str) -> Dict[int, List[dict]]:
    """Index barre → entités connaissables À cette barre."""
    out: Dict[int, List[dict]] = {}
    for e in entites:
        i = e.get(cle)
        if i is None:
            continue
        out.setdefault(int(i), []).append(e)
    return out


def structure_states(df: pl.DataFrame, res: dict,
                     params: Optional[dict] = None) -> Dict[str, Any]:
    """État de structure, niveaux protégés et cassures qualifiées, par barre.

    ``res`` est le retour d'``app.core.smc.analyze``. Retourne des tableaux
    alignés sur ``df`` plus les listes d'événements MSS/BOS qualifiés.
    """
    p = _params(params)
    n = len(df)
    vide = {
        "states": np.zeros(max(n, 0), dtype=np.int8),
        "sequences": np.zeros(max(n, 0), dtype=np.int8),
        "protected_high": np.full(max(n, 0), np.nan),
        "protected_low": np.full(max(n, 0), np.nan),
        "mss_events": [], "bos_events": [],
    }
    if n == 0 or not res:
        return vide

    o = df["open"].to_numpy().astype(float)
    c = df["close"].to_numpy().astype(float)
    atr = np.asarray(res.get("_atr_arr"), dtype=float)
    if atr.size != n:
        atr = np.full(n, np.nan)

    swings_par_conf = _par_barre(res.get("_all_swings") or [], "confirmed_at")
    sweeps_par_barre = _par_barre(res.get("_all_sweeps") or [], "index")

    seuil = float(p["break_body_atr"])
    fenetre_sweep = int(p["mss_sweep_lookback"])
    warn_max = int(p["warning_max_bars"])

    etats = np.zeros(n, dtype=np.int8)
    seqs = np.zeros(n, dtype=np.int8)
    prot_h = np.full(n, np.nan)
    prot_l = np.full(n, np.nan)
    mss_events: List[dict] = []
    bos_events: List[dict] = []

    etat = "UNKNOWN"
    seq = "NONE"
    biais = 0                      # biais EXTERNE confirmé
    dernier_h: Optional[dict] = None   # dernier swing haut confirmé, non consommé
    dernier_l: Optional[dict] = None
    label_h: Optional[str] = None      # dernier label de swing haut (HH/LH)
    label_l: Optional[str] = None
    protege_h: Optional[float] = None
    protege_l: Optional[float] = None
    dernier_sweep_haut = -10**9    # barre du dernier balayage buy_side
    dernier_sweep_bas = -10**9
    warn_depuis: Optional[int] = None
    # Niveau à casser pour confirmer le retournement en cours (nouveau LH/HL).
    pivot_confirmation: Optional[float] = None

    for i in range(n):
        a = atr[i]
        corps = abs(c[i] - o[i])
        displacement = bool(a > 0 and corps >= seuil * a)

        for sw in sweeps_par_barre.get(i, ()):
            if sw.get("kind") == "buy_side":
                dernier_sweep_haut = i
            elif sw.get("kind") == "sell_side":
                dernier_sweep_bas = i

        # ── Swings confirmés à cette barre : labels et niveaux protégés ───────
        for sw in swings_par_conf.get(i, ()):
            if sw["kind"] == "high":
                dernier_h = sw
                label_h = sw.get("label")
                # §64 — un nouveau HH consacre le dernier HL comme plancher
                # protégé : tant qu'il tient, la structure haussière est intacte.
                if label_h == "HH" and dernier_l is not None:
                    protege_l = float(dernier_l["price"])
            else:
                dernier_l = sw
                label_l = sw.get("label")
                if label_l == "LL" and dernier_h is not None:
                    protege_h = float(dernier_h["price"])

        # ── Cassures qualifiées ──────────────────────────────────────────────
        mss_haut = mss_bas = bos_haut = bos_bas = False

        if dernier_h is not None and c[i] > dernier_h["price"]:
            niveau = float(dernier_h["price"])
            if biais >= 0 and displacement:
                bos_haut = True
                bos_events.append({"index": i, "direction": "up",
                                   "level": niveau, "kind": "BOS"})
            elif (i - dernier_sweep_bas) <= fenetre_sweep and displacement \
                    and label_h == "LH":
                mss_haut = True
                mss_events.append({"index": i, "direction": "up",
                                   "level": niveau, "kind": "MSS",
                                   "sweep_bar": dernier_sweep_bas})
            dernier_h = None            # niveau consommé

        if dernier_l is not None and c[i] < dernier_l["price"]:
            niveau = float(dernier_l["price"])
            if biais <= 0 and displacement:
                bos_bas = True
                bos_events.append({"index": i, "direction": "down",
                                   "level": niveau, "kind": "BOS"})
            elif (i - dernier_sweep_haut) <= fenetre_sweep and displacement \
                    and label_l == "HL":
                mss_bas = True
                mss_events.append({"index": i, "direction": "down",
                                   "level": niveau, "kind": "MSS",
                                   "sweep_bar": dernier_sweep_haut})
            dernier_l = None

        # ── Machine à états ──────────────────────────────────────────────────
        # Ordre délibéré : la rupture d'un niveau protégé prime sur tout le
        # reste, puis la confirmation d'un retournement en cours, puis les
        # continuations. Sans cette priorité, un BOS de continuation pourrait
        # masquer la cassure du plancher qui vient d'invalider la tendance.
        casse_protege_bas = (protege_l is not None and c[i] < protege_l
                             and displacement)
        casse_protege_haut = (protege_h is not None and c[i] > protege_h
                              and displacement)

        if biais > 0 and casse_protege_bas:
            etat, seq = "BEARISH_WARNING", "EARLY_REVERSAL"
            warn_depuis, pivot_confirmation = i, None
            protege_l = None
        elif biais < 0 and casse_protege_haut:
            etat, seq = "BULLISH_WARNING", "EARLY_REVERSAL"
            warn_depuis, pivot_confirmation = i, None
            protege_h = None
        elif etat in ("BEARISH_WARNING", "REVERSAL_BEARISH_PENDING"):
            # §73 — le retournement échoue s'il ne produit pas de LH et que le
            # prix repart dans le sens initial.
            if bos_haut:
                etat, seq = "FAILED_BEARISH_REVERSAL", "FAILED_REVERSAL"
                warn_depuis = None
            elif bos_bas and etat == "REVERSAL_BEARISH_PENDING":
                etat, seq, biais = "BEARISH_CONFIRMED", "REVERSAL", -1
                warn_depuis = None
            elif label_h == "LH" and pivot_confirmation is None:
                etat = "REVERSAL_BEARISH_PENDING"
                pivot_confirmation = float(dernier_l["price"]) if dernier_l else None
            elif warn_depuis is not None and (i - warn_depuis) > warn_max:
                etat, seq = ("BULLISH" if biais > 0 else "RANGING"), "NONE"
                warn_depuis = None
        elif etat in ("BULLISH_WARNING", "REVERSAL_BULLISH_PENDING"):
            if bos_bas:
                etat, seq = "FAILED_BULLISH_REVERSAL", "FAILED_REVERSAL"
                warn_depuis = None
            elif bos_haut and etat == "REVERSAL_BULLISH_PENDING":
                etat, seq, biais = "BULLISH_CONFIRMED", "REVERSAL", 1
                warn_depuis = None
            elif label_l == "HL" and pivot_confirmation is None:
                etat = "REVERSAL_BULLISH_PENDING"
                pivot_confirmation = float(dernier_h["price"]) if dernier_h else None
            elif warn_depuis is not None and (i - warn_depuis) > warn_max:
                etat, seq = ("BEARISH" if biais < 0 else "RANGING"), "NONE"
                warn_depuis = None
        elif mss_haut or mss_bas:
            # Premier MSS contraire : AVERTISSEMENT, jamais confirmation (§62).
            etat = "BULLISH_WARNING" if mss_haut else "BEARISH_WARNING"
            seq = "EARLY_REVERSAL"
            warn_depuis, pivot_confirmation = i, None
        elif bos_haut or bos_bas:
            biais = 1 if bos_haut else -1
            etat, seq = ("BULLISH" if bos_haut else "BEARISH"), "CONTINUATION"
        elif etat in ("BULLISH_CONFIRMED", "FAILED_BEARISH_REVERSAL"):
            etat = "BULLISH"
        elif etat in ("BEARISH_CONFIRMED", "FAILED_BULLISH_REVERSAL"):
            etat = "BEARISH"
        else:
            # §82 — une structure interne contraire dans une tendance intacte est
            # un PULLBACK, pas un retournement. Le retournement externe exige la
            # cassure du niveau protégé, traitée plus haut.
            if biais > 0:
                etat = "BULLISH_PULLBACK" if label_l == "LL" or label_h == "LH" \
                    else "BULLISH"
            elif biais < 0:
                etat = "BEARISH_PULLBACK" if label_l == "HL" or label_h == "HH" \
                    else "BEARISH"
            elif label_h and label_l:
                etat = "RANGING"

        etats[i] = CODE[etat]
        seqs[i] = SEQ_CODE[seq]
        if protege_h is not None:
            prot_h[i] = protege_h
        if protege_l is not None:
            prot_l[i] = protege_l

    return {
        "states": etats, "sequences": seqs,
        "protected_high": prot_h, "protected_low": prot_l,
        "mss_events": mss_events, "bos_events": bos_events,
    }
