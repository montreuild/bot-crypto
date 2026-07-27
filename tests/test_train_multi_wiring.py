"""ML-16 — ``train_multi`` câblé à l'API, aux jobs et à l'UI.

Le pooling était « testé mais appelé par personne » : ``recipe_trainer.
train_multi`` avait ses tests unitaires et aucun chemin depuis
``/api/ml/train``, la page « Modèles » ou le backfill actions. Ces tests
portent sur le CÂBLAGE — la mécanique du pooling elle-même reste couverte par
``test_recipe_trainer.py``.

Deux propriétés méritent une attention particulière parce qu'elles sont
faciles à casser sans que rien n'échoue bruyamment :

* **Le holdout est prélevé par symbole AVANT l'entraînement.** L'oublier
  donnerait des scores de complaisance : le gate jugerait le modèle sur des
  barres qu'il a vues. Rien ne planterait, les chiffres seraient simplement
  faux et flatteurs.
* **Un symbole écarté est rapporté, pas tu.** Sur actions, les historiques
  sont très inégaux ; un titre silencieusement absent du pool ferait croire à
  une couverture qui n'existe pas.
"""
import datetime as dt
import warnings

import numpy as np
import polars as pl
import pytest

warnings.filterwarnings("ignore")
pytest.importorskip("lightgbm")


def _series(n: int, seed: int, start: dt.datetime) -> pl.DataFrame:
    from app.core.indicators import precompute_df
    rng = np.random.RandomState(seed)
    times = [start + dt.timedelta(days=i) for i in range(n)]
    close = 100 * np.cumprod(1 + rng.normal(0.0002, 0.012, n))
    open_ = np.concatenate([[100.0], close[:-1]])
    return precompute_df(pl.DataFrame({
        "time": times, "open": open_, "close": close,
        "high": np.maximum(open_, close) * 1.004,
        "low": np.minimum(open_, close) * 0.996,
        "volume": rng.uniform(100, 1000, n),
    }))


# ─────────────────────────────────────────────────────────────────────────────
#  Runner
# ─────────────────────────────────────────────────────────────────────────────
class TestRunner:
    @pytest.fixture
    def cached(self, monkeypatch):
        """Remplace le cache Parquet par des séries en mémoire."""
        frames = {
            "A.PA": _series(2400, 1, dt.datetime(2017, 1, 1)),
            "B.PA": _series(2400, 2, dt.datetime(2017, 1, 1)),
            "C.PA": _series(300, 3, dt.datetime(2024, 1, 1)),   # trop court
        }

        def fake_load(symbol, tf, *, as_of=None, window_bars=None):
            return frames.get(symbol)

        monkeypatch.setattr("app.ml.train_runner.load_offline_ohlcv", fake_load)
        return frames

    def test_pooling_produces_a_model_and_stays_dry(self, cached, tmp_path):
        from app.ml.train_runner import train_multi_and_publish
        out = train_multi_and_publish("omnibus_v4_multi", ["A.PA", "B.PA"], "1d",
                                      publish=False, base_dir=str(tmp_path))
        assert out["decision"].startswith("dry_run_would_")
        assert sorted(out["pooled_symbols"]) == ["A.PA", "B.PA"]
        assert out["train_meta"]["n_symbols"] == 2
        assert not list(tmp_path.iterdir()), "un dry-run ne doit rien écrire"

    def test_a_short_symbol_is_skipped_with_its_reason(self, cached, tmp_path):
        """Écarté, pas fatal — et le motif remonte jusqu'à l'appelant."""
        from app.ml.train_runner import train_multi_and_publish
        out = train_multi_and_publish("omnibus_v4_multi", ["A.PA", "B.PA", "C.PA"],
                                      "1d", publish=False, base_dir=str(tmp_path))
        assert "C.PA" in out["skipped_symbols"]
        assert "trop court" in out["skipped_symbols"]["C.PA"]
        assert "C.PA" not in out["pooled_symbols"]

    def test_holdout_is_withheld_from_training(self, cached, monkeypatch, tmp_path):
        """La propriété qui rend le score honnête. On intercepte ce que
        ``train_multi`` reçoit : ses frames doivent être plus courts que
        l'historique complet, d'exactement ``holdout_bars``."""
        seen = {}
        import app.ml.recipe_trainer as rt
        real = rt.train_multi

        def spy(recipe, frames, tf, **kw):
            seen.update({s: len(f) for s, f in frames.items()})
            return real(recipe, frames, tf, **kw)

        monkeypatch.setattr(rt, "train_multi", spy)
        from app.ml.train_runner import train_multi_and_publish
        train_multi_and_publish("omnibus_v4_multi", ["A.PA", "B.PA"], "1d",
                                publish=False, base_dir=str(tmp_path))
        # Attendu DÉRIVÉ de la recette, pas codé en dur : `gate.holdout_bars`
        # est un réglage d'exploitation qui bouge (1500 → 1400 en 2026-07-28).
        # Figer 900 ici faisait échouer ce test à chaque arbitrage sur le gate,
        # alors que ce qu'il vérifie — la retenue du holdout — est invariant.
        import app.ml.policy as policy
        from app.ml.recipe import load_recipe
        r = load_recipe("omnibus_v4_multi")
        gc_ = policy.GateConfig.from_params({**r.gate_spec(), **r.gate_params()})
        expected = 2400 - gc_.holdout_bars
        assert seen == {"A.PA": expected, "B.PA": expected}

    def test_empty_symbol_list_is_refused_clearly(self, tmp_path):
        from app.ml.train_runner import train_multi_and_publish
        out = train_multi_and_publish("omnibus_v4_multi", [], "1d",
                                      base_dir=str(tmp_path))
        assert out["decision"] == "failed"
        assert "aucun symbole" in out["reason"]

    def test_publish_records_the_pool_in_the_registry(self, cached, tmp_path):
        """La provenance doit permettre de savoir, six mois plus tard, sur
        quels titres le modèle actif a été fabriqué."""
        import app.ml.model_registry as registry
        from app.ml.train_runner import train_multi_and_publish
        out = train_multi_and_publish("omnibus_v4_multi", ["A.PA", "B.PA"], "1d",
                                      publish=True, base_dir=str(tmp_path))
        assert out["decision"] in ("refresh", "initial", "keep")
        assert out["published_version"]
        versions = registry.list_versions("1d", "omnibus_v4_multi",
                                          base_dir=str(tmp_path))
        assert versions
        d = versions[-1].to_dict()
        # ``train_symbol`` est un champ d'AFFICHAGE et ne peut pas porter une
        # liste : la provenance complète voyage dans ``train_meta``, qui est le
        # seul de ces deux champs que ``to_dict`` expose à la page « Modèles ».
        assert d["train_symbol"] == "pooled:2"
        assert d["train_meta"]["pooled_symbols"] == ["A.PA", "B.PA"]
        assert d["train_meta"]["n_symbols"] == 2
        # ``recipe_cfg`` n'est PAS archivé par ``publish`` — il ne sert qu'à
        # calculer ``recipe_hash``. Y passer la composition du pool a donc un
        # effet voulu et un seul : deux pools de composition différente
        # produisent des lignées de versions distinctes.
        assert versions[-1].recipe_hash


