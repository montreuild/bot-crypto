"""Journal daté des motifs SMC/ICT — un symbole, plusieurs timeframes.

Ce module ne DÉTECTE rien. Les détecteurs existent (``app/core/smc_structure``,
``smc_state``, ``ict``, ``smc_sessions``) et sont causaux ; on les consomme, on
les date, on les met bout à bout.

Ce qu'il apporte, et qui n'existait pas : **une date de connaissabilité par
motif**. Le moteur SMC rend des entités qui portent plusieurs indices —
``index`` (là où le motif se situe sur le graphique), ``confirmed_at``,
``created_at`` — et se tromper d'indice ne casse rien visiblement : ça produit
simplement des statistiques flatteuses et fausses. La table
``CONNAISSABILITE`` ci-dessous est donc la pièce centrale du module, pas un
détail d'implémentation.

    swing            confirmed_at   le pivot n'existe qu'une fois ses barres
                                    de droite connues
    structure_event  index          la cassure est datée à sa clôture
    sweep            index          idem
    order_block      created_at     la bougie d'origine est antérieure, le
    breaker          created_at     bloc n'est qualifié qu'à l'impulsion
    rejection_block  created_at
    fvg              index + 1      le gap se voit à la TROISIÈME bougie ;
                                    ``index`` désigne celle du milieu
    liquidity_void   end_index + 1  la barre où l'on CONSTATE que la course ne
                                    se prolonge pas ; à ``end_index`` on ne le
                                    sait pas encore
    liquidity_pool   formed_at      dernier renforcement (entité mutable, cf.
                                    FAMILLES_MUTABLES)

Le contexte inter-TF suit la même exigence, avec un piège de plus : à la barre
LTF ``i``, seuls les buckets HTF **entièrement clôturés** sont connaissables.
On réutilise ``smc_sessions._htf_buckets`` — la même primitive que le reste du
dépôt — plutôt que d'écrire une jointure « au plus proche » qui laisserait voir
une bougie 4 h en cours depuis le 15 m.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)

#: Indice qui fait foi pour dater chaque famille d'entités, et pourquoi.
#: Une famille absente de cette table n'est pas journalisée : mieux vaut un
#: motif manquant qu'un motif mal daté.
CONNAISSABILITE: Dict[str, str] = {
    "swings": "confirmed_at",
    "structure_events": "index",
    "sweeps": "index",
    "order_blocks": "created_at",
    "breakers": "created_at",
    "rejection_blocks": "created_at",
    "fvgs": "index+1",
    "liquidity_voids": "end_index+1",
    "liquidity_pools": "formed_at",
}

#: Familles dont l'entité est MUTABLE : son indice de datation avance au fil de
#: l'historique. Un pool de liquidité est renforcé à chaque nouveau swing qui
#: le rejoint, et ``formed_at`` désigne le DERNIER renforcement — connaissable
#: à cette barre, avec le contenu qu'il a à cette barre, mais dépendant de
#: l'avenir pour son existence même.
#:
#: Conséquence assumée : le journal complet SOUS-COMPTE ces motifs — un pool
#: renforcé trois fois donne une ligne, pas trois. C'est le sens conservateur.
#: L'inverse — dater un motif plus tôt que ce que l'historique permettait de
#: savoir — est le seul défaut qui fabriquerait des statistiques flatteuses, et
#: il est interdit sans exception (cf. tests/test_smc_patterns_journal.py).
#:
#: ``liquidity_voids`` a quitté cette liste : dater la course à ``end_index+1``
#: — la barre où l'on constate qu'elle ne se prolonge PAS — la rend stable,
#: pour le même prix.
FAMILLES_MUTABLES = frozenset({"liquidity_pools"})

#: Clé de ``analyze`` portant TOUTES les entités (non tronquées à _MAX_KEEP).
_SOURCE_COMPLETE: Dict[str, str] = {
    "swings": "_all_swings",
    "structure_events": "_all_struct_events",
    "sweeps": "_all_sweeps",
    "order_blocks": "_all_obs",
    "breakers": "_all_breakers",
    "rejection_blocks": "_all_rejections",
    "fvgs": "_all_fvgs",
    "liquidity_voids": "_all_voids",
    "liquidity_pools": "_all_pools",
}

#: Champs recopiés tels quels depuis l'entité quand ils existent.
_QUALIFICATIFS = ("kind", "direction", "label", "level", "price", "top",
                  "bottom", "source", "swing_index", "ref_index", "index")


def barre_connaissable(famille: str, entite: dict) -> Optional[int]:
    """Barre à laquelle ``entite`` devient connaissable, ou ``None``.

    ``None`` = l'entité ne porte pas l'indice attendu ; elle est écartée du
    journal plutôt que datée au jugé.
    """
    regle = CONNAISSABILITE.get(famille)
    if regle is None:
        return None
    if regle.endswith("+1"):
        base = entite.get(regle[:-2])
        return None if base is None else int(base) + 1
    valeur = entite.get(regle)
    return None if valeur is None else int(valeur)


def _type_motif(famille: str, entite: dict) -> str:
    """Nom du motif : famille + qualificatif discriminant.

    ``structure_events`` porte déjà BOS/CHoCH/MSS dans ``kind`` — c'est le
    vocabulaire du dépôt, on ne le renomme pas.
    """
    kind = entite.get("kind")
    if famille == "structure_events":
        return str(kind or "STRUCTURE").upper()
    if kind is None:
        return famille.upper()
    return f"{famille.upper()}:{str(kind).upper()}"


def _cote(famille: str, entite: dict) -> str:
    """Côté directionnel du motif, ``""`` si la notion n'a pas de sens."""
    kind = str(entite.get("kind") or "")
    direction = str(entite.get("direction") or "")
    if direction in ("up", "down"):
        return "long" if direction == "up" else "short"
    if kind in ("bullish", "buy_side"):
        # Un balayage BUY-SIDE prend la liquidité au-dessus : c'est un événement
        # HAUSSIER par sa mécanique et souvent BAISSIER par sa suite. On note
        # ici la mécanique ; l'interprétation appartient aux statistiques.
        return "long" if kind == "bullish" else "buy_side"
    if kind in ("bearish", "sell_side"):
        return "short" if kind == "bearish" else "sell_side"
    return ""


