"""ML-02 : ml_mode de l'optimiseur (défaut "inline" = comportement historique
inchangé, opt-in "frozen"/"simulated_live" via cfg["optimizer"]["ml_mode"])
+ purge des dimensions direction (setup_*_dir_min/dir_max) de PARAM_SPACES."""
from datetime import datetime, timedelta

import polars as pl

from app.engine import opt_workers
from app.engine.optimizer_search import StrategyOptimizer


def _tiny_df(n=60):
    t0 = datetime(2024, 1, 1)
    return pl.DataFrame({
        "time": [t0 + timedelta(hours=i) for i in range(n)],
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.0] * n, "volume": [10.0] * n,
    })


def _make_opt(cfg=None, ml_mode=None):
    df = _tiny_df()
    return StrategyOptimizer(
        "opus_omnibus_v11", cfg or {"trading": {}}, df[:40], df[40:],
        param_space={"p": [1, 2, 3]}, symbol="BTC/USDC", timeframe="1h",
        ml_mode=ml_mode,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Dérivation du mode
# ─────────────────────────────────────────────────────────────────────────────
def test_ml_mode_defaults_to_inline_when_unconfigured():
    opt = _make_opt()
    assert opt.ml_mode == "inline"


def test_ml_mode_read_from_cfg_optimizer_section():
    opt = _make_opt(cfg={"trading": {}, "optimizer": {"ml_mode": "frozen"}})
    assert opt.ml_mode == "frozen"


def test_ml_mode_explicit_constructor_arg_overrides_cfg():
    opt = _make_opt(cfg={"trading": {}, "optimizer": {"ml_mode": "frozen"}},
                    ml_mode="simulated_live")
    assert opt.ml_mode == "simulated_live"


# ─────────────────────────────────────────────────────────────────────────────
#  _eval() construit bien le Backtester avec ce mode (in-process)
# ─────────────────────────────────────────────────────────────────────────────
def test_eval_constructs_backtester_with_configured_ml_mode(monkeypatch):
    captured = {}

    class _FakeResult:
        total_pnl = 0.0
        sharpe = 0.0
        total_trades = 0
        win_rate = 0.0
        max_drawdown = 0.0
        alpha = 0.0

    class _FakeBacktester:
        def __init__(self, eng, cfg, cancel_event=None, ml_mode="frozen", **_kw):
            captured["ml_mode"] = ml_mode

        def run(self, df, symbol, timeframe=None):
            return _FakeResult()

    import app.engine.optimizer_search as opt_search
    monkeypatch.setattr(opt_search, "Backtester", _FakeBacktester)

    opt = _make_opt(cfg={"trading": {}, "optimizer": {"ml_mode": "frozen"}})
    opt._eval({"p": 1})

    assert captured["ml_mode"] == "frozen"


def test_eval_defaults_to_inline_backtester_when_unconfigured(monkeypatch):
    captured = {}

    class _FakeResult:
        total_pnl = 0.0
        sharpe = 0.0
        total_trades = 0
        win_rate = 0.0
        max_drawdown = 0.0
        alpha = 0.0

    class _FakeBacktester:
        def __init__(self, eng, cfg, cancel_event=None, ml_mode="frozen", **_kw):
            captured["ml_mode"] = ml_mode

        def run(self, df, symbol, timeframe=None):
            return _FakeResult()

    import app.engine.optimizer_search as opt_search
    monkeypatch.setattr(opt_search, "Backtester", _FakeBacktester)

    opt = _make_opt()  # pas de cfg["optimizer"]["ml_mode"]
    opt._eval({"p": 1})

    assert captured["ml_mode"] == "inline"  # comportement historique (ml_mode="inline")


# ─────────────────────────────────────────────────────────────────────────────
#  Worker subprocess : même dérivation depuis cfg["optimizer"]["ml_mode"]
# ─────────────────────────────────────────────────────────────────────────────
def test_eval_worker_derives_ml_mode_from_cfg(monkeypatch):
    import io as _io

    import yaml as _yaml

    captured = {}

    class _FakeResult:
        total_pnl = 0.0
        sharpe = 0.0
        total_trades = 0
        win_rate = 0.0
        max_drawdown = 0.0
        alpha = 0.0

    class _FakeBacktester:
        def __init__(self, eng, cfg, cancel_event=None, ml_mode="frozen", **_kw):
            captured["ml_mode"] = ml_mode

        def run(self, df, symbol, timeframe=None):
            return _FakeResult()

    # ``_eval_worker`` importe ``Backtester`` LOCALEMENT à chaque appel
    # (``from app.engine.backtest import Backtester as _Backtester``) — on
    # patche donc la source, pas un attribut de module ``opt_workers`` qui
    # n'existe pas (l'import local ferait un nouveau lookup et ignorerait
    # tout attribut posé directement sur ``opt_workers``).
    monkeypatch.setattr("app.engine.backtest.Backtester", _FakeBacktester)
    opt_workers._W.clear()  # force le chemin de désérialisation (pas de fast-path)

    df = _tiny_df()
    buf = _io.BytesIO()
    df.write_ipc(buf)
    df_ipc = buf.getvalue()

    cfg_yaml = _yaml.dump({"trading": {}, "optimizer": {"ml_mode": "frozen"}})
    args = ("opus_omnibus_v11", cfg_yaml, df_ipc, df_ipc, "BTC/USDC", {"p": 1}, "1h")
    result = opt_workers._eval_worker(args)

    assert "error" not in result
    assert captured["ml_mode"] == "frozen"


# ─────────────────────────────────────────────────────────────────────────────
#  Purge des dimensions direction (AUC direction mesurée = hasard)
# ─────────────────────────────────────────────────────────────────────────────
def test_v11_param_space_has_no_direction_thresholds():
    from app.strategies.opus_omnibus_v11 import Strategy as V11

    keys = set(V11.param_space.keys())
    assert not any("dir_min" in k or "dir_max" in k for k in keys)
    # Les seuils d'amplitude restent optimisables — seule la direction est purgée.
    assert "setup_signal_up_amp_min" in keys
    assert "setup_long_tu_amp_min" in keys
    assert "di_rescue" in keys


def test_v12_inherits_purged_param_space():
    from app.strategies.opus_omnibus_v12 import Strategy as V12

    keys = set(V12.param_space.keys())
    assert not any("dir_min" in k or "dir_max" in k for k in keys)
    assert "fusion_mode" in keys  # espace propre à v12 toujours présent


def test_apply_setup_overrides_falls_back_to_hardcoded_default_when_absent():
    """La purge de param_space ne casse pas le runtime : _apply_setup_overrides
    a toujours son propre défaut câblé en dur (_DEFAULT_SETUPS) quand la clé
    est absente des params résolus."""
    from app.strategies.opus_omnibus_v11 import _apply_setup_overrides

    setups = _apply_setup_overrides({})  # aucun override -> défauts _DEFAULT_SETUPS
    signal_up = next(s for s in setups if s["name"] == "SIGNAL_UP")
    assert signal_up["dir_min"] == 0.60
