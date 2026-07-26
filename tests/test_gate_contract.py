"""Contrat de gate porté par la recette (ML-02) — app.ml.scoring.

Remplace l'empilement de cas particuliers dans le gate : chaque recette
déclare ses conventions (``gate_spec``) et, si son format diffère vraiment,
surcharge ``score_holdout``. Ces tests verrouillent le contrat ET les six
recettes dont la convention de labels avait été mal devinée.
"""
import datetime as dt
import json

import numpy as np
import polars as pl
import pytest

from app.engine.engine import BaseStrategyML
from app.ml.policy import GateConfig, decide_gate, score_holdout
from app.ml.scoring import resolve_gate_spec, resolve_scorer, score_amp_dir_bundle


def _ohlcv(n: int, seed: int = 0) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    ret = rng.normal(0, 0.01, n)
    close = 100.0 * np.cumprod(1.0 + ret)
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    times = [dt.datetime(2024, 1, 1) + dt.timedelta(hours=i) for i in range(n)]
    return pl.DataFrame({"time": times, "open": open_, "high": high, "low": low,
                         "close": close, "volume": rng.uniform(100, 1000, n)})


# ─────────────────────────────────────────────────────────────────────────────
#  gate_spec — conventions de labels déclarées par chaque recette
# ─────────────────────────────────────────────────────────────────────────────
# Ces six recettes labellisent en single-horizon t+1 (ret_t1 câblé en dur dans
# leur _train_impl) : sans déclaration, le gate les évaluait sur le défaut
# multi-horizon [1,3,6] hérité de V11 — une cible jamais apprise.
SINGLE_HORIZON_RECIPES = [
    "opus_stat_retrained_v4",
    "opus_omnibus_v7",
    "opus_omnibus_v10_retrained",
    "scoring_statistique_opus_v4",
    "scoring_statistique_opus_v5",
]
MULTI_HORIZON_RECIPES = [
    "opus_omnibus_v11",
    "opus_omnibus_v11_followsetup",
    "opus_omnibus_v12",   # hérite de v11
]


@pytest.mark.parametrize("recipe", SINGLE_HORIZON_RECIPES)
def test_single_horizon_recipes_declare_label_horizon_1(recipe):
    assert resolve_gate_spec(recipe).get("label_horizons") == [1]


@pytest.mark.parametrize("recipe", MULTI_HORIZON_RECIPES)
def test_multi_horizon_recipes_declare_1_3_6(recipe):
    assert resolve_gate_spec(recipe).get("label_horizons") == [1, 3, 6]


def test_v12_inherits_gate_spec_from_v11_without_duplication():
    assert resolve_gate_spec("opus_omnibus_v12") == resolve_gate_spec("opus_omnibus_v11")


@pytest.mark.parametrize("strategy", SINGLE_HORIZON_RECIPES + MULTI_HORIZON_RECIPES)
def test_recipe_labels_match_what_the_strategy_actually_trains_with(strategy):
    """La recette-fichier et les paramètres d'entraînement ne doivent pas dériver.

    Le gate mesure le candidat sur les labels DÉCLARÉS par la recette ; la
    stratégie s'entraîne sur ceux de ses ``fixed_params``/``_DEFAULTS``. Si les
    deux divergent, le gate note un modèle sur une cible qu'il n'a jamais
    apprise — exactement l'écart 0.732 vs 0.702 qui a motivé tout ce contrat.
    Le garde-fou était auparavant une identité d'objet intra-fichier ; il est
    désormais inter-fichiers, donc il couvre le vrai risque.
    """
    import importlib

    from app.ml.recipe import load_recipe, primary_recipe
    cls = importlib.import_module(f"app.strategies.{strategy}").Strategy
    recipe = load_recipe(primary_recipe(cls))
    declared = (getattr(cls, "fixed_params", {}) or {}).get("label_horizons")
    if declared is None:
        declared = (getattr(cls, "_DEFAULTS", {}) or {}).get("label_horizons")
    if declared is None:
        pytest.skip(f"{strategy} ne déclare pas label_horizons — recette seule source")
    assert list(declared) == list(recipe.label_horizons), (
        f"{strategy} s'entraîne sur {declared} mais sa recette "
        f"{recipe.name!r} déclare {recipe.label_horizons}"
    )


def test_unknown_recipe_resolves_to_empty_spec():
    assert resolve_gate_spec("recette_inexistante") == {}


def test_gate_spec_ignores_unknown_keys():
    class Bogus(BaseStrategyML):
        gate_spec = {"label_horizons": [1], "holdout_bars": 999, "n'importe_quoi": 1}
    # holdout_bars est une décision d'exploitation (YAML), pas de recette.
    assert resolve_gate_spec(Bogus) == {"label_horizons": [1]}


# ─────────────────────────────────────────────────────────────────────────────
#  GateConfig — préséance recette (metric) vs exploitation (gate_metric)
# ─────────────────────────────────────────────────────────────────────────────
def test_recipe_metric_is_honoured():
    gc_ = GateConfig.from_params({**resolve_gate_spec("ml_dynamic_threshold")})
    assert gc_.metric == "auc_dir"


def test_operator_gate_metric_overrides_recipe_metric():
    merged = {**resolve_gate_spec("ml_dynamic_threshold"), "gate_metric": "auc_amp"}
    assert GateConfig.from_params(merged).metric == "auc_amp"


