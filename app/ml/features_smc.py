"""Bloc de features Smart Money Concepts / ICT pour le ML.

Le moteur SMC (``app/core/smc*.py``, ~1 240 lignes) était invisible pour la
couche ML : aucun import depuis ``app/ml/``, aucune colonne SMC dans les
catalogues. Le modèle voyait l'analyse technique classique — RSI, MACD, ADX,
moyennes mobiles, volume — et rien de la structure de marché. C'est la famille
de signaux dans laquelle le dépôt a le plus investi.

Ce module la rend disponible, sous une contrainte non négociable : **aucune
fuite temporelle**. Une feature à la barre ``i`` ne peut utiliser que des
entités confirmées à ``i`` ou avant.

Comment la causalité est obtenue
--------------------------------
``smc.analyze()`` renvoie des entités portant leur indice de CONFIRMATION, qui
n'est pas leur indice de formation :

- un pivot repéré en ``index`` n'est ``confirmed_at`` que ``swing_right``
  barres plus tard — l'utiliser dès ``index`` serait lire le futur ;
- un order block formé en ``index`` n'est ``created_at`` qu'après le
  déplacement qui le qualifie ;
- une poche de liquidité est ``formed_at`` quand le second sommet égal apparaît.

Chaque compteur ci-dessous n'incorpore donc une entité qu'à partir de son
indice de confirmation, et l'en retire à son invalidation. La vérification est
faite par ``tests/test_features_smc.py``, qui reconstruit les features sur un
préfixe et exige l'égalité avec le calcul sur la série complète — le seul test
qui attrape réellement une fuite.

Normalisation
-------------
Toutes les distances sont divisées par l'ATR de la barre. Un modèle entraîné
sur BTC à 60 000 et servi sur XRP à 0,50 ne peut rien faire d'une distance en
devise ; en multiples d'ATR, « le prix est à 1,5 ATR sous l'order block » veut
dire la même chose partout.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)

#: Plafond des compteurs « barres depuis … ». Au-delà, l'information n'a plus de
#: valeur discriminante et une valeur non bornée écraserait l'échelle de l'arbre.
_MAX_AGE = 200.0

#: Durée de vie maximale d'une zone (order block, FVG, void, breaker, poche de
#: liquidité) que le prix n'a jamais invalidée. Voir :func:`_intervalles` pour
#: la raison — sans péremption, les compteurs deviennent des horloges.
_AGE_MAX_ZONE = 200

#: Distance retenue quand aucune entité du type n'est visible. Choisir une
#: valeur FINIE et grande plutôt que NaN : LightGBM traite les NaN comme une
#: branche à part, ce qui ferait apprendre « absence d'order block » comme un
#: signal en soi alors qu'on veut « très loin ».
_FAR = 10.0

SMC_FEATURE_NAMES: List[str] = [
    # Structure de marché
    "smc_trend",
    "smc_bars_since_bos",
    "smc_bars_since_choch",
    "smc_last_break_dir",
    # Order blocks encore valides
    "smc_ob_bull_dist_atr",
    "smc_ob_bear_dist_atr",
    "smc_ob_bull_n",
    "smc_ob_bear_n",
    # Fair Value Gaps non comblés
    "smc_fvg_up_dist_atr",
    "smc_fvg_dn_dist_atr",
    "smc_fvg_up_n",
    "smc_fvg_dn_n",
    # Liquidité
    "smc_liq_buy_dist_atr",
    "smc_liq_sell_dist_atr",
    "smc_bars_since_sweep",
    "smc_last_sweep_dir",
    # Premium / Discount (dealing range)
    "smc_pd_position",
    "smc_pd_in_ote",
    # Zones fines et breakers
    "smc_void_n_open",
    "smc_breaker_n_fresh",
    # Contexte de séance
    "smc_killzone",
]


def _confirm_index(entity: Dict[str, Any], *cles: str) -> Optional[int]:
    """Indice à partir duquel l'entité est CONNAISSABLE.

    On prend la première clé présente et non nulle parmi ``cles`` — l'ordre
    d'appel place toujours l'indice de confirmation avant l'indice de formation.
    """
    for k in cles:
        v = entity.get(k)
        if v is not None:
            return int(v)
    return None


def _fin_de_validite(entity: Dict[str, Any], *cles: str) -> int:
    """Indice à partir duquel l'entité n'est plus valide (exclu), ou l'infini.

    On prend le MINIMUM des fins candidates, surtout pas la première clé
    renseignée. C'est ce qui rend la fonction causale, et le piège mérite d'être
    raconté parce qu'il a été détecté par le test de préfixe et par rien d'autre.

    Un FVG porte ``mitigated_at`` (le prix est entré dans le gap) et
    ``filled_at`` (le gap est refermé), avec ``mitigated_at <= filled_at``. Si on
    lit ``filled_at`` en premier, la fin dépend de la LONGUEUR DE LA SÉRIE
    ANALYSÉE : sur un préfixe s'arrêtant avant le comblement, ``filled_at`` vaut
    ``None`` et on retombe sur ``mitigated_at`` ; sur la série complète,
    ``filled_at`` est renseigné et masque ``mitigated_at``. Deux réponses
    différentes pour la même barre — donc une feature qui ne se reproduit pas en
    direct. Le minimum ne dépend, lui, que de ce qui s'est déjà produit.
    """
    fins = [int(entity[k]) for k in cles if entity.get(k) is not None]
    return min(fins) if fins else np.iinfo(np.int32).max


def _intervalles(entities: List[Dict[str, Any]],
                 cles_debut: Tuple[str, ...],
                 cles_fin: Tuple[str, ...],
                 age_max: int = _AGE_MAX_ZONE) -> List[Tuple[int, int, Dict[str, Any]]]:
    """Transforme les entités en intervalles de validité ``[debut, fin)``.

    ``age_max`` fait expirer une zone que le prix n'a jamais invalidée. Sans
    cette péremption, les compteurs ne mesuraient plus la structure de marché
    mais le TEMPS ÉCOULÉ : mesuré sur BTC 1h, ``smc_breaker_n_fresh`` corrélait
    à 0.96 avec l'indice de barre, ``smc_ob_bear_n`` à 0.86. Une telle colonne
    est une horloge — le modèle s'en sert pour reconnaître l'époque du jeu
    d'entraînement, ce qui gonfle l'AUC en validation et ne vaut rien en direct.

    La péremption a aussi un sens métier : un order block que le prix n'a pas
    revisité depuis 200 barres n'est plus un niveau que quiconque surveille.
    """
    out = []
    for e in entities or []:
        d = _confirm_index(e, *cles_debut)
        if d is None:
            continue
        f = min(_fin_de_validite(e, *cles_fin), d + age_max)
        if f > d:
            out.append((d, f, e))
    out.sort(key=lambda t: t[0])
    return out


def _balayage_zones(n: int, close: np.ndarray, atr: np.ndarray,
                    intervalles: List[Tuple[int, int, Dict[str, Any]]],
                    au_dessus: bool) -> Tuple[np.ndarray, np.ndarray]:
    """Pour chaque barre : distance (en ATR) à la zone active la plus proche du
    bon côté du prix, et nombre de zones actives.

    Une seule passe sur les barres, avec un pointeur d'activation — O(n + m).
    """
    dist = np.full(n, _FAR, dtype=np.float64)
    compte = np.zeros(n, dtype=np.float64)
    actives: List[Tuple[int, Dict[str, Any]]] = []
    ptr = 0
    for i in range(n):
        while ptr < len(intervalles) and intervalles[ptr][0] <= i:
            actives.append((intervalles[ptr][1], intervalles[ptr][2]))
            ptr += 1
        if actives:
            actives = [(f, e) for f, e in actives if f > i]
        if not actives:
            continue
        a = atr[i] if atr[i] > 0 else 1e-9
        px = close[i]
        meilleures = []
        for _, e in actives:
            haut = float(e.get("top", e.get("level", px)))
            bas = float(e.get("bottom", e.get("level", px)))
            if au_dessus and bas >= px:
                meilleures.append((bas - px) / a)
            elif (not au_dessus) and haut <= px:
                meilleures.append((px - haut) / a)
        compte[i] = float(len(meilleures))
        if meilleures:
            dist[i] = min(min(meilleures), _FAR)
    return dist, compte


def build_smc_features(df: pl.DataFrame,
                       params: Optional[dict] = None) -> Optional[pl.DataFrame]:
    """Construit le bloc SMC aligné sur ``df`` (mêmes lignes, même ordre).

    Retourne ``None`` si l'analyse SMC n'est pas exploitable (série trop courte),
    ce que l'appelant traite comme « pas de bloc SMC » plutôt que comme une
    erreur — une recette sans SMC reste entraînable.
    """
    n = len(df)
    if n < 50 or not {"open", "high", "low", "close"}.issubset(set(df.columns)):
        return None

    try:
        from app.core.smc import analyze, killzone_flags, premium_discount_at
        res = analyze(df, params)
    except Exception as e:  # pragma: no cover — défensif
        logger.warning(f"[features_smc] analyse SMC KO : {e}")
        return None

    h = df["high"].to_numpy().astype(float)
    lo = df["low"].to_numpy().astype(float)
    c = df["close"].to_numpy().astype(float)
    atr = np.asarray(res.get("_atr_arr"), dtype=float)
    if atr.shape[0] != n:
        atr = np.full(n, np.nan)
    atr = np.where(np.isfinite(atr) & (atr > 0), atr, np.nanmedian(atr[atr > 0]) if (atr > 0).any() else 1.0)

    cols: Dict[str, np.ndarray] = {}

    # ── Structure : tendance par barre, âge des cassures ────────────────────
    brut = res.get("_trend_arr")
    trend = np.zeros(n) if brut is None else np.asarray(brut, dtype=float)
    cols["smc_trend"] = trend if trend.shape[0] == n else np.zeros(n)

    age_bos = np.full(n, _MAX_AGE)
    age_choch = np.full(n, _MAX_AGE)
    dir_break = np.zeros(n)
    evts = sorted((e for e in (res.get("_all_struct_events") or [])
                   if e.get("index") is not None), key=lambda e: int(e["index"]))
    ptr, dernier_bos, dernier_choch, dernier_dir = 0, None, None, 0.0
    for i in range(n):
        while ptr < len(evts) and int(evts[ptr]["index"]) <= i:
            evt = evts[ptr]
            if str(evt.get("kind", "")).upper() == "BOS":
                dernier_bos = int(evt["index"])
            else:
                dernier_choch = int(evt["index"])
            dernier_dir = 1.0 if str(evt.get("direction", "")) == "up" else -1.0
            ptr += 1
        if dernier_bos is not None:
            age_bos[i] = min(i - dernier_bos, _MAX_AGE)
        if dernier_choch is not None:
            age_choch[i] = min(i - dernier_choch, _MAX_AGE)
        dir_break[i] = dernier_dir
    cols["smc_bars_since_bos"] = age_bos
    cols["smc_bars_since_choch"] = age_choch
    cols["smc_last_break_dir"] = dir_break

    # ── Order blocks encore valides ────────────────────────────────────────
    obs = res.get("_all_obs") or []
    for sens, nom in (("bullish", "bull"), ("bearish", "bear")):
        sous = [e for e in obs if str(e.get("kind")) == sens]
        iv = _intervalles(sous, ("created_at", "index"), ("invalidated_at",))
        # Un OB haussier est une zone de DEMANDE : elle se situe sous le prix.
        d, k = _balayage_zones(n, c, atr, iv, au_dessus=(sens == "bearish"))
        cols[f"smc_ob_{nom}_dist_atr"] = d
        cols[f"smc_ob_{nom}_n"] = k

    # ── Fair Value Gaps non comblés ────────────────────────────────────────
    fvgs = res.get("_all_fvgs") or []
    for sens, nom, au_dessus in (("bullish", "dn", False), ("bearish", "up", True)):
        sous = [e for e in fvgs if str(e.get("kind")) == sens]
        iv = _intervalles(sous, ("index",), ("filled_at", "mitigated_at"))
        d, k = _balayage_zones(n, c, atr, iv, au_dessus=au_dessus)
        cols[f"smc_fvg_{nom}_dist_atr"] = d
        cols[f"smc_fvg_{nom}_n"] = k

    # ── Liquidité : poches non balayées ────────────────────────────────────
    pools = res.get("_all_pools") or []
    for sens, nom, au_dessus in (("buy_side", "buy", True), ("sell_side", "sell", False)):
        sous = [e for e in pools if str(e.get("kind")) == sens]
        iv = _intervalles(sous, ("formed_at",), ("swept_at",))
        d, _ = _balayage_zones(n, c, atr, iv, au_dessus=au_dessus)
        cols[f"smc_liq_{nom}_dist_atr"] = d

    age_sweep = np.full(n, _MAX_AGE)
    dir_sweep = np.zeros(n)
    sweeps = sorted((e for e in (res.get("_all_sweeps") or [])
                     if e.get("index") is not None), key=lambda e: int(e["index"]))
    ptr, dernier, dernier_kind = 0, None, 0.0
    for i in range(n):
        while ptr < len(sweeps) and int(sweeps[ptr]["index"]) <= i:
            dernier = int(sweeps[ptr]["index"])
            dernier_kind = 1.0 if str(sweeps[ptr].get("kind")) == "buy_side" else -1.0
            ptr += 1
        if dernier is not None:
            age_sweep[i] = min(i - dernier, _MAX_AGE)
        dir_sweep[i] = dernier_kind
    cols["smc_bars_since_sweep"] = age_sweep
    cols["smc_last_sweep_dir"] = dir_sweep

    # ── Premium / Discount, barre par barre ────────────────────────────────
    pos = np.full(n, 0.5)
    ote = np.zeros(n)
    for i in range(n):
        try:
            pd_i = premium_discount_at(res, h, lo, c, i)
        except Exception:
            pd_i = None
        if pd_i:
            p = pd_i.get("position")
            if p is not None and np.isfinite(p):
                pos[i] = float(p)
            ote[i] = 1.0 if pd_i.get("in_ote") else 0.0
    cols["smc_pd_position"] = pos
    cols["smc_pd_in_ote"] = ote

    # ── Zones fines et breakers encore frais ───────────────────────────────
    voids = _intervalles(res.get("_all_voids") or [],
                         ("end_index", "start_index"), ("filled_at", "mitigated_at"))
    breakers = _intervalles(res.get("_all_breakers") or [],
                            ("created_at", "index"), ("invalidated_at",))
    for nom, iv in (("smc_void_n_open", voids), ("smc_breaker_n_fresh", breakers)):
        k = np.zeros(n)
        ptr, actives = 0, []
        for i in range(n):
            while ptr < len(iv) and iv[ptr][0] <= i:
                actives.append(iv[ptr][1])
                ptr += 1
            actives = [f for f in actives if f > i]
            k[i] = float(len(actives))
        cols[nom] = k

    # ── Contexte de séance (causal par nature : dérivé de l'horodatage) ────
    if "time" in df.columns:
        try:
            epoch = df["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
            cols["smc_killzone"] = killzone_flags(epoch).astype(float)
        except Exception:
            cols["smc_killzone"] = np.zeros(n)
    else:
        cols["smc_killzone"] = np.zeros(n)

    manquantes = [nom for nom in SMC_FEATURE_NAMES if nom not in cols]
    if manquantes:  # pragma: no cover — filet, ne doit pas arriver
        logger.warning(f"[features_smc] colonnes manquantes : {manquantes}")
        for nom in manquantes:
            cols[nom] = np.zeros(n)

    return pl.DataFrame({nom: cols[nom] for nom in SMC_FEATURE_NAMES})
