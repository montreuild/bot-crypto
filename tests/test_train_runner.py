"""app.ml.train_runner — chargement offline (cache Parquet local, ZÉRO appel
exchange), dry-run vs publication gatée, window sweep sur holdout commun
(ML-02 §3.3 / tâche E3 — reproductibilité de l'entraînement)."""
import datetime as dt

import numpy as np
import polars as pl
import pytest

pytest.importorskip("lightgbm")

import app.core.candle_store as candle_store_mod
import app.ml.model_registry as registry
from app.ml.train_runner import load_offline_ohlcv, train_and_publish, window_sweep

# Les artefacts sont indexés par RECETTE, plus par nom de stratégie
# (fracture b) : opus_omnibus_v11 consomme omnibus_v4_multi.
_RECIPE = "omnibus_v4_multi"


def _make_ohlcv(n: int, seed: int = 1, start_price: float = 100.0,
                start: dt.datetime = dt.datetime(2020, 1, 1)) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    times = [start + dt.timedelta(hours=i) for i in range(n)]
    rets = rng.normal(0, 0.01, n)
    close = start_price * np.cumprod(1 + rets)
    open_ = np.concatenate([[start_price], close[:-1]])
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.002, n)))
    volume = rng.uniform(100, 1000, n)
    return pl.DataFrame({"time": times, "open": open_, "high": high,
                         "low": low, "close": close, "volume": volume})


@pytest.fixture
def seeded_store(tmp_path):
    """Injecte un CandleStore isolé (aucun appel exchange) via get_store.set,
    et le désarme après le test (ré-active la création paresseuse normale)."""
    store = candle_store_mod.CandleStore(base_dir=str(tmp_path / "ohlcv_cache"))
    candle_store_mod.get_store.set(store)
    yield store
    candle_store_mod.get_store.set(None)


def _seed(store, symbol, tf, df):
    store._save(store._path(symbol, tf), df.with_columns(pl.col("time").cast(pl.Datetime("ms"))))


# ─────────────────────────────────────────────────────────────────────────────
#  load_offline_ohlcv
# ─────────────────────────────────────────────────────────────────────────────
def test_load_offline_ohlcv_reads_cache(seeded_store):
    df = _make_ohlcv(300)
    _seed(seeded_store, "BTC/USDC", "1h", df)
    got = load_offline_ohlcv("BTC/USDC", "1h")
    assert got is not None
    assert len(got) == 300


def test_load_offline_ohlcv_none_when_absent(seeded_store):
    assert load_offline_ohlcv("BTC/USDC", "1h") is None


def test_load_offline_ohlcv_as_of_filters_future_bars(seeded_store):
    df = _make_ohlcv(300)
    _seed(seeded_store, "BTC/USDC", "1h", df)
    cutoff = df["time"][149]
    got = load_offline_ohlcv("BTC/USDC", "1h", as_of=str(cutoff))
    assert got is not None
    assert len(got) == 150
    assert got["time"][-1] <= cutoff


def test_load_offline_ohlcv_window_bars_tails_after_as_of(seeded_store):
    df = _make_ohlcv(300)
    _seed(seeded_store, "BTC/USDC", "1h", df)
    cutoff = df["time"][199]
    got = load_offline_ohlcv("BTC/USDC", "1h", as_of=str(cutoff), window_bars=50)
    assert got is not None
    assert len(got) == 50
    assert got["time"][-1] <= cutoff


# ─────────────────────────────────────────────────────────────────────────────
#  train_and_publish — dry-run vs publication réelle
# ─────────────────────────────────────────────────────────────────────────────
_FAST_PARAMS = {"n_estimators": 20, "num_leaves": 7, "gate_holdout_bars": 250,
                "gate_min_window_bars": 700, "gate_auc_floor": 0.0}


@pytest.mark.slow
def test_train_and_publish_dry_run_writes_nothing(seeded_store, tmp_path):
    df = _make_ohlcv(1400, seed=11)
    _seed(seeded_store, "BTC/USDC", "1h", df)
    registry_base = str(tmp_path / "models")

    result = train_and_publish("opus_omnibus_v11", "BTC/USDC", "1h",
                               params=_FAST_PARAMS, publish=False, base_dir=registry_base)

    assert result["decision"].startswith("dry_run_would_")
    assert "note" in result
    versions = registry.list_versions("1h", _RECIPE, base_dir=registry_base)
    assert len(versions) == 0  # dry-run : rien n'est écrit


@pytest.mark.slow
def test_train_and_publish_publish_true_writes_registry(seeded_store, tmp_path):
    df = _make_ohlcv(1400, seed=12)
    _seed(seeded_store, "BTC/USDC", "1h", df)
    registry_base = str(tmp_path / "models")

    result = train_and_publish("opus_omnibus_v11", "BTC/USDC", "1h",
                               params=_FAST_PARAMS, publish=True, base_dir=registry_base)

    assert result["decision"] in ("initial", "promote")
    versions = registry.list_versions("1h", _RECIPE, base_dir=registry_base)
    assert len(versions) == 1


def test_train_and_publish_no_cached_data_fails_cleanly(seeded_store, tmp_path):
    result = train_and_publish("opus_omnibus_v11", "BTC/USDC", "1h",
                               params=_FAST_PARAMS, publish=False,
                               base_dir=str(tmp_path / "models"))
    assert result["decision"] == "failed"