def _prix_reference(entite: dict) -> Optional[float]:
    for cle in ("level", "price", "top"):
        v = entite.get(cle)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _contexte_htf(df_ltf: pl.DataFrame, df_htf: pl.DataFrame,
                  tf_htf: str) -> Dict[str, np.ndarray]:
    """État HTF connaissable à chaque barre LTF, sans regarder devant.

    L'alignement passe par ``_htf_buckets`` : ``idx[i]`` est l'index du dernier
    bucket HTF **entièrement clôturé** à la clôture de la barre LTF ``i``. Un
    ``join_asof`` sur l'horodatage donnerait le bucket EN COURS — la barre 4 h
    dont on ne connaît pas encore la clôture — et toute statistique bâtie
    dessus serait spectaculaire et fausse.
    """
    n = len(df_ltf)
    vide = {
        "htf_state": np.zeros(n, dtype=np.int8),
        "htf_trend": np.zeros(n, dtype=np.int8),
        "htf_bar": np.full(n, -1, dtype=np.int64),
    }
    if df_htf is None or len(df_htf) < 10:
        return vide

    from app.core.smc import analyze as smc_analyze
    from app.core.smc_state import structure_states

    res_htf = smc_analyze(df_htf)
    etats_htf = structure_states(df_htf, res_htf)
    # `or []` serait ambigu sur un ndarray (ValueError) : tester None.
    _trend = res_htf.get("_trend_arr")
    trend_htf = np.asarray(_trend if _trend is not None else [], dtype=np.int8)
    states_htf = np.asarray(etats_htf.get("states"), dtype=np.int8)

    # Position de chaque barre LTF dans la série HTF, par bornes d'horloge.
    idx = _index_htf_causal(df_ltf, df_htf, tf_htf)
    out = dict(vide)
    valides = idx >= 0
    if not valides.any():
        return out
    out["htf_bar"] = idx
    j = np.clip(idx, 0, max(len(states_htf) - 1, 0))
    if len(states_htf):
        out["htf_state"][valides] = states_htf[j[valides]]
    if len(trend_htf):
        j2 = np.clip(idx, 0, len(trend_htf) - 1)
        out["htf_trend"][valides] = trend_htf[j2[valides]]
    return out


