"""Contrat ``Predictor`` — quatre sources de prédiction, une interface.

Ce fichier verrouille les deux propriétés qui rendent le contrat utile :

  1. **Équivalence** — chaque implémentation produit EXACTEMENT ce que
     produisait le code qu'elle remplace. Un contrat qui change les nombres
     ne factorise pas, il réécrit.
  2. **Interchangeabilité** — le routing peut consommer n'importe laquelle
     sans savoir laquelle. C'est ce qui permet à `models: {signal: …}` de
     remplacer trois fichiers de stratégie par trois lignes de YAML.
"""
import datetime as dt
import json

import numpy as np
import polars as pl
import pytest

pytest.importorskip("lightgbm")

from app.ml.predictor import (  # noqa: E402
    PROXY_SPEC,
    LgbmBundlePredictor,
    LgbmScalerPredictor,
    LgbmSinglePredictor,
    Predictor,
    ProxyPredictor,
    build_predictor,
    proxy_p_event,
    proxy_p_up,
)

ALL_IMPLS = (LgbmBundlePredictor, LgbmScalerPredictor, LgbmSinglePredictor,
             ProxyPredictor)


def _ohlcv(n: int, seed: int = 0) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pl.DataFrame({
        "time": [dt.datetime(2024, 1, 1) + dt.timedelta(hours=i) for i in range(n)],
        "open": open_, "high": close * 1.004, "low": close * 0.996,
        "close": close, "volume": rng.uniform(100, 1000, n),
    })


def _tiny_booster(seed: int, n_feat: int = 4):
    import lightgbm as lgb
    rng = np.random.RandomState(seed)
    X = rng.rand(200, n_feat)
    y = (X[:, 0] + rng.rand(200) * 0.1 > 0.5).astype(int)
    return lgb.train({"objective": "binary", "verbose": -1, "num_leaves": 3},
                     lgb.Dataset(X, y), num_boost_round=4)


# ─────────────────────────────────────────────────────────────────────────────
#  Interface
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("impl", ALL_IMPLS)
def test_every_impl_declares_the_contract(impl):
    for attr in ("heads", "predict_last", "predict_series"):
        assert hasattr(impl, attr), f"{impl.__name__} n'expose pas {attr}"
    assert isinstance(impl.heads, tuple) and impl.heads


def test_proxy_satisfies_runtime_protocol():
    assert isinstance(ProxyPredictor(), Predictor)


def test_heads_describe_what_the_recipe_actually_has():
    """``ml_dynamic_threshold`` n'a PAS de notion d'amplitude : le dire par
    ``heads`` plutôt que fabriquer un p_event de complaisance."""
    assert LgbmSinglePredictor.heads == ("dir",)
    assert LgbmBundlePredictor.heads == ("amp", "dir")


# ─────────────────────────────────────────────────────────────────────────────
#  Équivalence avec le code remplacé
# ─────────────────────────────────────────────────────────────────────────────
def test_bundle_predictor_matches_predict_batch_raw(tmp_path):
    """``LgbmBundlePredictor`` doit rendre exactement ce que rendait l'appel
    direct à ``predict_batch_raw`` — c'est le chemin qu'il enveloppe."""
    from app.ml.backend.persistence import load_amp_dir_bundle, save_amp_dir_bundle
    from app.ml.backend.predictor import predict_batch_raw

    cols = ["f0", "f1", "f2", "f3"]
    prefix = str(tmp_path / "r_1h")
    assert save_amp_dir_bundle(prefix, "1h", _tiny_booster(1), _tiny_booster(2),
                               cols, {c: 0.0 for c in cols}, 0.61, {})

    rng = np.random.RandomState(7)
    feats = pl.DataFrame({c: rng.rand(60) for c in cols})

    bundle = load_amp_dir_bundle(prefix)
    expected_amp = predict_batch_raw(bundle["amp_model"], bundle.get("amp_cal"),
                                     bundle["features"], bundle["medians"], feats)
    pred = LgbmBundlePredictor.from_artifact(prefix, recipe="r")
    assert pred is not None
    got = pred.predict_series(feats, "1h")
    assert np.array_equal(got["amp"], expected_amp)
    assert got["dir"] is not None


