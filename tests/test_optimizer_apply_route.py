"""`POST /api/optimize/apply` — garde-fou, refus 409, et override `force=true`.

Ces tests remplacent ceux de `OptimizerResultApplier`, une classe supprimée
parce qu'elle n'était appelée par personne : ni la route, ni
`auto_optimizer._run_one_job`. Ils testaient donc un orchestrateur mort, pas le
chemin qui tourne réellement en production.

Le contrat vérifié est identique — gate passé → application, gate refusé → rien
d'écrit, `force=true` → application malgré le refus — mais il est désormais
exercé sur la route elle-même. Auparavant, la seule trace de `force` sur le vrai
chemin était un `assert "force" in src` dans `test_apply_guard.py` : un grep sur
le code source, qui ne dit rien du comportement.
"""
import yaml
from starlette.testclient import TestClient

import app.engine.auto_optimizer as auto_opt
from app.api.helpers import verify_api_key
from app.api.main import app

BASELINE = {"pnl": 50.0, "wr": 40.0, "sharpe": 1.0}
JOB_ID = "supertrend_macd@1h@BTC/USDC"


def _job(pnl=80.0, trades=12, wr=45.0, sharpe=1.2):
    """Job terminé, tel que `AutoOptimizer` le dépose dans le registre."""
    return {
        "status": "done",
        "strategy": "supertrend_macd",
        "timeframe": "1h",
        "symbol": "BTC/USDC",
        "baseline": BASELINE,
        # n_trials=1 : neutralise le gate Deflated Sharpe, qui a sa propre
        # batterie dans `test_deflated_sharpe_gate.py`. On isole ici le
        # garde-fou de base et le chemin `force`.
        "n_trials": 1,
        "result": {
            "best_params": {"st_period": 10},
            "best_oos_trades": trades, "best_oos_pnl": pnl,
            "best_oos_wr": wr, "best_oos_sharpe": sharpe,
            "best_oos_score": 0.8,
        },
    }


def _fixture_config(tmp_path):
    """Config + fichier de stratégie isolés, comme attendu par apply_best_params."""
    sdir = tmp_path / "strategies"
    sdir.mkdir()
    spath = sdir / "supertrend_macd.yaml"
    yaml.safe_dump({"params": {"st_period": 7}, "optimizer_results": {}}, spath.open("w"))
    cfgpath = tmp_path / "config.yaml"
    cfgpath.write_text("trading: {}\n")
    return spath, cfgpath


def _client(monkeypatch, tmp_path, job):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auto_opt, "_jobs", {JOB_ID: job}, raising=False)
    app.dependency_overrides[verify_api_key] = lambda: None
    return TestClient(app)


def _appliquer(client, cfgpath, force=False):
    return client.post(
        "/api/optimize/apply",
        params={"job_id": JOB_ID, "config_path": str(cfgpath), "force": force},
    )


def _params_ecrits(spath):
    data = yaml.safe_load(spath.open())
    return data.get("optimizer_results", {})


def test_applique_quand_le_gate_passe(monkeypatch, tmp_path):
    spath, cfgpath = _fixture_config(tmp_path)
    client = _client(monkeypatch, tmp_path, _job())
    try:
        r = _appliquer(client, cfgpath)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "applied"
        assert _params_ecrits(spath)["1h"]["BTC/USDC"]["params"] == {"st_period": 10}
    finally:
        app.dependency_overrides.pop(verify_api_key, None)


def test_refuse_en_409_et_n_ecrit_rien_quand_le_gate_echoue(monkeypatch, tmp_path):
    """Un refus doit être total : pas d'écriture partielle dans le YAML."""
    spath, cfgpath = _fixture_config(tmp_path)
    client = _client(monkeypatch, tmp_path, _job(pnl=-5.0))
    try:
        r = _appliquer(client, cfgpath)
        assert r.status_code == 409
        assert "PnL" in r.json()["detail"]
        assert _params_ecrits(spath) == {}, "un refus ne doit laisser aucune trace"
    finally:
        app.dependency_overrides.pop(verify_api_key, None)


def test_force_outrepasse_le_gate(monkeypatch, tmp_path):
    """`force=true` est l'échappatoire assumée exposée par l'UI (ConfirmDialog).

    Elle n'était couverte sur le chemin réel par aucun test de comportement.
    """
    spath, cfgpath = _fixture_config(tmp_path)
    client = _client(monkeypatch, tmp_path, _job(pnl=-5.0))
    try:
        r = _appliquer(client, cfgpath, force=True)
        assert r.status_code == 200, r.text
        assert _params_ecrits(spath)["1h"]["BTC/USDC"]["params"] == {"st_period": 10}
    finally:
        app.dependency_overrides.pop(verify_api_key, None)


def test_job_sans_best_params_est_refuse(monkeypatch, tmp_path):
    spath, cfgpath = _fixture_config(tmp_path)
    job = _job()
    job["result"]["best_params"] = {}
    client = _client(monkeypatch, tmp_path, job)
    try:
        r = _appliquer(client, cfgpath)
        assert r.status_code == 400
        assert _params_ecrits(spath) == {}
    finally:
        app.dependency_overrides.pop(verify_api_key, None)


def test_le_gate_manuel_decide_sur_le_holdout(monkeypatch, tmp_path):
    """N-02 : le bouton Appliquer juge le holdout, pas la tranche de sélection.

    Ici la sélection passerait (PnL +80) mais le holdout est perdant :
    le chemin manuel doit refuser, comme l'auto-apply.
    """
    spath, cfgpath = _fixture_config(tmp_path)
    job = _job(pnl=80.0, trades=12, wr=45.0, sharpe=1.2)
    job["holdout"] = {"trades": 12, "pnl": -5.0, "wr": 30.0, "sharpe": -0.4}
    job["gate_source"] = "holdout"
    client = _client(monkeypatch, tmp_path, job)
    try:
        r = _appliquer(client, cfgpath)
        assert r.status_code == 409
        assert "PnL" in r.json()["detail"]
        assert _params_ecrits(spath) == {}
    finally:
        app.dependency_overrides.pop(verify_api_key, None)


def test_le_gate_manuel_applique_quand_le_holdout_passe(monkeypatch, tmp_path):
    """Symétrique : sélection perdante, holdout gagnant → apply OK + gate_source."""
    spath, cfgpath = _fixture_config(tmp_path)
    job = _job(pnl=-20.0, trades=12, wr=30.0, sharpe=-0.5)
    job["holdout"] = {"trades": 12, "pnl": 80.0, "wr": 45.0, "sharpe": 1.2}
    job["gate_source"] = "holdout"
    client = _client(monkeypatch, tmp_path, job)
    try:
        r = _appliquer(client, cfgpath)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "applied"
        assert body["gate_source"] == "holdout"
        assert _params_ecrits(spath)["1h"]["BTC/USDC"]["params"] == {"st_period": 10}
    finally:
        app.dependency_overrides.pop(verify_api_key, None)


def test_job_inconnu_ou_non_termine(monkeypatch, tmp_path):
    _, cfgpath = _fixture_config(tmp_path)
    job = _job()
    job["status"] = "running"
    client = _client(monkeypatch, tmp_path, job)
    try:
        assert _appliquer(client, cfgpath).status_code == 400
        r = client.post("/api/optimize/apply",
                        params={"job_id": "inconnu", "config_path": str(cfgpath)})
        assert r.status_code == 404
    finally:
        app.dependency_overrides.pop(verify_api_key, None)
