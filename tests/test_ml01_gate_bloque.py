"""ML-01 — le verdict `block` du garde-fou de sur-apprentissage bloque vraiment.

`validate_model_quality` refuse un modèle dont l'intervalle de confiance sur
l'AUC recouvre le hasard. Ce refus n'alimentait qu'un `logger.warning` et des
métadonnées de registre : `gate.decision` restait `promote`, le modèle était
publié et servi. Le renvoi vers `gate_auc_floor` ne remplaçait pas ce refus —
`auc_floor` compare une AUC ponctuelle à un plancher, `validate_model_quality`
teste si son IC recouvre 0,50, ce qui dépend de `n_oos_samples`.
"""
import inspect

import pytest

from app.ml.overfitting_gate import (
    AUC_RANDOM,
    auc_hanley_ci_low,
    validate_model_quality,
)

# ── La borne de confiance ────────────────────────────────────────────────────

def test_une_auc_faible_sur_peu_d_echantillons_est_indistinguable_du_hasard():
    """0,58 sur 60 barres : la borne basse passe sous 0,50."""
    assert auc_hanley_ci_low(0.58, 60) < AUC_RANDOM


def test_la_meme_auc_sur_beaucoup_d_echantillons_tient():
    assert auc_hanley_ci_low(0.58, 5000) > AUC_RANDOM


def test_le_verdict_block_est_bien_emis():
    v = validate_model_quality(auc_oos=0.58, strategy_name="t", n_oos_samples=60,
                               n_trials_optimization=1)
    assert v["level"] == "block"
    assert v["ok"] is False
    assert v["auc_ci_low"] < AUC_RANDOM


def test_un_modele_solide_n_est_pas_bloque():
    v = validate_model_quality(auc_oos=0.68, strategy_name="t",
                               n_oos_samples=5000, n_trials_optimization=1)
    assert v["level"] in ("good", "strong")
    assert v["ok"] is True


# ── Le câblage : le verdict change la décision ───────────────────────────────

@pytest.mark.slow
@pytest.mark.parametrize("bloquant,decision_attendue,publie", [
    (True, "keep", False),
    (False, ("initial", "promote"), True),
])
def test_un_verdict_block_empeche_la_publication(tmp_path, monkeypatch,
                                                 bloquant, decision_attendue,
                                                 publie):
    """Le cœur du constat, vérifié sur le COMPORTEMENT et non sur le source.

    Une première version cherchait `decision="keep"` dans le source de
    `maybe_refresh` — un grep, qui ne dit rien de ce que le code fait. C'est
    exactement le reproche que `test_optimizer_apply_route` adresse à l'ancien
    `assert "force" in src`. On appelle donc la vraie fonction et on regarde
    le registre.

    Le verdict est forcé plutôt que provoqué par les données : obtenir une AUC
    dans `[0,55 ; …]` avec un IC assez large sur une série synthétique n'est
    pas reproductible, et ce qu'on teste ici est le CÂBLAGE — un refus
    change-t-il la décision ? La qualité du verdict lui-même est couverte par
    les tests de `validate_model_quality` ci-dessus.
    """
    import app.ml.model_registry as registry
    import app.ml.overfitting_gate as og
    from app.ml.policy import maybe_refresh
    from tests.test_ml_policy import _make_synthetic_ohlcv, _v11_strategy

    monkeypatch.setattr(og, "validate_model_quality", lambda **kw: {
        "ok": False, "level": "block", "auc": kw.get("auc_oos"),
        "auc_ci_low": 0.42,
        "reason": "AUC OOS 0.580 (IC95 bas 0.420) indistinguable du hasard",
        "min_significant_samples": 10,
    })

    base = str(tmp_path / "models")
    res = maybe_refresh(
        _v11_strategy(), "BTC/USDC", "1h", _make_synthetic_ohlcv(1400, seed=11),
        params={"n_estimators": 30, "num_leaves": 7, "warmup_bars": 250,
                "gate_holdout_bars": 250, "gate_min_window_bars": 700,
                "gate_auc_floor": 0.0, "gate_overfitting_block": bloquant},
        recipe="opus_omnibus_v11", source="test", base_dir=base)

    if isinstance(decision_attendue, tuple):
        assert res["decision"] in decision_attendue, res["reason"]
    else:
        assert res["decision"] == decision_attendue, res["reason"]
        assert "overfitting_gate" in res["reason"], res["reason"]

    servi = registry.latest_promoted("1h", "opus_omnibus_v11", base_dir=base)
    assert (servi is not None) is publie, (
        "aucun modèle ne doit être servi après un refus du garde-fou"
        if not publie else "le modèle devait être publié"
    )


