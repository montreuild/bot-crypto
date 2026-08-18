"""Résolution des paramètres de stratégie — fondation core (V4-B : ARCH-02).

Historiquement dans app/live/utils.py « évitant les imports circulaires » :
la couche engine (backtest, opt_persistence, opt_workers) et les routes API
importaient depuis app/live — une inversion de dépendance. Le module vit
désormais dans app/core, importable par TOUTES les couches ; app/live/utils
conserve des ré-exports de compatibilité.

Précédence de résolution : strategy_params (base) < optimizer_results
[strat][tf][symbole] — les clés globales (_GLOBAL_PARAM_KEYS) ne sont jamais
écrasées par l'optimiseur, côté backtest COMME côté live (ARCH-01).
"""
from typing import Optional

# ---------------------------------------------------------------------------
# Fusion des paramètres de stratégie
# ---------------------------------------------------------------------------

def _is_nan_like(v) -> bool:
    """True si ``v`` est un scalaire invalide : ``NaN`` *ou* ``NaT``.

    ``math.isnan`` ne gère que les floats — il lève sur un ``NaT``
    pandas/numpy, lequel se glissait jusque dans ``float(param)`` côté
    stratégie et provoquait ``float() argument ... not 'NaTType'``. On
    s'appuie sur l'auto-inégalité (``v != v``), vraie uniquement pour NaN et
    NaT ; les listes/dicts/chaînes/nombres valides renvoient ``False``
    (``[] != []`` vaut bien ``False``), et tout type exotique est ignoré sans
    risque.
    """
    try:
        return bool(v != v)
    except Exception:
        return False


def _clean_param_dict(d: dict) -> dict:
    """Retourne une copie de ``d`` sans les valeurs scalaires invalides (NaN/NaT).

    Appliqué aux params *de base* (``strategy_params``) en plus de l'overlay
    optimiseur : un NaT persisté dans le bloc ``params:`` d'un YAML stratégie
    (via ``apply_best_params``) était rechargé comme base et atteignait
    ``float(p.get(...))`` côté stratégie sans passer par le filtre overlay.
    Une valeur retirée fait retomber la stratégie sur son défaut interne.
    """
    if not isinstance(d, dict):
        return d
    return {k: v for k, v in d.items() if not _is_nan_like(v)}


def _merge_params(base: dict, optimized: dict) -> dict:
    """
    Fusionne les params de config (base) avec les params optimisés.

    - base     : cfg["strategy_params"] complet — ex. {"trend": {...}, "supertrend_macd": {...}}
    - optimized: params d'un entry _active_per_tf — ex. {"trend": {"adx_min": 25, ...}}

    Résultat : dict complet avec toutes les stratégies, params optimisés écrasant le base.
    Les valeurs NaN/None sont remplacées par les valeurs base ou les défauts.

    Les clés GLOBALES (`_GLOBAL_PARAM_KEYS` : score_threshold, risk_per_trade,
    capital…) ne sont JAMAIS écrasées par l'overlay — même filtre que
    ``resolve_strategy_params`` (cf. ARCH-01 : le live appliquait une clé
    globale glissée par erreur dans optimizer_results là où le backtest la
    bloquait — divergence de parité).
    """
    merged = {k: _clean_param_dict(v) if isinstance(v, dict) else v
              for k, v in base.items()}
    for strat_key, strat_params in optimized.items():
        if not isinstance(strat_params, dict):
            continue
        base_for_strat = dict(merged.get(strat_key, {}))
        for k, v in strat_params.items():
            if k in _GLOBAL_PARAM_KEYS or v is None or _is_nan_like(v):
                continue
            base_for_strat[k] = v
        merged[strat_key] = base_for_strat
    return merged


# Clés globales à ne jamais écraser par les résultats de l'optimiseur
_GLOBAL_PARAM_KEYS = frozenset({
    "score_threshold", "risk_per_trade", "capital", "timeframe", "timeframes",
    "paper_mode", "max_positions", "taker_fee", "maker_fee",
})