def test_bundle_predict_last_is_the_last_row_of_predict_series(tmp_path):
    from app.ml.backend.persistence import save_amp_dir_bundle
    cols = ["f0", "f1", "f2", "f3"]
    prefix = str(tmp_path / "r_1h")
    save_amp_dir_bundle(prefix, "1h", _tiny_booster(3), _tiny_booster(4),
                        cols, {c: 0.0 for c in cols}, 0.6, {})
    rng = np.random.RandomState(11)
    feats = pl.DataFrame({c: rng.rand(40) for c in cols})

    pred = LgbmBundlePredictor.from_artifact(prefix)
    series = pred.predict_series(feats, "1h")
    last = pred.predict_last(feats, "1h")
    assert last["amp"] == pytest.approx(float(series["amp"][-1]))
    assert last["dir"] == pytest.approx(float(series["dir"][-1]))


def test_single_predictor_reads_the_dyn_threshold_format(tmp_path):
    cols = ["f0", "f1", "f2", "f3"]
    prefix = str(tmp_path / "dyn_1h")
    _tiny_booster(5).save_model(f"{prefix}.lgb")
    with open(f"{prefix}.meta.json", "w", encoding="utf-8") as f:
        json.dump({"features": cols}, f)

    pred = LgbmSinglePredictor.from_artifact(prefix, recipe="dyn")
    assert pred is not None and pred.heads == ("dir",)
    rng = np.random.RandomState(13)
    out = pred.predict_series(pl.DataFrame({c: rng.rand(50) for c in cols}), "1h")
    assert out["dir"] is not None and len(out["dir"]) == 50
    assert "amp" not in out


def test_scaler_predictor_takes_a_matrix_not_a_frame(tmp_path):
    """``scoring_statistique_opus_v4`` construit un ndarray (n, 48), pas un
    catalogue polars : le contrat ne doit pas imposer une conversion."""
    from app.ml.backend.persistence import save_lgb_with_scaler
    prefix = str(tmp_path / "stat_1h")
    assert save_lgb_with_scaler(_tiny_booster(6), _tiny_booster(7), None,
                                prefix, "1h", 0.58, {})
    pred = LgbmScalerPredictor.from_artifact(prefix, recipe="stat")
    assert pred is not None
    X = np.random.RandomState(17).rand(30, 4)
    out = pred.predict_series(X, "1h")
    assert out["amp"] is not None and len(out["amp"]) == 30
    last = pred.predict_last(X, "1h")
    assert last["amp"] == pytest.approx(float(out["amp"][-1]))


# ─────────────────────────────────────────────────────────────────────────────
#  ProxyPredictor — équivalence avec les variantes _no_ml
# ─────────────────────────────────────────────────────────────────────────────
def test_proxy_functions_are_the_no_ml_ones_verbatim():
    """Les 5 variantes ``*_no_ml`` portaient un AST identique de ces deux
    fonctions. Une divergence ici changerait les signaux de toutes."""
    import app.strategies.opus_omnibus_v11_no_ml as noml
    kw_up = dict(pdi=25.0, ndi=15.0, rsi=58.0, macd_hist=0.4, atr=1.2, roc=1.8,
                 c=105.0, sma50=100.0, rsi_vel=3.0, range_pos=0.7, body=0.004,
                 gain=3.0)
    assert proxy_p_up(**kw_up) == pytest.approx(noml._proxy_p_up(**kw_up))
    kw_ev = dict(atr_pct_r=1.4, range_r=1.2, volstd_r=1.1, volr=1.6, adx=28.0,
                 body_abs_r=1.3, center=0.45, gain=5.0)
    assert proxy_p_event(**kw_ev) == pytest.approx(noml._proxy_p_event(**kw_ev))


