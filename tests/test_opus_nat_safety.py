"""Tests — robustesse NaT/NaN du chemin de scoring Opus V4.

``float(pd.NaT)`` lève « float() argument must be a string or a real number, not
'NaTType' ». Le pipeline de features V4 étant pandas, une valeur non finie peut
ressortir d'un jeu de données réel (trous, bougies dupliquées, jointures cache).
Ces tests verrouillent la coercion sûre dans le chemin de scoring.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.strategies.opus_omnibus_v10 import (
    _safe_num, _regime_history_from_features,
)


class TestSafeNum:
    def test_nat_returns_default(self):
        assert _safe_num(pd.NaT, 0.0) == 0.0
        assert _safe_num(pd.NaT, 5.0) == 5.0

    def test_nan_inf_none(self):
        assert _safe_num(np.nan, 1.0) == 1.0
        assert _safe_num(np.inf, 2.0) == 2.0
        assert _safe_num(-np.inf, 3.0) == 3.0
        assert _safe_num(None, 4.0) == 4.0

    def test_valid_numbers_passthrough(self):
        assert _safe_num(42.0) == 42.0
        assert _safe_num(0) == 0.0
        assert _safe_num(np.float64(3.5)) == 3.5

    def test_non_numeric_string(self):
        assert _safe_num("abc", 9.0) == 9.0


class TestRegimeHistoryNaTSafe:
    def test_regime_history_with_nat_and_nan(self):
        # Trame de features avec ADX NaN et une colonne datetime contenant NaT :
        # la fonction ne doit jamais lever, mais classer proprement.
        df = pd.DataFrame({
            "time":             [pd.Timestamp("2024-01-01"), pd.NaT, pd.Timestamp("2024-01-03")],
            "ADX":              [np.nan, 30.0, 22.0],
            "MM_bullish_align": [1, 0, np.nan],
            "MM_bearish_align": [0, 1, 0],
        })
        out = _regime_history_from_features(df, n_last=3, adx_threshold=20.0)
        assert len(out) == 3
        assert all(isinstance(r, int) for r in out)
