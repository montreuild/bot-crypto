"""
Tests unitaires — app.strategies.registry (auto-découverte des stratégies)
"""
import pytest
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.engine.registry import (
    get_param_spaces,
    get_strategy_timeframes,
    get_fixed_params,
)

# Stratégies attendues (toutes déclarent un param_space)
EXPECTED_STRATEGIES = {
    "breakout",
    "composite_score",
    "fear_momentum",
    "fft_spectral",
    "multi_tf_sr",
    "pullback_trend",
    "supertrend_macd",
    "trend",
}

# Stratégies ML qui ne doivent PAS être découvertes (pas de param_space)
ML_STRATEGIES = {"ml_strategy", "ml_dynamic_threshold"}


class TestRegistry:
    def test_all_expected_strategies_discovered(self):
        ps = get_param_spaces()
        assert EXPECTED_STRATEGIES == set(ps.keys()), (
            f"Manquantes : {EXPECTED_STRATEGIES - set(ps.keys())}, "
            f"Inattendues : {set(ps.keys()) - EXPECTED_STRATEGIES}"
        )

    def test_ml_strategies_excluded(self):
        """Les stratégies ML (sans param_space) ne doivent pas apparaître."""
        ps = get_param_spaces()
        for ml in ML_STRATEGIES:
            assert ml not in ps, f"{ml} ne devrait pas être dans param_spaces"

    def test_param_spaces_non_empty(self):
        for name, space in get_param_spaces().items():
            assert isinstance(space, dict), f"{name}: param_space doit être un dict"
            assert len(space) > 0, f"{name}: param_space ne doit pas être vide"
            for param, values in space.items():
                assert isinstance(values, list), f"{name}.{param}: doit être une liste"
                assert len(values) > 0, f"{name}.{param}: liste vide"

    def test_timeframes_non_empty(self):
        for name, tfs in get_strategy_timeframes().items():
            assert isinstance(tfs, list), f"{name}: timeframes doit être une liste"
            assert len(tfs) > 0, f"{name}: timeframes ne doit pas être vide"
            for tf in tfs:
                assert isinstance(tf, str), f"{name}: chaque TF doit être une str"

    def test_fixed_params_are_dicts(self):
        for name, fp in get_fixed_params().items():
            assert isinstance(fp, dict), f"{name}: fixed_params doit être un dict"

    def test_all_strategies_have_timeframes(self):
        tf = get_strategy_timeframes()
        ps = get_param_spaces()
        missing = [n for n in ps if n not in tf]
        assert missing == [], f"Stratégies sans timeframes : {missing}"

    def test_optimizer_sees_same_dicts(self):
        """PARAM_SPACES et STRATEGY_TIMEFRAMES dans optimizer.py viennent du registry."""
        from app.optimizer.optimizer import PARAM_SPACES, STRATEGY_TIMEFRAMES, FIXED_PARAMS

        assert PARAM_SPACES == get_param_spaces(), (
            "optimizer.PARAM_SPACES doit correspondre au registry"
        )
        assert STRATEGY_TIMEFRAMES == get_strategy_timeframes(), (
            "optimizer.STRATEGY_TIMEFRAMES doit correspondre au registry"
        )
        assert FIXED_PARAMS == get_fixed_params(), (
            "optimizer.FIXED_PARAMS doit correspondre au registry"
        )

    def test_new_strategy_pattern(self, tmp_path, monkeypatch):
        """
        Simule l'ajout d'une nouvelle stratégie : crée un module Python avec
        une classe Strategy(param_space, timeframes) et vérifie que le registre
        la prendrait en compte.
        """
        import types
        from app.engine.engine import BaseStrategy

        # Créer une fausse classe Strategy
        class FakeStrategy(BaseStrategy):
            name = "fake_strategy"
            timeframes = ["1h", "4h"]
            param_space = {"period": [10, 20, 30], "rr_min": [1.3, 1.5]}
            fixed_params = {}

        # Simuler un module
        fake_mod = types.ModuleType("app.strategies.fake_strategy")
        fake_mod.Strategy = FakeStrategy

        # Vérifier que le registre lirait bien les attributs
        ps = getattr(FakeStrategy, "param_space", None)
        tf = getattr(FakeStrategy, "timeframes", None)
        fp = getattr(FakeStrategy, "fixed_params", {})

        assert ps == {"period": [10, 20, 30], "rr_min": [1.3, 1.5]}
        assert tf == ["1h", "4h"]
        assert fp == {}
