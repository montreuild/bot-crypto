"""Régression : alignement des features cachées de scoring_statistique_opus_v4/v5.

Avant correctif, ``_get_or_build_features`` figeait X à la longueur du premier
accès (``df.head(_bt_features_len)`` sur une fenêtre plus courte) ET
``prepare_for_backtest`` semait sur un df non précalculé (sans ``_pre_*`` →
``None``). Conséquence : toutes les barres lisaient les features de la barre
~warmup. Ce test vérifie que X[:len(df)][-1] correspond bien à la barre courante.
"""
import tempfile
from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

pytest.importorskip("sklearn")
pytest.importorskip("lightgbm")


def _make_df(n=500):
    np.random.seed(21)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(max(closes[-1] + np.random.randn() * 0.6 + 0.02, 1.0))
    closes = np.array(closes)
    highs = closes * (1 + np.abs(np.random.randn(n) * 0.005))
    lows = closes * (1 - np.abs(np.random.randn(n) * 0.005))
    opens = np.roll(closes, 1); opens[0] = closes[0]
    vols = np.random.uniform(1000, 5000, n)
    times = [datetime(2024, 1, 1) + timedelta(hours=i) for i in range(n)]
    return pl.DataFrame({"time": times, "open": opens, "high": highs,
                         "low": lows, "close": closes, "volume": vols})


@pytest.mark.parametrize("module_name", [
    "scoring_statistique_opus_v4",
    "scoring_statistique_opus_v5",
])
def test_scoring_features_aligned_per_bar(tmp_path, module_name):
    import importlib
    import app.core.feature_store as fs
    from app.core.indicators import precompute_df

    fs.get_feature_store.set(fs.FeatureStore(base_dir=str(tmp_path)))
    mod = importlib.import_module(f"app.strategies.{module_name}")

    df = _make_df()
    pre = precompute_df(df)                      # df précalculé (comme dans le backtest)
    thr = 20.0
    ground_truth = mod._build_features(pre, thr)  # X complet, vérité terrain
    assert ground_truth is not None

    strat = mod.Strategy()
    strat._bt_symbol = "BTC/USDC"
    strat._bt_tf = "1h"
    strat.prepare_for_backtest(df)               # reçoit le df BRUT (comme Backtester.run)

    # Le cache doit être semé (correctif : prepare précalcule désormais en interne).
    assert len(strat._bt_features_cache) > 0, "prepare n'a rien semé (bug latent ?)"

    # Simule la boucle backtest : pour plusieurs barres, la dernière ligne du X
    # tronqué doit refléter la barre courante (pas une barre figée).
    n = len(df)
    for i in (250, 300, 400, n - 1):
        window = pre.slice(0, i + 1)             # df[:i+1] précalculé
        X = strat._get_or_build_features(window, thr)
        assert X is not None and len(X) == i + 1, f"X mal aligné à i={i}"
        np.testing.assert_allclose(
            X[-1], ground_truth[i], rtol=1e-5, atol=1e-5,
            err_msg=f"features figées/désalignées à la barre {i}")