# ─────────────────────────────────────────────────────────────────────────────
#  window_sweep
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.slow
def test_window_sweep_compares_without_publishing_by_default(seeded_store, tmp_path):
    df = _make_ohlcv(1800, seed=13)
    _seed(seeded_store, "BTC/USDC", "1h", df)
    registry_base = str(tmp_path / "models")

    result = window_sweep("opus_omnibus_v11", "BTC/USDC", "1h", [800, 1200],
                          params=_FAST_PARAMS, publish_best=False, base_dir=registry_base)

    assert len(result["candidates"]) == 2
    assert result["best_window_bars"] in (800, 1200)
    assert "gate_decision" not in result
    versions = registry.list_versions("1h", _RECIPE, base_dir=registry_base)
    assert len(versions) == 0  # comparaison seule, rien publié


@pytest.mark.slow
def test_window_sweep_publish_best_writes_only_one_version(seeded_store, tmp_path):
    df = _make_ohlcv(1800, seed=14)
    _seed(seeded_store, "BTC/USDC", "1h", df)
    registry_base = str(tmp_path / "models")

    result = window_sweep("opus_omnibus_v11", "BTC/USDC", "1h", [800, 1200],
                          params=dict(_FAST_PARAMS, gate_auc_floor=0.0),
                          publish_best=True, base_dir=registry_base)

    assert result["gate_decision"] in ("initial", "promote")
    versions = registry.list_versions("1h", _RECIPE, base_dir=registry_base)
    assert len(versions) == 1  # seul le meilleur candidat est publié, pas les deux


def test_window_sweep_no_cached_data_returns_error(seeded_store, tmp_path):
    result = window_sweep("opus_omnibus_v11", "BTC/USDC", "1h", [800, 1200],
                          params=_FAST_PARAMS, base_dir=str(tmp_path / "models"))
    assert "error" in result


def test_load_offline_ohlcv_precomputes_shared_indicator_columns(seeded_store):
    """Le cache Parquet ne stocke que l'OHLCV brut, mais les stratégies
    « bespoke » (scoring_statistique_opus_v4/v5…) attendent les colonnes
    ``_pre_*`` en entrée de ``fit()`` — le chemin LIVE les reçoit de
    ``scanner.fetch_ohlcv``. Sans elles, entraîner depuis l'UI échouait par
    « _build_features=None » là où le live entraînait la même recette."""
    _seed(seeded_store, "BTC/USDC", "1h", _make_ohlcv(300))
    got = load_offline_ohlcv("BTC/USDC", "1h")
    assert got is not None
    assert "_pre_atr14" in got.columns
    assert len(got) == 300, "le pré-calcul ne doit ni tronquer ni réordonner"


# ─────────────────────────────────────────────────────────────────────────────
#  Diagnostics d'entraînement remontés dans le résultat de job
# ─────────────────────────────────────────────────────────────────────────────
def test_dry_run_carries_train_meta(seeded_store, tmp_path):
    """Un dry-run n'écrit RIEN au registre : sans ce champ, les diagnostics de
    l'expérience (top features, calibration, AUC par régime) mourraient avec
    l'instance — alors que c'est justement le mode fait pour expérimenter."""
    _seed(seeded_store, "BTC/USDC", "1h", _make_ohlcv(4200, seed=11))
    res = train_and_publish(
        "opus_omnibus_v11", "BTC/USDC", "1h", publish=False,
        base_dir=str(tmp_path / "models"),
        params={"gate_holdout_bars": 900, "gate_min_window_bars": 1200,
                "gate_auc_floor": 0.0, "n_estimators": 20, "num_leaves": 7},
    )
    assert res["decision"].startswith("dry_run_would_"), res
    tm = res.get("train_meta") or {}
    assert tm.get("feature_importance_amp"), "top features amplitude attendues"
    assert tm.get("feature_importance_dir"), "top features direction attendues"
    assert "calibrated" in tm
    # Le format attendu par la page Modèles : {feature, gain}, pas un nom seul.
    assert set(tm["feature_importance_amp"][0]) == {"feature", "gain"}


def test_publish_carries_candidate_train_meta_even_when_rejected(seeded_store, tmp_path):
    """Les diagnostics doivent être ceux du CANDIDAT, capturés AVANT le
    ``reset_model()`` de la branche « keep » — c'est précisément quand il est
    rejeté qu'on veut voir pourquoi.

    Le plancher à 0.99 force le rejet : c'est le seul moyen d'exercer
    réellement cette branche (deux passes successives promeuvent). Le test est
    porteur — ``reset_model()`` vide bel et bien ``_train_meta``, donc une
    capture faite après le gate rendrait ``{}``.
    """
    _seed(seeded_store, "BTC/USDC", "1h", _make_ohlcv(4200, seed=12))
    res = train_and_publish(
        "opus_omnibus_v11", "BTC/USDC", "1h", publish=True,
        base_dir=str(tmp_path / "models"),
        params={"gate_holdout_bars": 900, "gate_min_window_bars": 1200,
                "gate_auc_floor": 0.99, "n_estimators": 20, "num_leaves": 7},
    )
    assert res["decision"] == "keep", res["reason"]
    assert (res.get("train_meta") or {}).get("feature_importance_amp"), (
        "diagnostics du candidat rejeté perdus — capturés après reset_model() ?")
