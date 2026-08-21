"""Motifs composés — découverts et confirmés sur des tranches DISJOINTES.

C'est la partie qui répond le mieux à « quels enchaînements reviennent ? », et
celle qui fabrique le plus facilement de fausses découvertes. Chercher des
séquences de trois maillons parmi vingt motifs sur cinq fenêtres, c'est tester
des milliers d'hypothèses ; publier les meilleures sans rien d'autre revient à
trier du bruit et à l'appeler un résultat.

Trois garde-fous, dans l'ordre où ils mordent :

1. **Espace borné et déclaré.** Longueur ≤ 3 maillons, fenêtre temporelle
   maximale entre maillons, maillons pris dans le vocabulaire du journal — pas
   dans une soupe d'entités brutes. Les candidats sont d'abord filtrés par
   support minimal : un enchaînement vu quatre fois n'est pas un enchaînement.

2. **Découverte et confirmation sur des barres disjointes.** Les composés sont
   cherchés sur la première tranche de l'historique et mesurés sur une tranche
   finale qui n'a servi à rien d'autre. Un composé qui n'existe que dans la
   tranche de découverte est un artefact, et c'est ce découpage qui le dit —
   pas une intuition a posteriori. Même principe que
   ``app/core/is_oos.py::split_with_holdout``, appliqué ici aux motifs plutôt
   qu'aux paramètres.

3. **Nombre d'hypothèses annoncé.** ``mine`` retourne le décompte des candidats
   ÉNUMÉRÉS, pas seulement des survivants. C'est ce nombre qui gouverne la
   correction pour test multiple, et il va en tête du rapport.

Les composés inter-TF sont permis : un maillon porte son TF, donc
« sweep 4 h → MSS 1 h → retest d'OB 15 m » est une séquence comme une autre.
La contrainte de causalité vaut telle quelle — chaque maillon est daté à sa
barre de connaissabilité, et l'ordre est celui du TEMPS, pas des index de
barres, qui ne sont pas comparables entre TF.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import polars as pl

from app.core.stats_thresholds import MIN_SIGNIFICANT_TRADES

logger = logging.getLogger(__name__)

#: Longueur maximale d'un composé. Au-delà de trois maillons, le support
#: s'effondre et le nombre d'hypothèses explose — on n'y gagne que du bruit.
LONGUEUR_MAX = 3

#: Support minimal d'un candidat dans la tranche de découverte.
SUPPORT_MIN = MIN_SIGNIFICANT_TRADES

#: Part de l'historique réservée à la CONFIRMATION (fin de série).
FRACTION_CONFIRMATION = 0.30


def _cle_maillon(tf: str, motif: str) -> str:
    return f"{tf}|{motif}"


def _evenements_ordonnes(journal: pl.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """``(epochs, maillons)`` triés par TEMPS.

    Le tri est temporel et non par ``bar`` : un index de barre 4 h et un index
    1 h ne sont pas comparables, et les trier ensemble fabriquerait des
    séquences qui n'ont jamais eu lieu.
    """
    if journal.height == 0:
        return np.zeros(0, dtype=np.int64), []
    j = journal.sort("time")
    ep = j["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
    maillons = [_cle_maillon(t, m) for t, m in zip(j["tf"].to_list(), j["motif"].to_list())]
    return ep, maillons


#: Au-delà de ce nombre d'événements dans une fenêtre, l'énumération des
#: triplets devient cubique et la fenêtre n'a plus de sens : tout est « proche
#: de tout ». La fenêtre est alors ignorée et signalée, plutôt que de laisser
#: l'étude tourner des heures pour produire du bruit.
MAX_EVENEMENTS_FENETRE = 60


def _sequences(ep: np.ndarray, maillons: List[str], longueur: int,
               fenetre_s: int) -> Dict[Tuple[str, ...], List[int]]:
    """Séquences de ``longueur`` maillons dans une fenêtre de ``fenetre_s``.

    Retourne ``{séquence: [indices du DERNIER maillon]}`` — c'est le dernier
    maillon qui date le composé, puisque c'est à lui qu'on le connaît en entier.
    """
    trouve: Dict[Tuple[str, ...], List[int]] = {}
    n = len(ep)
    if n == 0 or longueur < 1:
        return trouve
    if longueur == 1:
        for i, m in enumerate(maillons):
            trouve.setdefault((m,), []).append(i)
        return trouve

    # Fenêtre glissante : pour chaque fin i, les débuts admissibles sont ceux
    # dont l'horodatage est dans [ep[i] - fenetre_s, ep[i]].
    debut = np.searchsorted(ep, ep - fenetre_s, side="left")
    denses = 0
    for i in range(n):
        d = int(debut[i])
        if i - d < longueur - 1:
            continue
        if i - d > MAX_EVENEMENTS_FENETRE:
            denses += 1
            continue
        # On énumère les combinaisons ORDONNÉES se terminant en i. Borné par
        # LONGUEUR_MAX, donc au plus deux boucles imbriquées.
        if longueur == 2:
            for a in range(d, i):
                trouve.setdefault((maillons[a], maillons[i]), []).append(i)
        elif longueur == 3:
            for a in range(d, i - 1):
                for b in range(a + 1, i):
                    trouve.setdefault(
                        (maillons[a], maillons[b], maillons[i]), []).append(i)
    if denses:
        logger.warning(
            "[composites] %d fenêtre(s) ignorée(s) : plus de %d événements — "
            "réduire --fenetre-composes, sinon « proche » ne veut plus rien dire",
            denses, MAX_EVENEMENTS_FENETRE)
    return trouve


def mine(journal: pl.DataFrame, fenetre_s: int,
         longueurs: Sequence[int] = (2, 3),
         support_min: int = SUPPORT_MIN,
         fraction_confirmation: float = FRACTION_CONFIRMATION) -> Dict[str, Any]:
    """Découvre les composés sur la première tranche, les compte sur la seconde.

    Retourne ``{"candidats": DataFrame, "n_enumeres": int, "coupure": epoch,
    "n_decouverte": int, "n_confirmation": int}``.

    ``n_enumeres`` est le nombre de séquences DISTINCTES rencontrées dans la
    tranche de découverte, avant filtrage par support : c'est le décompte
    d'hypothèses à publier, pas celui des survivants.
    """
    ep, maillons = _evenements_ordonnes(journal)
    vide = {"candidats": _candidats_vides(), "n_enumeres": 0, "coupure": None,
            "n_decouverte": 0, "n_confirmation": 0}
    if len(ep) < 2 * support_min:
        return vide

    coupure = int(np.quantile(ep, 1.0 - fraction_confirmation))
    masque_dec = ep < coupure
    if masque_dec.sum() < support_min or (~masque_dec).sum() < support_min:
        return vide

    ep_dec, mail_dec = ep[masque_dec], [m for m, k in zip(maillons, masque_dec) if k]
    ep_conf, mail_conf = ep[~masque_dec], [m for m, k in zip(maillons, masque_dec) if not k]

    lignes: List[Dict[str, Any]] = []
    n_enumeres = 0
    for longueur in longueurs:
        if longueur > LONGUEUR_MAX:
            logger.warning("[composites] longueur %d ignorée (max %d)",
                           longueur, LONGUEUR_MAX)
            continue
        dec = _sequences(ep_dec, mail_dec, longueur, fenetre_s)
        n_enumeres += len(dec)
        retenus = {s: idx for s, idx in dec.items() if len(idx) >= support_min}
        if not retenus:
            continue
        conf = _sequences(ep_conf, mail_conf, longueur, fenetre_s)
        for seq, idx_dec in retenus.items():
            idx_conf = conf.get(seq, [])
            lignes.append({
                "sequence": " → ".join(seq),
                "longueur": longueur,
                "n_decouverte": len(idx_dec),
                "n_confirmation": len(idx_conf),
                # Un composé absent de la tranche de confirmation est un
                # artefact de la tranche de découverte : la colonne le dit
                # sans qu'il faille l'interpréter.
                "confirme": len(idx_conf) >= support_min,
            })

    if not lignes:
        return {**vide, "n_enumeres": n_enumeres, "coupure": coupure,
                "n_decouverte": int(masque_dec.sum()),
                "n_confirmation": int((~masque_dec).sum())}
    return {
        "candidats": pl.DataFrame(lignes).sort(
            ["confirme", "n_confirmation"], descending=[True, True]),
        "n_enumeres": n_enumeres,
        "coupure": coupure,
        "n_decouverte": int(masque_dec.sum()),
        "n_confirmation": int((~masque_dec).sum()),
    }


def _candidats_vides() -> pl.DataFrame:
    return pl.DataFrame(schema={"sequence": pl.Utf8, "longueur": pl.Int64,
                                "n_decouverte": pl.Int64, "n_confirmation": pl.Int64,
                                "confirme": pl.Boolean})


def mouvement_apres_composes(resultat_mine: Dict[str, Any], journal: pl.DataFrame,
                             frames: Dict[str, pl.DataFrame], fenetre_s: int,
                             tf_mesure: str, horizons: Sequence[int] = (6, 12, 24),
                             graine: int = 0,
                             n_tirages: Optional[int] = None) -> pl.DataFrame:
    """Rendement après les composés CONFIRMÉS, sur la seule tranche de confirmation.

    Mesurer sur tout l'historique réintroduirait exactement le biais que le
    découpage vient d'écarter : les barres de découverte ont servi à choisir
    ces composés-là.

    ``tf_mesure`` est le TF dont on lit les prix — un composé inter-TF se
    mesure sur le TF le plus bas, celui où l'on agirait.
    """
    from app.engine.smc_patterns.stats import (
        _rendements_a,
        _stats_echantillon,
        _temoin_decale,
        p_valeur_empirique,
    )

    candidats = resultat_mine.get("candidats")
    coupure = resultat_mine.get("coupure")
    df = frames.get(tf_mesure)
    if (candidats is None or candidats.height == 0 or coupure is None
            or df is None or len(df) < 50):
        return pl.DataFrame(schema={"sequence": pl.Utf8, "horizon": pl.Int64})

    confirmes = candidats.filter(pl.col("confirme"))
    if confirmes.height == 0:
        return pl.DataFrame(schema={"sequence": pl.Utf8, "horizon": pl.Int64})

    ep, maillons = _evenements_ordonnes(journal)
    masque_conf = ep >= int(coupure)
    ep_conf = ep[masque_conf]
    mail_conf = [m for m, k in zip(maillons, masque_conf) if k]

    ep_prix = df["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
    rng = np.random.default_rng(graine)
    lignes: List[Dict[str, Any]] = []

    # L'énumération est faite UNE fois par longueur, pas une fois par composé.
    # Elle coûte O(n · fenêtre²) : la refaire dans la boucle multipliait ce coût
    # par le nombre de confirmés — quelques centaines — et faisait passer un
    # run 4 TF de quelques secondes à plus de dix minutes.
    par_longueur = {
        longueur: _sequences(ep_conf, mail_conf, longueur, fenetre_s)
        for longueur in sorted({len(c["sequence"].split(" → "))
                                for c in confirmes.iter_rows(named=True)})
    }

    for ligne in confirmes.iter_rows(named=True):
        seq = tuple(ligne["sequence"].split(" → "))
        occurrences = par_longueur.get(len(seq), {}).get(seq, [])
        if not occurrences:
            continue
        # Barre du TF de mesure à laquelle le composé est COMPLET : la dernière
        # barre dont la clôture précède ou égale l'horodatage du dernier maillon.
        instants = ep_conf[np.asarray(occurrences, dtype=int)]
        bars = np.searchsorted(ep_prix, instants, side="right") - 1
        bars = bars[bars >= 0]
        if len(bars) == 0:
            continue
        for h in horizons:
            obs = _rendements_a(df, bars, h)
            if len(obs) == 0:
                continue
            s_obs = _stats_echantillon(obs)
            moy_dec = _temoin_decale(bars, df, h, rng, n_tirages)
            s_dec = _stats_echantillon(moy_dec)
            p_dec = p_valeur_empirique(s_obs["moyenne"], moy_dec)
            lignes.append({
                "sequence": ligne["sequence"],
                "longueur": ligne["longueur"],
                "horizon": int(h),
                "n": s_obs["n"],
                "moyenne_pct": round(s_obs["moyenne"], 4),
                "ic95_pct": round(s_obs["ic95"], 4) if np.isfinite(s_obs["ic95"]) else None,
                "temoin_decale_pct": round(s_dec["moyenne"], 4) if s_dec["n"] else None,
                "ecart_decale": (round(s_obs["moyenne"] - s_dec["moyenne"], 4)
                                 if s_dec["n"] else None),
                "p_decale": (round(float(p_dec), 5)
                             if p_dec == p_dec else None),   # NaN-safe
                "significatif": s_obs["n"] >= MIN_SIGNIFICANT_TRADES,
            })
    if not lignes:
        return pl.DataFrame(schema={"sequence": pl.Utf8, "horizon": pl.Int64})
    return pl.DataFrame(lignes).sort("ecart_decale", descending=True, nulls_last=True)


def survivants_composes(mesures: pl.DataFrame, resultat_mine: Dict[str, Any],
                        n_tirages: Optional[int] = None) -> Dict[str, Any]:
    """Composés dont l'écart au témoin survit au contrôle du FDR.

    LE DÉCOMPTE D'HYPOTHÈSES EST CELUI DE L'ÉNUMÉRATION, pas celui des lignes
    mesurées. Corriger sur le nombre de survivants reviendrait à ne pas
    corriger du tout : les candidats ont déjà été filtrés par support, et c'est
    précisément ce filtrage qui a fait le tri. Sur une marche aléatoire, ce
    module produit des composés à +3 % de rendement moyen ; c'est cette
    correction-là qui les élimine.

    BENJAMINI-HOCHBERG PLUTÔT QUE BONFERRONI. Sur 73 000 hypothèses énumérées,
    Bonferroni exigeait 6.9e-7 — mille fois moins que le plancher du test de
    permutation (1 / (1 + tirages)). Aucun composé ne pouvait passer, quelles
    que soient les données : le « 0 survivant » ne mesurait rien. BH contrôle
    la proportion de fausses découvertes parmi les rejets, ce qui est la
    garantie pertinente pour une liste de candidats à retester.

    ``plancher_p`` est publié à côté du seuil : si le seuil retenu descend
    jusqu'au plancher, c'est que la résolution du test redevient le facteur
    limitant et qu'il faut monter ``n_tirages``.
    """
    from statistics import NormalDist

    from app.engine.smc_patterns.stats import plancher_p, seuil_bh

    n_hyp = int(resultat_mine.get("n_enumeres") or 0)
    plancher = plancher_p(n_tirages)
    vide = {"survivants": mesures.head(0), "n_hypotheses": n_hyp,
            "z_requis": None, "alpha_bonferroni": None,
            "methode": "benjamini-hochberg", "plancher_p": plancher,
            "seuil_bh": None}
    if mesures.height == 0 or n_hyp <= 0:
        return vide

    alpha = 0.05 / n_hyp
    z = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    eligibles = mesures.filter(pl.col("significatif")
                               & pl.col("p_decale").is_not_null())
    seuil = seuil_bh(eligibles["p_decale"].to_list(), m=n_hyp, alpha=0.05)
    survivants = (eligibles
                  .filter(pl.col("p_decale") <= seuil)
                  .sort("ecart_decale", descending=True, nulls_last=True)
                  if seuil is not None else mesures.head(0))
    return {"survivants": survivants, "n_hypotheses": n_hyp,
            "z_requis": round(z, 3), "alpha_bonferroni": alpha,
            "methode": "benjamini-hochberg", "plancher_p": plancher,
            "seuil_bh": seuil}
