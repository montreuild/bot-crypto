"""Hiérarchie de liquidité et qualité des zones (§12, §15, §65–§67, §77–§79,
§83–§86, §91).

Le moteur SMC produit des poches, des balayages, des FVG et des order blocks
sans les distinguer les uns des autres : un swing local de 15 minutes et le plus
haut de la semaine précédente sont deux « pools ». Le ciblage prenait donc la
PREMIÈRE cible satisfaisant le R/R, sans notion de qualité — et L0 a mesuré ce
que ça donne : la cible n'est atteinte que 28 à 36 % du temps.

Ce module ne détecte rien de neuf. Il CLASSE et NOTE ce qu'``analyze`` produit
déjà, en fonctions pures et causales, sans modifier le moteur.

Toutes les notes sont bornées `[0, 1]` et toutes les fonctions n'utilisent que
des entités dont l'indice de connaissance est ≤ i.
"""
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

# ── §77 — hiérarchie de liquidité ───────────────────────────────────────────
# Une cible n'a pas la même probabilité d'être atteinte selon ce qu'elle
# représente. L'ordre vient de la spécification ; les POIDS sont des paramètres,
# pas des vérités : L8 doit les remplacer par des fréquences mesurées (§79).
CLASSES = ("HTF_EXTERNAL", "PREV_WEEK", "PREV_DAY", "SESSION", "SWING", "INTERNAL")
POIDS_CLASSE: Dict[str, float] = {
    "HTF_EXTERNAL": 1.00,
    "PREV_WEEK":    0.85,
    "PREV_DAY":     0.70,
    "SESSION":      0.55,
    "SWING":        0.40,
    "INTERNAL":     0.25,
}


def classe_liquidite(source: str, taille_pool: int = 1) -> str:
    """Classe d'une cible depuis sa provenance.

    ``source`` suit le vocabulaire déjà émis par le moteur (``pool``, ``swing``)
    et par ``calendar_liquidity_levels`` (``pdh``/``pdl``/``pwh``/``pwl``…).
    """
    s = (source or "").lower()
    if s.startswith("pm") or "month" in s:
        return "HTF_EXTERNAL"
    if s.startswith("pw") or "week" in s:
        return "PREV_WEEK"
    if s.startswith("pd") or "day" in s:
        return "PREV_DAY"
    if "asian" in s or "session" in s:
        return "SESSION"
    if s == "pool" or taille_pool > 1:
        # Un pool d'egal highs/lows concentre plus de stops qu'un swing isolé.
        return "SWING" if taille_pool <= 2 else "PREV_DAY"
    if s == "swing":
        return "SWING"
    return "INTERNAL"


def valeur_attendue(niveau: float, entree: float, risque: float,
                    classe: str, proba: Optional[Dict[str, float]] = None) -> float:
    """§79 — ``probabilité × gain net``, en multiples de R.

    ``proba`` permet d'injecter des fréquences MESURÉES (L8). Tant qu'elles
    n'existent pas, on retombe sur les poids de classe : c'est une préférence
    déclarée, pas une probabilité, et le nom de la variable le dit.
    """
    if risque <= 0:
        return 0.0
    gain_r = abs(niveau - entree) / risque
    p = (proba or POIDS_CLASSE).get(classe, 0.25)
    return float(p) * gain_r


# ── §67 — dealing range explicite ───────────────────────────────────────────

def dealing_range(res: dict, i: int, mode: str = "swing",
                  lookback: int = 200) -> Optional[Dict[str, float]]:
    """Range de travail à la barre ``i``, avec sa provenance.

    ``mode`` : ``swing`` (dernier haut/bas confirmés), ``htf`` (extrêmes des
    ``lookback`` barres). La spécification insiste (§67) : ne pas prendre
    systématiquement le dernier high/low visible sans contexte — d'où le champ
    ``source``, qui rend le choix lisible dans le journal.
    """
    swings = [s for s in (res.get("_all_swings") or [])
              if s.get("confirmed_at") is not None and s["confirmed_at"] <= i]
    if mode == "swing" and swings:
        hauts = [s["price"] for s in swings if s["kind"] == "high"]
        bas = [s["price"] for s in swings if s["kind"] == "low"]
        if hauts and bas:
            haut, bas_ = float(hauts[-1]), float(bas[-1])
            if haut > bas_:
                return {"high": haut, "low": bas_, "mid": (haut + bas_) / 2.0,
                        "source": "swing"}
    if swings:
        recents = [s for s in swings if s["index"] >= i - lookback]
        if recents:
            haut = max(float(s["price"]) for s in recents)
            bas_ = min(float(s["price"]) for s in recents)
            if haut > bas_:
                return {"high": haut, "low": bas_, "mid": (haut + bas_) / 2.0,
                        "source": f"htf{lookback}"}
    return None


