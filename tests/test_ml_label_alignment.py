"""Régression : alignement features/labels dans l'entraînement ML (V4/V5).

Avant correctif, ``_train`` calculait ``n = len(close) - 1`` puis ``X_train = X[:n]``
sans borner ``n`` par ``len(X)``. Quand les features sont plus courtes que la série
de prix (warmup / cache de features), le jeu de validation ``X_s[split:n]`` et ses
labels ``y_amp[split:n]`` divergeaient en taille → LightGBM lève
« Length of labels differs from the length of #data ».
"""
import logging

import numpy as np
import polars as pl
import pytest

pytest.importorskip("lightgbm")
pytest.importorskip("sklearn")


def _make_df(n=400, seed=3):
    rng = np.random.default_rng(seed)
    close = np.cumprod(1 + rng.standard_normal(n) * 0.01) * 100
    return pl.DataFrame({
        "close": close, "open": close,
        "high": close * 1.001, "low": close * 0.999,
        "volume": rng.random(n) * 1000,
    })


@pytest.mark.parametrize("module_name", [
    "scoring_statistique_opus_v5",
    "scoring_statistique_opus_v4",
])
def test_train_aligns_short_features(module_name, monkeypatch):
    import importlib
    mod = importlib.import_module(f"app.strategies.{module_name}")
    s = mod.Strategy()
    df = _make_df()

    # Simule le cas du bug : features plus COURTES que len(close)-1.
    short = len(df) - 40
    monkeypatch.setattr(
        s, "_get_or_build_features",
        lambda d, *a, **k: np.random.default_rng(1).standard_normal((short, 8)).astype(np.float32),
    )

    logging.disable(logging.WARNING)
    try:
        ok = s._train(df, "1h")          # ne doit PAS lever de mismatch de longueur
    finally:
        logging.disable(logging.NOTSET)
    assert ok in (True, False)           # booléen propre, pas d'exception
