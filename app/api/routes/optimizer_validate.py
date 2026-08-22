"""Validation d'un résultat d'optimisation — Monte-Carlo et régimes.

Extrait d'`optimizer.py`, qui repassait au-dessus de 700 lignes (DETTE-04).
Route montée par le même routeur : `app/api/routes/optimizer.py` l'importe.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api import state
from app.api.helpers import verify_api_key
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL

router = APIRouter()
logger = logging.getLogger(__name__)


def _with_pnl_pct(by_strategy: dict, capital) -> dict:
    """Ajoute le gain % de la stratégie (PnL / capital) à chaque régime."""
    cap = float(capital or 0)
    out = {}
    for regime, stats in (by_strategy or {}).items():
        row = dict(stats)
        pnl = float(row.get("pnl") or 0)
        row["pnl_pct"] = round(pnl / cap * 100, 2) if cap else 0.0
        out[regime] = row
    return out


@router.post("/api/optimize/validate", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("5/minute")
def optimizer_validate(
    request: Request,
    job_id: str,
    method: str = "monte_carlo",
):
    """Valide les best_params d'un job terminé via Monte-Carlo ou Regime Stress Test.

    Prend les ``best_params`` d'un job terminé, lance l'analyse de robustesse,
    et retourne le dict résultat. Évite à l'utilisateur de relancer un backtest
    manuel pour estimer la probabilité de ruine ou la performance par régime.

    Parameters
    ----------
    job_id : str
        ID du job terminé dont on veut valider les best_params.
    method : str
        ``monte_carlo`` (défaut) ou ``regime``.
    """
    from app.core.candle_store import get_store
    from app.core.exchange import create_exchange
    from app.engine.auto_optimizer import get_job

    if state.cfg is None:
        raise HTTPException(503, "Config non chargée")
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' introuvable")
    if job.get("status") != "done":
        raise HTTPException(400, "Job non terminé")
    result = job.get("result", {})
    best_params = result.get("best_params", {})
    if not best_params:
        raise HTTPException(400, "Aucun meilleur paramètre")
    strat = job.get("strategy", "")
    tf = job.get("timeframe", "")
    symbol = job.get("symbol") or DEFAULT_CONFIG_SYMBOL

    try:
        # Récupérer les données
        exchange = create_exchange(state.cfg)
        from app.engine.optimizer_search import auto_fetch_limit
        fetch_limit = auto_fetch_limit(tf, [strat])
        df = get_store().fetch(exchange, symbol, tf, total=fetch_limit, prefer_cache=True)
        if df is None or len(df) == 0:
            raise HTTPException(400, f"Aucune donnée pour {symbol}/{tf}")

        # Appliquer les best_params et lancer le backtest
        import importlib

        from app.core.config import load_config as _reload_cfg
        from app.engine.backtest import Backtester
        from app.engine.engine import Engine
        cfg = _reload_cfg("config.yaml")
        # Override avec best_params
        sp = dict(cfg.get("strategy_params", {}))
        sp[strat] = {**(sp.get(strat, {})), **best_params}
        cfg["strategy_params"] = sp
        cfg["optimizer_results"] = {}  # pas d'overlay

        mod = importlib.import_module(f"app.strategies.{strat}")
        eng = Engine()
        eng.register(mod.Strategy(), silent=True)
        bt = Backtester(eng, cfg)
        res = bt.run(df, symbol, timeframe=tf)
        trades = [t for t in res.trades if t.get("status", "").startswith("closed")]

        if not trades:
            raise HTTPException(400, "Le backtest avec les best_params n'a produit aucun trade")

        if method == "monte_carlo":
            from app.core.risk.gate import _default_venue_capital
            from app.engine.monte_carlo import MonteCarlo
            mc = MonteCarlo(n_runs=cfg.get("backtest", {}).get("monte_carlo_runs", 200))
            mc_result = mc.run(trades, _default_venue_capital(cfg))
            return {"method": "monte_carlo", "result": mc_result, "n_trades": len(trades)}

        elif method == "regime":
            # `regime_summary` décrit ce qu'a fait le MARCHÉ sur chaque segment
            # (rendement, Sharpe et drawdown du PRIX). Pris seul, il ne valide
            # aucun paramétrage : deux stratégies opposées produiraient le même
            # résumé, et le backtest lancé juste au-dessus n'aurait servi qu'à
            # compter les trades. C'est ce que `by_strategy` corrige — il
            # rattache chaque trade au régime dans lequel il a été ouvert.
            from app.engine.regime_stress_test import (
                regime_summary,
                strategy_performance_by_regime,
                stress_test_by_regime,
            )
            segments = stress_test_by_regime(df, regime_type='trend', min_segment_bars=50)
            return {
                "method": "regime",
                "segments": [
                    {"regime": s.regime, "start_time": s.start_time, "end_time": s.end_time,
                     "n_bars": s.n_bars, "metrics": s.metrics}
                    for s in segments
                ],
                # Contexte : comportement du sous-jacent par régime.
                "market": regime_summary(segments),
                # Réponse à la question posée : tenue de la STRATÉGIE par régime.
                "by_strategy": _with_pnl_pct(
                    strategy_performance_by_regime(segments, res.trades),
                    res.initial_capital,
                ),
                "n_trades": len(trades),
            }
        else:
            raise HTTPException(400, f"Méthode inconnue: {method} (attendu: monte_carlo ou regime)")
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} optimize/validate : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


# ── P1-11 : Purge automatique des jobs backend ───────────────────────────────