def test_default_metric_when_recipe_declares_nothing():
    assert GateConfig.from_params({}).metric == "auc_amp"


# ─────────────────────────────────────────────────────────────────────────────
#  resolve_scorer — seule une vraie surcharge compte
# ─────────────────────────────────────────────────────────────────────────────
def test_recipe_without_override_uses_default_scorer():
    assert resolve_scorer("opus_omnibus_v11") is None
    assert resolve_scorer("opus_stat_retrained_v4") is None


def test_recipe_with_override_is_detected():
    assert resolve_scorer("ml_dynamic_threshold") is not None


def test_unknown_recipe_has_no_scorer():
    assert resolve_scorer("recette_inexistante") is None


# ─────────────────────────────────────────────────────────────────────────────
#  Dispatch : score_holdout délègue à la recette, et retombe proprement
# ─────────────────────────────────────────────────────────────────────────────
def test_score_holdout_dispatches_to_recipe_scorer(tmp_path):
    called = {}

    class Custom(BaseStrategyML):
        name = "custom"

        @classmethod
        def score_holdout(cls, path_prefix, holdout_df, *, gate_cfg=None, params=None):
            called["yes"] = (path_prefix, params)
            return {"n": 42, "auc_dir": 0.77}

    out = score_holdout(str(tmp_path / "x_1h"), _ohlcv(300),
                        strategy=Custom, params={"lookahead": 5})
    assert out == {"n": 42, "auc_dir": 0.77}
    assert called["yes"][1] == {"lookahead": 5}


def test_score_holdout_falls_back_when_recipe_scorer_raises(tmp_path):
    class Broken(BaseStrategyML):
        name = "broken"

        @classmethod
        def score_holdout(cls, path_prefix, holdout_df, *, gate_cfg=None, params=None):
            raise RuntimeError("boom")

    # Repli sur le scorer par défaut : artefact absent -> {}, pas d'exception.
    assert score_holdout(str(tmp_path / "absent_1h"), _ohlcv(300), strategy=Broken) == {}


def test_score_holdout_without_strategy_uses_default_scorer(tmp_path):
    assert score_holdout(str(tmp_path / "absent_1h"), _ohlcv(300)) == {}


# ─────────────────────────────────────────────────────────────────────────────
#  Formats non lisibles par le scorer par défaut -> diagnostic honnête
# ─────────────────────────────────────────────────────────────────────────────
def test_single_booster_format_is_reported_unsupported(tmp_path):
    prefix = str(tmp_path / "ml_dynamic_threshold_1h")
    (tmp_path / "ml_dynamic_threshold_1h.lgb").write_text("stub")
    assert score_amp_dir_bundle(prefix, _ohlcv(300)) == {"unsupported_format": True}


def test_bundle_without_feature_list_is_reported_unsupported(tmp_path):
    """Format save_lgb_with_scaler (scoring_statistique_opus_v4/v5) : boosters
    présents mais meta.json sans features -> matrice d'entrée irreconstruisible."""
    import lightgbm as lgb
    rng = np.random.RandomState(0)
    X, y = rng.randn(200, 4), (rng.rand(200) > 0.5).astype(int)
    booster = lgb.train({"objective": "binary", "verbosity": -1},
                        lgb.Dataset(X, label=y), num_boost_round=3)
    prefix = str(tmp_path / "sso_1h")
    booster.save_model(f"{prefix}.amp.lgb")
    booster.save_model(f"{prefix}.dir.lgb")
    with open(f"{prefix}.meta.json", "w", encoding="utf-8") as f:
        json.dump({"tf": "1h", "best_auc": 0.6, "format_version": 1}, f)

    assert score_amp_dir_bundle(prefix, _ohlcv(300)) == {"unsupported_format": True}


def test_gate_keeps_with_honest_reason_on_unsupported_format():
    gate = decide_gate({"unsupported_format": True}, None)
    assert gate.decision == "keep"
    assert "format de persistance non reconnu" in gate.reason
    assert "mono-classe" not in gate.reason


# ─────────────────────────────────────────────────────────────────────────────
#  ml_dynamic_threshold : scorer dédié de bout en bout (plus "non supporté")
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.slow
def test_ml_dynamic_threshold_scores_its_own_artifact(tmp_path):
    import app.strategies.ml_dynamic_threshold as mdt

    df = _ohlcv(1200, seed=3)
    strat = mdt.Strategy()
    strat.n_trials = 1
    strat.fit(df, params={})
    assert strat.is_trained

    prefix = str(tmp_path / "ml_dynamic_threshold_1h")
    strat.save_model(prefix)

    metrics = score_holdout(prefix, df, strategy=strat, params={})
    assert "auc_dir" in metrics and metrics["auc_dir"] is not None
    assert 0.0 <= metrics["auc_dir"] <= 1.0
    assert metrics["n"] > 0
    # Le gate arbitre bien sur auc_dir pour cette recette (et non auc_amp).
    gc_ = GateConfig.from_params(resolve_gate_spec(strat))
    assert decide_gate(metrics, None, metric=gc_.metric).decision in ("initial", "keep")
    assert "format de persistance non reconnu" not in decide_gate(
        metrics, None, metric=gc_.metric).reason
