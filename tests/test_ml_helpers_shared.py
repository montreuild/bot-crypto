"""Helpers V4 partagés : une seule implémentation, dans app/ml/backend/features.py.

Ces fonctions (``_build_features`` et ses satellites) ont longtemps été
recopiées dans chaque stratégie « auto-portante » — ~390 lignes par fichier,
strictement équivalentes entre elles ET au backend (vérifié sur données
réelles BTC/USDC et cas dégénérés avant factorisation).

Ce fichier verrouille deux choses :

  1. **Identité** — chaque stratégie expose bien l'objet du backend, pas une
     copie. Une nouvelle copie locale ferait échouer le test : c'est le
     garde-fou contre la ré-duplication (le mécanisme qui avait produit six
     variantes divergentes de conventions de labels, cf. ML-02).
  2. **Non-régression fonctionnelle** — le builder partagé produit toujours le
     même contrat (nombre de colonnes, causalité, robustesse aux cas limites).

Ce qui n'est PAS partagé et ne doit pas l'être : le routing (setups, seuils,
early-exit, sizing). C'est ce qui différencie les stratégies entre elles ;
les fusionner supprimerait le produit, pas de la duplication.
"""
import datetime as dt
import importlib

import numpy as np
import polars as pl
import pytest

import app.ml.backend.features as backend

# Stratégies factorisées + nom local -> nom backend.
SHARED = {
    "_build_features": "build_features",
    "_detect_timeframe": "detect_timeframe",
    "_window_polars": "window_polars",
    "_last_bar_hour_dow": "last_bar_hour_dow",
    "_select_feature_columns": "select_feature_columns",
    "_impute_inplace": "impute_inplace",
    "_ewm_alpha_np": "_ewm_alpha_np",
}
FACTORED_STRATEGIES = [
    "opus_omnibus_v7",
    "opus_omnibus_v10_retrained",
    "opus_omnibus_v11_followsetup",
    "opus_stat_retrained_v4",
]


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


# ─────────────────────────────────────────────────────────────────────────────
#  1. Identité — pas de copie locale
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("strategy", FACTORED_STRATEGIES)
@pytest.mark.parametrize("local,backend_name", sorted(SHARED.items()))
def test_strategy_reuses_backend_helper(strategy, local, backend_name):
    mod = importlib.import_module(f"app.strategies.{strategy}")
    local_fn = getattr(mod, local, None)
    assert local_fn is not None, f"{strategy}.{local} a disparu"
    assert local_fn is getattr(backend, backend_name), (
        f"{strategy}.{local} n'est plus l'implémentation partagée — une copie "
        f"locale a été réintroduite. Utiliser app.ml.backend.features."
    )


def test_pretrained_v4_also_delegates_feature_building():
    """opus_stat_pretrained_v4 enveloppe le builder (garde son propre garde de
    longueur minimale) : on vérifie l'équivalence de sortie, pas l'identité."""
    import app.strategies.opus_stat_pretrained_v4 as pt
    df = _ohlcv(400)
    assert pt._build_features(df).equals(backend.build_features(df))


def test_scoring_statistique_opus_v4_is_not_a_duplicate():
    """Collision de NOM, pas duplication : ce ``_build_features`` a une autre
    signature et retourne 48 features en ndarray, pas les 462 colonnes V4.
    Il ne doit surtout pas être factorisé avec le backend."""
    import app.strategies.scoring_statistique_opus_v4 as sso
    assert sso._build_features is not backend.build_features
    import inspect
    assert "adx_threshold" in inspect.signature(sso._build_features).parameters


# ─────────────────────────────────────────────────────────────────────────────
#  2. Contrat du builder partagé
# ─────────────────────────────────────────────────────────────────────────────
def test_builder_produces_expected_column_count():
    feats = backend.build_features(_ohlcv(600))
    assert feats is not None
    assert len(feats.columns) == 462


def test_builder_returns_none_below_minimum_bars():
    assert backend.build_features(_ohlcv(100)) is None


def test_builder_is_causal_prefix_stable():
    """Une feature à la barre i ne doit pas dépendre des barres > i : tronquer
    la fenêtre ne change pas les valeurs déjà calculées. C'est la propriété qui
    rend le backtest walk-forward légitime."""
    full = _ohlcv(600, seed=3)
    a = backend.build_features(full)
    b = backend.build_features(full.head(500))
    assert a is not None and b is not None
    checked = 0
    for c in ("ATR_pct", "RSI_14", "range_size", "dist_SMA200", "MACD_hist"):
        if c not in a.columns:
            continue
        va = a.get_column(c).cast(pl.Float64, strict=False).to_numpy()[:500]
        vb = b.get_column(c).cast(pl.Float64, strict=False).to_numpy()
        assert np.allclose(va, vb, equal_nan=True, rtol=1e-9), f"{c} non causale"
        checked += 1
    assert checked >= 3


def test_builder_survives_degenerate_constant_prices():
    n = 400
    flat = pl.DataFrame({
        "time": [dt.datetime(2024, 1, 1) + dt.timedelta(hours=i) for i in range(n)],
        "open": np.full(n, 100.0), "high": np.full(n, 100.0),
        "low": np.full(n, 100.0), "close": np.full(n, 100.0),
        "volume": np.full(n, 1.0),
    })
    feats = backend.build_features(flat)
    assert feats is not None and len(feats) == n


def test_builder_output_ignores_extra_input_columns():
    """La sortie ne doit dépendre que des bougies, jamais de la décoration du
    frame par l'appelant — c'est ce qui rend légitime le partage d'UN cache
    FeatureStore entre toutes les stratégies V4. Sans cette normalisation, une
    colonne technique (ex. ``_pre_atr14``) survivrait dans le frame de sortie
    et serait retenue comme feature par ``select_feature_columns``."""
    df = _ohlcv(600, seed=5)
    decorated = df.with_columns([
        pl.lit(1.0).alias("_pre_atr14"),
        pl.lit(7.0).alias("colonne_technique"),
    ])
    plain, deco = backend.build_features(df), backend.build_features(decorated)
    assert plain is not None and deco is not None
    assert plain.columns == deco.columns
    assert "colonne_technique" not in backend.select_feature_columns(deco)
    assert "_pre_atr14" not in backend.select_feature_columns(deco)


def test_single_shared_featurestore_catalog():
    """Un seul catalogue pour les features V4 : deux noms cachaient les mêmes
    462 colonnes (doublon de disque et de CPU)."""
    import pathlib
    import re
    names = set()
    for f in pathlib.Path("app/strategies").glob("*.py"):
        names |= set(re.findall(r'name="(\w*v4_polars)"', f.read_text(encoding="utf-8")))
    assert names in ({"v4_polars"}, set()), f"catalogues V4 multiples : {names}"


def test_select_feature_columns_excludes_raw_and_non_stationary():
    feats = backend.build_features(_ohlcv(600))
    cols = set(backend.select_feature_columns(feats))
    # OHLCV brut et MM en niveau : non stationnaires, jamais des features.
    for excluded in ("open", "high", "low", "close", "volume", "time",
                     "SMA_20", "EMA_50", "ATR_14", "OBV"):
        assert excluded not in cols, f"{excluded} ne doit pas être une feature"
    assert len(cols) > 300
