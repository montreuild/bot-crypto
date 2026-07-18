"""n_jobs de StrategyOptimizer.random_search : la signature l'acceptait mais
la boucle restait toujours séquentielle (ProcessPoolExecutor jamais utilisé,
contrairement à bayesian_search qui a déjà cette infra). Ces tests vérifient
le ROUTAGE (n_jobs<=1 → boucle inchangée, n_jobs>1 → délégation à
_run_parallel, déjà utilisé et testé indirectement par
_bayesian_search_legacy/_optuna_parallel en production) sans payer le coût
d'un vrai ProcessPoolExecutor (spawn de subprocess, lent et non déterministe
en CI)."""
import polars as pl

from app.engine.optimizer import StrategyOptimizer


def _tiny_df(n=60):
    from datetime import datetime, timedelta
    t0 = datetime(2024, 1, 1)
    return pl.DataFrame({
        "time": [t0 + timedelta(hours=i) for i in range(n)],
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.0] * n, "volume": [10.0] * n,
    })


def _fake_eval_result(score: float) -> dict:
    return {
        "params": {"x": 1}, "is_score": score, "oos_score": score, "overfit": 1.0,
        "is_pnl": 0.0, "oos_pnl": 0.0, "is_sharpe": 0.0, "oos_sharpe": 0.0,
        "is_trades": 1, "oos_trades": 1, "is_wr": 50.0, "oos_wr": 50.0,
        "oos_dd": 0.0, "oos_alpha": 0.0,
    }


def _make_opt(param_space=None):
    df = _tiny_df()
    return StrategyOptimizer(
        "opus_omnibus_v11", {"trading": {}}, df[:40], df[40:],
        param_space=param_space if param_space is not None else {"p": [1, 2, 3]},
        symbol="BTC/USDC", timeframe="1h",
    )


def test_no_param_space_returns_error_regardless_of_n_jobs():
    # param_space={} au constructeur retombe sur PARAM_SPACES[strategy_name]
    # (``param_space or PARAM_SPACES.get(...)``) — pour tester le cas
    # "aucun espace" il faut l'écraser après coup.
    opt = _make_opt()
    opt.param_space = {}
    assert opt.random_search(n_trials=5, n_jobs=1) == {
        "error": "Aucun espace de params pour opus_omnibus_v11"}
    opt2 = _make_opt()
    opt2.param_space = {}
    assert opt2.random_search(n_trials=5, n_jobs=4) == {
        "error": "Aucun espace de params pour opus_omnibus_v11"}


def test_n_jobs_1_stays_sequential_and_never_calls_run_parallel(monkeypatch):
    opt = _make_opt()
    calls = {"eval": 0, "run_parallel": 0}
    monkeypatch.setattr(opt, "_eval", lambda params: (
        calls.__setitem__("eval", calls["eval"] + 1), _fake_eval_result(1.0))[1])
    monkeypatch.setattr(opt, "_run_parallel", lambda *a, **k: calls.__setitem__(
        "run_parallel", calls["run_parallel"] + 1))

    result = opt.random_search(n_trials=5, n_jobs=1)

    assert calls["eval"] == 5
    assert calls["run_parallel"] == 0
    assert result["best_oos_score"] == 1.0
    assert len(opt.results) == 5


def test_n_jobs_gt_1_delegates_to_run_parallel_and_skips_own_loop(monkeypatch):
    opt = _make_opt()
    calls = {"eval": 0, "run_parallel_args": None}

    def _fake_run_parallel(n, n_total, trial_offset=0, sampler=None, n_jobs=1):
        calls["run_parallel_args"] = (n, n_total, trial_offset, n_jobs)
        # Simule ce que _run_parallel ferait réellement : peupler self.results.
        opt.results.append(_fake_eval_result(2.5))

    monkeypatch.setattr(opt, "_eval", lambda params: (
        calls.__setitem__("eval", calls["eval"] + 1), _fake_eval_result(1.0))[1])
    monkeypatch.setattr(opt, "_run_parallel", _fake_run_parallel)

    result = opt.random_search(n_trials=8, n_jobs=4)

    assert calls["eval"] == 0, "n_jobs>1 doit déléguer entièrement, sans jamais passer par la boucle inline"
    assert calls["run_parallel_args"] == (8, 8, 0, 4)
    assert result["best_oos_score"] == 2.5


def test_n_jobs_1_respects_early_stop_patience(monkeypatch):
    opt = _make_opt()
    scores = iter([1.0, 1.0, 1.0, 1.0, 1.0])  # jamais d'amélioration après le 1er
    calls = {"eval": 0}

    def _fake_eval(params):
        calls["eval"] += 1
        return _fake_eval_result(next(scores))

    monkeypatch.setattr(opt, "_eval", _fake_eval)
    opt.random_search(n_trials=10, n_jobs=1, early_stop_patience=2)

    # trial 1 : score=1.0 > -999 → improve (no_improve=0)
    # trial 2 : score=1.0, pas d'amélioration → no_improve=1
    # trial 3 : idem → no_improve=2 >= patience(2) → stop après ce trial
    assert calls["eval"] == 3