def irl_erl(res: dict, i: int, prix: float, side: str,
            rng: Optional[Dict[str, float]] = None) -> Dict[str, Optional[float]]:
    """§66 — liquidité interne (inefficience à combler) et externe (bord du
    range) dans le sens du trade.

    Le prix peut revenir chercher l'IRL avant d'aller viser l'ERL : distinguer
    les deux évite de poser un TP sur un FVG en pensant viser un extrême.
    """
    rng = rng or dealing_range(res, i)
    erl = None
    if rng:
        erl = rng["high"] if side == "long" else rng["low"]
    irl = None
    meilleur = None
    for fvg in (res.get("_all_fvgs") or []):
        cree = fvg.get("index")
        if cree is None or cree > i or fvg.get("filled_at") is not None:
            continue
        milieu = (float(fvg["top"]) + float(fvg["bottom"])) / 2.0
        if side == "long" and milieu <= prix:
            continue
        if side == "short" and milieu >= prix:
            continue
        d = abs(milieu - prix)
        if meilleur is None or d < meilleur:
            meilleur, irl = d, milieu
    return {"internal_target": irl, "external_target": erl}


# ── §65 — inducement / liquidité interne ────────────────────────────────────

def inducement(res: dict, i: int, side: str, prix: float,
               lookback: int = 30) -> Optional[float]:
    """Petit swing interne non balayé entre le POI et le prix.

    Bullish : un HL mineur sous le prix, qui attire des stops vendeurs et sert
    de carburant à l'expansion. Sa présence distingue un pullback « nourri »
    d'un simple retracement.
    """
    candidats = []
    for s in (res.get("_all_swings") or []):
        conf = s.get("confirmed_at")
        if conf is None or conf > i or i - conf > lookback:
            continue
        if s.get("swept_at") is not None:
            continue
        p = float(s["price"])
        if side == "long" and s["kind"] == "low" and p < prix:
            candidats.append(p)
        elif side == "short" and s["kind"] == "high" and p > prix:
            candidats.append(p)
    if not candidats:
        return None
    return max(candidats) if side == "long" else min(candidats)


# ── §83 §84 — qualité du balayage et du displacement ────────────────────────

def qualite_balayage(sweep: dict, high: np.ndarray, low: np.ndarray,
                     close: np.ndarray, open_: np.ndarray, atr: float) -> float:
    """§83 — un balayage propre perce assez, revient vite, et laisse une mèche.

    Trois composantes également pondérées : profondeur du dépassement (en ATR),
    part de mèche dans la barre, et vigueur de la réintégration. Bornée [0, 1].
    """
    i = int(sweep.get("index", -1))
    if i < 0 or i >= len(close) or atr <= 0:
        return 0.0
    niveau = float(sweep.get("level", 0.0))
    haut, bas = float(high[i]), float(low[i])
    amplitude = haut - bas
    if amplitude <= 0:
        return 0.0
    if sweep.get("kind") == "buy_side":
        depassement = max(haut - niveau, 0.0)
        meche = haut - max(float(open_[i]), float(close[i]))
        reintegration = max(niveau - float(close[i]), 0.0)
    else:
        depassement = max(niveau - bas, 0.0)
        meche = min(float(open_[i]), float(close[i])) - bas
        reintegration = max(float(close[i]) - niveau, 0.0)
    profondeur = min(depassement / (0.5 * atr), 1.0)
    part_meche = min(max(meche, 0.0) / amplitude, 1.0)
    reclaim = min(reintegration / (0.5 * atr), 1.0)
    return round((profondeur + part_meche + reclaim) / 3.0, 4)


