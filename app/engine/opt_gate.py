"""Gates d'auto-application d'un résultat d'optimisation (DETTE-04b).

Extraits de `_run_one_job` (355 lignes). Deux gates conjonctifs, plus la mesure
qui les alimente :

  - la mesure se fait sur le **holdout** quand il existe — la tranche de
    sélection a servi à classer N essais, le score du gagnant y est un maximum
    d'ordre N, pas une estimation ;
  - `gate_qualite` compare au baseline (`beats_baseline`, partagé avec la route
    d'apply manuel) ;
  - `gate_walk_forward` exige que les paramètres FIGÉS restent positifs sur une
    majorité de fenêtres OOS glissantes.

Le découpage précédent d'une fonction de cette taille avait déplacé la
comptabilité des frais et introduit `FIN-01`/`FIN-02` : les fonctions ci-dessous
n'ont aucun effet de bord hors les logs et l'unique `_update_job` de la
consistance walk-forward.
"""

import importlib
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Optional

import polars as pl

logger = logging.getLogger(__name__)


@dataclass
class MesureOOS:
    """Métriques sur lesquelles le gate se prononce, et leur provenance.

    ``source`` vaut ``holdout`` ou ``selection`` : un gate mesuré sur la
    tranche de sélection est plus permissif, et doit se voir dans le job.
    """

    source: str
    trades: int
    pnl: float
    wr: float
    sharpe: Optional[float]
    pf: Optional[float] = None
    expectancy: Optional[float] = None
    dd: Optional[float] = None
    brut: dict = field(default_factory=dict)


def mesurer_oos(result: dict, df_holdout: Optional[pl.DataFrame],
                mesurer_holdout) -> MesureOOS:
    """Métriques du candidat, sur le holdout si possible, sinon la sélection.

    ``mesurer_holdout`` est appelé au plus une fois — c'est ce qui fait du
    holdout une tranche « jamais vue ». Un holdout illisible retombe sur la
    sélection plutôt que de bloquer : le repli est journalisé, pas silencieux.
    """
    m = MesureOOS(
        source="selection",
        trades=result.get("best_oos_trades", 0),
        pnl=result.get("best_oos_pnl", 0),
        wr=result.get("best_oos_wr", 0),
        sharpe=result.get("best_oos_sharpe", 0),
        # OPT-01 : la tranche de sélection publie désormais pf et expectancy ;
        # le holdout les écrase s'il tourne.
        pf=result.get("best_oos_pf"),
        expectancy=result.get("best_oos_expectancy"),
    )
    if df_holdout is None or not result.get("best_params"):
        return m
    h = mesurer_holdout(df_holdout)
    if not h:
        return m
    return MesureOOS(
        source="holdout",
        trades=h.get("trades", 0), pnl=h.get("pnl", 0), wr=h.get("wr", 0),
        sharpe=h.get("sharpe", 0), pf=h.get("profit_factor"),
        expectancy=h.get("expectancy"), dd=h.get("dd"), brut=h,
    )


def gate_qualite(job_id: str, cfg: dict, result: dict, m: MesureOOS,
                 baseline: dict, n_trials_eff: int) -> bool:
    """Garde-fou partagé avec `POST /api/optimize/apply` (BT-04/BT-06)."""
    from app.engine.opt_scoring import beats_baseline, resolve_dd_max_abs

    opt_cfg = (cfg.get("optimizer") or {})
    ds_actif = bool(opt_cfg.get("deflated_sharpe_gate", False))
    # Le nombre d'essais RÉELLEMENT tirés, pas celui demandé : c'est lui qui
    # mesure le biais de sélection multiple que le Deflated Sharpe corrige.
    n_trials = int(result.get("n_trials") or n_trials_eff) if ds_actif else 1

    ok, raison = beats_baseline(
        m.trades, m.pnl, m.wr, m.sharpe, baseline,
        n_trials=n_trials,
        min_deflated_sharpe=(float(opt_cfg.get("deflated_sharpe_min", 0.5))
                             if ds_actif else None),
        oos_dd=(m.dd or result.get("best_oos_dd") or result.get("best_val_dd")),
        oos_pf=m.pf,
        oos_expectancy=m.expectancy,
        dd_max_abs=resolve_dd_max_abs(cfg),
    )
    if not ok:
        logger.info(f"[AutoOpt] {job_id} : gate d'apply refusé "
                    f"[{m.source}] — {raison}")
    return ok


