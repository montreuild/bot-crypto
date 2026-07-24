"""Routes /api/ml/registry, /api/ml/train, /api/ml/sweep (ML-02 tâche E7 —
page « Modèles »). Isolation du models/ réel du dépôt via monkeypatch.chdir
(le registre résout un chemin relatif "models", cf. app.ml.model_registry.
DEFAULT_BASE_DIR) — sans ce chdir, ces tests liraient/écriraient le
répertoire models/ réel du projet."""
import datetime as dt

import numpy as np
import polars as pl
import pytest
from starlette.testclient import TestClient

pytest.importorskip("lightgbm")

import app.ml.model_registry as registry
from app.api.helpers import verify_api_key
from app.api.main import app
from app.ml.backend.persistence import save_amp_dir_bundle


@pytest.fixture
def client():
    app.dependency_overrides[verify_api_key] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_api_key, None)


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path, monkeypatch):
    """CWD isolé -> "models" (chemin relatif par défaut du registre) pointe
    vers un répertoire vide propre à chaque test, jamais le models/ réel."""
    monkeypatch.chdir(tmp_path)
    yield


def _train_tiny_booster(seed: int):
    import lightgbm as lgb
    rng = np.random.RandomState(seed)
    X = rng.rand(200, 3).astype(np.float32)
    y = (X[:, 0] > 0.5).astype(np.int32)
    ds = lgb.Dataset(X, label=y, free_raw_data=False)
    return lgb.train({"objective": "binary", "verbosity": -1, "num_leaves": 4}, ds, num_boost_round=3)


def _publish(tmp_path, symbol, tf, recipe, train_end, decision="promote", auc=0.6, tag="v"):
    prefix = str(tmp_path / f"src_{tag}_{recipe}_{tf}")
    amp, dir_ = _train_tiny_booster(1), _train_tiny_booster(2)
    save_amp_dir_bundle(prefix, tf, amp, dir_, ["f0", "f1", "f2"], {}, auc, {})
    return registry.publish(symbol, tf, recipe, prefix, train_end=train_end,
                            decision=decision, base_dir="models")


def _make_ohlcv(n, seed=1, start=dt.datetime(2020, 1, 1)):
    rng = np.random.RandomState(seed)
    times = [start + dt.timedelta(hours=i) for i in range(n)]
    rets = rng.normal(0, 0.01, n)
    close = 100.0 * np.cumprod(1 + rets)
    open_ = np.concatenate([[100.0], close[:-1]])
    high = np.maximum(open_, close) * 1.002
    low = np.minimum(open_, close) * 0.998
    volume = rng.uniform(100, 1000, n)
    return pl.DataFrame({"time": times, "open": open_, "high": high,
                         "low": low, "close": close, "volume": volume})


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/ml/registry
# ─────────────────────────────────────────────────────────────────────────────
def test_registry_overview_empty(client):
    r = client.get("/api/ml/registry")
    assert r.status_code == 200
    assert r.json() == {"models": []}


def test_registry_overview_lists_published_models(client, tmp_path):
    _publish(tmp_path, "BTC/USDC", "1h", "opus_omnibus_v11", "2026-01-01T00:00:00")
    r = client.get("/api/ml/registry")
    assert r.status_code == 200
    models = r.json()["models"]
    assert len(models) == 1
    m = models[0]
    assert m["symbol"] == "BTC/USDC"
    assert m["tf"] == "1h"
    assert m["recipe"] == "opus_omnibus_v11"
    assert m["active"] is not None
    assert m["pinned_version_id"] is None


def test_registry_overview_flags_no_active_version(client, tmp_path):
    _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-01-01T00:00:00", decision="keep")
    r = client.get("/api/ml/registry")
    m = r.json()["models"][0]
    assert m["active"] is None
    assert "aucune version active" in m["freshness_warning"]


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/ml/registry/versions, /decisions
# ─────────────────────────────────────────────────────────────────────────────
def test_registry_versions_newest_first(client, tmp_path):
    _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-01-01T00:00:00", tag="old")
    _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-03-01T00:00:00", tag="new")
    r = client.get("/api/ml/registry/versions", params={"symbol": "BTC/USDC", "tf": "1h", "recipe": "r"})
    versions = r.json()["versions"]
    assert len(versions) == 2
    assert versions[0]["train_end"] == "2026-03-01T00:00:00"  # plus récent en premier


