"""Statistiques du journal SMC/ICT — chaque chiffre avec ses témoins.

Trois familles de mesures : fréquence, enchaînement, mouvement suivant. Aucune
n'est publiable seule, et c'est le point de ce module.

POURQUOI LES TÉMOINS SONT OBLIGATOIRES
« Ce motif est suivi de +0,4 % à 12 barres » ne veut rien dire tant qu'on
ignore ce que rend une barre quelconque du même échantillon. Sur une série
haussière, TOUT motif est suivi d'une hausse. La campagne d'ablation du dépôt a
produit cinq faux positifs ; le dernier n'a été intercepté que parce qu'un
témoin avait été ajouté au harnais — sans lui, « Silver Bullet et AMD validés »
aurait été publié alors que tout l'effet venait d'un mécanisme hors périmètre
(cf. docs/SUITE_ABLATION_V3.md, docs/ABLATION_BAS_TF_ET_ACTIONS.md).

Deux témoins, qui ne disent pas la même chose :

  inconditionnel  barres tirées au hasard, à distribution de SESSIONS
                  identique à celle du motif. Répond à : « le motif fait-il
                  mieux qu'une barre quelconque prise au même moment de la
                  journée ? »
  décalé          les mêmes barres d'événement, décalées circulairement d'un
                  offset aléatoire. L'ESPACEMENT entre événements est conservé
                  à l'identique, seul l'alignement au prix est détruit. Répond
                  à : « l'effet vient-il du motif, ou de la façon dont ses
                  occurrences se répartissent dans le temps ? »

Un motif qui bat le premier témoin mais pas le second n'a pas d'edge propre :
il hérite de son calendrier.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import polars as pl

from app.core.stats_thresholds import MIN_SIGNIFICANT_TRADES

logger = logging.getLogger(__name__)

#: Tirages par témoin. Ce nombre fixe le PLANCHER des p-values : avec la
#: convention (1 + k) / (1 + N) de ``p_valeur_empirique``, aucune ligne ne peut
#: descendre sous 1 / (1 + N).
#:
#: À 200 tirages ce plancher valait 0.00498 — au-dessus de TOUS les seuils
#: corrigés de l'étude (1.4e-4 pour 360 motifs, 6.9e-7 pour 73 000 composés).
#: Le « 0 survivant » était donc garanti par construction, quelles que soient
#: les données : le test n'avait pas la résolution pour rejeter quoi que ce
#: soit. Mesuré sur BTC/USDC, cf. research/RESULTATS_smc_patterns_btc.md.
#:
#: À 2000 le plancher tombe à 5.0e-4, ce qui rend les rejets ATTEIGNABLES sous
#: Benjamini-Hochberg (cf. ``seuil_bh``) : il faut alors 4 découvertes pour 360
#: motifs, 730 pour 73 000 composés. Le coût de ce dixuplement est absorbé par
#: la vectorisation de ``_temoin_decale`` — c'est elle qui l'a rendu abordable.
N_TIRAGES_TEMOIN = 2000

#: Plafond d'éléments d'une matrice de tirages, pour borner la mémoire du
#: témoin décalé : un motif à 5 500 occurrences et un composé à 30 n'ont pas le
#: même appétit, la taille de bloc s'ajuste donc en éléments, pas en tirages.
_ELEMENTS_MAX_BLOC = 2_000_000

#: Horizons de mesure du mouvement suivant, en barres.
HORIZONS_DEFAUT = (1, 3, 6, 12, 24)


def _arrondi(v: float, n: int = 5):
    return None if v is None or not np.isfinite(v) else round(float(v), n)


def _stats_echantillon(valeurs: np.ndarray) -> Dict[str, float]:
    """Moyenne, écart-type, et demi-largeur de l'IC à 95 % de la moyenne."""
    v = np.asarray(valeurs, dtype=float)
    v = v[np.isfinite(v)]
    n = len(v)
    if n == 0:
        return {"n": 0, "moyenne": float("nan"), "ecart_type": float("nan"),
                "ic95": float("nan")}
    moyenne = float(v.mean())
    ecart = float(v.std(ddof=1)) if n > 1 else 0.0
    return {"n": n, "moyenne": moyenne, "ecart_type": ecart,
            "ic95": 1.96 * ecart / math.sqrt(n) if n > 1 else float("nan")}


