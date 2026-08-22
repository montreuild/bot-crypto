"""DETTE-04b — le contrat observable de `_run_one_job`, avant découpage.

Un découpage précédent (`_manage_open_position`) avait déplacé la comptabilité
des frais et introduit FIN-01 et FIN-02 : le code était plus lisible et faux.
Ces tests pinnent ce que le job FAIT — statut final, écriture de config, trace
d'audit, ordre d'évaluation des gates — pour que le découpage soit vérifiable
autrement qu'à la lecture.

Ils sont écrits AVANT le découpage et doivent passer des deux côtés.
"""
import threading

import polars as pl
import pytest

import app.engine.auto_optimizer as ao
import app.engine.opt_jobs as opt_jobs

# ── Harnais ──────────────────────────────────────────────────────────────────

def _df(n=300):
    return pl.DataFrame({
        "time": list(range(n)),
        "open": [100.0] * n, "high": [101.0] * n,
        "low": [99.0] * n, "close": [100.0] * n, "volume": [1.0] * n,
    })


RESULTAT = {
    "best_params": {"p": 1},
    "best_oos_score": 0.8,
    "best_oos_trades": 20,
    "best_oos_pnl": 100.0,
    "best_oos_wr": 60.0,
    "best_oos_sharpe": 1.5,
    "n_trials": 40,
}


class _OptimiseurFactice:
    """Rend un résultat figé sans toucher au moteur de backtest."""

    param_space = {"p": [1, 2]}
    resultat = RESULTAT

    def __init__(self, **kw):
        self.kw = kw

    def bayesian_search(self, *a, **k):
        return dict(self.resultat)

    grid_search = random_search = optimize_two_phase = bayesian_search


@pytest.fixture
def journal(monkeypatch):
    """Neutralise tout ce qui sort du process et enregistre les effets."""
    effets = {"applied": [], "audits": [], "holdout": None, "ordre": []}

    monkeypatch.setattr(ao, "StrategyOptimizer", _OptimiseurFactice)
    monkeypatch.setattr(ao, "apply_best_params",
                        lambda *a, **k: (effets["applied"].append((a, k)), True)[1])
    monkeypatch.setattr(ao, "record_optimizer_audit",
                        lambda *a, **k: effets["audits"].append(a))
    monkeypatch.setattr(ao, "_is_ml_strategy", lambda _n: False)
    monkeypatch.setattr(ao, "_estimate_job_bytes", lambda *a, **k: 0)
    monkeypatch.setattr(ao, "_acquire_mem_slot", lambda *a, **k: True)
    monkeypatch.setattr(ao, "_release_mem_slot", lambda *a, **k: None)

    def _baseline_factice(strategy_name, cfg, df, symbol, timeframe=None):
        effets["holdout"] = len(df)
        effets["ordre"].append("holdout")
        return {"trades": 20, "pnl": 100.0, "wr": 60.0, "sharpe": 1.5,
                "dd": -5.0, "profit_factor": 2.0, "expectancy": 5.0}

    monkeypatch.setattr(ao, "_run_baseline", _baseline_factice)
    # Les registres sont VIDÉS, pas remplacés : `auto_optimizer` les ré-exporte
    # par liaison de nom, donc rebinder laisserait le code lire les originaux.
    opt_jobs._jobs.clear()
    opt_jobs._cancel_flags.clear()
    yield effets
    opt_jobs._jobs.clear()
    opt_jobs._cancel_flags.clear()


def _optimiseur(tmp_path, **kw):
    cfg = {"optimizer": {"wf_gate": False}, "trading": {}}
    cfg["optimizer"].update(kw.pop("optimizer", {}))
    o = ao.AutoOptimizer(cfg, n_trials=40, **kw)
    o.config_path = str(tmp_path / "config.yaml")
    return o


def _lancer(o, journal, *, auto_apply=True, holdout=True, job_id="s@1h@BTC/USDC"):
    opt_jobs._jobs[job_id] = {"status": "queued", "started_at": 0.0,
                              "baseline": {"pnl": 50.0, "wr": 40.0, "sharpe": 1.0}}
    o._run_one_job(job_id, "supertrend_macd", "1h", _df(), _df(), "BTC/USDC",
                   auto_apply, df_recherche=_df(600), split=300,
                   df_holdout=_df(200) if holdout else None)
    return opt_jobs._jobs[job_id]


# ── Contrat ──────────────────────────────────────────────────────────────────

def test_le_job_termine_en_done_et_a_100_pourcent(journal, tmp_path):
    job = _lancer(_optimiseur(tmp_path), journal)
    assert job["status"] == "done"
    assert job["progress"] == 100
    assert job["finished_at"] > 0
    assert job["result"]["best_params"] == {"p": 1}


def test_le_gate_passe_ecrit_la_config_et_pas_la_trace_d_audit(journal, tmp_path):
    """« Non appliqué = non utilisé » : les deux chemins s'excluent."""
    job = _lancer(_optimiseur(tmp_path), journal)
    assert job["applied"] is True
    assert len(journal["applied"]) == 1
    assert journal["audits"] == []


