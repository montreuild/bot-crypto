"""Prédiction par lot : bit-à-bit identique à la prédiction ligne à ligne.

Tout l'intérêt du lot repose sur cette égalité. Elle n'est pas évidente a
priori — on pourrait imaginer une normalisation par lot, ou un ordre de
sommation dépendant du batch — donc elle se vérifie plutôt qu'elle ne se
suppose, NaN et inf compris puisque les features de production en contiennent.
"""
import numpy as np
import pytest

from app.ml.bt_predictions import predict_full, value_at

lgb = pytest.importorskip("lightgbm")


@pytest.fixture(scope="module")
def booster_et_matrice():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(1200, 40)).astype(np.float32)
    y = (X[:, 0] + 0.5 * X[:, 3] + rng.normal(0, 0.5, 1200) > 0).astype(int)
    ds = lgb.Dataset(X[:900], label=y[:900], free_raw_data=False)
    b = lgb.train(
        dict(objective="binary", num_leaves=31, learning_rate=0.05, max_bin=63,
             force_col_wise=True, verbosity=-1, n_jobs=1),
        ds, num_boost_round=60,
    )
    return b, X


def test_lot_identique_a_ligne_a_ligne(booster_et_matrice):
    b, X = booster_et_matrice
    lot = predict_full(b, X)
    ligne = np.array([b.predict(X[i:i + 1])[0] for i in range(len(X))])
    assert np.array_equal(lot, ligne)


def test_identique_avec_nan_et_inf(booster_et_matrice):
    """Les features de production portent des NaN/inf : l'égalité doit tenir."""
    b, X = booster_et_matrice
    Xn = X.copy()
    Xn[::7, 2] = np.nan
    Xn[::11, 5] = np.inf
    Xn[::13, 8] = -np.inf
    lot = predict_full(b, Xn)
    ligne = np.array([b.predict(Xn[i:i + 1])[0] for i in range(len(Xn))])
    assert np.array_equal(lot, ligne)


def test_calibration_appliquee_au_lot(booster_et_matrice):
    b, X = booster_et_matrice

    class _Doubleur:
        def predict(self, p):
            return np.asarray(p) * 0.5

    assert np.allclose(predict_full(b, X, calibrator=_Doubleur()),
                       predict_full(b, X) * 0.5)


def test_echec_rend_none_jamais_une_exception(booster_et_matrice):
    """Au pire on perd la vitesse, jamais la justesse."""
    b, X = booster_et_matrice

    class _Casse:
        def predict(self, _):
            raise RuntimeError("boom")

    assert predict_full(None, X) is None
    assert predict_full(b, X[:0]) is None
    assert predict_full(_Casse(), X) is None
    assert predict_full(b, X, calibrator=_Casse()) is None


def test_value_at_borne_les_indices():
    arr = np.array([0.1, 0.2, np.nan])
    assert value_at(arr, 0) == pytest.approx(0.1)
    assert value_at(arr, 2) is None          # NaN → non servable
    assert value_at(arr, 3) is None          # hors bornes
    assert value_at(arr, -1) is None
    assert value_at(None, 0) is None