# ─────────────────────────────────────────────────────────────────────────────
#  Fréquence
# ─────────────────────────────────────────────────────────────────────────────
def frequences(journal: pl.DataFrame,
               frames: Dict[str, pl.DataFrame]) -> pl.DataFrame:
    """Fréquence par motif et par TF, en barres ET en temps calendaire.

    Les deux unités sont nécessaires dès qu'on met plusieurs TF côte à côte :
    « 12 occurrences pour 1 000 barres » se compare entre TF, mais ne dit pas
    la même chose en 15 m et en 4 h — 1 000 barres valent dix jours ou six
    mois. La colonne ``par_mois`` rétablit la comparaison en temps réel.
    """
    if journal.height == 0:
        return pl.DataFrame(schema={"tf": pl.Utf8, "motif": pl.Utf8, "n": pl.UInt32,
                                    "par_1000_barres": pl.Float64,
                                    "par_mois": pl.Float64, "significatif": pl.Boolean})
    lignes = []
    for tf, df in frames.items():
        n_barres = len(df) if df is not None else 0
        if n_barres == 0:
            continue
        mois = _duree_en_mois(df)
        sous = journal.filter(pl.col("tf") == tf)
        for motif, n in sous.group_by("motif").len().rows():
            lignes.append({
                "tf": tf, "motif": motif, "n": int(n),
                "par_1000_barres": round(1000.0 * n / n_barres, 3),
                "par_mois": round(n / mois, 3) if mois > 0 else float("nan"),
                "significatif": int(n) >= MIN_SIGNIFICANT_TRADES,
            })
    if not lignes:
        return pl.DataFrame(schema={"tf": pl.Utf8, "motif": pl.Utf8, "n": pl.UInt32,
                                    "par_1000_barres": pl.Float64,
                                    "par_mois": pl.Float64, "significatif": pl.Boolean})
    return pl.DataFrame(lignes).sort(["tf", "n"], descending=[False, True])


def _duree_en_mois(df: pl.DataFrame) -> float:
    try:
        ep = df["time"].dt.epoch(time_unit="s").to_numpy()
        return max((float(ep[-1]) - float(ep[0])) / (30.44 * 86400.0), 1e-9)
    except Exception:
        return 0.0


def ventilation(journal: pl.DataFrame, par: str = "session") -> pl.DataFrame:
    """Répartition de chaque motif selon un axe de contexte.

    ``par`` ∈ {session, killzone, silver_bullet, etat, sequence, htf_etat,
    htf_trend}. La part est rapportée à l'effectif du motif, pas au total :
    on cherche « ce motif se produit-il surtout en killzone ? », pas « quelle
    part des killzones ce motif occupe-t-il ? ».
    """
    if journal.height == 0 or par not in journal.columns:
        return pl.DataFrame(schema={"tf": pl.Utf8, "motif": pl.Utf8, par: pl.Utf8,
                                    "n": pl.UInt32, "part": pl.Float64})
    tot = journal.group_by(["tf", "motif"]).len().rename({"len": "n_motif"})
    return (journal.group_by(["tf", "motif", par]).len()
            .join(tot, on=["tf", "motif"])
            .with_columns((pl.col("len") / pl.col("n_motif")).round(4).alias("part"))
            .rename({"len": "n"})
            .drop("n_motif")
            .sort(["tf", "motif", "n"], descending=[False, False, True]))


