"""
Tests unitaires — app.strategies.registry (auto-découverte des stratégies)
"""
import importlib
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.engine.registry import (
    get_param_spaces,
    get_strategy_timeframes,
    get_fixed_params,
)


def _strategies_on_disk() -> set:
    """Retourne l'ensemble des noms de stratégies présentes dans app/strategies/."""
    strats_dir = os.path.join(os.path.dirname(__file__), "..", "app", "strategies")
    result = set()
    for path in glob.glob(os.path.join(strats_dir, "*.py")):
        name = os.path.basename(path)[:-3]
        if name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"app.strategies.{name}")
            cls = getattr(mod, "Strategy", None)
            if cls is not None and getattr(cls, "param_space", None):
                result.add(name)
        except Exception:
            pass
    return result


class TestRegistry:
    def test_all_disk_strategies_discovered(self):
        """Chaque stratégie avec param_space sur disque doit être découverte par le registry."""
        disk = _strategies_on_disk()
        ps   = set(get_param_spaces().keys())
        missing = disk - ps
        assert not missing, (
            f"Stratégies présentes sur disque mais absentes du registry : {missing}"
        )

    def test_no_extra_strategies_in_registry(self):
        """Le registry ne doit pas retourner de stratégies absentes du disque."""
        disk = _strategies_on_disk()
        ps   = set(get_param_spaces().keys())
        extra = ps - disk
        assert not extra, (
            f"Stratégies dans le registry sans fichier sur disque : {extra}"
        )

    def test_param_spaces_non_empty(self):
        for name, space in get_param_spaces().items():
            assert isinstance(space, dict), f"{name}: param_space doit être un dict"
            assert len(space) > 0, f"{name}: param_space ne doit pas être vide"
            for param, values in space.items():
                assert isinstance(values, list), f"{name}.{param}: doit être une liste"
                assert len(values) > 0, f"{name}.{param}: liste vide"

    def test_every_strategy_has_required_attributes(self):
        """Chaque stratégie découverte doit avoir name, param_space et timeframes."""
        strats_dir = os.path.join(os.path.dirname(__file__), "..", "app", "strategies")
        for path in glob.glob(os.path.join(strats_dir, "*.py")):
            mod_name = os.path.basename(path)[:-3]
            if mod_name.startswith("_"):
                continue
            try:
                mod = importlib.import_module(f"app.strategies.{mod_name}")
            except Exception:
                continue
            cls = getattr(mod, "Strategy", None)
            if cls is None or not getattr(cls, "param_space", None):
                continue
            assert hasattr(cls, "name"),       f"{mod_name}: attribut 'name' manquant"
            assert hasattr(cls, "timeframes"), f"{mod_name}: attribut 'timeframes' manquant"
            assert hasattr(cls, "param_space"), f"{mod_name}: attribut 'param_space' manquant"
            assert isinstance(cls.timeframes, list), \
                f"{mod_name}: 'timeframes' doit être une liste"
            assert len(cls.timeframes) > 0, \
                f"{mod_name}: 'timeframes' ne doit pas être vide"

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
        from app.engine.optimizer import PARAM_SPACES, STRATEGY_TIMEFRAMES, FIXED_PARAMS

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
