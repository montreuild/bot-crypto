"""Régression : persistance des paramètres d'optimisation.

Sémantique attendue (cf. correctif « non appliqué = non utilisé ») :
  * ``apply_best_params`` écrit UNIQUEMENT dans ``optimizer_results[tf]`` et
    laisse le bloc ``params:`` (configuration par défaut) intact.
  * ``record_optimizer_audit`` ne touche jamais ``optimizer_results`` : un
    résultat non appliqué ne devient pas actif via la précédence de
    ``resolve_strategy_params``.
  * ``apply_best_params`` sans timeframe est refusé (rien à activer).
"""
import yaml

from app.engine import optimizer as O


def _make_strategy_file(tmp_path):
    sdir = tmp_path / "strategies"
    sdir.mkdir()
    spath = sdir / "supertrend_macd.yaml"
    yaml.safe_dump(
        {
            "params": {"st_period": 7, "st_mult": 2.0, "macd_fast": 10, "cooldown": 15},
            "optimizer_results": {
                "1h": {"run_date": "2026-06-06", "oos_score": 18.7,
                       "params": {"st_period": 7, "st_mult": 2.0}}
            },
        },
        spath.open("w"),
    )
    cfgpath = tmp_path / "config.yaml"
    cfgpath.write_text("trading: {}\n")
    return str(spath), str(cfgpath)


def test_audit_leaves_optimizer_results_untouched(tmp_path):
    spath, cfgpath = _make_strategy_file(tmp_path)
    O.record_optimizer_audit("supertrend_macd", "1h",
                             {"st_period": 10, "st_mult": 2.5}, 0.84, cfgpath)
    data = yaml.safe_load(open(spath))
    # Ni le store actif ni les params par défaut ne bougent.
    assert data["optimizer_results"]["1h"]["params"] == {"st_period": 7, "st_mult": 2.0}
    assert data["params"]["st_period"] == 7


def test_apply_writes_only_optimizer_results(tmp_path):
    spath, cfgpath = _make_strategy_file(tmp_path)
    O.apply_best_params("supertrend_macd",
                        {"st_period": 10, "st_mult": 2.5, "macd_fast": 12},
                        cfgpath, timeframe="1h", oos_score=0.84)
    data = yaml.safe_load(open(spath))
    # params: par défaut strictement inchangé.
    assert data["params"] == {"st_period": 7, "st_mult": 2.0,
                              "macd_fast": 10, "cooldown": 15}
    # optimizer_results.1h reçoit le nouveau paramétrage.
    assert data["optimizer_results"]["1h"]["params"] == {
        "st_period": 10, "st_mult": 2.5, "macd_fast": 12}
    assert data["optimizer_results"]["1h"]["oos_score"] == 0.84


def test_apply_without_timeframe_refused(tmp_path):
    spath, cfgpath = _make_strategy_file(tmp_path)
    before = yaml.safe_load(open(spath))
    ok = O.apply_best_params("supertrend_macd", {"st_period": 99}, cfgpath,
                             timeframe=None)
    assert ok is False
    assert yaml.safe_load(open(spath)) == before
