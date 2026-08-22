"""Analyse de stabilité temporelle (souvent appelée walk-forward dans l'UI).

B-04 : ce n'est PAS un walk-forward au sens de la littérature — aucune
réoptimisation par fold. Chaque fold rejoue le MÊME paramétrage figé.
Le résultat mesure la stabilité temporelle, pas la procédure d'optimisation.

Ré-exportée depuis ``app.engine.backtest`` pour compatibilité ascendante.
"""
import importlib as _imp
import logging

import numpy as np
import polars as pl

from app.core.is_oos import WARMUP_BARS_DEFAULT, resolve_ml_mode
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL
from app.core.sanitize import safe_float as _sf
from app.engine.engine import Engine

logger = logging.getLogger(__name__)


# ── Walk-Forward ──
class WalkForwardAnalyzer:
    def __init__(self, engine: Engine, cfg: dict, n_folds: int = 5,
                 ml_mode: str | None = None):
        """``ml_mode`` (ML-02, cf. ``Backtester``) : transmis tel quel à
        chaque ``Backtester`` de fold (IS et OOS). ``None`` (défaut) résout
        via ``resolve_ml_mode`` — même source que l'optimiseur, dont ce
        walk-forward gate la recherche.

        En ``frozen``, chaque fold résout le modèle via le registre avec
        ``as_of`` = début de la fenêtre DE CE FOLD, ce qui empêche par
        construction de charger un modèle entraîné sur des données qu'il
        évalue (fuite possible avec le chemin plat non daté)."""
        self.engine  = engine
        self.cfg     = cfg
        self.n_folds = n_folds
        self.ml_mode = resolve_ml_mode(cfg, ml_mode)

    def run(self, df: pl.DataFrame, symbol: str = DEFAULT_CONFIG_SYMBOL,
            timeframe: str | None = None) -> dict:
        # Import lazy : ``Backtester`` vit dans ``app.engine.backtest`` qui
        # ré-exporte ``WalkForwardAnalyzer`` (cycle sinon).
        from app.engine.backtest import Backtester

        n      = len(df)
        fold_n = n // (self.n_folds + 1)
        WARMUP = WARMUP_BARS_DEFAULT
        MIN_IS = WARMUP + 50
        MIN_OOS = 40
        if fold_n < MIN_OOS:
            return {"error": f"Données insuffisantes pour Walk-Forward ({n} barres · min {MIN_OOS * (self.n_folds+1)})"}
        if fold_n < MIN_IS:
            return {
                "error": (
                    f"IS trop court pour les stratégies EMA ({fold_n} barres/fold · "
                    f"min {MIN_IS} requis). Augmentez les bougies à ≥{MIN_IS * (self.n_folds+1)} "
                    f"ou réduisez les folds."
                ),
                "n_bars": n,
                "fold_n": fold_n,
                "min_required": MIN_IS * (self.n_folds + 1),
            }

        in_sample_results  = []
        out_sample_results = []
        n_failed = 0
        fold_errors: list[str] = []
        from app.core.is_oos import resolve_realistic_risk
        _rr = resolve_realistic_risk(self.cfg)

        for k in range(self.n_folds):
            is_end  = fold_n * (k + 1)
            oos_end = min(fold_n * (k + 2), n)
            df_is   = df[:is_end]
            # BT-01 : le warmup du Backtester est pris DANS le fold si on
            # passe df[is_end:oos_end]. On préfixe l'amont pour ne trader
            # qu'à partir de is_end.
            oos_start = max(0, is_end - WARMUP)
            df_oos  = df[oos_start:oos_end]
            if (oos_end - is_end) < 30:
                n_failed += 1
                fold_errors.append(f"fold {k}: OOS trop court ({oos_end - is_end})")
                continue
            try:
                fresh_strats = []
                for s in self.engine.strategies:
                    mod = _imp.import_module(f"app.strategies.{s.name}")
                    fresh_strats.append(mod.Strategy())
                eng_is  = Engine()
                [eng_is.register(s, silent=True)  for s in fresh_strats]
                fresh_strats_oos = []
                for s in self.engine.strategies:
                    mod = _imp.import_module(f"app.strategies.{s.name}")
                    fresh_strats_oos.append(mod.Strategy())
                eng_oos = Engine()
                [eng_oos.register(s, silent=True) for s in fresh_strats_oos]

                bt_is  = Backtester(eng_is,  self.cfg, ml_mode=self.ml_mode,
                                    realistic_risk=_rr)
                bt_oos = Backtester(eng_oos, self.cfg, ml_mode=self.ml_mode,
                                    realistic_risk=_rr)
                r_is   = bt_is.run(df_is,  symbol, timeframe=timeframe).to_dict()
                r_oos  = bt_oos.run(df_oos, symbol, timeframe=timeframe).to_dict()
                in_sample_results.append(r_is)
                out_sample_results.append(r_oos)
            except Exception as e:
                n_failed += 1
                fold_errors.append(f"fold {k}: {e}")
                logger.error(f"[WF] Fold {k} : {e}", exc_info=True)

        if not out_sample_results:
            return {
                "error": "Aucun fold OOS valide",
                "n_folds_requested": self.n_folds,
                "n_folds_failed": n_failed,
                "erreurs": fold_errors,
            }

        oos_pnl    = [r["total_pnl"]  for r in out_sample_results]
        oos_sharpe = [r["sharpe"] for r in out_sample_results if r.get("sharpe") is not None]
        oos_wr     = [r["win_rate"]   for r in out_sample_results]

        avg_pnl = round(_sf(float(np.mean(oos_pnl)), 0.0), 4)

        def _fold_summary(r: dict) -> dict:
            return {
                "total_pnl":     r.get("total_pnl"),
                "win_rate":      r.get("win_rate"),
                "sharpe":        r.get("sharpe"),
                "total_trades":  r.get("total_trades"),
                "max_drawdown":  r.get("max_drawdown"),
                "profit_factor": r.get("profit_factor"),
                "realistic_risk": r.get("realistic_risk"),
            }

        return {
            "n_folds":        len(out_sample_results),
            "n_folds_requested": self.n_folds,
            "n_folds_failed": n_failed,
            "erreurs":        fold_errors,
            "kind":           "stability",
            "reoptimizes":    False,
            "is_nested":      True,
            "realistic_risk": _rr,
            "avg_oos_pnl":    avg_pnl,
            "avg_fold_pnl":   avg_pnl,
            "avg_oos_sharpe": (round(_sf(float(np.mean(oos_sharpe)), 0.0), 3)
                               if oos_sharpe else None),
            "avg_oos_wr":     round(_sf(float(np.mean(oos_wr)),     0.0), 2),
            "consistency":    round(sum(1 for p in oos_pnl if p > 0) / len(oos_pnl) * 100, 1),
            "in_sample":      [_fold_summary(r) for r in in_sample_results],
            "out_of_sample":  [_fold_summary(r) for r in out_sample_results],
        }
