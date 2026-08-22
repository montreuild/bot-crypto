"""LAB-B — ce que le serveur décide remonte à l'écran.

Le moteur reproportionne le budget d'essais et peut s'arrêter pour trois
raisons sans rapport. Il calculait déjà tout cela — `format_budget` construit
même une phrase destinée à l'opérateur, sa docstring le dit — et l'envoyait
dans le log seul. « 200 essais sur 400 » après en avoir demandé 60 restait
indéchiffrable.
"""
import pytest
from starlette.testclient import TestClient

from app.api import state
from app.api.helpers import verify_api_key
from app.api.main import app
from app.engine.opt_budget import effective_n_trials
from app.engine.optimizer_search import PARAM_SPACES


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(state, "cfg", {"optimizer": {"trials_per_param": 15,
                                                     "max_trials": 400}})
    app.dependency_overrides[verify_api_key] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_api_key, None)


def test_la_route_rend_la_fourchette_reelle(client):
    r = client.get("/api/optimize/budget?n_trials=60&strategies=smart_money")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["demande"] == 60
    assert d["min"] == d["max"] == 400, d
    assert d["par_strategie"][0]["raison"] == "plafonné (max_trials)"


def test_l_ecart_annonce_est_reel_sur_le_parc(client):
    """Le constat chiffré, verrouillé : si la mise à l'échelle disparaissait,
    ce lot n'aurait plus lieu d'être et le test le dirait."""
    d = client.get("/api/optimize/budget?n_trials=60").json()
    assert d["max"] > d["demande"], "plus aucune stratégie au-dessus du demandé"
    au_dessus = sum(1 for x in d["par_strategie"] if x["n_trials_eff"] > 60)
    assert au_dessus >= 30, f"seulement {au_dessus} stratégies reproportionnées"


def test_une_selection_vide_couvre_tout_le_parc(client):
    d = client.get("/api/optimize/budget?n_trials=60&strategies=").json()
    assert len(d["par_strategie"]) == len(PARAM_SPACES)


def test_un_nom_inconnu_est_ignore_sans_erreur(client):
    d = client.get("/api/optimize/budget?n_trials=60&strategies=nexiste_pas").json()
    assert d["par_strategie"] == []
    assert d["min"] == d["max"] == 0


def test_la_route_dit_la_meme_chose_que_le_moteur(client):
    """Une formule recopiée dérive ; celle-ci est la seule."""
    d = client.get("/api/optimize/budget?n_trials=60&strategies=breakout,trend").json()
    for ligne in d["par_strategie"]:
        attendu, _ = effective_n_trials(PARAM_SPACES[ligne["strategy"]], 60, state.cfg)
        assert ligne["n_trials_eff"] == attendu, ligne


def test_le_budget_est_consigne_meme_quand_il_est_respecte():
    """« demandé 60, tourné 60 » est une information, pas un non-événement."""
    import inspect

    from app.engine.auto_optimizer import AutoOptimizer

    src = inspect.getsource(AutoOptimizer._chercher)
    pose = src.index("_update_job(job_id, n_trials=n_trials_eff, n_trials_budget=budget)")
    garde = src.index("if n_trials_eff != self.n_trials:")
    assert pose < garde, "le budget n'est consigné que lorsqu'il diffère"


def test_le_flux_sse_transporte_le_budget():
    import inspect

    from app.api.routes import optimizer

    src = inspect.getsource(optimizer)
    debut = src.index("optimize/stream")
    assert '"n_trials_budget"' in src[debut:debut + 3000]


@pytest.mark.parametrize("attribut", ["stop_reason", "trials_failed"])
def test_le_resultat_porte_le_motif_d_arret(attribut):
    """LAB-05 : budget épuisé, arrêt anticipé et essais en échec tronquent
    tous le compteur. Sans ces champs, l'UI n'en distingue aucun."""
    import inspect

    from app.engine.opt_result import OptimizerResultMixin

    assert f'"{attribut}"' in inspect.getsource(OptimizerResultMixin._best_result)
