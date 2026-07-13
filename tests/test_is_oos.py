"""BT-08 — convention unique de split IS/OOS (app/core/is_oos.py).

Le helper doit reproduire EXACTEMENT l'ancien calcul dupliqué en dur dans
auto_optimizer (WARMUP=210, fraction OOS 0.35) : mêmes indices de coupure.
"""
import polars as pl

from app.core.is_oos import (
    split_is_oos, WARMUP_BARS_DEFAULT, OOS_FRACTION_DEFAULT,
)


def _df(n):
    return pl.DataFrame({"close": [float(i) for i in range(n)]})


def test_split_matches_legacy_formula():
    # Ancienne formule : split = max(210 + 100, int(n * 0.65))
    for n in (300, 310, 400, 477, 1000, 5000, 15601):
        df_is, df_oos, split = split_is_oos(_df(n))
        legacy = max(210 + 100, int(n * 0.65))
        assert split == legacy, f"n={n}: {split} != legacy {legacy}"
        # Historique trop court (split > n) : tout part en IS, OOS vide —
        # même clamp de slicing que l'ancien code en dur.
        assert len(df_is) == min(split, n)
        assert len(df_is) + len(df_oos) == n


def test_constants_are_canonical():
    assert WARMUP_BARS_DEFAULT == 210
    assert abs((1.0 - OOS_FRACTION_DEFAULT) - 0.65) < 1e-12


def test_none_df():
    assert split_is_oos(None) == (None, None, 0)


def test_optimizer_imports_shared_fraction():
    from app.engine.optimizer import _OOS_FRACTION
    assert _OOS_FRACTION == OOS_FRACTION_DEFAULT