# ─────────────────────────────────────────────────────────────────────────────
#  Jobs
# ─────────────────────────────────────────────────────────────────────────────
class TestJobs:
    def test_symbols_route_to_the_pooled_runner(self, monkeypatch):
        called = {}

        def fake(recipe, symbols, tf, **kw):
            called.update(recipe=recipe, symbols=symbols, tf=tf)
            return {"decision": "dry_run_would_initial"}

        monkeypatch.setattr("app.ml.train_runner.train_multi_and_publish", fake)
        import app.engine.ml_jobs as jobs
        job_id = jobs.start_train_job("", "IGNORÉ", "1d", recipe="omnibus_v4_multi",
                                      symbols=["A.PA", "B.PA"])
        for _ in range(100):
            if jobs.get_job(job_id)["status"] != "running":
                break
            import time
            time.sleep(0.05)
        assert called["symbols"] == ["A.PA", "B.PA"]
        assert jobs.get_job(job_id)["pooled_symbols"] == ["A.PA", "B.PA"]

    def test_without_symbols_the_single_symbol_path_is_kept(self, monkeypatch):
        """Non-régression : le mode existant ne doit pas basculer par erreur."""
        called = {}
        monkeypatch.setattr("app.ml.train_runner.train_recipe_and_publish",
                            lambda recipe, symbol, tf, **kw: called.update(
                                symbol=symbol) or {"decision": "x"})
        import app.engine.ml_jobs as jobs
        job_id = jobs.start_train_job("", "BTC/USDC", "1h", recipe="omnibus_v4_multi")
        for _ in range(100):
            if jobs.get_job(job_id)["status"] != "running":
                break
            import time
            time.sleep(0.05)
        assert called["symbol"] == "BTC/USDC"

    def test_duplicates_are_collapsed(self, monkeypatch):
        called = {}
        monkeypatch.setattr("app.ml.train_runner.train_multi_and_publish",
                            lambda recipe, symbols, tf, **kw: called.update(
                                symbols=symbols) or {"decision": "x"})
        import app.engine.ml_jobs as jobs
        job_id = jobs.start_train_job("", "x", "1d", recipe="omnibus_v4_multi",
                                      symbols=["A.PA", "A.PA", "B.PA", ""])
        for _ in range(100):
            if jobs.get_job(job_id)["status"] != "running":
                break
            import time
            time.sleep(0.05)
        assert called["symbols"] == ["A.PA", "B.PA"]


# ─────────────────────────────────────────────────────────────────────────────
#  API
# ─────────────────────────────────────────────────────────────────────────────
class TestApi:
    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient

        import app.api.helpers as helpers
        from app.api.main import app
        app.dependency_overrides[helpers.verify_api_key] = lambda: True
        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_pooled_training_requires_a_recipe(self, client):
        """Refusé à l'entrée plutôt que découvert une minute plus tard dans un
        statut de job — le chemin stratégie ne sait pas pooler."""
        r = client.post("/api/ml/train", json={
            "strategy": "opus_omnibus_v11", "tf": "1d",
            "symbols": ["A.PA", "B.PA"]})
        assert r.status_code == 400
        assert "recette" in r.json()["detail"]

    def test_symbols_reach_the_job(self, client, monkeypatch):
        seen = {}
        monkeypatch.setattr("app.engine.ml_jobs.start_train_job",
                            lambda *a, **kw: seen.update(kw) or "job1")
        r = client.post("/api/ml/train", json={
            "recipe": "omnibus_v4_multi", "tf": "1d",
            "symbols": ["A.PA", "B.PA"]})
        assert r.status_code == 200, r.text
        assert seen["symbols"] == ["A.PA", "B.PA"]

    def test_absent_symbols_stay_backward_compatible(self, client, monkeypatch):
        seen = {}
        monkeypatch.setattr("app.engine.ml_jobs.start_train_job",
                            lambda *a, **kw: seen.update(kw) or "job1")
        r = client.post("/api/ml/train", json={"recipe": "omnibus_v4_multi",
                                               "tf": "1h", "symbol": "BTC/USDC"})
        assert r.status_code == 200, r.text
        assert seen["symbols"] == []
