"""Param Search Optim : OPTION (activée par défaut) appliquée en amont de
random_search/bayesian_search/grid_search — dépistage sur fenêtre réduite +
gel des paramètres à faible impact — PAS un 4e mode de recherche. Tests de
contrôle avec ``_eval`` stubbé (rapide, pas de vrai Backtester)."""
import polars as pl

from app.engine.optimizer import StrategyOptimizer


def _tiny_df(n=200):
    from datetime import datetime, timedelta
    t0 = datetime(2024, 1, 1)
    return pl.DataFrame({
        "time": [t0 + timedelta(hours=i) for i in range(n)],
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.0] * n, "volume": [10.0] * n,
    })


def _fake_eval_result(params: dict, score: float) -> dict:
    return {
        "params": dict(params), "is_score": score, "oos_score": score, "overfit": 1.0,
        "is_pnl": 0.0, "oos_pnl": 0.0, "is_sharpe": 0.0, "oos_sharpe": 0.0,
        "is_trades": 1, "oos_trades": 1, "is_wr": 50.0, "oos_wr": 50.0,
        "oos_dd": 0.0, "oos_alpha": 0.0, "final_score": score,
    }


def _make_opt(param_space, n=200):
    df = _tiny_df(n)
    return StrategyOptimizer(
        "opus_omnibus_v11", {"trading": {}}, df[:int(n * 0.7)], df[int(n * 0.7):],
        param_space=param_space, symbol="BTC/USDC", timeframe="1h",
    )


# Espace à 6 paramètres, cardinalité 3**6=729, choisi pour dépasser le seuil
# de déclenchement (_should_reduce_space : card > n_trials*200) avec un
# n_trials modeste dans les tests.
def _wide_space():
    return {f"p{i}": [0, 1, 2] for i in range(6)}


class TestShouldReduceSpace:
    def test_small_space_never_reduced(self):
        opt = _make_opt({"a": [0, 1, 2], "b": [0, 1, 2]})  # 2 params < 6
        assert opt._should_reduce_space(n_trials=1) is False

    def test_well_covered_wide_space_not_reduced(self):
        opt = _make_opt(_wide_space())  # 729 combos
        assert opt._should_reduce_space(n_trials=1000) is False  # 1000*200 >> 729

    def test_poorly_covered_wide_space_is_reduced(self):
        opt = _make_opt(_wide_space())
        assert opt._should_reduce_space(n_trials=1) is True  # 729 > 1*200


class TestReduceParamSpace:
    def test_freezes_low_impact_keeps_high_impact(self, monkeypatch):
        opt = _make_opt({"important": [0, 1, 2], **{f"noise{i}": [0, 1, 2] for i in range(5)}})

        def _fake_eval(params):
            return _fake_eval_result(params, score=float(params["important"]))

        monkeypatch.setattr(opt, "_eval", _fake_eval)
        diag = opt.reduce_param_space(n_jobs=1, freeze_fraction=0.5)

        assert "important" not in diag["frozen_params"]
        assert "important" in diag["kept_params"]
        # Les params "noise" (impact nul) sont les meilleurs candidats au gel.
        assert set(diag["frozen_params"]).issubset({f"noise{i}" for i in range(5)})
        # self.param_space est réduit EN PLACE : chaque clé gelée -> 1 seule valeur.
        for k in diag["frozen_params"]:
            assert len(opt.param_space[k]) == 1
        assert opt.param_space["important"] == [0, 1, 2]  # inchangé

    def test_restore_param_space_reverts_mutation(self, monkeypatch):
        opt = _make_opt(_wide_space())
        original = {k: list(v) for k, v in opt.param_space.items()}
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(params, 0.0))

        opt.reduce_param_space(n_jobs=1)
        assert any(len(v) == 1 for v in opt.param_space.values())  # au moins 1 gelé

        opt._restore_param_space()
        assert opt.param_space == original

    def test_never_freezes_every_param(self, monkeypatch):
        """Un espace de recherche totalement gelé (0 dimension restante)
        rendrait grid_search/random_search inutiles — jamais permis."""
        opt = _make_opt(_wide_space())
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(params, 0.0))
        diag = opt.reduce_param_space(n_jobs=1, freeze_fraction=1.0)
        assert len(diag["kept_params"]) >= 1


class TestRandomSearchIntegration:
    def test_no_param_space_returns_error(self):
        opt = _make_opt({"x": [1]})
        opt.param_space = {}
        assert opt.random_search(n_trials=5) == {
            "error": "Aucun espace de params pour opus_omnibus_v11"}

    def test_small_space_skips_reduction(self, monkeypatch):
        """Espace à 2 paramètres : jamais réduit, param_search_optim=True
        (défaut) ne doit rien changer au comportement historique."""
        opt = _make_opt({"a": [0, 1, 2], "b": [0, 1, 2]})
        calls = {"reduce": 0}
        monkeypatch.setattr(opt, "reduce_param_space",
                            lambda **kw: calls.__setitem__("reduce", calls["reduce"] + 1))
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(params, 1.0))

        result = opt.random_search(n_trials=5, n_jobs=1)
        assert calls["reduce"] == 0
        assert "param_search_optim" not in result

    def test_wide_space_triggers_reduction_and_restores_after(self, monkeypatch):
        opt = _make_opt(_wide_space())
        original = {k: list(v) for k, v in opt.param_space.items()}
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(
            params, score=float(params["p0"])))

        result = opt.random_search(n_trials=1, n_jobs=1)  # 729 > 1*200 -> réduit

        assert "param_search_optim" in result
        assert len(result["param_search_optim"]["frozen_params"]) >= 1
        # self.param_space restauré après coup (n'affecte pas un run suivant).
        assert opt.param_space == original

    def test_disabling_the_toggle_matches_legacy_behaviour(self, monkeypatch):
        opt = _make_opt(_wide_space())
        calls = {"reduce": 0}
        monkeypatch.setattr(opt, "reduce_param_space",
                            lambda **kw: calls.__setitem__("reduce", calls["reduce"] + 1))
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(params, 1.0))

        result = opt.random_search(n_trials=1, n_jobs=1, param_search_optim=False)
        assert calls["reduce"] == 0
        assert "param_search_optim" not in result


class TestGridSearchIntegration:
    def test_small_grid_not_reduced(self, monkeypatch):
        opt = _make_opt({"a": [0, 1, 2], "b": [0, 1, 2]})
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(params, 1.0))
        result = opt.grid_search()
        assert "param_search_optim" not in result
        assert len(opt.results) == 9  # 3*3, grille intacte

    def test_huge_grid_is_reduced_before_exhaustive_run(self, monkeypatch):
        # 6 params à 3 choix = 729 > seuil (5000) ? non -> forcer un espace
        # plus large pour dépasser _GRID_REDUCE_THRESHOLD.
        space = {f"p{i}": [0, 1, 2, 3, 4] for i in range(6)}  # 5**6 = 15625
        opt = _make_opt(space)
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(
            params, score=float(params["p0"])))
        result = opt.grid_search(n_jobs=1)
        assert "param_search_optim" in result
        # La grille finale (post-gel) doit être bien plus petite que 15625.
        assert len(opt.results) < 15625
