"""Threads LightGBM : contexte d'appel, et invariance du modèle produit.

Le second test est celui qui autorise le premier. Rendre `n_jobs` dépendant du
contexte ne serait pas acceptable si cela changeait les artefacts publiés —
c'est vérifié, pas supposé.
"""
import numpy as np
import pytest

from app.ml.threads import lgb_threads, single_thread


def test_worker_optimisation_reste_mono_thread(monkeypatch):
    """`OMP_NUM_THREADS=1` est la signature d'un worker spawné."""
    monkeypatch.delenv("ML_LGB_THREADS", raising=False)
    monkeypatch.setenv("OMP_NUM_THREADS", "1")
    assert lgb_threads() == 1


def test_entrainement_autonome_utilise_plusieurs_coeurs(monkeypatch):
    monkeypatch.delenv("ML_LGB_THREADS", raising=False)
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    assert lgb_threads() == max(1, min(4, (__import__("os").cpu_count() or 1) - 1))


def test_contexte_single_thread(monkeypatch):
    """Essai in-process : rien dans l'environnement ne le signale."""
    monkeypatch.delenv("ML_LGB_THREADS", raising=False)
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    avant = lgb_threads()
    with single_thread():
        assert lgb_threads() == 1
    assert lgb_threads() == avant, "le contexte doit se refermer"


def test_contexte_imbrique(monkeypatch):
    monkeypatch.delenv("ML_LGB_THREADS", raising=False)
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    with single_thread():
        with single_thread():
            assert lgb_threads() == 1
        assert lgb_threads() == 1, "le contexte interne ne doit pas relâcher l'externe"


def test_variable_denvironnement_force(monkeypatch):
    monkeypatch.setenv("ML_LGB_THREADS", "3")
    monkeypatch.setenv("OMP_NUM_THREADS", "1")
    assert lgb_threads() == 3
    with single_thread():
        assert lgb_threads() == 3, "la surcharge explicite gagne sur le contexte"
    monkeypatch.setenv("ML_LGB_THREADS", "n'importe quoi")
    assert lgb_threads() == 1, "valeur ininterprétable → règle normale"


def test_le_modele_ne_depend_pas_du_nombre_de_threads():
    """Seule la ligne `num_threads` du booster change ; les arbres non."""
    lgb = pytest.importorskip("lightgbm")
    rng = np.random.default_rng(3)
    X = rng.normal(size=(3000, 40)).astype(np.float32)
    y = (X[:, 0] + 0.4 * X[:, 7] + rng.normal(0, 0.6, 3000) > 0).astype(int)
    common = dict(objective="binary", num_leaves=31, learning_rate=0.05,
                  min_child_samples=20, subsample=0.8, subsample_freq=5,
                  colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=0.5,
                  max_bin=63, force_col_wise=True, verbosity=-1)

    modeles = []
    for nj in (1, 2):
        ds = lgb.Dataset(X[:2200], label=y[:2200], free_raw_data=False)
        modeles.append(lgb.train({**common, "n_jobs": nj}, ds, num_boost_round=80))

    a, b = (m.model_to_string().split("\n") for m in modeles)
    ecarts = [(x, y_) for x, y_ in zip(a, b) if x != y_]
    assert ecarts, "deux sérialisations rigoureusement égales : test sans objet"
    assert all("num_threads" in x for x, _ in ecarts), \
        f"seule la ligne num_threads devait changer, vu : {ecarts[:3]}"
    assert np.array_equal(modeles[0].predict(X[2200:]), modeles[1].predict(X[2200:]))