def qualite_displacement(i: int, open_: np.ndarray, high: np.ndarray,
                         low: np.ndarray, close: np.ndarray, atr: float,
                         casse_structure: bool = False) -> float:
    """§84 — corps/ATR, corps/amplitude, position de clôture, et bonus si
    l'impulsion casse la structure.

    Un grand chandelier qui casse simultanément la structure interne et un
    niveau protégé vaut bien plus qu'un grand chandelier isolé — c'est le seul
    endroit où la note dépasse ce que mesure `disp_body_atr`.
    """
    if i < 0 or i >= len(close) or atr <= 0:
        return 0.0
    corps = abs(float(close[i]) - float(open_[i]))
    amplitude = float(high[i]) - float(low[i])
    if amplitude <= 0:
        return 0.0
    n_corps = min(corps / (1.5 * atr), 1.0)
    ratio = min(corps / amplitude, 1.0)
    # Clôture proche de l'extrême dans le sens du mouvement.
    if close[i] >= open_[i]:
        position = (float(close[i]) - float(low[i])) / amplitude
    else:
        position = (float(high[i]) - float(close[i])) / amplitude
    note = 0.4 * n_corps + 0.3 * ratio + 0.3 * position
    if casse_structure:
        note = min(note + 0.15, 1.0)
    return round(note, 4)


# ── §15 §85 §86 — qualité des zones ─────────────────────────────────────────

def taux_mitigation(zone: dict, high: np.ndarray, low: np.ndarray,
                    i: int) -> float:
    """§15 — fraction de la zone déjà traversée par le prix, dans [0, 1].

    Le moteur ne suit que `mitigated_at` / `filled_at`, deux booléens datés :
    une zone touchée à 10 % et une touchée à 90 % étaient indiscernables.
    """
    haut, bas = float(zone.get("top", 0.0)), float(zone.get("bottom", 0.0))
    epaisseur = haut - bas
    debut = zone.get("index", zone.get("created_at"))
    if epaisseur <= 0 or debut is None or int(debut) >= i:
        return 0.0
    d = int(debut) + 1
    penetration = max(0.0, haut - float(np.min(low[d:i + 1]))) \
        if zone.get("kind") in ("bullish", "sell_side") \
        else max(0.0, float(np.max(high[d:i + 1])) - bas)
    return round(min(penetration / epaisseur, 1.0), 4)


def rang_fvg(fvg: dict, res: dict, mss_bars: Optional[set] = None) -> str:
    """§85 — `MSS_FVG` > `HTF_FVG` > `CONTINUATION` > `MICRO`.

    Le FVG né du displacement qui a PROVOQUÉ le MSS reçoit le meilleur rang :
    c'est la zone que la spécification désigne comme le point d'entrée du
    modèle de retournement, et rien ne la distinguait d'un gap quelconque.
    """
    i = fvg.get("index")
    if i is not None and mss_bars and int(i) in mss_bars:
        return "MSS_FVG"
    taille = abs(float(fvg.get("top", 0)) - float(fvg.get("bottom", 0)))
    atr = res.get("_atr_arr")
    if atr is not None and i is not None and 0 <= int(i) < len(atr):
        a = float(atr[int(i)])
        if a > 0 and taille < 0.25 * a:
            return "MICRO"
        if a > 0 and taille >= 1.0 * a:
            return "HTF_FVG"
    return "CONTINUATION"


def qualite_order_block(ob: dict, res: dict, zone_en_discount: bool,
                        mitigation: float) -> float:
    """§86 — displacement, cassure de structure, fraîcheur et zone."""
    note = 0.0
    if ob.get("broke_structure"):
        note += 0.35
    if int(ob.get("strength", 1)) >= 2:
        note += 0.15
    if ob.get("invalidated_at") is None:
        note += 0.15
    if zone_en_discount:
        note += 0.20
    note += 0.15 * (1.0 - min(max(mitigation, 0.0), 1.0))
    return round(min(note, 1.0), 4)


# ── §91 — ouvertures journalière / hebdomadaire / mensuelle ─────────────────

