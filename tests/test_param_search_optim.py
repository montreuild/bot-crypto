"""param_search_optim : nouveau mode combinant gel des paramètres à faible
impact + optimisation étagée + successive halving (fenêtre réduite -> fenêtre
complète). Tests de routage/logique avec ``_eval`` stubbé (rapide, pas de
vrai Backtester) — la validité du calcul de backtest lui-même est couverte
ailleurs ; ici on vérifie le CONTRÔLE : gel du bon paramètre, isolation de
self.results entre phases, comptes de phase cohérents."""
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


def _make_opt(param_space):
    df = _tiny_df()
    return StrategyOptimizer(
        "opus_omnibus_v11", {"trading": {}}, df[:150], df[50:],
        param_space=param_space, symbol="BTC/USDC", timeframe="1h",
    )


def test_no_param_space_returns_error():
    # param_space={} au constructeur retombe sur PARAM_SPACES[strategy_name]
    # (``param_space or PARAM_SPACES.get(...)``) — il faut l'écraser après coup.
    opt = _make_opt({"x": [1]})
    opt.param_space = {}
    assert opt.param_search_optim(n_trials=10) == {
        "error": "Aucun espace de params pour opus_omnibus_v11"}


def test_freezes_low_impact_param_keeps_high_impact_one(monkeypatch):
    """``important`` détermine le score (impact fort) ; ``noise`` n'a aucun
    effet (impact nul) -> ``noise`` doit être gelé, ``important`` conservé."""
    opt = _make_opt({"important": [0, 1, 2], "noise": ["a", "b", "c"]})

    def _fake_eval(params):
        return _fake_eval_result(params, score=float(params["important"]))

    monkeypatch.setattr(opt, "_eval", _fake_eval)
    result = opt.param_search_optim(n_trials=30, n_jobs=1, freeze_fraction=0.5)

    assert "noise" in result["frozen_params"]
    assert "important" not in result["frozen_params"]
    assert result["kept_params"] == ["important"]
    assert result["best_params"]["important"] == 2  # le plus haut score


def test_phase_counts_and_results_isolation(monkeypatch):
    opt = _make_opt({"a": [0, 1, 2], "b": [0, 1, 2], "c": [0, 1, 2]})

    def _fake_eval(params):
        return _fake_eval_result(params, score=float(params["a"] + params["b"]))

    monkeypatch.setattr(opt, "_eval", _fake_eval)
    result = opt.param_search_optim(n_trials=20, n_jobs=1, freeze_fraction=0.34,
                                    keep_fraction=0.4)

    counts = result["phase_counts"]
    assert counts["screen"] >= 12
    assert counts["round_a"] >= 8
    # successive halving : le nombre de survivants finaux est une fraction
    # du round A, jamais plus.
    assert 1 <= counts["final"] <= counts["round_a"]
    # self.results, à la fin, ne contient QUE la phase finale (fenêtre
    # complète) — jamais le dépistage/étage réduit (scores non comparables).
    assert len(opt.results) == counts["final"]


def test_n_jobs_gt_1_routes_through_run_parallel(monkeypatch):
    """n_jobs>1 doit emprunter _run_parallel (comme random_search) à chaque
    phase plutôt que d'appeler _eval en boucle inline."""
    opt = _make_opt({"a": [0, 1, 2], "b": [0, 1, 2]})
    calls = {"eval": 0, "run_parallel": 0}

    def _fake_run_parallel(n, n_total, trial_offset=0, sampler=None, n_jobs=1):
        calls["run_parallel"] += 1
        sampler = sampler or (lambda: opt._with_hp(
            {k: v[0] for k, v in opt.param_space.items()}))
        for _ in range(n):
            p = sampler()
            opt.results.append(_fake_eval_result(p, score=float(p.get("a", 0))))

    monkeypatch.setattr(opt, "_eval", lambda params: (
        calls.__setitem__("eval", calls["eval"] + 1), _fake_eval_result(params, 0.0))[1])
    monkeypatch.setattr(opt, "_run_parallel", _fake_run_parallel)

    result = opt.param_search_optim(n_trials=20, n_jobs=4)

    assert calls["run_parallel"] >= 2   # au moins dépistage + étage réduit (+ final si kept_keys)
    assert calls["eval"] == 0
    assert "error" not in result


def test_result_shape_matches_best_result_contract(monkeypatch):
    """Le retour doit rester consommable par les mêmes appelants que
    random_search/bayesian_search (best_params, best_oos_score, top5…)."""
    opt = _make_opt({"a": [0, 1, 2]})
    monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(
        params, score=float(params["a"])))

    result = opt.param_search_optim(n_trials=15, n_jobs=1)

    for key in ("strategy", "best_params", "best_is_score", "best_oos_score",
               "top5", "frozen_params", "kept_params", "phase_counts", "elapsed_s"):
        assert key in result, f"clé manquante: {key}"
