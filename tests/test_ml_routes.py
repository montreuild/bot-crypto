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
    return registry.publish(tf, recipe, prefix, train_symbol=symbol,
                            train_end=train_end, decision=decision, base_dir="models")


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
    assert m["train_symbol"] == "BTC/USDC"
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
    r = client.get("/api/ml/registry/versions", params={"tf": "1h", "recipe": "r"})
    versions = r.json()["versions"]
    assert len(versions) == 2
    assert versions[0]["train_end"] == "2026-03-01T00:00:00"  # plus récent en premier


def test_registry_decisions_reflects_publish(client, tmp_path):
    _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-01-01T00:00:00")
    r = client.get("/api/ml/registry/decisions", params={"tf": "1h", "recipe": "r"})
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
        "tf": "1h", "recipe": "r", "version_id": old.version_id,
    })
    assert r.status_code == 200

    overview = client.get("/api/ml/registry").json()["models"][0]
    assert overview["pinned_version_id"] == old.version_id
    assert overview["active"]["version_id"] == old.version_id


def test_pin_unknown_version_404(client, tmp_path):
    _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-01-01T00:00:00")
    r = client.post("/api/ml/registry/pin", json={
        "tf": "1h", "recipe": "r", "version_id": "does-not-exist",
    })
    assert r.status_code == 404


def test_unpin_clears_it(client, tmp_path):
    old = _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-01-01T00:00:00")
    client.post("/api/ml/registry/pin", json={
        "tf": "1h", "recipe": "r", "version_id": old.version_id,
    })
    r = client.post("/api/ml/registry/unpin", json={"tf": "1h", "recipe": "r"})
    assert r.status_code == 200
    assert client.get("/api/ml/registry").json()["models"][0]["pinned_version_id"] is None


# ─────────────────────────────────────────────────────────────────────────────
#  Promote
# ─────────────────────────────────────────────────────────────────────────────
def test_promote_rejected_candidate(client, tmp_path):
    art = _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-01-01T00:00:00", decision="keep")
    r = client.post("/api/ml/registry/promote", json={
        "tf": "1h", "recipe": "r",
        "version_id": art.version_id, "decision": "manual",
    })
    assert r.status_code == 200
    overview = client.get("/api/ml/registry").json()["models"][0]
    assert overview["active"]["version_id"] == art.version_id


def test_promote_invalid_decision_400(client, tmp_path):
    art = _publish(tmp_path, "BTC/USDC", "1h", "r", "2026-01-01T00:00:00")
    r = client.post("/api/ml/registry/promote", json={
        "tf": "1h", "recipe": "r",
        "version_id": art.version_id, "decision": "bogus",
    })
    assert r.status_code == 400