def opens_calendaires(df: pl.DataFrame) -> Dict[str, np.ndarray]:
    """Ouvertures du jour, de la semaine et du mois EN COURS, par barre.

    Causal par construction : l'ouverture d'une période est connue dès sa
    première barre — contrairement à ses extrêmes, qui ne le sont qu'à sa
    clôture (d'où `calendar_liquidity_levels` qui publie la période PRÉCÉDENTE).

    Usage §91 : au-dessus de l'ouverture = contexte haussier, jamais un signal.
    """
    n = len(df)
    vide = {c: np.full(max(n, 0), np.nan)
            for c in ("daily_open", "weekly_open", "monthly_open")}
    if n == 0 or "time" not in df.columns:
        return vide
    ts = df["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
    o = df["open"].to_numpy().astype(float)
    jour = ts // 86400
    # Semaine ISO ancrée sur le lundi : l'epoch 0 était un jeudi.
    semaine = (ts + 3 * 86400) // (7 * 86400)
    mois = np.array([(t // 86400) // 30 for t in ts])   # approximation stable
    out = {}
    for nom, cle in (("daily_open", jour), ("weekly_open", semaine),
                     ("monthly_open", mois)):
        depart = np.flatnonzero(np.diff(cle, prepend=cle[0] - 1))
        valeurs = np.empty(n)
        for k, s in enumerate(depart):
            fin = depart[k + 1] if k + 1 < len(depart) else n
            valeurs[s:fin] = o[s]
        out[nom] = valeurs
    return out


# ── §78 §79 — sélection de cible ────────────────────────────────────────────

def frequences_atteinte(trades: List[dict], min_trades: int = 8,
                        defaut: Optional[Dict[str, float]] = None
                        ) -> Dict[str, float]:
    """§79 — fréquence MESURÉE d'atteinte de la cible, par classe de liquidité.

    C'est la brique que `docs/MESURE_HIERARCHIE_LIQUIDITE.md` §5 réclame :
    ``POIDS_CLASSE`` est un ordre postulé, et la mesure l'a contredit là où
    l'échantillon était exploitable. Ici on ne postule plus — on compte, sur une
    fenêtre d'ENTRAÎNEMENT, la proportion de trades dont l'excursion favorable a
    effectivement atteint la cible visée.

    Un trade compte comme « atteint » si son MFE, en multiples du risque
    planifié, a dépassé la distance à la cible. Le MFE est utilisé plutôt que le
    motif de sortie parce qu'un trailing peut couper un trade APRÈS qu'il a
    touché sa cible : ce serait compter un succès comme un échec.

    Les classes sous ``min_trades`` retombent sur ``defaut`` (les poids
    postulés) : une fréquence estimée sur trois trades serait plus trompeuse
    qu'un poids assumé comme arbitraire.
    """
    defaut = defaut or POIDS_CLASSE
    compte: Dict[str, List[int]] = {}
    for t in trades:
        ind = t.get("indicators") or {}
        classe = ind.get("tp_class")
        entree = float(t.get("entry") or 0)
        stop = t.get("planned_stop")
        cible = ind.get("tp_target")
        if not classe or not entree or stop is None or cible is None:
            continue
        risque = abs(entree - float(stop))
        if risque <= 0:
            continue
        cible_r = abs(float(cible) - entree) / risque
        mfe_r = abs(float(t.get("mfe") or 0.0)) / 100.0 * entree / risque
        compte.setdefault(classe, []).append(1 if mfe_r >= cible_r else 0)
    out = dict(defaut)
    for classe, atteints in compte.items():
        if len(atteints) >= min_trades:
            out[classe] = round(sum(atteints) / len(atteints), 4)
    return out


def meilleure_cible(cibles: List[Tuple[float, str]], entree: float,
                    risque: float, side: str, rr_min: float,
                    gain_min_pct: float, front_run: float = 0.0,
                    proba: Optional[Dict[str, float]] = None
                    ) -> Optional[Dict[str, Any]]:
    """§78/§79 — parmi les cibles éligibles, celle de meilleure valeur attendue.

    ``cibles`` : ``[(niveau, source), …]``. L'éligibilité (gain minimal, R/R
    minimal) est celle du ciblage historique — seul le CHOIX change : on prenait
    la première, on prend désormais la mieux notée. Sur des poids tous égaux,
    le comportement redevient « la plus proche », donc le changement est
    activable et mesurable.
    """
    if risque <= 0 or entree <= 0:
        return None
    sens = 1 if side == "long" else -1
    meilleur = None
    for niveau, source in cibles:
        cible = niveau - front_run if sens > 0 else niveau + front_run
        gain_pct = (cible - entree) / entree * 100.0 * sens
        if gain_pct <= gain_min_pct:
            continue
        rr = (cible - entree) * sens / risque
        if rr < rr_min:
            continue
        cl = classe_liquidite(source)
        ev = valeur_attendue(cible, entree, risque, cl, proba)
        if meilleur is None or ev > meilleur["expected_value"]:
            meilleur = {"price": cible, "source": source, "classe": cl,
                        "rr": rr, "gain_pct": gain_pct,
                        "expected_value": round(ev, 4)}
    return meilleur
