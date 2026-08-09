"""Appliqueur de résultats d'optimisation — garde-fou + persistance.

Extrait de ``optimizer.py`` (découpage ARCH-007). Responsabilités :
  - **Garde-fou qualité** : ``beats_baseline`` (cf. ``opt_scoring``) — échantillon
    OOS suffisant, PnL positif et meilleur que le baseline, amélioration WR ou
    Sharpe. Partagé entre l'auto-apply (``auto_optimizer``) et la route manuelle
    ``/api/optimize/apply`` (cf. BT-04/BT-06).
  - **Application effective** : délègue à ``opt_persistence.apply_best_params``
    qui écrit sous ``optimizer_results[tf][symbol]`` SANS toucher au bloc
    ``params:`` (configuration par défaut réglée à la main).

.. deprecated:: P2-1
    ``OptimizerResultApplier`` est du **code mort** : ni la route ``/api/optimize/apply``
    ni ``auto_optimizer._run_one_job`` n'utilisent cette classe — ils appellent
    directement ``opt_scoring.beats_baseline`` + ``opt_persistence.apply_best_params``.
    Créée pour factoriser mais jamais câblée. Conservée pour les tests
    ``test_optimizer_applier.py`` qui valident la logique de garde-fou indépendamment.
    À supprimer dans une future refactorisation une fois les tests migrés vers
    ``test_opt_scoring.py``.
"""
import logging
from typing import Optional, Tuple

from app.engine.opt_persistence import apply_best_params as _apply_best_params
from app.engine.opt_scoring import beats_baseline as _beats_baseline

logger = logging.getLogger(__name__)


class OptimizerResultApplier:
    """Applique un paramétrage optimisé en respectant le garde-fou qualité.

    Stateless : un même applier peut être réutilisé pour plusieurs stratégies /
    timeframes / symboles. Les flags ``force`` et ``auto_apply`` sont passés à
    chaque appel (pas au constructeur) pour éviter tout état caché entre appels.

    Le garde-fou ``beats_baseline`` est TOUJOURS évalué (même en ``force=True``)
    pour tracer la raison de l'éventuel contournement — seul l'effet (appliquer
    ou non) diffère, pas le diagnostic.
    """

    def beats_baseline(self, result: dict, baseline: dict,
                       min_trades: Optional[int] = None) -> Tuple[bool, str]:
        """Évalue le garde-fou qualité à partir d'un dict résultat d'optimisation.

        Extrait les champs OOS du résultat (``best_oos_trades``, ``best_oos_pnl``,
        ``best_oos_wr``, ``best_oos_sharpe``) et délègue à
        ``opt_scoring.beats_baseline``. Retourne ``(ok, raison)``.
        """
        return _beats_baseline(
            result.get("best_oos_trades", 0),
            result.get("best_oos_pnl", 0),
            result.get("best_oos_wr", 0),
            result.get("best_oos_sharpe", 0),
            baseline or {},
            **({"min_trades": min_trades} if min_trades is not None else {}),
        )

    def apply(self, strategy_name: str, params: dict,
              config_path: str, timeframe: Optional[str] = None,
              oos_score: float = 0.0,
              symbol: Optional[str] = None) -> bool:
        """Applique le paramétrage dans ``optimizer_results[tf][symbol]``.

        Délègue à ``opt_persistence.apply_best_params`` : écrit UNIQUEMENT dans
        ``optimizer_results`` (le bloc ``params:`` par défaut reste intact).
        Thread-safe via ``_config_write_lock``.
        """
        return _apply_best_params(strategy_name, params, config_path,
                                  timeframe=timeframe, oos_score=oos_score,
                                  symbol=symbol)

    def apply_if_beats_baseline(self, strategy_name: str, result: dict,
                                config_path: str, baseline: dict,
                                symbol: Optional[str] = None,
                                force: bool = False,
                                min_trades: Optional[int] = None
                                ) -> Tuple[bool, str, bool]:
        """Applique le paramétrage si le garde-fou qualité est passé.

        Retourne ``(applied, raison, gate_ok)`` :
          - ``applied`` : l'écriture dans ``optimizer_results`` a bien eu lieu ;
          - ``raison``  : motif de refus éventuel (vide si ok) ;
          - ``gate_ok`` : résultat du garde-fou (avant override éventuel).

        ``force=True`` outrepasse le refus (apply effectué, gate_ok reste False,
        ``raison`` documente le contournement assumé).
        """
        if not result.get("best_params"):
            return False, "aucun best_params", False

        tf = result.get("timeframe") or result.get("tf")
        gate_ok, raison = self.beats_baseline(result, baseline, min_trades=min_trades)
        if not gate_ok and not force:
            logger.info(
                "[OptimizerApply] %s@%s/%s : application refusée — %s",
                strategy_name, tf, symbol, raison,
            )
            return False, raison, False

        if not gate_ok and force:
            logger.warning(
                "[OptimizerApply] %s@%s/%s : application FORCÉE malgré garde-fou — %s",
                strategy_name, tf, symbol, raison,
            )

        applied = self.apply(
            strategy_name, result["best_params"], config_path,
            timeframe=tf, oos_score=result.get("best_oos_score", 0.0),
            symbol=symbol,
        )
        return applied, ("" if gate_ok else raison), gate_ok


# Instance singleton par défaut — commode pour les appelants qui ne veulent pas
# gérer de cycle de vie (stateless, sûr à partager entre threads).
default_applier = OptimizerResultApplier()
