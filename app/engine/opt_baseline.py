"""Tranches d'évaluation et baseline de comparaison (DETTE-04).

Le baseline est mesuré sur la MÊME tranche que le candidat : c'est ce qui rend
la comparaison du gate honnête. Extrait d'`auto_optimizer.py`, inchangé.
"""

import importlib
import logging
from copy import deepcopy

import polars as pl

from app.core.is_oos import split_with_holdout
from app.engine.backtest import Backtester
from app.engine.engine import Engine

logger = logging.getLogger(__name__)

def _slices_for(df, strategy_name: str, timeframe: str, min_bars: int,
                holdout_fraction: float):
    """``(df_is, df_oos, split, df_recherche, df_holdout, df_gate)``.

    Un seul endroit décide du découpage à trois tranches, pour que le chemin
    asynchrone et le chemin séquentiel ne divergent pas — c'est exactement le
    genre d'écart qui produit un gate mesuré sur le holdout d'un côté et sur la
    sélection de l'autre, sans que rien ne le signale.
    """
    from app.core.is_oos import default_purge_embargo
    n = len(df) if df is not None else 0
    lookahead = 0
    try:
        mod = importlib.import_module(f"app.strategies.{strategy_name}")
        cls = getattr(mod, "Strategy", None)
        raw = (getattr(cls, "label_horizons", None)
               or getattr(cls, "lookahead", None)
               or getattr(cls, "horizon", None))
        if raw is not None:
            from app.ml.splitting import label_embargo
            lookahead = label_embargo(raw if hasattr(raw, "__iter__")
                                      and not isinstance(raw, (str, bytes))
                                      else [raw])
    except Exception:
        lookahead = 0
    purge, embargo = default_purge_embargo(n, lookahead)
    df_is, df_oos, split, df_recherche, df_holdout = split_with_holdout(
        df, holdout_fraction=holdout_fraction, min_holdout_bars=min_bars,
        purge_bars=purge, embargo_bars=embargo)
    if df_holdout is None and holdout_fraction > 0:
        logger.warning(
            f"[AutoOpt] {strategy_name}/{timeframe} : pas de holdout — "
            f"{len(df)} bougies ne permettent pas d'en réserver une tranche "
            f"exploitable ({min_bars} barres min pour cette stratégie). "
            f"L'auto-apply sera refusé (OPT-04) ; le chiffre affiché reste "
            f"mesuré sur la tranche de sélection.")
    # Le baseline se mesure là où le candidat sera jugé, sinon la comparaison
    # n'a pas de sens. Sans holdout, df_gate sert à AFFICHER, pas à décider.
    df_gate = df_holdout if df_holdout is not None else df_oos
    return df_is, df_oos, split, df_recherche, df_holdout, df_gate


def _cfg_avec_params(cfg: dict, strategy_name: str, params: dict) -> dict:
    """Copie de ``cfg`` où ``strategy_name`` porte ``params``, sans overlay.

    ``optimizer_results`` est vidé : c'est l'entrée qui, dans le YAML, gagne sur
    ``strategy_params`` — la laisser reviendrait à mesurer le paramétrage
    ACTUEL en croyant mesurer le candidat.
    """
    cfg2 = {k: v for k, v in cfg.items()}
    sp = deepcopy(cfg.get("strategy_params") or {})
    fusion = dict(sp.get(strategy_name, {}))
    fusion.update(params or {})
    sp[strategy_name] = fusion
    cfg2["strategy_params"] = sp
    cfg2["optimizer_results"] = {}
    return cfg2


def _run_baseline(strategy_name: str, cfg: dict,
                  df_oos: pl.DataFrame, symbol: str,
                  timeframe: str | None = None) -> dict:
    try:
        mod = importlib.import_module(f"app.strategies.{strategy_name}")
        eng = Engine()
        eng.register(mod.Strategy())
        # BT-01 : même résolution que le walk-forward, sinon le gate d'apply
        # compare un baseline avec circuit breakers à des folds sans.
        from app.core.is_oos import resolve_realistic_risk
        bt  = Backtester(eng, cfg, realistic_risk=resolve_realistic_risk(cfg))
        # timeframe transmis pour que resolve_strategy_params superpose
        # optimizer_results[tf] : le baseline reflète ainsi le paramétrage
        # RÉELLEMENT actif (params: + optimizer_results), comme le live/comparatif,
        # et non le seul bloc params: par défaut.
        res = bt.run(df_oos, symbol, timeframe=timeframe).to_dict()
        return {
            "trades": res.get("total_trades", 0),
            "pnl":    round(res.get("total_pnl", 0), 4),
            "sharpe": (None if res.get("sharpe") is None
                       else round(res.get("sharpe", 0), 3)),
            "wr":     round(res.get("win_rate", 0), 1),
            "dd":     round(res.get("max_drawdown", 0), 2),
            "alpha":  round(res["alpha"], 4) if res.get("alpha") is not None else None,
            # OPT-01 : `beats_baseline` sait comparer le profit factor et
            # l'expectancy, mais ses branches restaient mortes faute de ces
            # deux clés côté baseline. Cette fonction sert AUSSI au holdout :
            # les alimenter ici les rend disponibles des deux côtés de la
            # comparaison d'un seul geste.
            "profit_factor": (None if res.get("profit_factor") is None
                              else round(res["profit_factor"], 3)),
            "expectancy":    (None if res.get("expectancy") is None
                              else round(res["expectancy"], 4)),
        }
    except Exception as e:
        logger.debug(f"[AutoOpt] baseline {strategy_name} KO : {e}")
        return {}


# ════════════════════════════════════════════════════════════════════════════
#  AutoOptimizer
# ════════════════════════════════════════════════════════════════════════════
