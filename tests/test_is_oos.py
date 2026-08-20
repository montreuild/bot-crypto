"""BT-08 — convention unique de split IS/OOS (app/core/is_oos.py).

Le helper doit reproduire EXACTEMENT l'ancien calcul dupliqué en dur dans
auto_optimizer (WARMUP=210, fraction OOS 0.35) : mêmes indices de coupure.
"""
import polars as pl

from app.core.is_oos import (
    EMBARGO_FRACTION_DEFAULT,
    OOS_FRACTION_DEFAULT,
    WARMUP_BARS_DEFAULT,
    default_purge_embargo,
    split_is_oos,
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


def test_purge_and_embargo_carve_the_boundary():
    """B-08 : purge retire la fin de l'IS, embargo saute le début de l'OOS."""
    df = _df(1000)
    df_is, df_oos, split = split_is_oos(df, purge_bars=10, embargo_bars=5)
    a, b, s = split_is_oos(df)
    assert s == split
    assert len(df_is) == len(a) - 10
    assert len(df_oos) == len(b) - 5
    assert float(df_is["close"][-1]) + 1 + 10 + 5 == float(df_oos["close"][0])


def test_default_purge_embargo_is_one_percent():
    purge, embargo = default_purge_embargo(10_000, lookahead=12)
    assert purge == 12
    assert embargo == max(12, int(10_000 * EMBARGO_FRACTION_DEFAULT))


def test_optimizer_imports_shared_fraction():
    from app.engine.optimizer_search import _OOS_FRACTION
    assert _OOS_FRACTION == OOS_FRACTION_DEFAULT


def test_warmup_est_partage_par_walk_forward_et_forward_test():
    """B-09 : plus de 210 / 220 / 250 indépendants."""
    from app.engine import forward_test, walk_forward
    assert walk_forward.WARMUP_BARS_DEFAULT == WARMUP_BARS_DEFAULT
    assert forward_test._WARMUP_BARS == WARMUP_BARS_DEFAULT
