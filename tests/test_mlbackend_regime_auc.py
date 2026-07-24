"""app.ml.backend.trainer — ventilation de l'AUC direction par régime sur le
set de validation (ML-02, question ouverte sur la purge dir_min/dir_max de
l'optimiseur V11/V12, commit d6eb9db). Diagnostic pur : n'affecte ni
l'entraînement ni le routing, seulement train_meta/logs.
"""
import datetime as dt

import numpy as np
import polars as pl
import pytest

from app.ml.backend.features import REGIME_CHOPPY, REGIME_RANGE, REGIME_TREND_DN
from app.ml.backend.trainer import TrainState, _auc_dir_by_regime, _rank_auc, train


# ─────────────────────────────────────────────────────────────────────────────
#  _rank_auc — doit coïncider avec app.ml.policy.rank_auc (même formule,
#  dupliquée pour garder app.ml.backend indépendant de app.ml.policy).
# ─────────────────────────────────────────────────────────────────────────────
def test_rank_auc_matches_policy_rank_auc():
    from app.ml.policy import rank_auc as policy_rank_auc
    rng = np.random.RandomState(0)
    y = rng.randint(0, 2, 200)
    scores = rng.rand(200)
    assert _rank_auc(y, scores) == pytest.approx(policy_rank_auc(y, scores))


def test_rank_auc_perfect_separation_is_one():
    y = np.array([0, 0, 0, 1, 1, 1])
    scores = np.array([0.1, 0.2, 0.3, 0.7, 0.8, 0.9])
    assert _rank_auc(y, scores) == pytest.approx(1.0)


def test_rank_auc_single_class_is_none():
    assert _rank_auc(np.array([1, 1, 1]), np.array([0.1, 0.5, 0.9])) is None


# ─────────────────────────────────────────────────────────────────────────────
#  _auc_dir_by_regime — isole correctement le signal par bucket de régime.
# ─────────────────────────────────────────────────────────────────────────────
def test_auc_dir_by_regime_isolates_signal_to_correct_bucket():
    rng = np.random.RandomState(1)
    n_per_regime = 100
    regimes, y, scores = [], [], []

    # Trend Down : signal parfait (scores parfaitement séparés par label).
    for i in range(n_per_regime):
        label = i % 2
        regimes.append(REGIME_TREND_DN)
        y.append(label)
        scores.append(0.9 if label == 1 else 0.1)

    # Range : bruit pur (aucune corrélation label/score).
    for i in range(n_per_regime):
        regimes.append(REGIME_RANGE)
        y.append(i % 2)
        scores.append(rng.rand())

    y = np.array(y, dtype=np.int64)
    scores = np.array(scores, dtype=np.float64)

    out = _auc_dir_by_regime(regimes, y, scores)

    assert out["trend_down"]["n"] == n_per_regime
    assert out["trend_down"]["auc"] == pytest.approx(1.0)
    assert out["range"]["n"] == n_per_regime
    assert 0.3 < out["range"]["auc"] < 0.7  # bruit -> proche de 0.5, jamais 1.0
    # Régimes absents du batch : présents avec n=0, auc=None (pas d'exception).
    assert out["trend_up"] == {"n": 0, "auc": None}
    assert out["choppy"] == {"n": 0, "auc": None}


def test_auc_dir_by_regime_below_min_samples_returns_none_not_crash():
    # < 15 échantillons dans un régime -> auc=None (pas assez pour un AUC fiable).
    regimes = [REGIME_CHOPPY] * 5
    y = np.array([0, 1, 0, 1, 0])
    scores = np.array([0.2, 0.8, 0.3, 0.7, 0.1])
    out = _auc_dir_by_regime(regimes, y, scores)
    assert out["choppy"]["n"] == 5
    assert out["choppy"]["auc"] is None


def test_auc_dir_by_regime_covers_all_four_regime_labels():
    out = _auc_dir_by_regime([], np.array([], dtype=np.int64), np.array([], dtype=np.float64))
    assert set(out.keys()) == {"range", "trend_up", "trend_down", "choppy"}


# ─────────────────────────────────────────────────────────────────────────────
#  Intégration : train() peuple train_meta["auc_dir_by_regime"] sans planter,
#  avec les 4 clés attendues (n/auc), sur des données synthétiques réalistes.
# ─────────────────────────────────────────────────────────────────────────────
def _make_trending_ohlcv(n: int, seed: int = 0) -> pl.DataFrame:
    """OHLCV synthétique avec alternance de phases tendance/range, pour que
    les 4 régimes (Range/TrendUp/TrendDown/Choppy) apparaissent tous."""
    rng = np.random.RandomState(seed)
    rets = np.empty(n)
    phase_len = 300
    price_drift = 0.0
    for i in range(n):
        if i % phase_len == 0:
            price_drift = rng.choice([-0.003, 0.0, 0.003])
        rets[i] = price_drift + rng.normal(0, 0.006)
    close = 100.0 * np.cumprod(1.0 + rets)
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    vol = rng.uniform(100, 1000, n)
    times = [dt.datetime(2024, 1, 1) + dt.timedelta(hours=i) for i in range(n)]
    return pl.DataFrame({"time": times, "open": open_, "high": high,
                         "low": low, "close": close, "volume": vol})


@pytest.mark.slow
def test_train_populates_auc_dir_by_regime_with_all_four_keys():
    df = _make_trending_ohlcv(3000, seed=7)
    state = TrainState()
    import threading
    lock = threading.Lock()
    ok = train(state, lock, df, "1h", params={}, defaults={})
    assert ok
    meta = state.train_meta["1h"]
    assert "auc_dir_by_regime" in meta
    assert set(meta["auc_dir_by_regime"].keys()) == {"range", "trend_up", "trend_down", "choppy"}
    for v in meta["auc_dir_by_regime"].values():
        assert "n" in v and "auc" in v
        assert v["n"] >= 0
        if v["auc"] is not None:
            assert 0.0 <= v["auc"] <= 1.0
