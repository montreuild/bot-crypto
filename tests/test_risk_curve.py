"""BT-09 / FIN-11 — courbe de dé-risquage en drawdown partagée backtest/live."""
from app.core.risk.curve import risk_multiplier, risk_multiplier_steps


def test_multiplier_levels():
    assert risk_multiplier(0.0) == 1.0
    assert risk_multiplier(0.05) == 1.0
    assert abs(risk_multiplier(0.10) - 0.75) < 1e-12
    assert risk_multiplier(0.15) == 0.5
    assert risk_multiplier(0.50) == 0.5
    # Continu : un bp autour de 10 % ne saute plus de 0,25.
    assert abs(risk_multiplier(0.1001) - risk_multiplier(0.10)) < 0.01


def test_fin11_ramp_vs_steps_max_jump():
    """Mesure FIN-11 : l'escalier saute de 0,25 au seuil ; la rampe non."""
    xs = [i / 10_000 for i in range(0, 2001)]  # 0 → 20 % par 1 bp
    def max_jump(fn) -> float:
        prev = fn(xs[0])
        worst = 0.0
        for x in xs[1:]:
            cur = fn(x)
            worst = max(worst, abs(cur - prev))
            prev = cur
        return worst

    jump_steps = max_jump(risk_multiplier_steps)
    jump_ramp = max_jump(risk_multiplier)
    assert jump_steps == 0.25
    assert jump_ramp < 0.01
    # Politique conservée aux bornes.
    assert risk_multiplier(0.05) == risk_multiplier_steps(0.05) == 1.0
    assert abs(risk_multiplier(0.10) - 0.75) < 1e-12
    assert risk_multiplier(0.15) == 0.5


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

    from app.core.risk.gate import RiskManager
    src = inspect.getsource(RiskManager.compute_risk)
    assert "risk_multiplier" in src
