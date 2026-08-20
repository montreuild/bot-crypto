"""ML-02 — la borne de confiance sur l'AUC tient compte du déséquilibre.

La précision d'une AUC ne dépend pas du nombre de lignes mais des effectifs de
CHAQUE classe. `auc_hanley_ci_low` supposait `n1 = n0 = n/2` alors que `n`
était un nombre de barres : sur des labels construits par seuil d'amplitude —
le cas normal ici — cette hypothèse surestime `n1·n0`, sous-estime la variance,
et remonte artificiellement la borne basse. Le garde-fou était donc trop
permissif exactement là où il aurait dû se déclencher.
"""
import numpy as np
import pytest

from app.ml.overfitting_gate import (
    AUC_RANDOM,
    MIN_PER_CLASS,
    auc_hanley_ci_low,
    validate_model_quality,
)
from app.ml.scoring import rank_auc, rank_auc_counts

# ── La borne elle-même ──────────────────────────────────────────────────────

@pytest.mark.parametrize("n,pos,auc", [
    (200, 0.10, 0.58),
    (200, 0.05, 0.58),
    (200, 0.10, 0.62),
    (400, 0.08, 0.60),
    (100, 0.15, 0.65),
    (1000, 0.05, 0.56),
])
def test_l_hypothese_d_equilibre_est_permissive(n, pos, auc):
    """Elle annonce une borne au-dessus du hasard là où la vraie est en dessous."""
    n_pos = max(int(n * pos), 1)
    n_neg = n - n_pos
    equilibre = auc_hanley_ci_low(auc, n)
    reelle = auc_hanley_ci_low(auc, n, n_pos=n_pos, n_neg=n_neg)
    assert reelle < equilibre, "le déséquilibre doit élargir l'intervalle"
    assert equilibre >= AUC_RANDOM > reelle, (
        f"cas non discriminant : équilibre={equilibre:.4f} réelle={reelle:.4f}"
    )


def test_sur_classes_equilibrees_les_deux_coincident():
    """Garde-fou : le correctif ne doit rien changer au cas équilibré."""
    assert auc_hanley_ci_low(0.58, 200) == pytest.approx(
        auc_hanley_ci_low(0.58, 200, n_pos=100, n_neg=100), abs=1e-12)


def test_plus_d_echantillons_resserre_la_borne():
    etroite = auc_hanley_ci_low(0.58, None, n_pos=500, n_neg=4500)
    large = auc_hanley_ci_low(0.58, None, n_pos=20, n_neg=180)
    assert etroite > large > 0.0


@pytest.mark.parametrize("n_pos,n_neg", [(0, 100), (100, 0), (0, 0)])
def test_une_classe_absente_ne_produit_pas_de_borne(n_pos, n_neg):
    """Pas de mesure possible : on rend l'AUC telle quelle plutôt qu'un
    intervalle inventé."""
    assert auc_hanley_ci_low(0.58, 200, n_pos=n_pos, n_neg=n_neg) == 0.58


# ── Le gate ─────────────────────────────────────────────────────────────────

def test_le_gate_bloque_un_desequilibre_que_l_equilibre_laissait_passer():
    kw = dict(auc_oos=0.58, strategy_name="t", n_oos_samples=200)
    assert validate_model_quality(**kw)["level"] != "block"
    strict = validate_model_quality(**kw, n_pos=20, n_neg=180)
    assert strict["level"] == "block"
    assert strict["auc_ci_low"] < AUC_RANDOM


def test_une_classe_trop_rare_est_refusee_meme_avec_une_bonne_auc():
    """200 lignes dont 3 positifs franchissent `MIN_SAMPLES` sans rien mesurer :
    c'est la classe rare qui fixe la précision."""
    r = validate_model_quality(auc_oos=0.72, strategy_name="t",
                               n_oos_samples=200, n_pos=3, n_neg=197)
    assert r["level"] == "block" and r["ok"] is False
    assert "Classe trop rare" in r["reason"]
    assert str(MIN_PER_CLASS) in r["reason"]


def test_le_repli_sans_effectifs_est_signale():
    """Un scorer tiers peut ne pas publier les effectifs. Le gate fonctionne
    quand même, mais le résultat dit qu'il a supposé l'équilibre."""
    sans = validate_model_quality(auc_oos=0.58, strategy_name="t", n_oos_samples=200)
    assert sans["classes_estimees"] is True
    assert sans["n_pos"] is None and sans["n_neg"] is None
    avec = validate_model_quality(auc_oos=0.58, strategy_name="t",
                                  n_oos_samples=200, n_pos=100, n_neg=100)
    assert avec["classes_estimees"] is False


@pytest.mark.parametrize("kw", [
    dict(auc_oos=0.58, n_oos_samples=200),
    dict(auc_oos=0.58, n_oos_samples=5),
    dict(auc_oos=1.5, n_oos_samples=200),
    dict(auc_oos=0.40, n_oos_samples=200),
    dict(auc_oos=0.52, n_oos_samples=200),
    dict(auc_oos=0.58, n_oos_samples=200, n_pos=100, n_neg=100),
    dict(auc_oos=0.68, n_oos_samples=5000, n_pos=2500, n_neg=2500),
    dict(auc_oos=0.75, n_oos_samples=5000, n_pos=2500, n_neg=2500),
    dict(auc_oos=0.72, n_oos_samples=200, n_pos=3, n_neg=197),
])
def test_toutes_les_branches_renvoient_le_meme_contrat(kw):
    """Les diagnostics sont consommés par `policy` et écrits au registre :
    une branche qui omet une clé casse l'audit en silence."""
    r = validate_model_quality(strategy_name="t", **kw)
    assert set(r) == {"ok", "auc", "auc_ci_low", "level", "reason",
                      "min_significant_samples", "n_pos", "n_neg",
                      "classes_estimees"}, sorted(r)


# ── La source des effectifs ─────────────────────────────────────────────────

def test_rank_auc_counts_rend_les_effectifs_reellement_utilises():
    y = np.array([1] * 20 + [0] * 180)
    s = np.random.RandomState(0).rand(200)
    auc, n_pos, n_neg = rank_auc_counts(y, s)
    assert (n_pos, n_neg) == (20, 180)
    assert rank_auc(y, s) == auc, "rank_auc doit rester le même calcul"


def test_les_scores_non_finis_sont_retires_du_compte():
    """Les effectifs doivent décrire ce qui a servi au calcul, pas l'entrée."""
    y = np.array([1, 1, 1, 0, 0, 0])
    s = np.array([0.9, np.nan, 0.7, 0.2, np.inf, 0.1])
    _, n_pos, n_neg = rank_auc_counts(y, s)
    assert (n_pos, n_neg) == (2, 2)


def test_une_seule_classe_ne_donne_pas_d_auc():
    auc, n_pos, n_neg = rank_auc_counts(np.array([1, 1, 1]), np.array([0.1, 0.5, 0.9]))
    assert auc is None and (n_pos, n_neg) == (3, 0)


def test_le_scorer_par_defaut_publie_les_effectifs():
    """Contrat entre `app.ml.scoring` et `app.ml.policy` : sans ces clés, le
    gate retombe silencieusement sur l'hypothèse d'équilibre."""
    import inspect

    from app.ml.scoring import score_amp_dir_bundle
    src = inspect.getsource(score_amp_dir_bundle)
    for cle in ("n_pos_amp", "n_neg_amp", "n_pos_dir", "n_neg_dir"):
        assert cle in src, f"{cle} absent du scorer par défaut"