def test_registry_decisions_reflects_publish(client, tmp_path):
    _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-01-01T00:00:00")
    r = client.get("/api/ml/registry/decisions", params={"symbol": "BTC/USDC", "tf": "1h", "recipe": "r"})
    decisions = r.json()["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "promote"


# ─────────────────────────────────────────────────────────────────────────────
#  Pin / unpin
# ─────────────────────────────────────────────────────────────────────────────
def test_pin_then_registry_overview_reflects_it(client, tmp_path):
    old = _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-01-01T00:00:00", tag="old")
    _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-03-01T00:00:00", tag="new")

    r = client.post("/api/ml/registry/pin", json={
        "symbol": "BTC/USDC", "tf": "1h", "recipe": "r", "version_id": old.version_id,
    })
    assert r.status_code == 200

    overview = client.get("/api/ml/registry").json()["models"][0]
    assert overview["pinned_version_id"] == old.version_id
    assert overview["active"]["version_id"] == old.version_id


def test_pin_unknown_version_404(client, tmp_path):
    _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-01-01T00:00:00")
    r = client.post("/api/ml/registry/pin", json={
        "symbol": "BTC/USDC", "tf": "1h", "recipe": "r", "version_id": "does-not-exist",
    })
    assert r.status_code == 404


def test_unpin_clears_it(client, tmp_path):
    old = _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-01-01T00:00:00")
    client.post("/api/ml/registry/pin", json={
        "symbol": "BTC/USDC", "tf": "1h", "recipe": "r", "version_id": old.version_id,
    })
    r = client.post("/api/ml/registry/unpin", json={"symbol": "BTC/USDC", "tf": "1h", "recipe": "r"})
    assert r.status_code == 200
    assert client.get("/api/ml/registry").json()["models"][0]["pinned_version_id"] is None


# ─────────────────────────────────────────────────────────────────────────────
#  Promote
# ─────────────────────────────────────────────────────────────────────────────
def test_promote_rejected_candidate(client, tmp_path):
    art = _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-01-01T00:00:00", decision="keep")
    r = client.post("/api/ml/registry/promote", json={
        "symbol": "BTC/USDC", "tf": "1h", "recipe": "r",
        "version_id": art.version_id, "decision": "manual",
    })
    assert r.status_code == 200
    overview = client.get("/api/ml/registry").json()["models"][0]
    assert overview["active"]["version_id"] == art.version_id


def test_promote_invalid_decision_400(client, tmp_path):
    art = _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-01-01T00:00:00")
    r = client.post("/api/ml/registry/promote", json={
        "symbol": "BTC/USDC", "tf": "1h", "recipe": "r",
        "version_id": art.version_id, "decision": "bogus",
    })
    assert r.status_code == 400


def test_promote_unknown_version_404(client, tmp_path):
    r = client.post("/api/ml/registry/promote", json={
        "symbol": "BTC/USDC", "tf": "1h", "recipe": "r", "version_id": "nope",
    })
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
#  Train / sweep — validation + job asynchrone bout en bout
# ─────────────────────────────────────────────────────────────────────────────
def test_train_unknown_strategy_400(client):
    r = client.post("/api/ml/train", json={"strategy": "not_a_real_strategy", "tf": "1h"})
    assert r.status_code == 400


def test_train_status_unknown_job_404(client):
    r = client.get("/api/ml/train/status", params={"job_id": "nope"})
    assert r.status_code == 404


def test_sweep_empty_windows_400(client):
    r = client.post("/api/ml/sweep", json={"strategy": "opus_omnibus_v11", "tf": "1h", "windows": []})
    assert r.status_code == 400


@pytest.mark.slow
def test_train_job_runs_and_completes_dry_run(client, tmp_path, monkeypatch):
    import app.core.candle_store as candle_store_mod
    store = candle_store_mod.CandleStore(base_dir=str(tmp_path / "ohlcv_cache"))
    candle_store_mod.get_store.set(store)
    monkeypatch.setattr(candle_store_mod, "OHLCV_DIR", str(tmp_path / "ohlcv_cache"))
    try:
        df = _make_ohlcv(1400, seed=21)
        store._save(store._path("BTC/USDC", "1h"), df.with_columns(pl.col("time").cast(pl.Datetime("ms"))))

        r = client.post("/api/ml/train", json={
            "strategy": "opus_omnibus_v11", "symbol": "BTC/USDC", "tf": "1h",
            "params": {"n_estimators": 20, "num_leaves": 7, "gate_holdout_bars": 250,
                      "gate_min_window_bars": 700, "gate_auc_floor": 0.0},
            "publish": False,
        })
        assert r.status_code == 200
        job_id = r.json()["job_id"]

        import time as _time
        deadline = _time.monotonic() + 30
        job = None
        while _time.monotonic() < deadline:
            job = client.get("/api/ml/train/status", params={"job_id": job_id}).json()
            if job["status"] != "running":
                break
            _time.sleep(0.2)
        assert job is not None
        assert job["status"] == "done", job
        assert job["result"]["decision"].startswith("dry_run_would_")
    finally:
        candle_store_mod.get_store.set(None)