def test_proxy_predictor_needs_no_artifact():
    p = ProxyPredictor()
    assert p.recipe is None and p.version_id is None


def test_proxy_predictor_outputs_are_probabilities():
    df = _ohlcv(200, seed=3).with_columns(
        [pl.lit(v).alias(k) for k, v in PROXY_SPEC.items()])
    out = ProxyPredictor().predict_last(df, "1h")
    for head in ("amp", "dir"):
        assert 0.0 <= out[head] <= 1.0, f"{head}={out[head]} hors [0,1]"


def test_proxy_predictor_reacts_to_direction():
    """Un proxy qui ne bouge pas avec les indicateurs ne sert à rien : DI
    fortement haussier doit produire un p_up nettement plus élevé que DI
    fortement baissier."""
    base = _ohlcv(200, seed=4)
    def _p_up(pdi, ndi):
        df = base.with_columns([pl.lit(v).alias(k) for k, v in PROXY_SPEC.items()])
        df = df.with_columns([pl.lit(pdi).alias("_pre_pdi14"),
                              pl.lit(ndi).alias("_pre_ndi14")])
        return ProxyPredictor().predict_last(df, "1h")["dir"]
    assert _p_up(35.0, 10.0) > _p_up(10.0, 35.0) + 0.05


def test_proxy_spec_lists_every_column_the_proxy_reads():
    """``PROXY_SPEC`` est le contrat de précalcul : si une colonne lue n'y
    figure pas, l'appelant ne saura pas qu'il doit la fournir."""
    import inspect
    src = inspect.getsource(ProxyPredictor._scalars)
    assert "PROXY_SPEC" in src
    df = _ohlcv(100).with_columns([pl.lit(v).alias(k) for k, v in PROXY_SPEC.items()])
    # Sans aucune colonne _pre_*, les replis doivent suffire (pas d'exception).
    assert ProxyPredictor().predict_last(_ohlcv(100), "1h")["dir"] is not None
    assert ProxyPredictor().predict_last(df, "1h")["dir"] is not None


# ─────────────────────────────────────────────────────────────────────────────
#  Fabrique
# ─────────────────────────────────────────────────────────────────────────────
def test_build_predictor_dispatches_on_declared_persistence(tmp_path):
    from app.ml.backend.persistence import save_amp_dir_bundle
    cols = ["f0", "f1", "f2", "f3"]
    prefix = str(tmp_path / "r_1h")
    save_amp_dir_bundle(prefix, "1h", _tiny_booster(8), _tiny_booster(9),
                        cols, {c: 0.0 for c in cols}, 0.6, {})
    p = build_predictor("lgbm_amp_dir_bundle", prefix, recipe="r")
    assert isinstance(p, LgbmBundlePredictor) and p.recipe == "r"


def test_build_predictor_proxy_never_needs_a_path():
    p = build_predictor("proxy", "/chemin/qui/nexiste/pas")
    assert isinstance(p, ProxyPredictor)


def test_build_predictor_missing_artifact_returns_none(tmp_path):
    """Artefact absent = ``None``, pas d'exception ni de prédicteur vide :
    l'appelant doit pouvoir décider d'entraîner."""
    assert build_predictor("lgbm_amp_dir_bundle", str(tmp_path / "rien_1h")) is None


def test_build_predictor_unknown_persistence_is_none_and_logged(caplog):
    with caplog.at_level("ERROR"):
        assert build_predictor("format_inconnu", "/x") is None
    assert "persistence inconnue" in caplog.text


def test_no_format_sniffing_remains_in_the_factory():
    """La recette DÉCLARE son format : la fabrique ne doit jamais deviner
    depuis les fichiers présents (c'est ce qui produisait
    ``unsupported_format``)."""
    import inspect

    import app.ml.predictor as mod
    src = inspect.getsource(mod.build_predictor)
    assert "os.path.exists" not in src