def test_le_test_d_intervalle_ne_couvre_que_les_auc_au_dessus_de_faible():
    """Limite connue, à ne pas confondre avec un défaut de câblage.

    Sous `AUC_WEAK` (0,55), `validate_model_quality` sort en `warn` AVANT
    d'évaluer l'intervalle de confiance. Une AUC de 0,535 sur 250 barres — dont
    la borne basse vaut pourtant ~0,46 — n'est donc pas `block`, et un
    `gate_auc_floor` bas suffit à la promouvoir. C'est le pendant du périmètre
    assumé ci-dessous : élargir demanderait de revoir l'ordre des branches
    d'`overfitting_gate`, pas le câblage de `policy`.
    """
    assert auc_hanley_ci_low(0.535, 250) < AUC_RANDOM
    v = validate_model_quality(auc_oos=0.535, strategy_name="t",
                               n_oos_samples=250, n_trials_optimization=1)
    assert v["level"] == "warn" and v["ok"] is False


def test_le_refus_precede_la_publication():
    """Garde-fou d'ordre : annuler APRÈS `registry.publish` laisserait le
    registre enregistrer une promotion qui n'a pas eu lieu."""
    import app.ml.policy as policy

    src = inspect.getsource(policy.maybe_refresh)
    assert src.index('decision="keep"') < src.index("registry.publish(")


def test_gate_result_supporte_le_remplacement():
    """`replace` exige une dataclass : verrouille le contrat utilisé."""
    from dataclasses import replace

    from app.ml.policy import GateResult

    g = GateResult(decision="promote", reason="ok")
    g2 = replace(g, decision="keep", reason="overfitting_gate: bloqué")
    assert g2.decision == "keep"
    assert g.decision == "promote", "le remplacement ne doit pas muter l'original"


@pytest.mark.parametrize("auc,n,niveau,ok", [
    (0.58, 60, "block", False),     # IC recouvre le hasard → refus
    (0.52, 5000, "warn", False),    # sous AUC_WEAK
    (0.56, 5000, "good", True),
    (0.68, 5000, "good", True),
    (0.75, 5000, "strong", True),
])
def test_niveaux_et_verdicts(auc, n, niveau, ok):
    v = validate_model_quality(auc_oos=auc, strategy_name="t", n_oos_samples=n,
                               n_trials_optimization=1)
    assert (v["level"], v["ok"]) == (niveau, ok), v["reason"]


def test_seul_block_annule_la_promotion_pas_warn():
    """Périmètre assumé du câblage ML-01.

    `level='warn'` avec `ok=False` (AUC sous 0,55) n'annule PAS la promotion :
    ce cas relève de `decide_gate` et de son `auc_floor`, réglable par
    l'opérateur. Seul `block` — l'IC qui recouvre le hasard, qu'aucun réglage
    d'`auc_floor` ne sait exprimer — force le refus.

    À noter : `overfitting_gate` journalise « MODÈLE BLOQUÉ » dans les deux
    cas alors qu'il ne renvoie `block` que dans le premier. Incohérence de
    libellé du module, pas du câblage — élargir le refus à `warn` changerait
    le comportement de promotion bien au-delà du constat ML-01.
    """
    faible = validate_model_quality(auc_oos=0.52, strategy_name="t",
                                    n_oos_samples=5000, n_trials_optimization=1)
    assert faible["level"] == "warn" and faible["ok"] is False
