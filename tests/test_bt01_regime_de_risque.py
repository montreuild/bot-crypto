"""BT-01 — le baseline et les folds walk-forward tournent au même régime.

Le gate d'auto-apply confronte `_run_baseline` à des folds
`WalkForwardAnalyzer`. `_run_baseline` figeait `realistic_risk=True` tandis que
le walk-forward le lisait dans `cfg["backtest"]["realistic_risk"]`, clé absente
de `config.yaml` : le baseline était mesuré circuit breakers actifs et les
folds sans. Un paramétrage dont la rentabilité vient de séquences que les
circuit breakers auraient coupées franchissait alors le gate, puis décevait en
live — qui, lui, les applique.
"""
import inspect

from app.core.is_oos import resolve_realistic_risk


def test_le_defaut_est_le_regime_realiste():
    """Le live applique ses circuit breakers : un walk-forward qui les ignore
    promet un comportement que le live ne reproduira pas."""
    assert resolve_realistic_risk(None) is True
    assert resolve_realistic_risk({}) is True
    assert resolve_realistic_risk({"backtest": {}}) is True


def test_la_desactivation_explicite_reste_possible():
    assert resolve_realistic_risk({"backtest": {"realistic_risk": False}}) is False


def test_baseline_et_walk_forward_resolvent_au_meme_endroit():
    """Garde-fou d'asymétrie : les deux doivent passer par le même résolveur,
    sinon une config explicite recréerait l'écart que ce correctif supprime."""
    import app.engine.auto_optimizer as ao
    import app.engine.walk_forward as wf

    src_baseline = inspect.getsource(ao._run_baseline)
    src_wf = inspect.getsource(wf.WalkForwardAnalyzer.run)
    assert "resolve_realistic_risk" in src_baseline, (
        "_run_baseline doit résoudre le régime, pas le figer"
    )
    assert "resolve_realistic_risk" in src_wf, (
        "le walk-forward doit utiliser le même résolveur que le baseline"
    )


def test_les_deux_cotes_donnent_la_meme_valeur_pour_une_config_donnee():
    for cfg in ({}, {"backtest": {}}, {"backtest": {"realistic_risk": False}},
                {"backtest": {"realistic_risk": True}}):
        assert resolve_realistic_risk(cfg) == resolve_realistic_risk(dict(cfg))
