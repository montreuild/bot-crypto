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

def test_le_verdict_block_annule_la_promotion():
    """Le cœur du constat : sans ce câblage, un modèle refusé était publié."""
    import app.ml.policy as policy

    src = inspect.getsource(policy.maybe_refresh)
    assert 'decision="keep"' in src, (
        "le verdict block doit ramener gate.decision à 'keep' ; sans cela il "
        "n'alimente qu'un log et le modèle est publié quand même"
    )
    # Le remplacement doit précéder la publication au registre, sinon le
    # registre enregistre une promotion qui n'a pas eu lieu.
    i_block = src.index('decision="keep"')
    i_publish = src.index("registry.publish(")
    assert i_block < i_publish, (
        "l'annulation doit intervenir AVANT registry.publish"
    )


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
