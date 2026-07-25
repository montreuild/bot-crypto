"""Tests — robustesse NaT/NaN du chemin de scoring Opus V4.

``float(None)`` et ``float(np.nan)`` sont gérés par ``safe_num``. Le pipeline
de features V4 étant Polars (depuis phase6-pandas-removal), une valeur non
finie peut ressortir d'un jeu de données réel (trous, bougies dupliquées,
jointures cache). Ces tests verrouillent la coercion sûre dans le chemin de
scoring.

Cibles : les implémentations PARTAGÉES (``app.core.indicators.safe_num`` et
``app.ml.backend.features.regime_history``). Ils visaient auparavant les copies
locales d'``opus_omnibus_v10``, supprimé avec le pack V4 figé
(docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §3.1) — la propriété testée est la
même, à un seul endroit désormais.
"""
import numpy as np
import polars as pl

from app.core.indicators import safe_num as _safe_num
from app.ml.backend.features import regime_history


class TestSafeNum:
    def test_nat_returns_default(self):
        # Polars utilise None pour les NaT (pas pd.NaT) — safe_num gère None.
        assert _safe_num(None, 0.0) == 0.0
        assert _safe_num(None, 5.0) == 5.0

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
        # Trame de features Polars avec ADX null et colonnes d'alignement
        # nulles : la fonction ne doit jamais lever, mais classer proprement.
        df = pl.DataFrame({
            "ADX":              [None, 30.0, 22.0],
            "MM_bullish_align": [1, 0, None],
            "MM_bearish_align": [0, 1, 0],
            "DI_diff":          [None, 12.0, -3.0],
            "slope_SMA20":      [0.5, None, -0.2],
            "BB_width_rank100": [None, 0.5, 0.1],
        }, strict=False)
        regimes, labels = regime_history(df, n_last=3, adx_threshold=20.0,
                                         di_rescue=10.0)
        assert len(regimes) == 3 and len(labels) == 3
        assert all(isinstance(r, int) for r in regimes)
        assert all(isinstance(s, str) and s for s in labels)

    def test_regime_history_on_fully_null_frame(self):
        """Cas dégénéré : tout est null — ADX null ⇒ 0.0 ⇒ régime Range."""
        df = pl.DataFrame({
            "ADX":              [None, None],
            "MM_bullish_align": [None, None],
            "MM_bearish_align": [None, None],
            "DI_diff":          [None, None],
            "slope_SMA20":      [None, None],
            "BB_width_rank100": [None, None],
        }, strict=False)
        regimes, _ = regime_history(df, n_last=2, adx_threshold=20.0, di_rescue=10.0)
        assert regimes == [0, 0]