# Symbole de référence : un jeu ``optimizer_results[strat][tf]`` HÉRITÉ (sans
# dimension symbole) est considéré comme calibré pour ce symbole (historiquement,
# l'optimiseur tournait sur BTC/USDC).
DEFAULT_CONFIG_SYMBOL = "BTC/USDC"


def _is_legacy_tf_entry(tf_entry: dict) -> bool:
    """Vrai si ``tf_entry`` est une entrée d'optimisation UNIQUE (schéma hérité,
    sans dimension symbole) plutôt qu'un mapping ``{symbole: entrée}``. On la
    reconnaît à ses clés de métadonnées (``params``/``oos_score``/``run_date``)."""
    return any(k in tf_entry for k in ("params", "oos_score", "run_date"))


def _select_symbol_entry(tf_entry: dict, symbol: str | None = None) -> Optional[dict]:
    """Sélectionne l'entrée d'optimisation applicable à ``symbol`` dans
    ``optimizer_results[strat][tf]`` (schéma hérité OU ``{symbole: entrée}``).

    - Entrée héritée (unique) = config de ``DEFAULT_CONFIG_SYMBOL`` (BTC/USDC) :
      appliquée si ``symbol`` est None (appelants historiques → byte-identique)
      ou vaut BTC/USDC ; sinon None (une config BTC ne déteint pas sur ETH).
    - Mapping par symbole : entrée exacte de ``symbol`` ; à défaut, entrée
      BTC/USDC quand ``symbol`` est None. Sinon None → params de base."""
    if _is_legacy_tf_entry(tf_entry):
        if symbol is None or symbol == DEFAULT_CONFIG_SYMBOL:
            return tf_entry
        return None
    if symbol is not None:
        return tf_entry.get(symbol)
    return tf_entry.get(DEFAULT_CONFIG_SYMBOL)


def resolve_strategy_params(cfg: dict, timeframe: str | None = None,
                            symbol: str | None = None) -> dict:
    """
    Construit le dict de paramètres de stratégie en superposant les résultats
    de l'optimiseur (optimizer_results) sur les params de base (strategy_params).

    Utilisé par Backtester.run() et LiveTrader pour garantir que les deux
    chemins de code utilisent exactement la même logique de résolution.

    Précédence : strategy_params (base) < optimizer_results[strat][tf][symbol]
    Les clés globales (_GLOBAL_PARAM_KEYS) ne sont jamais écrasées par l'optimiseur.

    Parameters
    ----------
    cfg       : dict config globale (doit contenir "strategy_params")
    timeframe : str ou None — si fourni, superpose optimizer_results[strat][tf][symbol]
    symbol    : str ou None — symbole cible. None = comportement hérité (une config
                sans dimension symbole s'applique ; sinon défaut BTC/USDC). Une
                config héritée est réputée calibrée pour BTC/USDC et ne s'applique
                PAS aux autres symboles (séparation des configs par symbole).
    """
    strat_params = {
        name: _clean_param_dict(p) if isinstance(p, dict) else p
        for name, p in cfg.get("strategy_params", {}).items()
    }

    if timeframe:
        opt_results = cfg.get("optimizer_results") or {}
        for strat_name, tf_map in opt_results.items():
            if not isinstance(tf_map, dict):
                continue
            tf_entry = tf_map.get(timeframe)
            if not isinstance(tf_entry, dict):
                continue
            entry = _select_symbol_entry(tf_entry, symbol)
            if not isinstance(entry, dict):
                continue
            opt_p = entry.get("params", {})
            if not opt_p:
                continue
            base = dict(strat_params.get(strat_name, {}))
            for k, v in opt_p.items():
                if k in _GLOBAL_PARAM_KEYS or v is None or _is_nan_like(v):
                    continue
                base[k] = v
            strat_params[strat_name] = base

    return strat_params