def _index_htf_causal(df_ltf: pl.DataFrame, df_htf: pl.DataFrame,
                      tf_htf: str) -> np.ndarray:
    """``idx[i]`` = index de la dernière barre HTF CLÔTURÉE à la clôture de la
    barre LTF ``i`` ; ``-1`` si aucune.

    Les deux séries viennent du même CandleStore : leurs horodatages sont des
    bornes d'horloge. Une barre HTF d'horodatage ``t`` couvre ``[t, t + durée)``
    et n'est close qu'à ``t + durée``. La barre LTF ``i`` d'horodatage ``ti``
    est close à ``ti + durée_ltf``. La condition est donc
    ``t_htf + durée_htf <= t_ltf + durée_ltf``.
    """
    from app.core.timeframes import TF_SECONDS

    ep_ltf = df_ltf["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
    ep_htf = df_htf["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
    sec_htf = int(TF_SECONDS.get(tf_htf, 0))
    if sec_htf <= 0:
        d = np.diff(ep_htf)
        d = d[d > 0]
        sec_htf = int(np.median(d)) if len(d) else 0
    d_ltf = np.diff(ep_ltf)
    d_ltf = d_ltf[d_ltf > 0]
    sec_ltf = int(np.median(d_ltf)) if len(d_ltf) else 0
    if sec_htf <= 0 or sec_ltf <= 0:
        return np.full(len(ep_ltf), -1, dtype=np.int64)

    fins_htf = ep_htf + sec_htf
    fins_ltf = ep_ltf + sec_ltf
    # `side="right"` puis −1 : la dernière barre HTF dont la FIN est ≤ la fin
    # de la barre LTF. Une barre HTF qui se termine exactement en même temps
    # est connaissable ; une qui se termine après ne l'est pas.
    return np.searchsorted(fins_htf, fins_ltf, side="right").astype(np.int64) - 1


def journal_un_tf(df: pl.DataFrame, tf: str, symbol: str,
                  params: Optional[dict] = None,
                  contexte_htf: Optional[Dict[str, np.ndarray]] = None,
                  tf_htf: str = "") -> pl.DataFrame:
    """Journal des motifs d'UN timeframe, une ligne par occurrence.

    Colonnes : ``symbol, tf, bar, time, motif, cote, prix, famille`` + le
    contexte connaissable à cette barre (session, killzone, silver bullet,
    état séquentiel, séquence, état HTF).
    """
    from app.core.ict import silver_bullet_flags
    from app.core.smc import analyze as smc_analyze
    from app.core.smc_sessions import killzone_flags, session_label
    from app.core.smc_state import sequence_label, state_label, structure_states

    n = len(df)
    if n < 20:
        return _journal_vide()

    res = smc_analyze(df, params)
    etats = structure_states(df, res, params)
    states = np.asarray(etats.get("states"), dtype=np.int8)
    seqs = np.asarray(etats.get("sequences"), dtype=np.int8)

    ep = df["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
    kz = killzone_flags(ep)
    sb = silver_bullet_flags(ep)
    sessions = [session_label(float(t)) for t in ep]

    ctx = contexte_htf or {}
    htf_state = ctx.get("htf_state")
    htf_trend = ctx.get("htf_trend")

    lignes: List[Dict[str, Any]] = []
    for famille, cle_source in _SOURCE_COMPLETE.items():
        for entite in (res.get(cle_source) or []):
            barre = barre_connaissable(famille, entite)
            if barre is None or barre < 0 or barre >= n:
                continue
            ligne = {
                "symbol": symbol,
                "tf": tf,
                "bar": int(barre),
                "time": df["time"][int(barre)],
                "famille": famille,
                "motif": _type_motif(famille, entite),
                "cote": _cote(famille, entite),
                "prix": _prix_reference(entite),
                "session": sessions[barre],
                "killzone": bool(kz[barre]) if len(kz) > barre else False,
                "silver_bullet": bool(sb[barre]) if len(sb) > barre else False,
                "etat": state_label(int(states[barre])) if len(states) > barre else "UNKNOWN",
                "sequence": sequence_label(int(seqs[barre])) if len(seqs) > barre else "NONE",
                "tf_htf": tf_htf,
                "htf_etat": (state_label(int(htf_state[barre]))
                             if htf_state is not None and len(htf_state) > barre
                             else ""),
                "htf_trend": (int(htf_trend[barre])
                              if htf_trend is not None and len(htf_trend) > barre
                              else 0),
            }
            lignes.append(ligne)

    if not lignes:
        return _journal_vide()
    return pl.DataFrame(lignes).sort(["bar", "motif"])


def _journal_vide() -> pl.DataFrame:
    return pl.DataFrame(schema={
        "symbol": pl.Utf8, "tf": pl.Utf8, "bar": pl.Int64, "time": pl.Datetime("ms"),
        "famille": pl.Utf8, "motif": pl.Utf8, "cote": pl.Utf8, "prix": pl.Float64,
        "session": pl.Utf8, "killzone": pl.Boolean, "silver_bullet": pl.Boolean,
        "etat": pl.Utf8, "sequence": pl.Utf8, "tf_htf": pl.Utf8,
        "htf_etat": pl.Utf8, "htf_trend": pl.Int64,
    })


def journal_multi_tf(frames: Dict[str, pl.DataFrame], symbol: str,
                     params: Optional[dict] = None) -> pl.DataFrame:
    """Journal unifié de plusieurs TF du MÊME symbole.

    Chaque TF est journalisé avec, en contexte, l'état du TF immédiatement
    supérieur présent dans ``frames`` — le TF le plus haut n'a donc pas de
    contexte, ce qui est la seule réponse honnête.
    """
    from app.core.timeframes import TF_SECONDS

    ordonnes = sorted(frames, key=lambda t: TF_SECONDS.get(t, 0))
    morceaux: List[pl.DataFrame] = []
    for rang, tf in enumerate(ordonnes):
        df = frames[tf]
        if df is None or len(df) < 20:
            logger.warning("[smc_patterns] %s/%s ignoré : %d barres", symbol, tf,
                           0 if df is None else len(df))
            continue
        tf_sup = ordonnes[rang + 1] if rang + 1 < len(ordonnes) else ""
        ctx = (_contexte_htf(df, frames[tf_sup], tf_sup) if tf_sup else None)
        morceaux.append(journal_un_tf(df, tf, symbol, params, ctx, tf_sup))

    if not morceaux:
        return _journal_vide()
    return pl.concat(morceaux, how="vertical_relaxed").sort(["tf", "bar"])
