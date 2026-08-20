"""S-09 / M-08 / P-03 — restes d'audit hors X-01/X-02/X-06."""
from datetime import datetime

from app.core.sanitize import safe_float
from app.ml.model_registry import git_commit, recipe_hash


def test_recipe_hash_does_not_collide_datetime_and_iso_string():
    dt = datetime(2024, 1, 2, 3, 4, 5)
    a = recipe_hash({"as_of": dt})
    b = recipe_hash({"as_of": str(dt)})
    assert a != b


def test_git_commit_prefers_env(monkeypatch):
    import app.ml.model_registry as reg
    monkeypatch.setattr(reg, "_GIT_COMMIT_CACHE", "")
    monkeypatch.setenv("GIT_COMMIT", "abcdef1234567890")
    try:
        assert git_commit() == "abcdef123456"
    finally:
        monkeypatch.setattr(reg, "_GIT_COMMIT_CACHE", "")


def test_safe_float_is_the_shared_sf():
    assert safe_float(float("nan"), 0.0) == 0.0
    assert safe_float(1.5) == 1.5


def test_find_strategy_is_o1(monkeypatch):
    from app.engine.backtest import Backtester
    from app.engine.engine import Engine

    class S:
        name = "alpha"

    eng = Engine()
    eng.strategies = [S()]
    bt = Backtester(eng, {"trading": {}, "backtest": {}})
    assert bt._find_strategy("alpha") is eng.strategies[0]
    assert bt._find_strategy("missing") is None
    # second call uses the cache
    assert bt._strat_by_name["alpha"] is eng.strategies[0]
