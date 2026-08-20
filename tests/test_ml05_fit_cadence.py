"""ML-05 : un fit() qui échoue n'est pas relancé à chaque barre."""
from app.engine.backtest import Backtester
from app.engine.engine import BaseStrategy, Engine
from app.ml.retrain import due_for_training
from tests.test_backtest import _cfg, _make_df


class _FitQuiEchoue(BaseStrategy):
    name = "fit_fail"
    fits = 0

    def __init__(self):
        self.fits = 0
        self._calls = 0
        self._last_attempt = 0

    def score(self, df, params=None, df_htf=None, symbol=""):
        self._calls += 1
        if due_for_training(is_trained=False, calls=self._calls,
                            last_success=0, last_attempt=self._last_attempt,
                            cadence=50):
            try:
                self.fit(df, params)
            except RuntimeError:
                pass
            self._last_attempt = self._calls
        return {"side": "none", "score": 0}

    def fit(self, df, params=None):
        self.fits += 1
        raise RuntimeError("entraînement volontairement KO")


def test_fit_echoue_n_est_pas_relance_a_chaque_barre():
    s = _FitQuiEchoue()
    engine = Engine()
    engine.register(s)
    cfg = _cfg()
    cfg["strategies"] = {"enabled": [s.name]}
    Backtester(engine, cfg).run(_make_df(400), "BTC/USDC", "1h")
    assert s.fits >= 1
    assert s._calls >= 50
    assert s.fits < s._calls / 3
