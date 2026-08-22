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

import app.engine.opt_baseline as opt_baseline
import app.engine.opt_jobs as opt_jobs
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
    monkeypatch.setattr(opt_jobs, "_jobs", {JOB_ID: job}, raising=False)
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


# ── OPT-01 / OPT-02 — le chemin MANUEL applique le même garde-fou que l'auto ──
# `auto_apply` est désactivé par défaut : la route ci-dessous est le chemin
# réellement emprunté. Elle ne transmettait ni `oos_dd`, ni `oos_pf`, ni
# `oos_expectancy`, si bien que le garde-fou de drawdown ajouté à
# `beats_baseline` et ses critères de qualité y étaient inertes.

BASELINE_RICHE = {"pnl": 50.0, "wr": 40.0, "sharpe": 1.0, "dd": 10.0,
                  "profit_factor": 1.5, "expectancy": 2.0}


def _job_avec_holdout(**holdout):
    """Job dont le gate se prononce sur le holdout, comme en production."""
    job = _job()
    job["baseline"] = BASELINE_RICHE
    job["gate_source"] = "holdout"
    job["holdout"] = {"trades": 12, "pnl": 80.0, "wr": 45.0, "sharpe": 1.2,
                      "dd": 10.0, "profit_factor": 2.0, "expectancy": 5.0}
    job["holdout"].update(holdout)
    return job


def test_le_drawdown_degrade_est_refuse_sur_la_route_manuelle(monkeypatch, tmp_path):
    """OPT-02 : un DD holdout de 80 % contre un baseline à 10 % passait."""
    spath, cfgpath = _fixture_config(tmp_path)
    client = _client(monkeypatch, tmp_path, _job_avec_holdout(dd=80.0))
    try:
        r = _appliquer(client, cfgpath)
        assert r.status_code == 409, r.text
        assert "drawdown" in r.json()["detail"].lower()
        assert _params_ecrits(spath) == {}
    finally:
        app.dependency_overrides.pop(verify_api_key, None)


def test_le_drawdown_degrade_reste_forcable(monkeypatch, tmp_path):
    """Le refus est un garde-fou, pas un verrou : `force=true` passe outre."""
    spath, cfgpath = _fixture_config(tmp_path)
    client = _client(monkeypatch, tmp_path, _job_avec_holdout(dd=80.0))
    try:
        r = _appliquer(client, cfgpath, force=True)
        assert r.status_code == 200, r.text
        assert _params_ecrits(spath)["1h"]["BTC/USDC"]["params"] == {"st_period": 10}
    finally:
        app.dependency_overrides.pop(verify_api_key, None)


def test_le_profit_factor_seul_suffit_a_la_qualite(monkeypatch, tmp_path):
    """OPT-01 : Sharpe en retrait mais profit factor et expectancy nettement
    meilleurs. La règle codée est « Sharpe OU expectancy OU profit factor » ;
    elle était inapplicable, le baseline ne portant pas les deux dernières
    clés et la route ne transmettant pas les valeurs OOS."""
    spath, cfgpath = _fixture_config(tmp_path)
    job = _job_avec_holdout(sharpe=0.99, wr=40.0, profit_factor=3.5,
                            expectancy=15.0)
    client = _client(monkeypatch, tmp_path, job)
    try:
        r = _appliquer(client, cfgpath)
        assert r.status_code == 200, r.text
        assert _params_ecrits(spath)["1h"]["BTC/USDC"]["params"] == {"st_period": 10}
    finally:
        app.dependency_overrides.pop(verify_api_key, None)


def test_run_baseline_publie_profit_factor_et_expectancy():
    """Contrat : `_run_baseline` alimente le baseline ET le holdout. Sans ces
    deux clés, les branches correspondantes de `beats_baseline` comparent
    systématiquement à None et ne peuvent jamais s'activer."""
    import inspect

    src = inspect.getsource(opt_baseline._run_baseline)
    assert '"profit_factor"' in src, "clé profit_factor absente du baseline"
    assert '"expectancy"' in src, "clé expectancy absente du baseline"


# ── OPT-03 — plafond ABSOLU de drawdown, ancré sur la limite du moteur live ──
# Le plafond relatif (+25 % sur le baseline) se laisse contourner par cliquet :
# le baseline EST le paramétrage déjà appliqué, donc chaque application décale
# la référence. Sans ancrage, la suite 10 → 12,5 → 15,6 → 19,5 → 24,4 % passe
# en quatre auto-applies dont aucun n'est refusé — et la dernière dépasse la
# limite que `RiskManager` fait respecter en production.

def test_le_cliquet_du_plafond_relatif_est_reel():
    """Documente le mécanisme que le plafond absolu vient arrêter."""
    from app.engine.opt_scoring import beats_baseline

    dd = 10.0
    passes = []
    for _ in range(4):
        suivant = dd * 1.25
        ok, _ = beats_baseline(20, 300.0, 60.0, 2.0,
                               {"pnl": 50.0, "wr": 40.0, "sharpe": 1.0, "dd": dd},
                               oos_dd=suivant)
        passes.append((round(dd, 1), round(suivant, 1), ok))
        dd = suivant
    assert all(ok for *_, ok in passes), passes
    assert passes[-1][1] > 20.0, (
        f"le cliquet doit franchir la limite live de 20 % : {passes}"
    )


def test_le_plafond_absolu_arrete_le_cliquet():
    from app.engine.opt_scoring import beats_baseline

    base = {"pnl": 50.0, "wr": 40.0, "sharpe": 1.0, "dd": 19.5}
    ok, raison = beats_baseline(20, 300.0, 60.0, 2.0, base,
                                oos_dd=24.4, dd_max_abs=20.0)
    assert ok is False
    assert "plafond absolu" in raison, raison


def test_le_plafond_absolu_se_deduit_de_la_config_live():
    """Même source que `RiskManager.global_dd_limit` : une fraction sous
    `trading`, convertie en pourcentage."""
    from app.engine.opt_scoring import resolve_dd_max_abs

    assert resolve_dd_max_abs({"trading": {"max_drawdown_global": 0.2}}) == 20.0
    assert resolve_dd_max_abs({}) == 20.0                 # même défaut que le gate
    assert resolve_dd_max_abs({"trading": {"max_drawdown_global": 0}}) is None


def test_la_route_manuelle_applique_le_plafond_absolu(monkeypatch, tmp_path):
    """Un drawdown holdout sous le +25 % relatif mais au-delà du plafond
    absolu doit être refusé — c'est le cas que le cliquet fabrique."""
    spath, cfgpath = _fixture_config(tmp_path)
    job = _job_avec_holdout(dd=24.0)
    job["baseline"] = dict(BASELINE_RICHE, dd=19.5)   # +23 % : passe le relatif
    client = _client(monkeypatch, tmp_path, job)
    from app.api import state
    monkeypatch.setattr(state, "cfg",
                        {"trading": {"max_drawdown_global": 0.2}}, raising=False)
    try:
        r = _appliquer(client, cfgpath)
        assert r.status_code == 409, r.text
        assert "plafond absolu" in r.json()["detail"], r.json()["detail"]
        assert _params_ecrits(spath) == {}
    finally:
        app.dependency_overrides.pop(verify_api_key, None)
