"""ML-03 : l'entraînement inline en frozen sans modèle est causal.

``prepare_for_backtest`` reçoit le frame complet mais ne fait que cacher
des features. ``score()`` entraîne sur ``aligned_train_window(ctx.window)``,
et ``ctx.window`` est le préfixe ``df.slice(0, i+1)``. Un fit qui verrait
``len(df) == series_len`` serait un P0 (backtests ML à rejeter).
"""
from app.core.train_cache import aligned_train_window
from app.engine.backtest import Backtester
from app.engine.engine import BaseStrategyML, Engine
from app.ml.fit_trace import record, start, stop, summarize
from tests.test_backtest import _cfg, _make_df


class _InlineML(BaseStrategyML):
    """Recopie le contrat v4/v11 : features en prepare, fit sur la fenêtre."""

    name = "inline_ml"

    def __init__(self):
        self.fits: list[int] = []
        self._managed = False
        self._series_len = 0
        self.prepare_called = False

    @property
    def managed_externally(self) -> bool:
        return self._managed

    @managed_externally.setter
    def managed_externally(self, v: bool) -> None:
        self._managed = bool(v)

    def reset_model(self) -> None:
        self.fits = []
        self._managed = False

    def load_model(self, path: str) -> bool:
        return False

    def prepare_for_backtest(self, df) -> None:
        self.prepare_called = True
        self._series_len = len(df)
        # Features seulement — pas de fit (contrat MLBackend / v4).

    def score(self, df, params=None, df_htf=None, symbol=""):
        if self.managed_externally:
            return {"side": "none", "score": 0, "name": self.name}
        n_train = min(max(len(df) - 1, 1), 200)
        train_df, _ = aligned_train_window(df, 50, n_train)
        self.fit(train_df)
        return {"side": "none", "score": 0, "name": self.name}

    def fit(self, df, params=None):
        self.fits.append(len(df))
        from app.core.train_cache import cached_train
        cached_train(self, df, "default", {}, lambda *_a, **_k: False, (), ())

    @property
    def is_trained(self) -> bool:
        return False


def test_prepare_for_backtest_ne_fit_pas():
    s = _InlineML()
    s.prepare_for_backtest(_make_df(400))
    assert s.prepare_called
    assert s.fits == []
    assert s._series_len == 400


def test_aligned_train_window_est_un_prefixe_aligne():
    df = _make_df(137)
    train, offset = aligned_train_window(df, 50, 80)
    assert offset + len(train) <= len(df)
    assert len(train) <= 80
    # Fin alignée sur le multiple de 50 inférieur (100), pas la dernière barre.
    assert offset + len(train) == 100


def test_ml03_frozen_sans_modele_fit_est_causal():
    s = _InlineML()
    engine = Engine()
    engine.register(s, silent=True)
    cfg = _cfg()
    cfg["strategies"] = {"enabled": [s.name]}
    df = _make_df(350)
    result = Backtester(engine, cfg, ml_mode="frozen").run(df, "BTC/USDC", "1h")

    payload = result.to_dict()
    assert payload["fallback_to_inline"] is True
    info = result.ml_info["models"][s.name]
    assert info["fallback_to_inline"] is True
    assert info.get("resolved") is False

    assert s.prepare_called
    assert s.fits, "le repli inline doit entraîner au moins une fois"
    assert all(n < len(df) for n in s.fits)
    assert max(s.fits) <= 200

    trace = result.ml_info.get("fit_trace") or {}
    assert trace.get("n_fits", 0) >= 1
    assert trace["any_full_series"] is False
    assert trace["max_n_rows"] < len(df)


def test_fit_trace_signale_un_fit_sur_toute_la_serie():
    start()
    record(_make_df(80), site="test")
    out = stop(series_len=80)
    assert out["n_fits"] == 1
    assert out["any_full_series"] is True
    assert summarize([{"n_rows": 10}], 80)["any_full_series"] is False


def test_v11_prepare_ne_appelle_pas_fit(monkeypatch):
    pytest = __import__("pytest")
    pytest.importorskip("lightgbm")
    from app.strategies.opus_omnibus_v11 import Strategy

    s = Strategy()
    called = []

    def _boom(df, params=None, **_kw):
        called.append(len(df))
        raise AssertionError("fit() ne doit pas partir de prepare_for_backtest")

    monkeypatch.setattr(s, "fit", _boom)
    s.prepare_for_backtest(_make_df(300))
    assert called == []
