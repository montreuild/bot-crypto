"""app.ml.backend.trainer — ventilation de l'AUC direction par régime sur le
set de validation (ML-02, question ouverte sur la purge dir_min/dir_max de
l'optimiseur V11/V12, commit d6eb9db). Diagnostic pur : n'affecte ni
l'entraînement ni le routing, seulement train_meta/logs.
"""
import datetime as dt
import json

import numpy as np
import polars as pl
import pytest

from app.ml.backend.features import REGIME_CHOPPY, REGIME_RANGE, REGIME_TREND_DN
from app.ml.backend.trainer import (
    TrainState,
    _auc_dir_by_regime,
    _feature_importance_by_regime,
    _rank_auc,
    _regime_similarity,
    _spearman,
    train,
)


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


# ─────────────────────────────────────────────────────────────────────────────
#  Importance des features PAR RÉGIME (pred_contrib) — le modèle direction
#  lit-il d'AUTRES signaux selon le régime ? L'importance "gain" native de
#  LightGBM est globale et ne peut pas répondre ; les attributions par
#  échantillon, oui.
# ─────────────────────────────────────────────────────────────────────────────
def _toy_booster(n_feat: int = 6, n: int = 400, seed: int = 0):
    import lightgbm as lgb
    rng = np.random.RandomState(seed)
    X = rng.randn(n, n_feat)
    # La cible ne dépend que de la feature 0 -> elle doit dominer partout.
    y = (X[:, 0] > 0).astype(int)
    booster = lgb.train({"objective": "binary", "verbosity": -1, "num_leaves": 7},
                        lgb.Dataset(X, label=y), num_boost_round=20)
    return booster, X, [f"f{i}" for i in range(n_feat)]


def test_feature_importance_by_regime_returns_all_regimes_and_ranks_driver_first():
    booster, X, cols = _toy_booster()
    # Deux régimes de 200 échantillons chacun.
    regimes = [REGIME_TREND_DN] * 200 + [REGIME_RANGE] * 200
    out = _feature_importance_by_regime(booster, X, cols, regimes, top_n=3)

    assert set(out.keys()) == {"range", "trend_up", "trend_down", "choppy"}
    for key in ("trend_down", "range"):
        assert out[key]["n"] == 200
        # f0 pilote la cible -> première par |contribution| dans chaque régime.
        assert out[key]["top"][0]["feature"] == "f0"
        assert out[key]["top"][0]["contrib"] > 0
    # Régimes absents du batch : n=0, top vide, aucune exception.
    for absent in ("trend_up", "choppy"):
        assert out[absent]["n"] == 0
        assert out[absent]["top"] == []


def test_feature_importance_by_regime_below_min_samples_is_empty():
    booster, X, cols = _toy_booster(n=400)
    regimes = [REGIME_CHOPPY] * 5 + [REGIME_RANGE] * 395
    out = _feature_importance_by_regime(booster, X, cols, regimes, top_n=3)
    assert out["choppy"]["n"] == 5
    assert out["choppy"]["top"] == []


def test_feature_importance_by_regime_caps_samples_for_memory():
    from app.ml.backend.trainer import _CONTRIB_MAX_SAMPLES_PER_REGIME
    n = _CONTRIB_MAX_SAMPLES_PER_REGIME + 500
    booster, X, cols = _toy_booster(n=n)
    out = _feature_importance_by_regime(booster, X, cols, [REGIME_RANGE] * n, top_n=3)
    assert out["range"]["n"] == _CONTRIB_MAX_SAMPLES_PER_REGIME


# ── Spearman (sans scipy) ────────────────────────────────────────────────────
def test_spearman_perfect_monotonic_is_one():
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert _spearman(a, a * 3.0 + 1.0) == pytest.approx(1.0)


def test_spearman_reversed_is_minus_one():
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert _spearman(a, -a) == pytest.approx(-1.0)


def test_spearman_constant_vector_is_none():
    a = np.array([1.0, 2.0, 3.0, 4.0])
    assert _spearman(a, np.ones(4)) is None


def test_spearman_too_short_is_none():
    assert _spearman(np.array([1.0]), np.array([2.0])) is None


# ── Similarité entre régimes ─────────────────────────────────────────────────
def test_regime_similarity_identical_importances_are_maximally_similar():
    full = np.array([5.0, 3.0, 1.0, 0.5])
    top = [{"feature": "a", "contrib": 5.0}, {"feature": "b", "contrib": 3.0}]
    by_regime = {
        "trend_down": {"n": 100, "top": top, "_full": full},
        "range":      {"n": 100, "top": top, "_full": full},
    }
    sim = _regime_similarity(by_regime, top_n=2)
    entry = sim["trend_down__vs__range"]
    assert entry["spearman"] == pytest.approx(1.0)
    assert entry["top_overlap"] == pytest.approx(1.0)


def test_regime_similarity_disjoint_tops_have_zero_overlap():
    by_regime = {
        "trend_down": {"n": 100, "top": [{"feature": "a"}, {"feature": "b"}],
                       "_full": np.array([5.0, 3.0, 1.0])},
        "trend_up":   {"n": 100, "top": [{"feature": "x"}, {"feature": "y"}],
                       "_full": np.array([1.0, 3.0, 5.0])},
    }
    entry = _regime_similarity(by_regime, top_n=2)["trend_down__vs__trend_up"]
    assert entry["top_overlap"] == pytest.approx(0.0)
    assert entry["spearman"] == pytest.approx(-1.0)


def test_regime_similarity_skips_regimes_without_data():
    by_regime = {
        "trend_down": {"n": 100, "top": [{"feature": "a"}], "_full": np.array([1.0, 2.0, 3.0])},
        "choppy":     {"n": 0, "top": []},
    }
    assert _regime_similarity(by_regime) == {}


@pytest.mark.slow
def test_train_populates_regime_feature_diagnostics_and_strips_internals():
    import threading
    df = _make_trending_ohlcv(3000, seed=11)
    state = TrainState()
    assert train(state, threading.Lock(), df, "1h", params={}, defaults={})
    meta = state.train_meta["1h"]

    fi = meta["feature_importance_dir_by_regime"]
    assert set(fi.keys()) == {"range", "trend_up", "trend_down", "choppy"}
    for v in fi.values():
        # ``_full`` est un tableau numpy interne (calcul du Spearman) : il ne
        # doit jamais atteindre meta.json, qui est sérialisé en JSON.
        assert "_full" not in v
        for f in v["top"]:
            assert set(f.keys()) == {"feature", "contrib"}

    for entry in meta["regime_feature_similarity"].values():
        assert 0.0 <= entry["top_overlap"] <= 1.0
        if entry.get("spearman") is not None:
            assert -1.0 <= entry["spearman"] <= 1.0

    json.dumps(meta)  # doit rester sérialisable de bout en bout
