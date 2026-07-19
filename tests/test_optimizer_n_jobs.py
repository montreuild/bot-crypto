"""n_jobs de StrategyOptimizer.random_search : le routage se fait maintenant
via un pool de process PARTAGÉ (``_open_pool``), ouvert une seule fois par
appel et réutilisable entre le dépistage Param Search Optim et la recherche
principale — plus un ProcessPoolExecutor recréé à chaque phase. Ces tests
vérifient le ROUTAGE (n_jobs<=1 → ``_open_pool`` cède ``None`` → boucle
séquentielle ; n_jobs>1 → ``_open_pool`` cède un pool → ``_run_parallel``
l'utilise) sans payer le coût d'un vrai ProcessPoolExecutor (spawn de
subprocess, lent et non déterministe en CI)."""
from contextlib import contextmanager

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


def test_n_jobs_1_opens_no_pool_and_run_parallel_gets_pool_none(monkeypatch):
    opt = _make_opt()
    calls = {"eval": 0, "run_parallel_pool": "unset"}

    def _fake_run_parallel(n, n_total, trial_offset=0, sampler=None, pool=None,
                           early_stop_patience=0, initial_best=-999.0):
        calls["run_parallel_pool"] = pool
        for _ in range(n):
            calls["eval"] += 1
            opt.results.append(_fake_eval_result(1.0))

    monkeypatch.setattr(opt, "_run_parallel", _fake_run_parallel)
    result = opt.random_search(n_trials=5, n_jobs=1)

    assert calls["run_parallel_pool"] is None, "n_jobs<=1 -> _open_pool doit céder None"
    assert calls["eval"] == 5
    assert result["best_oos_score"] == 1.0


def test_n_jobs_gt_1_run_parallel_receives_the_shared_pool(monkeypatch):
    opt = _make_opt()
    calls = {"n_jobs_seen": None, "run_parallel_pool": "unset"}

    class _FakePool:
        safe_jobs = 4

    @contextmanager
    def _fake_open_pool(n_jobs):
        calls["n_jobs_seen"] = n_jobs
        yield _FakePool()

    def _fake_run_parallel(n, n_total, trial_offset=0, sampler=None, pool=None,
                           early_stop_patience=0, initial_best=-999.0):
        calls["run_parallel_pool"] = pool
        opt.results.append(_fake_eval_result(2.5))

    monkeypatch.setattr(opt, "_open_pool", _fake_open_pool)
    monkeypatch.setattr(opt, "_run_parallel", _fake_run_parallel)

    result = opt.random_search(n_trials=8, n_jobs=4)

    assert calls["n_jobs_seen"] == 4
    assert isinstance(calls["run_parallel_pool"], _FakePool), (
        "n_jobs>1 doit passer le pool ouvert par _open_pool à _run_parallel")
    assert result["best_oos_score"] == 2.5


class _FakePool:
    safe_jobs = 4


def test_failed_trials_consume_budget_not_resampled(monkeypatch):
    """Un trial en échec (worker KO/timeout/erreur stratégie, silencieusement
    absent des résultats de la vague) doit consommer son créneau du budget
    ``n`` — compter les seuls succès faisait échantillonner au-delà du budget
    (et, pour grid_search dont le sampler épuise une énumération finie,
    levait StopIteration en pleine grille)."""
    opt = _make_opt()
    draws = {"n": 0}

    def _sampler():
        draws["n"] += 1
        return {"p": draws["n"]}

    def _fake_submit_wave(pool, params_list, timeout=300):
        # Simule 1 échec par vague : le 1er trial de chaque vague disparaît
        # sans résultat (comme un worker KO réel dans _submit_wave).
        return [_fake_eval_result(1.0) for _ in params_list[1:]], False, []

    monkeypatch.setattr(opt, "_submit_wave", _fake_submit_wave)
    attempted = opt._run_parallel(10, 10, sampler=_sampler, pool=_FakePool())

    assert draws["n"] == 10, "le budget se compte en TENTATIVES : jamais plus de n tirages"
    assert attempted == 10
    assert len(opt.results) == 7  # 3 vagues (4+4+2), 1 échec par vague


def test_grid_with_failed_trials_exhausts_enumeration_without_stopiteration(monkeypatch):
    """grid_search en parallèle : le sampler est ``next()`` sur une
    énumération FINIE — si les échecs ne comptaient pas dans le budget, la
    boucle re-tirait après épuisement -> StopIteration en pleine grille."""
    opt = _make_opt({"a": [0, 1, 2], "b": [0, 1, 2]})  # 9 combos

    from contextlib import contextmanager

    @contextmanager
    def _fake_open_pool(n_jobs):
        yield _FakePool()

    def _fake_submit_wave(pool, params_list, timeout=300):
        return [_fake_eval_result(1.0) for _ in params_list[1:]], False, []

    monkeypatch.setattr(opt, "_open_pool", _fake_open_pool)
    monkeypatch.setattr(opt, "_submit_wave", _fake_submit_wave)

    result = opt.grid_search(n_jobs=4)  # ne doit PAS lever StopIteration

    assert "error" not in result
    assert len(opt.results) == 6  # 9 combos tentés, 1 échec par vague (4+4+1)


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