# ─────────────────────────────────────────────────────────────────────────────
#  Enchaînement
# ─────────────────────────────────────────────────────────────────────────────
def transitions(journal: pl.DataFrame, tf: str, fenetre: int = 12,
                n_barres: Optional[int] = None) -> pl.DataFrame:
    """P(motif B dans les ``fenetre`` barres | motif A), face à l'indépendance.

    La colonne ``attendu`` est la probabilité qu'on observerait si A et B
    étaient indépendants : ``1 − (1 − densité_B)^fenetre``, où densité_B est le
    nombre d'occurrences de B par barre. Sans elle, une transition à 80 % entre
    deux motifs très fréquents passerait pour une découverte alors qu'elle est
    mécanique.

    ``lift`` = observé / attendu. C'est lui qu'il faut lire, jamais ``observe``
    seul.
    """
    sous = journal.filter(pl.col("tf") == tf)
    if sous.height == 0:
        return pl.DataFrame(schema={"de": pl.Utf8, "vers": pl.Utf8, "n_de": pl.UInt32,
                                    "n_suivi": pl.UInt32, "observe": pl.Float64,
                                    "attendu": pl.Float64, "lift": pl.Float64,
                                    "significatif": pl.Boolean})
    n_barres = int(n_barres or (int(sous["bar"].max()) + 1))  # type: ignore[arg-type]
    # `group_by` rend une clé TUPLE : sans déballage, `de`/`vers` sortiraient
    # en list[str] et toute jointure ultérieure sur ces colonnes échouerait.
    barres_par_motif: Dict[str, np.ndarray] = {
        str(m[0] if isinstance(m, tuple) else m): np.sort(g["bar"].to_numpy())
        for m, g in sous.group_by("motif")
    }
    lignes = []
    for a, bars_a in barres_par_motif.items():
        for b, bars_b in barres_par_motif.items():
            # Une occurrence de B compte si elle tombe dans ]bar_a, bar_a+fenetre].
            gauche = np.searchsorted(bars_b, bars_a, side="right")
            droite = np.searchsorted(bars_b, bars_a + fenetre, side="right")
            suivi = int((droite > gauche).sum())
            n_a = len(bars_a)
            densite_b = len(bars_b) / max(n_barres, 1)
            attendu = 1.0 - (1.0 - min(densite_b, 1.0)) ** fenetre
            observe = suivi / max(n_a, 1)
            lignes.append({
                "de": a, "vers": b, "n_de": n_a, "n_suivi": suivi,
                "observe": round(observe, 4), "attendu": round(attendu, 4),
                "lift": round(observe / attendu, 3) if attendu > 0 else float("nan"),
                "significatif": n_a >= MIN_SIGNIFICANT_TRADES,
            })
    return pl.DataFrame(lignes).sort("lift", descending=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Mouvement suivant + témoins
# ─────────────────────────────────────────────────────────────────────────────
def _rendements_sur(close: np.ndarray, bars: Sequence[int] | np.ndarray,
                    h: int) -> np.ndarray:
    """Idem ``_rendements_a``, mais sur une colonne DÉJÀ matérialisée.

    ``df["close"].to_numpy()`` copie toute la colonne — 57 000 flottants en 15 m.
    Le faire à chaque tirage de témoin coûtait plus cher que le calcul lui-même ;
    les appelants qui bouclent hissent donc la matérialisation hors de la boucle.
    """
    n = len(close)
    b = np.asarray([x for x in bars if 0 <= x < n - h], dtype=int)
    if len(b) == 0:
        return np.zeros(0)
    base = np.maximum(close[b], 1e-12)
    return (close[b + h] - close[b]) / base * 100.0


def _rendements_a(df: pl.DataFrame, bars: Sequence[int] | np.ndarray, h: int) -> np.ndarray:
    """Rendement de la clôture de ``bar`` à celle de ``bar + h``, en %."""
    return _rendements_sur(df["close"].to_numpy().astype(float), bars, h)


def _tirages(n_tirages: Optional[int]) -> int:
    """Nombre de tirages effectif — l'argument prime, le module fait défaut."""
    return int(n_tirages) if n_tirages else N_TIRAGES_TEMOIN


def _mfe_mae_atr(df: pl.DataFrame, bars: Sequence[int] | np.ndarray, h: int,
                 atr: np.ndarray) -> Dict[str, np.ndarray]:
    """Excursions favorable/défavorable maximales sur ]bar, bar+h], en ATR."""
    high = df["high"].to_numpy().astype(float)
    low = df["low"].to_numpy().astype(float)
    close = df["close"].to_numpy().astype(float)
    n = len(close)
    mfe, mae = [], []
    for x in bars:
        if not (0 <= x < n - h):
            continue
        a = atr[x] if x < len(atr) and atr[x] > 0 else np.nan
        if not np.isfinite(a) or a <= 0:
            continue
        fen_h = high[x + 1:x + 1 + h]
        fen_l = low[x + 1:x + 1 + h]
        if len(fen_h) == 0:
            continue
        mfe.append((fen_h.max() - close[x]) / a)
        mae.append((fen_l.min() - close[x]) / a)
    return {"mfe_atr": np.asarray(mfe), "mae_atr": np.asarray(mae)}


def _temoin_inconditionnel(journal_motif: pl.DataFrame, df: pl.DataFrame,
                           sessions: np.ndarray, h: int,
                           rng: np.random.Generator,
                           n_tirages: Optional[int] = None) -> np.ndarray:
    """Barres au hasard, à distribution de sessions identique au motif.

    Sans l'appariement par session, un motif qui ne se produit qu'en session US
    serait comparé à la moyenne de toutes les heures — et l'écart mesurerait
    l'heure de la journée, pas le motif.
    """
    n = len(df)
    besoin: Dict[str, int] = {}
    for s, c in journal_motif.group_by("session").len().rows():
        besoin[str(s)] = int(c)
    index_par_session: Dict[str, np.ndarray] = {
        s: np.flatnonzero(sessions == s) for s in besoin
    }
    close = df["close"].to_numpy().astype(float)
    tirages = []
    for _ in range(_tirages(n_tirages)):
        bars: List[int] = []
        for s, c in besoin.items():
            pool = index_par_session.get(s, np.zeros(0, dtype=int))
            pool = pool[pool < n - h]
            if len(pool) == 0:
                continue
            bars.extend(rng.choice(pool, size=min(c, len(pool)),
                                   replace=len(pool) < c).tolist())
        if bars:
            r = _rendements_sur(close, bars, h)
            if len(r):
                tirages.append(float(r.mean()))
    return np.asarray(tirages, dtype=float)


def _temoin_decale(bars: Sequence[int] | np.ndarray, df: pl.DataFrame, h: int,
                   rng: np.random.Generator,
                   n_tirages: Optional[int] = None) -> np.ndarray:
    """Mêmes événements, décalés circulairement d'un offset aléatoire.

    L'espacement entre occurrences est conservé EXACTEMENT ; seul l'alignement
    au prix est détruit. C'est le témoin qui distingue un motif d'un calendrier.

    VECTORISÉ PAR BLOCS. C'est ce témoin-là qui domine le coût de l'étude : un
    appel par composé confirmé et par horizon, soit ~70 000 appels sur un run
    4 TF. En boucle Python il re-matérialisait la colonne des clôtures à chaque
    tirage, ce qui plafonnait de fait ``N_TIRAGES_TEMOIN`` à 200 — et donc le
    plancher des p-values à 0.00498. Le passage en matrice
    (tirages × occurrences) est ce qui a rendu un plancher utilisable abordable.
    """
    n = len(df)
    b = np.asarray([x for x in bars if 0 <= x < n], dtype=int)
    if len(b) == 0 or n <= h + 1:
        return np.zeros(0)
    close = df["close"].to_numpy().astype(float)
    total = _tirages(n_tirages)
    bloc = max(1, min(total, max(1, _ELEMENTS_MAX_BLOC // max(1, len(b)))))
    moyennes: List[np.ndarray] = []
    for debut in range(0, total, bloc):
        k = min(bloc, total - debut)
        offsets = rng.integers(1, n, size=k)
        decale = (b[None, :] + offsets[:, None]) % n          # (k, len(b))
        # Même filtre que `_rendements_sur` : une occurrence décalée dans les
        # h dernières barres n'a pas de futur, elle sort de la moyenne.
        valide = decale < n - h
        idx = np.where(valide, decale, 0)
        base = np.maximum(close[idx], 1e-12)
        brut = (close[idx + h] - close[idx]) / base * 100.0
        ok = valide & np.isfinite(brut)
        compte = ok.sum(axis=1)
        somme = np.where(ok, brut, 0.0).sum(axis=1)
        # Division protégée puis invalidation : `nanmean` sur une ligne vide
        # émet un avertissement et coûte un masque de plus.
        moy = somme / np.maximum(compte, 1)
        moy[compte == 0] = np.nan
        moyennes.append(moy)
    out = np.concatenate(moyennes) if moyennes else np.zeros(0)
    return out[np.isfinite(out)]


def mouvement_suivant(journal: pl.DataFrame, frames: Dict[str, pl.DataFrame],
                      horizons: Sequence[int] = HORIZONS_DEFAUT,
                      graine: int = 0,
                      n_tirages: Optional[int] = None) -> pl.DataFrame:
    """Rendement après confirmation, avec les deux témoins et les IC.

    Une ligne par (tf, motif, horizon). ``ecart_inconditionnel`` et
    ``ecart_decale`` sont les colonnes à lire ; ``moyenne`` seule ne dit rien.
    """
    from app.core.indicators import precompute_df

    rng = np.random.default_rng(graine)
    lignes: List[Dict[str, Any]] = []
    for tf, df in frames.items():
        if df is None or len(df) < 50:
            continue
        sous = journal.filter(pl.col("tf") == tf)
        if sous.height == 0:
            continue
        pre = precompute_df(df)
        atr = (pre["_pre_atr14"].to_numpy().astype(float)
               if "_pre_atr14" in pre.columns else np.full(len(df), np.nan))
        sessions = np.asarray(sous_sessions(df), dtype=object)

        for motif, g in sous.group_by("motif"):
            nom = motif[0] if isinstance(motif, tuple) else motif
            bars = g["bar"].to_numpy()
            for h in horizons:
                obs = _rendements_a(df, bars, h)
                if len(obs) == 0:
                    continue
                s_obs = _stats_echantillon(obs)
                moy_inc = _temoin_inconditionnel(g, df, sessions, h, rng,
                                                 n_tirages)
                moy_dec = _temoin_decale(bars, df, h, rng, n_tirages)
                s_inc = _stats_echantillon(moy_inc)
                s_dec = _stats_echantillon(moy_dec)
                exc = _mfe_mae_atr(df, bars, h, atr)
                lignes.append({
                    "tf": tf, "motif": nom, "horizon": int(h),
                    "n": s_obs["n"],
                    "moyenne_pct": round(s_obs["moyenne"], 4),
                    "ic95_pct": round(s_obs["ic95"], 4) if np.isfinite(s_obs["ic95"]) else None,
                    "temoin_inconditionnel_pct": round(s_inc["moyenne"], 4) if s_inc["n"] else None,
                    "temoin_decale_pct": round(s_dec["moyenne"], 4) if s_dec["n"] else None,
                    "ecart_inconditionnel": (round(s_obs["moyenne"] - s_inc["moyenne"], 4)
                                             if s_inc["n"] else None),
                    "ecart_decale": (round(s_obs["moyenne"] - s_dec["moyenne"], 4)
                                     if s_dec["n"] else None),
                    "mfe_atr": round(float(exc["mfe_atr"].mean()), 3) if len(exc["mfe_atr"]) else None,
                    "mae_atr": round(float(exc["mae_atr"].mean()), 3) if len(exc["mae_atr"]) else None,
                    "p_inconditionnel": _arrondi(p_valeur_empirique(s_obs["moyenne"], moy_inc)),
                    "p_decale": _arrondi(p_valeur_empirique(s_obs["moyenne"], moy_dec)),
                    "significatif": s_obs["n"] >= MIN_SIGNIFICANT_TRADES,
                })
    if not lignes:
        return pl.DataFrame(schema={"tf": pl.Utf8, "motif": pl.Utf8, "horizon": pl.Int64})
    return pl.DataFrame(lignes).sort(["tf", "motif", "horizon"])


def sous_sessions(df: pl.DataFrame) -> List[str]:
    from app.core.smc.sessions import session_label
    ep = df["time"].dt.epoch(time_unit="s").to_numpy()
    return [session_label(float(t)) for t in ep]


def p_valeur_empirique(observe: float, moyennes_temoin: np.ndarray) -> float:
    """p bilatérale de ``observe`` dans la distribution des moyennes du témoin.

    POURQUOI PAS UN TEST PARAMÉTRIQUE. Les fenêtres de rendement se
    CHEVAUCHENT : à l'horizon 12 avec un motif tous les trois barres, la même
    hausse est comptée quatre fois. L'écart-type naïf suppose des observations
    indépendantes, sous-estime massivement l'incertitude, et déclare
    significatif à peu près tout. Mesuré sur une marche aléatoire : 125
    « découvertes » survivant à Bonferroni — c'est-à-dire une machine à faux
    positifs avec un vernis de rigueur.

    Le témoin décalé, lui, porte EXACTEMENT le même espacement d'événements et
    le même recouvrement de fenêtres, puisque ce sont les mêmes événements
    décalés en bloc. Comparer la moyenne observée à la distribution des
    moyennes du témoin est donc un test de permutation : le chevauchement,
    l'autocorrélation et la non-normalité sont dans les deux bras, ils
    s'annulent au lieu de se cacher.

    Convention (1 + k) / (1 + N) : une p-valeur nulle n'existe pas avec un
    nombre fini de tirages, et prétendre le contraire serait le même genre de
    fausse précision.
    """
    t = np.asarray(moyennes_temoin, dtype=float)
    t = t[np.isfinite(t)]
    if len(t) == 0 or not np.isfinite(observe):
        return float("nan")
    centre = float(t.mean())
    ecart_observe = abs(float(observe) - centre)
    plus_extremes = int(np.sum(np.abs(t - centre) >= ecart_observe))
    return (1.0 + plus_extremes) / (1.0 + len(t))


# ─────────────────────────────────────────────────────────────────────────────
#  Test multiple
# ─────────────────────────────────────────────────────────────────────────────
def plancher_p(n_tirages: Optional[int] = None) -> float:
    """Plus petite p-value ATTEIGNABLE avec ce nombre de tirages.

    À publier avec le seuil : un seuil sous ce plancher ne rejette jamais, et
    le « 0 survivant » qui en résulte ne dit rien des données. C'est
    exactement ce qui s'est produit à 200 tirages.
    """
    return 1.0 / (1.0 + _tirages(n_tirages))


def seuil_bh(p_values: Sequence[Optional[float]], m: Optional[int] = None,
             alpha: float = 0.05) -> Optional[float]:
    """Seuil de Benjamini-Hochberg : contrôle le FDR, pas le FWER.

    POURQUOI PAS BONFERRONI. Bonferroni contrôle la probabilité de la MOINDRE
    fausse découverte : sur 73 000 composés il exige 6.9e-7, soit mille fois
    moins que le plancher du test de permutation. Il ne rejetait donc rien —
    pas par sévérité bien placée, mais par impossibilité arithmétique.

    BH contrôle la PROPORTION attendue de fausses découvertes parmi les
    rejets. Sur une étude exploratoire dont le produit est une liste de
    candidats à retester, c'est la bonne garantie : accepter 5 % de déchet
    dans une liste de vingt pistes est raisonnable, exiger zéro faux positif
    sur 73 000 tests ne l'est pas.

    Procédure (step-up) : p triées, on cherche le plus grand rang k tel que
    p(k) ≤ k/m · alpha, et on rejette tout jusqu'à ce rang.

    ``m`` est le nombre d'hypothèses ÉNUMÉRÉES, qui peut dépasser le nombre de
    p-values fournies : les hypothèses écartées en amont (support insuffisant)
    comptent au dénominateur mais ne peuvent pas être rejetées. C'est le même
    principe que le décompte d'énumération de ``composites.mine``.
    """
    p = np.asarray([float(x) for x in p_values
                    if x is not None and np.isfinite(x)], dtype=float)
    if len(p) == 0:
        return None
    total = int(m) if m else len(p)
    if total <= 0:
        return None
    p.sort()
    rangs = np.arange(1, len(p) + 1, dtype=float)
    passe = p <= (rangs / float(total)) * alpha
    if not passe.any():
        return None
    return float(p[int(np.flatnonzero(passe)[-1])])


def budget_hypotheses(resultats: pl.DataFrame,
                      n_tirages: Optional[int] = None) -> Dict[str, Any]:
    """Nombre d'hypothèses testées, seuils, et plancher de résolution du test.

    Tester 500 combinaisons puis publier les cinq meilleures sans le dire n'est
    pas une analyse, c'est du tri de bruit. Ce décompte va EN TÊTE du rapport,
    pas en note de bas de page.

    ``alpha_bonferroni`` reste publié à titre de COMPARAISON — c'est lui qui
    rendait l'étude incapable de conclure, et le rapport doit pouvoir le
    montrer à côté du plancher.
    """
    n = int(resultats.height)
    plancher = plancher_p(n_tirages)
    if n == 0:
        return {"n_hypotheses": 0, "alpha_nominal": 0.05, "alpha_bonferroni": None,
                "z_requis": None, "methode": "benjamini-hochberg",
                "plancher_p": plancher, "seuil_bh": None}
    alpha = 0.05 / n
    # Quantile normal bilatéral, sans scipy (absent du dépôt en production).
    from statistics import NormalDist
    z = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    return {"n_hypotheses": n, "alpha_nominal": 0.05,
            "alpha_bonferroni": alpha, "z_requis": round(z, 3),
            "methode": "benjamini-hochberg", "plancher_p": plancher,
            "seuil_bh": None}


def survivants(resultats: pl.DataFrame, budget: Dict[str, Any]) -> pl.DataFrame:
    """Lignes dont l'écart aux DEUX témoins survit au contrôle du FDR.

    Le témoin décalé est le juge principal : c'est celui qui distingue un motif
    de son calendrier. Mais les deux doivent tomber — un motif qui bat le
    témoin inconditionnel sans battre le décalé n'a pas d'edge propre, il
    hérite de son calendrier.

    « Les deux tombent » se teste par le MAXIMUM des deux p-values : c'est le
    test d'intersection-union, et ce maximum est une p-value valide au niveau
    visé pour l'hypothèse conjointe. BH s'applique donc à cette p-value
    combinée, une par ligne — pas deux fois séparément, ce qui ne contrôlerait
    plus rien.

    Une ligne sous ``MIN_SIGNIFICANT_TRADES`` occurrences est écartée quel que
    soit son écart. Le seuil effectivement appliqué est écrit dans
    ``budget["seuil_bh"]`` pour que le rapport le publie.
    """
    budget["seuil_bh"] = None
    if resultats.height == 0 or not budget.get("n_hypotheses"):
        return resultats.head(0)

    eligibles = resultats.filter(
        pl.col("significatif")
        & pl.col("p_decale").is_not_null()
        & pl.col("p_inconditionnel").is_not_null())
    if eligibles.height == 0:
        return resultats.head(0)

    combinee = (pl.max_horizontal(pl.col("p_decale"), pl.col("p_inconditionnel"))
                .alias("_p_combinee"))
    eligibles = eligibles.with_columns(combinee)
    seuil = seuil_bh(eligibles["_p_combinee"].to_list(),
                     m=int(budget["n_hypotheses"]),
                     alpha=float(budget.get("alpha_nominal") or 0.05))
    if seuil is None:
        return resultats.head(0)
    budget["seuil_bh"] = seuil
    return (eligibles
            .filter(pl.col("_p_combinee") <= seuil)
            .drop("_p_combinee")
            .sort("ecart_decale", descending=True, nulls_last=True))