def test_le_gate_refuse_trace_pour_audit_sans_ecrire_la_config(
        journal, monkeypatch, tmp_path):
    """Un paramétrage refusé ne doit pas devenir actif par `optimizer_results`.

    L'échantillon insuffisant est posé sur le HOLDOUT : c'est lui qui alimente
    le gate, la tranche de sélection est écrasée dès qu'il est exploitable.
    """
    monkeypatch.setattr(ao, "_run_baseline", lambda *a, **k: {
        "trades": 2, "pnl": 100.0, "wr": 60.0, "sharpe": 1.5, "dd": -5.0})
    job = _lancer(_optimiseur(tmp_path), journal)
    assert job["applied"] is False
    assert journal["applied"] == []
    assert len(journal["audits"]) == 1


def test_sans_auto_apply_on_ne_fait_que_tracer(journal, tmp_path):
    job = _lancer(_optimiseur(tmp_path), journal, auto_apply=False)
    assert job["applied"] is False
    assert journal["applied"] == []
    assert len(journal["audits"]) == 1


def test_sans_holdout_l_auto_apply_est_refuse(journal, tmp_path):
    """OPT-04 : le gate se prononce sur une tranche jamais vue, ou pas du tout."""
    job = _lancer(_optimiseur(tmp_path), journal, holdout=False)
    assert job["applied"] is False
    assert journal["applied"] == []


def test_le_gate_se_prononce_sur_le_holdout_pas_sur_la_selection(journal, tmp_path):
    job = _lancer(_optimiseur(tmp_path), journal)
    assert journal["holdout"] == 200, "le holdout n'a pas été mesuré"
    assert job["gate_source"] == "holdout"


def test_un_holdout_indisponible_retombe_sur_la_selection(journal, monkeypatch, tmp_path):
    monkeypatch.setattr(ao, "_run_baseline", lambda *a, **k: {})
    job = _lancer(_optimiseur(tmp_path), journal)
    assert job["gate_source"] == "selection"


def test_l_annulation_avant_le_creneau_laisse_le_job_cancelled(journal, tmp_path):
    ev = threading.Event()
    ev.set()
    opt_jobs._cancel_flags["j"] = ev
    opt_jobs._jobs["j"] = {"status": "queued", "started_at": 0.0}
    o = _optimiseur(tmp_path)
    o._run_one_job("j", "supertrend_macd", "1h", _df(), _df(), "BTC/USDC", True,
                   df_recherche=_df(600), split=300, df_holdout=_df(200))
    assert opt_jobs._jobs["j"]["status"] == "cancelled"
    assert journal["applied"] == []


def test_un_echec_global_de_recherche_marque_le_job_en_erreur(journal, tmp_path):
    _OptimiseurFactice.resultat = {"failed": True, "error": "OOM worker"}
    try:
        job = _lancer(_optimiseur(tmp_path), journal)
    finally:
        _OptimiseurFactice.resultat = RESULTAT
    assert job["status"] == "error"
    assert job["error"] == "OOM worker"


def test_une_exception_pendant_la_recherche_ne_laisse_pas_le_job_running(
        journal, monkeypatch, tmp_path):
    class _Explose(_OptimiseurFactice):
        def bayesian_search(self, *a, **k):
            raise RuntimeError("boum")
    monkeypatch.setattr(ao, "StrategyOptimizer", _Explose)
    job = _lancer(_optimiseur(tmp_path), journal)
    assert job["status"] == "error"
    assert "boum" in job["error"]


def test_le_creneau_cpu_est_toujours_rendu(journal, monkeypatch, tmp_path):
    """Le `finally` doit libérer même sur exception, sinon le cap CPU fuit."""
    class _Explose(_OptimiseurFactice):
        def bayesian_search(self, *a, **k):
            raise RuntimeError("boum")
    monkeypatch.setattr(ao, "StrategyOptimizer", _Explose)
    avant = ao._job_semaphore._value
    _lancer(_optimiseur(tmp_path), journal)
    assert ao._job_semaphore._value == avant


def test_le_callback_d_apply_est_appele_quand_la_config_est_ecrite(journal, tmp_path):
    vus = []
    o = _optimiseur(tmp_path, on_apply_callback=lambda n, p: vus.append((n, p)))
    _lancer(o, journal)
    assert vus == [("supertrend_macd", {"p": 1})]


def test_un_callback_qui_leve_ne_fait_pas_echouer_le_job(journal, tmp_path):
    def _boum(*_a):
        raise RuntimeError("callback KO")
    job = _lancer(_optimiseur(tmp_path, on_apply_callback=_boum), journal)
    assert job["status"] == "done"
    assert job["applied"] is True


def test_le_gate_walk_forward_bloque_meme_quand_la_qualite_passe(journal, tmp_path):
    """Les deux gates sont conjonctifs — l'un ne rattrape pas l'autre."""
    o = _optimiseur(tmp_path, optimizer={"wf_gate": True, "wf_min_consistency": 99.0})
    job = _lancer(o, journal)
    assert job["applied"] is False
    assert len(journal["audits"]) == 1
