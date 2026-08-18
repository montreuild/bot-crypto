"""BT-09 — courbe de dé-risquage en drawdown partagée backtest/live."""
from app.core.risk_curve import risk_multiplier


def test_multiplier_levels():
    assert risk_multiplier(0.0) == 1.0
    assert risk_multiplier(0.05) == 1.0          # seuil strict (>)
    assert risk_multiplier(0.051) == 0.75
    assert risk_multiplier(0.10) == 0.75
    assert risk_multiplier(0.101) == 0.5
    assert risk_multiplier(0.50) == 0.5


def test_backtester_tracks_peak_and_applies_curve():
    """Garde-fou statique : le Backtester suit peak_capital et applique
    _risk_multiplier au sizing (parité avec RiskManager.compute_risk)."""
    import inspect

    from app.engine import backtest, position_lifecycle
    src = inspect.getsource(backtest) + inspect.getsource(position_lifecycle)
    assert "peak_capital" in src
    assert "_risk_multiplier(dd)" in src


def test_risk_manager_delegates_to_shared_curve():
    import inspect

    from app.core.risk_gate import RiskManager
    src = inspect.getsource(RiskManager.compute_risk)
    assert "risk_multiplier" in src
