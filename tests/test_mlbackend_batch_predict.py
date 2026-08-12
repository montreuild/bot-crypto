"""Cache de prédictions par lot du `MLBackend` — équivalence et sûreté.

Le cache sert `arr[i]` au lieu de rappeler LightGBM sur une ligne. Le gain est
d'un ordre de grandeur, mais il repose sur une hypothèse : le frame reçu est le
PRÉFIXE de `_bt_features`. Si l'hypothèse est fausse et qu'on sert quand même,
la stratégie décide sur la prédiction d'une AUTRE barre — sans exception, sans
log, avec un backtest qui tourne normalement.

D'où deux familles de tests :

1. **équivalence** — le chemin rapide rend exactement la même valeur que le
   chemin ligne à ligne, sinon on aurait changé silencieusement le
   comportement de toutes les stratégies ML du dépôt ;
2. **refus de servir** — dès qu'on ne peut PAS prouver l'alignement (frame
   étranger de même longueur, cache invalidé par un réentraînement ou un
   changement de fenêtre), `_batch_at` doit rendre `None` et laisser le calcul
   exact reprendre la main.
"""
import numpy as np
import polars as pl
import pytest

pytest.importorskip("lightgbm")

from app.ml.backend import MLBackend


def _booster(seed: int, n_feat: int = 3):
    import lightgbm as lgb
    rng = np.random.RandomState(seed)
    X = rng.rand(400, n_feat).astype(np.float32)
    y = (X[:, 0] + 0.3 * rng.rand(400) > 0.6).astype(np.int32)
    ds = lgb.Dataset(X, label=y, free_raw_data=False)
    return lgb.train({"objective": "binary", "verbosity": -1, "num_leaves": 8},
                     ds, num_boost_round=12)


COLS = ["f0", "f1", "f2"]


def _frame(n: int, seed: int = 0) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    d = {c: rng.rand(n) for c in COLS}
    # Colonnes témoins utilisées par `_sentinel_cols`.
    d["close"] = 100 + np.cumsum(rng.normal(0, 1, n))
    d["high"] = d["close"] + rng.rand(n)
    d["low"] = d["close"] - rng.rand(n)
    d["volume"] = rng.uniform(100, 900, n)
    return pl.DataFrame(d)


@pytest.fixture()
def backend():
    ml = MLBackend(name="test_batch", model_dir="models")
    frame = _frame(600, seed=1)
    with ml.lock:
        ml.state.amp_models["1h"] = _booster(1)
        ml.state.dir_models["1h"] = _booster(2)
        ml.state.feature_cols["1h"] = list(COLS)
        ml.state.medians["1h"] = {c: 0.5 for c in COLS}
        ml.state.trained_tfs.add("1h")
    ml._bt_features = frame
    ml._bt_features_len = len(frame)
    ml._invalidate_batch()
    return ml, frame


# ─────────────────────────────────────────────────────────────────────────────
#  Équivalence
# ─────────────────────────────────────────────────────────────────────────────
def test_le_lot_rend_la_meme_valeur_que_le_calcul_ligne_a_ligne(backend):
    """LE test. Une divergence ici changerait le comportement de V11, V12 et
    des stratégies stat sans qu'aucun autre test ne le voie."""
    from app.ml.backend.predictor import predict_single

    ml, frame = backend
    for n in (50, 137, 400, len(frame)):
        prefixe = frame.head(n)
        for cible in ("amp", "dir"):
            exact = predict_single(ml.state, ml.lock, prefixe, "1h", cible)
            rapide = ml._batch_at(prefixe, "1h", cible)
            assert rapide is not None, f"le lot refuse de servir n={n} {cible}"
            assert rapide == pytest.approx(exact, abs=1e-12), (
                f"n={n} tête={cible} : lot={rapide} contre exact={exact}")


def test_predict_amplitude_et_direction_passent_par_le_lot(backend):
    """Le branchement doit être effectif : sans lui, le cache serait construit
    et jamais lu."""
    ml, frame = backend
    ml.predict_amplitude(frame.head(200), "1h")
    ml.predict_direction(frame.head(200), "1h")
    assert ("1h", "amp") in ml._batch_preds
    assert ("1h", "dir") in ml._batch_preds


# ─────────────────────────────────────────────────────────────────────────────
#  Refus de servir quand l'alignement n'est pas démontrable
# ─────────────────────────────────────────────────────────────────────────────
def test_refuse_un_frame_etranger_de_meme_longueur(backend):
    """Le piège que la vérification par témoins existe pour attraper.

    Une stratégie qui retombe sur sa branche « fenêtre glissante » produit un
    frame d'une longueur qui peut coïncider avec un préfixe du cache. Servir
    `arr[n-1]` donnerait alors la prédiction d'une barre sans rapport.
    """
    ml, frame = backend
    etranger = _frame(200, seed=99)          # même longueur, autres valeurs
    assert ml._batch_at(etranger, "1h", "amp") is None


def test_refuse_au_dela_de_la_longueur_du_cache(backend):
    ml, frame = backend
    plus_long = _frame(len(frame) + 50, seed=1)
    assert ml._batch_at(plus_long, "1h", "amp") is None


def test_le_reentrainement_invalide_le_lot(backend):
    """Un lot calculé avec l'ancien modèle servirait des prédictions périmées
    à toutes les barres suivantes."""
    ml, frame = backend
    ml.predict_amplitude(frame.head(300), "1h")
    assert ml._batch_preds, "le lot n'a pas été construit"

    avant = ml._batch_at(frame.head(300), "1h", "amp")
    with ml.lock:                                    # nouveau modèle
        ml.state.amp_models["1h"] = _booster(7)
    ml._invalidate_batch()
    apres = ml._batch_at(frame.head(300), "1h", "amp")
    assert apres is not None
    assert apres != pytest.approx(avant, abs=1e-12), (
        "la valeur n'a pas changé après réentraînement — le lot n'a pas été "
        "recalculé avec le nouveau modèle")


def test_changement_de_fenetre_invalide_le_lot(backend):
    """`prepare_for_backtest` remplace `_bt_features` : un lot aligné sur
    l'ancienne série serait décalé d'un bout à l'autre."""
    ml, frame = backend
    ml.predict_amplitude(frame.head(300), "1h")
    assert ml._batch_preds
    ml._bt_features = _frame(500, seed=4)
    ml._bt_features_len = 500
    ml._invalidate_batch()
    assert ml._batch_preds == {}


def test_sans_cache_de_backtest_le_lot_ne_sert_pas(backend):
    """Hors backtest (`_bt_features is None`), il n'y a rien à mettre en lot :
    la prédiction doit rester exacte, pas échouer."""
    from app.ml.backend.predictor import predict_single

    ml, frame = backend
    ml._bt_features = None
    ml._bt_features_len = 0
    ml._invalidate_batch()
    assert ml._batch_at(frame.head(100), "1h", "amp") is None
    exact = predict_single(ml.state, ml.lock, frame.head(100), "1h", "amp")
    assert ml.predict_amplitude(frame.head(100), "1h") == pytest.approx(exact)


def test_modele_absent_ne_leve_pas(backend):
    ml, frame = backend
    assert ml._batch_at(frame.head(100), "4h", "amp") is None
    assert ml.predict_amplitude(frame.head(100), "4h") is None
