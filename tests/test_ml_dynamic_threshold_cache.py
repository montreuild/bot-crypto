"""Cache process-wide des retrains de ml_dynamic_threshold._fit (cf. PERF :
avant ce câblage, chaque retrain RandomForest (+ random search interne 8
trials + fit OOS de validation) était relancé à l'identique à chaque trial
de l'optimiseur pour opus_omnibus_v12 — ~40-60% du coût par trial. Même
mécanisme que opus_omnibus_v11 (app/core/train_cache.py)."""
from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

pytest.importorskip("lightgbm")

from app.core import train_cache
from app.strategies.ml_dynamic_threshold import MLDynamicThresholdStrategy


def _make_ohlcv(n: int = 400, seed: int = 7) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    base = 30_000.0
    closes = [base]
    for i in range(n - 1):
        closes.append(max(closes[-1] + rng.standard_normal() * 60.0, 1.0))
    closes = np.array(closes)
    highs = closes * (1 + np.abs(rng.standard_normal(n) * 0.004))
    lows = closes * (1 - np.abs(rng.standard_normal(n) * 0.004))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = rng.uniform(500, 2000, n)
    start = datetime(2025, 1, 1)
    times = [start + timedelta(hours=i) for i in range(n)]
    return pl.DataFrame({
        "time": times, "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    })


@pytest.fixture(autouse=True)
def _clean_cache():
    train_cache.clear()
    yield
    train_cache.clear()


def test_fit_second_instance_hits_cache_and_skips_training():
    df = _make_ohlcv()
    a = MLDynamicThresholdStrategy(n_trials=3)
    b = MLDynamicThresholdStrategy(n_trials=3)

    a._fit(df, "1h")
    assert "1h" in a._trained_tfs
    assert train_cache.stats()["misses"] == 1

    calls = {"n": 0}
    real_impl = b._fit_impl
    def _spy(*args, **kwargs):
        calls["n"] += 1
        return real_impl(*args, **kwargs)
    b._fit_impl = _spy

    b._fit(df, "1h")
    assert calls["n"] == 0                    # cache hit : jamais rappelé
    assert "1h" in b._trained_tfs
    assert b._boosters["1h"] is a._boosters["1h"]  # même objet, par référence
    assert b._best_auc_per_tf["1h"] == a._best_auc_per_tf["1h"]
    assert train_cache.stats()["hits"] == 1


def test_fit_count_advances_even_on_cache_hit():
    """Non-régression : le compteur hyper_search_every doit avancer sur CHAQUE
    appel logique à _fit, même servi depuis le cache — sinon le cycle
    random-search-vs-refit se désynchronise entre deux trials de l'optimiseur
    qui n'ont pas eu exactement le même nombre de hits."""
    df = _make_ohlcv()
    a = MLDynamicThresholdStrategy(n_trials=3)
    a._fit(df, "1h")
    assert a._fit_count_per_tf["1h"] == 1

    b = MLDynamicThresholdStrategy(n_trials=3)
    b._fit(df, "1h")   # cache hit
    assert b._fit_count_per_tf["1h"] == 1

    b._fit(df, "1h")   # rappel logique : le compteur doit quand même avancer
    assert b._fit_count_per_tf["1h"] == 2


def test_different_routing_unrelated_params_still_hit_cache():
    """lookahead/vol_multiplier/n_trials/model_type pilotent l'entraînement ;
    rien d'autre (ex. adx_min, proba_long) ne doit influencer la clé du
    cache — ce sont des seuils de décision purs, comme pour v11."""
    df = _make_ohlcv()
    a = MLDynamicThresholdStrategy(n_trials=3, adx_min=15.0, proba_long=0.55)
    b = MLDynamicThresholdStrategy(n_trials=3, adx_min=30.0, proba_long=0.70)

    a._fit(df, "1h")
    b._fit(df, "1h")
    # Comparaison par sous-ensemble : ``stats()`` peut gagner des compteurs
    # (ex. ``empty_snapshots``) sans que ce test ait à le savoir.
    st = train_cache.stats()
    assert {k: st[k] for k in ("hits", "misses", "entries")} == {
        "hits": 1, "misses": 1, "entries": 1}


def test_get_or_build_features_offset_slices_not_head():
    strat = MLDynamicThresholdStrategy(n_trials=3)
    full_df = _make_ohlcv(n=200)
    from app.strategies.ml_dynamic_threshold import compute_features
    full_feats = compute_features(full_df)
    strat._bt_features = full_feats
    strat._bt_features_len = len(full_feats)

    tail_df = full_df.tail(50)
    got = strat._get_or_build_features(tail_df, offset=150)
    expected = full_feats.slice(150, 50)
    assert got.equals(expected)
    # Sans offset explicite, comportement historique inchangé (head).
    got_head = strat._get_or_build_features(tail_df)
    assert got_head.equals(full_feats.head(50))