def test_promote_unknown_version_404(client, tmp_path):
    r = client.post("/api/ml/registry/promote", json={
        "tf": "1h", "recipe": "r", "version_id": "nope",
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
        # Les diagnostics traversent bien le job asynchrone jusqu'à l'UI :
        # c'est tout ce que la page « Modèles » a pour afficher les top
        # features d'un dry-run, qui n'écrit aucune version au registre.
        tm = job["result"].get("train_meta") or {}
        assert tm.get("feature_importance_amp"), job["result"]
    finally:
        candle_store_mod.get_store.set(None)


# ─────────────────────────────────────────────────────────────────────────────
#  Résolution du nom envoyé par l'UI (recette vs stratégie) et du timeframe
# ─────────────────────────────────────────────────────────────────────────────
def test_train_accepts_a_recipe_name(client, monkeypatch):
    """La page « Modèles » est indexée par RECETTE : recopier `dyn_threshold_v1`
    dans le champ « Stratégie » donnait « Stratégie inconnue » alors qu'une
    seule stratégie déclare cette recette. On la résout au lieu de refuser."""
    seen = {}

    def _fake_start(strategy, symbol, tf, **kw):
        seen.update(strategy=strategy, symbol=symbol, tf=tf)
        return "job-1"

    import app.engine.ml_jobs as ml_jobs
    monkeypatch.setattr(ml_jobs, "start_train_job", _fake_start)

    r = client.post("/api/ml/train", json={"strategy": "dyn_threshold_v1", "tf": "1h"})
    assert r.status_code == 200, r.text
    assert seen["strategy"] == "ml_dynamic_threshold"


def test_train_ambiguous_recipe_400_lists_candidates(client):
    """`omnibus_v4_multi` est déclarée par v11 ET v12 : refuser, mais en disant
    entre quoi choisir — deviner publierait sous une lignée non demandée."""
    r = client.post("/api/ml/train", json={"strategy": "omnibus_v4_multi", "tf": "1h"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "opus_omnibus_v11" in detail and "opus_omnibus_v12" in detail


def test_train_unknown_tf_400(client):
    """« 15min » pour « 15m » partait en job de fond et échouait une minute
    plus tard sur un « aucune donnée en cache » qui accusait le cache."""
    r = client.post("/api/ml/train", json={"strategy": "opus_omnibus_v11", "tf": "15min"})
    assert r.status_code == 400
    assert "15m" in r.json()["detail"]


def test_sweep_unknown_tf_400(client):
    r = client.post("/api/ml/sweep", json={"strategy": "opus_omnibus_v11",
                                           "tf": "15min", "windows": [5000]})
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
#  Étape C — entraîner par RECETTE, sans stratégie
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def recipes_visible(tmp_path):
    """``recipes/`` est résolu relativement à la CWD (comme ``models/`` et
    ``data/`` — convention du dépôt), or ``_isolate_registry`` déplace la CWD.
    On y rend l'arborescence de recettes visible plutôt que de contourner
    l'isolation du registre, qui protège le models/ réel."""
    import os
    import shutil
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(repo, "recipes")
    link = tmp_path / "recipes"
    if not link.exists():
        try:
            os.symlink(src, link)
        except OSError:
            # Windows refuse les liens symboliques sans privilège (WinError
            # 1314) hors mode développeur ou session admin. La copie donne le
            # même résultat ici : la fixture est en lecture seule et tmp_path
            # est jeté après le test.
            shutil.copytree(src, link)
    yield


def test_recipes_endpoint_reports_trainability(client, recipes_visible):
    r = client.get("/api/ml/recipes")
    assert r.status_code == 200
    by_name = {x["recipe"]: x for x in r.json()["recipes"]}
    assert by_name["omnibus_v4_multi"]["trainable"] is True
    assert by_name["omnibus_v4_multi"]["features_catalog"] == "v4_polars@1"
    assert by_name["dyn_threshold_v1"]["label_scheme"] == "vol_adaptive_dir"
    # proxy_indicators n'a pas d'artefact : non entraînable, avec la raison.
    assert by_name["proxy_indicators"]["trainable"] is False
    assert by_name["proxy_indicators"]["reason"]


def test_train_accepts_a_recipe_without_any_strategy(client, monkeypatch, recipes_visible):
    """Le but du chantier : la page « Modèles », indexée par recette, n'a plus
    à traduire vers un nom de stratégie."""
    seen = {}

    def _fake_start(strategy, symbol, tf, **kw):
        seen.update(strategy=strategy, recipe=kw.get("recipe"))
        return "job-r"

    import app.engine.ml_jobs as ml_jobs
    monkeypatch.setattr(ml_jobs, "start_train_job", _fake_start)

    r = client.post("/api/ml/train", json={"recipe": "omnibus_v4_multi", "tf": "1h"})
    assert r.status_code == 200, r.text
    assert seen["recipe"] == "omnibus_v4_multi"
    assert not seen["strategy"]


def test_train_rejects_an_untrainable_recipe_with_its_reason(client, recipes_visible):
    """Répondre tout de suite plutôt que de laisser découvrir l'échec une
    minute plus tard dans un statut de job."""
    r = client.post("/api/ml/train", json={"recipe": "proxy_indicators", "tf": "1h"})
    assert r.status_code == 400
    assert "proxy" in r.json()["detail"]


def test_train_rejects_an_unknown_recipe(client):
    r = client.post("/api/ml/train", json={"recipe": "nexiste_pas", "tf": "1h"})
    assert r.status_code == 400
    assert "inconnue" in r.json()["detail"]


def test_train_without_strategy_nor_recipe_is_refused(client):
    r = client.post("/api/ml/train", json={"tf": "1h"})
    assert r.status_code == 400


def test_versioning_audit_summary_expose_des_compteurs_pas_des_chemins(client, tmp_path):
    """`/api/ml/versioning/audit` : `summary` ne contient que des nombres.

    Régression : `migration_check()` renvoie `without_hash` et `incompatible`
    sous forme de LISTES de chemins. Le `summary` les recopiait telles quelles,
    alors que l'UI (`ml-versioning-audit.tsx`) les traite comme des compteurs —
    elle affichait donc un chemin de fichier à la place d'un nombre, et le test
    `incompatible > 0` était toujours faux (comparaison sur un tableau JS).
    """
    models = tmp_path / "models"
    models.mkdir()
    # Un modèle avec hash, un sans → 1/2 = 50% de couverture.
    (models / "avec.meta.json").write_text('{"features_hash": "abc123"}', encoding="utf-8")
    (models / "sans.meta.json").write_text('{}', encoding="utf-8")

    r = client.get("/api/ml/versioning/audit")
    assert r.status_code == 200
    payload = r.json()

    summary = payload["summary"]
    for key in ("total", "with_hash", "without_hash", "incompatible", "coverage_pct"):
        assert isinstance(summary[key], (int, float)), f"{key} doit être un nombre"
    assert summary["total"] == 2
    assert summary["with_hash"] == 1
    assert summary["without_hash"] == 1
    assert summary["coverage_pct"] == 50.0

    # Le détail (chemins) reste disponible sous `audit`, pour savoir quoi
    # re-entraîner.
    assert isinstance(payload["audit"]["without_hash"], list)
    assert len(payload["audit"]["without_hash"]) == 1


def test_versioning_audit_sans_modele_ne_divise_pas_par_zero(client):
    r = client.get("/api/ml/versioning/audit")
    assert r.status_code == 200
    assert r.json()["summary"] == {
        "total": 0, "with_hash": 0, "without_hash": 0,
        "incompatible": 0, "coverage_pct": 0.0,
    }


# ── P1-1 : verdict de fuite temporelle ──────────────────────────────────────

def _artefact(train_start, train_end, version="v1"):
    class _A:
        pass
    a = _A()
    a.version_id = version
    a.train_start = train_start
    a.train_end = train_end
    return a


def test_overlaps_distingue_fuite_posterieur_et_valide(client, monkeypatch):
    """`overlaps()` ne teste qu'une intersection de fenêtres.

    Un modèle entraîné ENTIÈREMENT APRÈS la fenêtre évaluée en sort donc « sans
    chevauchement » — alors que c'est le look-ahead le plus extrême : il
    n'existait pas à la date testée. La route ajoute un `verdict` qui sépare les
    trois situations, sans quoi l'UI afficherait « causalement valide » sur le
    pire des cas.
    """
    import app.ml.model_registry as registry

    cas = [
        # (train_start, train_end, fenêtre, verdict attendu)
        ("2023-01-01", "2024-03-01", ("2024-01-01", "2024-06-01"), "leak"),
        ("2026-01-01", "2026-07-01", ("2024-01-01", "2024-06-01"), "posterior"),
        ("2022-01-01", "2023-06-01", ("2024-01-01", "2024-06-01"), "ok"),
        (None, None, ("2024-01-01", "2024-06-01"), "unknown"),
    ]
    for train_start, train_end, (ws, we), attendu in cas:
        monkeypatch.setattr(registry, "latest_promoted",
                            lambda tf, recipe, ts=train_start, te=train_end: _artefact(ts, te))
        r = client.get("/api/ml/registry/overlaps", params={
            "tf": "1h", "recipe": "omnibus_v4_multi",
            "window_start": ws, "window_end": we,
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["verdict"] == attendu, (
            f"{train_start}..{train_end} → {d['verdict']} "
            f"(attendu {attendu}) : {d['reason']}"
        )


def test_overlaps_sans_artefact_actif(client, monkeypatch):
    import app.ml.model_registry as registry
    monkeypatch.setattr(registry, "latest_promoted", lambda tf, recipe: None)
    r = client.get("/api/ml/registry/overlaps",
                   params={"tf": "1h", "recipe": "x",
                           "window_start": "2024-01-01", "window_end": "2024-06-01"})
    assert r.status_code == 200
    assert r.json()["active_version"] is None