def gate_walk_forward(job_id: str, cfg: dict, strategy_name: str, timeframe: str,
                      symbol: str, result: dict,
                      df_recherche: Optional[pl.DataFrame],
                      noter_consistance) -> bool:
    """BT-07 — les paramètres FIGÉS tiennent-ils sur des fenêtres glissantes ?

    Aucune re-optimisation par fold : un unique split IS/OOS ne suffit pas à
    conclure. Neutre (`True`) uniquement si le gate est explicitement désactivé
    — un walk-forward indisponible **bloque**, on ne relâche pas à l'aveugle.
    """
    from app.engine.backtest import WalkForwardAnalyzer
    from app.engine.engine import Engine

    opt_cfg = (cfg.get("optimizer") or {})
    if not bool(opt_cfg.get("wf_gate", True)):
        return True
    if df_recherche is None:
        logger.info(f"[AutoOpt] {job_id} : walk-forward non évaluable "
                    f"(pas de données) — auto-apply bloqué")
        return False

    min_cons = float(opt_cfg.get("wf_min_consistency", 60.0))
    try:
        cfg2 = {k: v for k, v in cfg.items()}
        sp = deepcopy(cfg.get("strategy_params") or {})
        frozen = dict(sp.get(strategy_name, {}))
        frozen.update(result["best_params"])
        sp[strategy_name] = frozen
        cfg2["strategy_params"] = sp
        cfg2["optimizer_results"] = {}
        mod = importlib.import_module(f"app.strategies.{strategy_name}")
        eng = Engine()
        eng.register(mod.Strategy(), silent=True)
        res_wf = WalkForwardAnalyzer(
            eng, cfg2, n_folds=int(opt_cfg.get("wf_folds", 5))
        ).run(df_recherche, symbol, timeframe=timeframe)

        if "error" in res_wf:
            logger.info(f"[AutoOpt] {job_id} : walk-forward non évaluable "
                        f"({res_wf['error']}) — auto-apply bloqué")
            return False
        if int(res_wf.get("n_folds_failed") or 0) > 0:
            logger.info(f"[AutoOpt] {job_id} : walk-forward partiel "
                        f"({res_wf.get('n_folds_failed')} fold(s) échoué(s)) "
                        f"— auto-apply bloqué")
            return False
        cons = float(res_wf.get("consistency", 0.0))
        noter_consistance(cons)
        if cons < min_cons:
            logger.info(f"[AutoOpt] {job_id} : gate walk-forward refusé — "
                        f"consistency {cons:.0f}% < {min_cons:.0f}%")
            return False
        return True
    except Exception as e:
        logger.warning(f"[AutoOpt] {job_id} : walk-forward KO ({e}) "
                       f"— auto-apply bloqué")
        return False


def gates_passes(job_id: str, cfg: dict, *, auto_apply: bool, result: dict,
                 df_holdout: Optional[Any], m: MesureOOS, baseline: dict,
                 n_trials_eff: int, strategy_name: str, timeframe: str,
                 symbol: str, df_recherche: Optional[pl.DataFrame],
                 noter_consistance) -> bool:
    """Conjonction des deux gates, court-circuitée.

    L'ordre compte : le walk-forward relance des backtests, il ne doit pas
    tourner quand la qualité a déjà tranché.
    """
    if auto_apply and df_holdout is None:
        logger.info(f"[AutoOpt] {job_id} : pas de holdout — auto-apply "
                    f"refusé (OPT-04), apply manuel requis")
    return bool(
        auto_apply and df_holdout is not None and result.get("best_params")
        and gate_qualite(job_id, cfg, result, m, baseline, n_trials_eff)
        and gate_walk_forward(job_id, cfg, strategy_name, timeframe, symbol,
                              result, df_recherche, noter_consistance)
    )
