"""BT-04 / BT-06 — garde-fou unique d'application d'un paramétrage optimisé.

``opt_scoring.beats_baseline`` est partagé par l'auto-apply ET la route
manuelle /api/optimize/apply : échantillon OOS ≥ MIN_SIGNIFICANT_TRADES,
PnL OOS positif et meilleur que le baseline, amélioration WR ou Sharpe.
"""
from app.core.stats_thresholds import MIN_SIGNIFICANT_TRADES
from app.engine.opt_scoring import beats_baseline

BASE = {"pnl": 50.0, "wr": 40.0, "sharpe": 1.0}


def test_ok_when_all_criteria_met():
    ok, reason = beats_baseline(12, 80.0, 45.0, 1.2, BASE)
    assert ok and reason == "ok"


def test_rejects_small_sample():
    ok, reason = beats_baseline(MIN_SIGNIFICANT_TRADES - 1, 80.0, 45.0, 1.2, BASE)
    assert not ok and "insuffisant" in reason


def test_rejects_negative_pnl():
    ok, reason = beats_baseline(12, -5.0, 45.0, 1.2, BASE)
    assert not ok and "non positif" in reason


def test_rejects_worse_than_baseline():
    ok, reason = beats_baseline(12, 40.0, 45.0, 1.2, BASE)
    assert not ok and "baseline" in reason


def test_rejects_no_quality_improvement():
    ok, reason = beats_baseline(12, 80.0, 39.0, 0.9, BASE)
    assert not ok and "qualité" in reason


def test_unmeasurable_sharpe_does_not_beat_baseline():
    """F-02 : Sharpe None n'est pas une amélioration de qualité."""
    ok, reason = beats_baseline(12, 80.0, 39.0, None, BASE)
    assert not ok and "qualité" in reason


def test_live_open_applique_le_mode_de_sortie():
    """N-04 : le live appelle apply_exit_mode, comme le backtest."""
    import inspect
    from app.live.position_open_mixin import PositionOpenMixin
    src = inspect.getsource(PositionOpenMixin._try_open_from_signal)
    assert "apply_exit_mode" in src
    assert "exit_mode" in src


def test_route_guards_with_beats_baseline():
    """Garde-fou statique : la route apply appelle bien beats_baseline et
    expose force=... (BT-04)."""
    import inspect

    from app.api.routes import optimizer as opt_route
    src = inspect.getsource(opt_route.optimizer_apply)
    assert "beats_baseline" in src and "force" in src and "409" in src


def test_auto_apply_has_walk_forward_gate():
    """BT-07 : l'auto-apply passe par un gate walk-forward (params figés,
    consistency minimale, désactivable via optimizer.wf_gate)."""
    import inspect

    from app.engine import auto_optimizer
    src = inspect.getsource(auto_optimizer.AutoOptimizer._run_one_job)
    assert "_wf_consistent" in src
    assert "WalkForwardAnalyzer" in src
    from app.engine.walk_forward import WalkForwardAnalyzer
    assert "reoptimizes" in inspect.getsource(WalkForwardAnalyzer.run)
    assert "stability" in inspect.getsource(WalkForwardAnalyzer.run)
    assert "wf_min_consistency" in src
    assert "wf_gate" in src
    # Le gate s'applique bien à la décision d'apply
    assert "_beats_baseline() and _wf_consistent()" in src
    # N-03 / B-03 : le WF tourne sur la recherche, avec le TF du job.
    assert "df_recherche" in src
    assert "timeframe=timeframe" in src
