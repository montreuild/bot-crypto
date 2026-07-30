"""Tests — ``OptimizerResultApplier`` (ARCH-007).

Couverture minimale du nouvel appliqueur : il orchestre ``beats_baseline``
(garde-fou qualité, BT-04/BT-06) et ``apply_best_params`` (persistance YAML).
Les cas extrêmes du garde-fou sont déjà couverts par ``test_apply_guard.py``
via ``opt_scoring.beats_baseline`` ; on vérifie ici que l'applier :
  1. applique quand le gate passe ;
  2. refuse d'appliquer (sans écrire) quand le gate échoue ;
  3. outrepasse le gate quand ``force=True`` (apply effectué, gate_ok=False).
"""
import yaml

from app.engine.optimizer_applier import OptimizerResultApplier


def _make_strategy_file(tmp_path):
    sdir = tmp_path / "strategies"
    sdir.mkdir()
    spath = sdir / "supertrend_macd.yaml"
    yaml.safe_dump({"params": {"st_period": 7}, "optimizer_results": {}}, spath.open("w"))
    cfgpath = tmp_path / "config.yaml"
    cfgpath.write_text("trading: {}\n")
    return str(spath), str(cfgpath)


def _result(pnl=80.0, trades=12, wr=45.0, sharpe=1.2, score=0.8):
    return {
        "best_params": {"st_period": 10},
        "best_oos_trades": trades, "best_oos_pnl": pnl,
        "best_oos_wr": wr, "best_oos_sharpe": sharpe,
        "best_oos_score": score, "timeframe": "1h",
    }


BASELINE = {"pnl": 50.0, "wr": 40.0, "sharpe": 1.0}


def test_apply_when_gate_passes(tmp_path):
    spath, cfgpath = _make_strategy_file(tmp_path)
    applier = OptimizerResultApplier()
    applied, raison, gate = applier.apply_if_beats_baseline(
        "supertrend_macd", _result(), cfgpath, BASELINE, symbol="BTC/USDC")
    assert applied and gate and raison == ""
    data = yaml.safe_load(open(spath))
    assert data["optimizer_results"]["1h"]["BTC/USDC"]["params"] == {"st_period": 10}


def test_refuses_when_gate_fails(tmp_path):
    spath, cfgpath = _make_strategy_file(tmp_path)
    applier = OptimizerResultApplier()
    # PnL négatif -> gate refusé.
    applied, raison, gate = applier.apply_if_beats_baseline(
        "supertrend_macd", _result(pnl=-5.0), cfgpath, BASELINE, symbol="BTC/USDC")
    assert not applied and not gate and "PnL" in raison
    # Rien écrit dans optimizer_results.
    data = yaml.safe_load(open(spath))
    assert data["optimizer_results"] == {}


def test_force_overrides_gate_but_keeps_diagnostic(tmp_path):
    spath, cfgpath = _make_strategy_file(tmp_path)
    applier = OptimizerResultApplier()
    applied, raison, gate = applier.apply_if_beats_baseline(
        "supertrend_macd", _result(pnl=-5.0), cfgpath, BASELINE,
        symbol="BTC/USDC", force=True)
    # Apply effectué MAIS gate_ok reste False + raison documentée.
    assert applied and not gate and "PnL" in raison
    data = yaml.safe_load(open(spath))
    assert data["optimizer_results"]["1h"]["BTC/USDC"]["params"] == {"st_period": 10}


def test_no_best_params_short_circuits(tmp_path):
    spath, cfgpath = _make_strategy_file(tmp_path)
    applier = OptimizerResultApplier()
    applied, raison, gate = applier.apply_if_beats_baseline(
        "supertrend_macd", {"best_oos_pnl": 80.0}, cfgpath, BASELINE, symbol="BTC/USDC")
    assert not applied and not gate and raison == "aucun best_params"
