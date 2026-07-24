"""app.ml.policy — rank_auc (Mann-Whitney sans sklearn), decide_gate, et
maybe_refresh() de bout en bout sur une vraie stratégie MLBackend (ML-02 §3.3/3.4)."""
import datetime as dt

import numpy as np
import polars as pl
import pytest

import app.ml.model_registry as registry
from app.ml.policy import (
    decide_gate,
    maybe_refresh,
    rank_auc,
    recipe_gate_defaults,
    score_holdout,
)


# ─────────────────────────────────────────────────────────────────────────────
#  rank_auc
# ─────────────────────────────────────────────────────────────────────────────
def test_rank_auc_perfect_separation_is_one():
    y = np.array([0, 0, 0, 1, 1, 1])
    scores = np.array([0.1, 0.2, 0.3, 0.7, 0.8, 0.9])
    assert rank_auc(y, scores) == pytest.approx(1.0)


def test_rank_auc_perfectly_wrong_is_zero():
    y = np.array([0, 0, 0, 1, 1, 1])
    scores = np.array([0.9, 0.8, 0.7, 0.3, 0.2, 0.1])
    assert rank_auc(y, scores) == pytest.approx(0.0)


def test_rank_auc_random_guess_is_half():
    y = np.array([0, 1, 0, 1])
    scores = np.array([0.5, 0.5, 0.5, 0.5])  # ex-aequo total -> 0.5 par construction
    assert rank_auc(y, scores) == pytest.approx(0.5)


def test_rank_auc_single_class_is_none():
    y = np.array([1, 1, 1])
    scores = np.array([0.1, 0.5, 0.9])
    assert rank_auc(y, scores) is None


def test_rank_auc_matches_bruteforce_pair_counting():
    rng = np.random.RandomState(0)
    y = rng.randint(0, 2, 40)
    scores = rng.rand(40)
    pos = scores[y == 1]
    neg = scores[y == 0]
    concordant = sum((p > n) for p in pos for n in neg)
    ties = sum((p == n) for p in pos for n in neg)
    expected = (concordant + 0.5 * ties) / (len(pos) * len(neg))
    assert rank_auc(y, scores) == pytest.approx(expected)


# ─────────────────────────────────────────────────────────────────────────────
#  decide_gate
# ─────────────────────────────────────────────────────────────────────────────
def test_decide_gate_cold_start_above_floor_is_initial():
    r = decide_gate({"auc_amp": 0.60}, None, auc_floor=0.55, epsilon=0.01)
    assert r.decision == "initial"


def test_decide_gate_cold_start_below_floor_is_keep():
    r = decide_gate({"auc_amp": 0.50}, None, auc_floor=0.55, epsilon=0.01)
    assert r.decision == "keep"


def test_decide_gate_candidate_missing_metric_is_keep():
    r = decide_gate({}, {"auc_amp": 0.60}, auc_floor=0.55)
    assert r.decision == "keep"
    assert "indisponible" in r.reason


def test_decide_gate_promotes_when_not_regressing():
    r = decide_gate({"auc_amp": 0.60}, {"auc_amp": 0.58}, auc_floor=0.55, epsilon=0.01)
    assert r.decision == "promote"


def test_decide_gate_rejects_regression_beyond_epsilon():
    r = decide_gate({"auc_amp": 0.56}, {"auc_amp": 0.65}, auc_floor=0.55, epsilon=0.01)
    assert r.decision == "keep"
    assert "régression" in r.reason


def test_decide_gate_epsilon_tolerates_small_regression():
    r = decide_gate({"auc_amp": 0.640}, {"auc_amp": 0.645}, auc_floor=0.55, epsilon=0.01)
    assert r.decision == "promote"


def test_decide_gate_floor_applies_even_with_weak_incumbent():
    # Le sortant est mauvais (0.40, sous son propre plancher) mais le
    # plancher continue de s'appliquer au candidat : pas de nivellement par
    # le bas.
    r = decide_gate({"auc_amp": 0.50}, {"auc_amp": 0.40}, auc_floor=0.55, epsilon=0.01)
    assert r.decision == "keep"


def test_decide_gate_unscoreable_incumbent_promotes_by_default():
    r = decide_gate({"auc_amp": 0.60}, {}, auc_floor=0.55, epsilon=0.01)
    assert r.decision == "promote"
    assert "non mesurable" in r.reason


# ─────────────────────────────────────────────────────────────────────────────
#  maybe_refresh — bout en bout avec une vraie stratégie MLBackend (v11)
# ─────────────────────────────────────────────────────────────────────────────
def _make_synthetic_ohlcv(n: int, seed: int = 0, start_price: float = 100.0) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    start = dt.datetime(2020, 1, 1)
    times = [start + dt.timedelta(hours=i) for i in range(n)]
    rets = rng.normal(0, 0.01, n)
    close = start_price * np.cumprod(1 + rets)
    open_ = np.concatenate([[start_price], close[:-1]])
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.002, n)))
    volume = rng.uniform(100, 1000, n)
    return pl.DataFrame({"time": times, "open": open_, "high": high,
                         "low": low, "close": close, "volume": volume})


def _v11_strategy():
    from app.strategies.opus_omnibus_v11 import Strategy
    return Strategy()


_FAST_PARAMS = {"n_estimators": 30, "num_leaves": 7, "warmup_bars": 250,
                "gate_holdout_bars": 250, "gate_min_window_bars": 700}


@pytest.mark.slow
def test_maybe_refresh_cold_start_publishes_initial(tmp_path):
    base = str(tmp_path / "models")
    df = _make_synthetic_ohlcv(1400, seed=1)
    strat = _v11_strategy()

    result = maybe_refresh(strat, "BTC/USDC", "1h", df, params=dict(_FAST_PARAMS, gate_auc_floor=0.0),
                           recipe="opus_omnibus_v11", source="test", base_dir=base)

    assert result["decision"] in ("initial", "promote")
    assert strat.is_trained
    art = registry.latest_promoted("BTC/USDC", "1h", "opus_omnibus_v11", base_dir=base)
    assert art is not None
    assert art.version_id == result["published_version"]


@pytest.mark.slow
def test_maybe_refresh_rejects_candidate_and_restores_incumbent(tmp_path):
    base = str(tmp_path / "models")
    df = _make_synthetic_ohlcv(1400, seed=2)
    strat = _v11_strategy()

    first = maybe_refresh(strat, "BTC/USDC", "1h", df,
                          params=dict(_FAST_PARAMS, gate_auc_floor=0.0),
                          recipe="opus_omnibus_v11", source="test", base_dir=base)
    assert first["decision"] in ("initial", "promote")
    first_version = registry.latest_promoted("BTC/USDC", "1h", "opus_omnibus_v11", base_dir=base).version_id

    df2 = _make_synthetic_ohlcv(1400, seed=3)
    second = maybe_refresh(strat, "BTC/USDC", "1h", df2,
                           params=dict(_FAST_PARAMS, gate_auc_floor=1.1),  # impossible -> keep garanti
                           recipe="opus_omnibus_v11", source="test", base_dir=base)
    assert second["decision"] == "keep"

    # Le sortant publié reste le premier -- le candidat rejeté n'a rien écrasé.
    still = registry.latest_promoted("BTC/USDC", "1h", "opus_omnibus_v11", base_dir=base)
    assert still.version_id == first_version
    # La stratégie a été rechargée sur le sortant, pas laissée sur le rejeté.
    assert strat.is_trained

    decisions = registry.read_decisions("BTC/USDC", "1h", "opus_omnibus_v11", base_dir=base)
    assert decisions[-1]["decision"] == "keep"


@pytest.mark.slow
def test_maybe_refresh_skips_when_insufficient_data(tmp_path):
    base = str(tmp_path / "models")
    df = _make_synthetic_ohlcv(500, seed=4)  # << gate_min_window_bars + holdout
    strat = _v11_strategy()

    result = maybe_refresh(strat, "BTC/USDC", "1h", df, params=dict(_FAST_PARAMS),
                           recipe="opus_omnibus_v11", source="test", base_dir=base)
    assert result["decision"] == "skipped"
    assert not strat.is_trained
    assert registry.latest_promoted("BTC/USDC", "1h", "opus_omnibus_v11", base_dir=base) is None


# ─────────────────────────────────────────────────────────────────────────────
#  recipe_gate_defaults — le gate doit évaluer un candidat sur LA MÊME
#  définition de labels que celle utilisée à son entraînement (régression
#  trouvée : opus_stat_retrained_v4 est single-horizon t+1, mais était gaté
#  contre des labels multi-horizon [1,3,6] pensés pour V11).
# ─────────────────────────────────────────────────────────────────────────────
def test_recipe_gate_defaults_single_horizon_strategy_by_name():
    d = recipe_gate_defaults("opus_stat_retrained_v4")
    assert d.get("label_horizons") == [1]
    assert d.get("amp_top_pct") == pytest.approx(0.30)


def test_recipe_gate_defaults_frozen_pretrained_strategy_by_name():
    d = recipe_gate_defaults("opus_stat_pretrained_v4")
    assert d.get("label_horizons") == [1]


def test_recipe_gate_defaults_multi_horizon_strategy_unaffected():
    d = recipe_gate_defaults("opus_omnibus_v11")
    assert d.get("label_horizons") == [1, 3, 6]


def test_recipe_gate_defaults_by_instance_matches_by_name():
    strat = _v11_strategy()
    assert recipe_gate_defaults(strat) == recipe_gate_defaults("opus_omnibus_v11")


def test_recipe_gate_defaults_unknown_recipe_returns_empty():
    assert recipe_gate_defaults("this_strategy_does_not_exist") == {}


def test_recipe_gate_defaults_explicit_params_override_recipe_default():
    # GateConfig.from_params applique les params explicites APRÈS les défauts
    # de recette — un appelant qui force label_horizons doit toujours gagner.
    from app.ml.policy import GateConfig
    merged = {**recipe_gate_defaults("opus_stat_retrained_v4"), "label_horizons": [1, 3]}
    gc_ = GateConfig.from_params(merged)
    assert gc_.label_horizons == [1, 3]


# ─────────────────────────────────────────────────────────────────────────────
#  score_holdout / decide_gate — recette à persistance non-V4 (ex.
#  ml_dynamic_threshold : un seul {path}.lgb, pas de bundle amp+dir). Le gate
#  doit répondre "keep" avec un motif honnête ("format non reconnu"), pas le
#  trompeur "labels mono-classe / holdout dégénéré" qui suggère un problème
#  de données plutôt qu'une recette non couverte par ce scorer.
# ─────────────────────────────────────────────────────────────────────────────
def _make_synthetic_ohlcv_local(n: int, seed: int = 0) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    ret = rng.normal(0, 0.01, n)
    close = 100.0 * np.cumprod(1.0 + ret)
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    vol = rng.uniform(100, 1000, n)
    times = [dt.datetime(2024, 1, 1) + dt.timedelta(hours=i) for i in range(n)]
    return pl.DataFrame({"time": times, "open": open_, "high": high,
                         "low": low, "close": close, "volume": vol})


def test_score_holdout_unsupported_single_booster_format(tmp_path):
    prefix = str(tmp_path / "ml_dynamic_threshold_1h")
    # Un seul .lgb (pas de .amp.lgb/.dir.lgb) reproduit exactement ce que
    # ml_dynamic_threshold.save_model() écrit — contenu factice, jamais lu
    # avant le court-circuit "unsupported_format".
    with open(f"{prefix}.lgb", "w", encoding="utf-8") as f:
        f.write("not a real booster")
    holdout_df = _make_synthetic_ohlcv_local(300)
    metrics = score_holdout(prefix, holdout_df)
    assert metrics == {"unsupported_format": True}


def test_score_holdout_missing_artifact_is_plain_empty(tmp_path):
    # Chemin totalement absent (aucun fichier) : reste {} — pas le nouveau
    # marqueur, qui est réservé au cas "artefact présent, format inconnu".
    prefix = str(tmp_path / "nothing_here_1h")
    holdout_df = _make_synthetic_ohlcv_local(300)
    assert score_holdout(prefix, holdout_df) == {}


def test_decide_gate_unsupported_format_keeps_with_honest_reason():
    gate = decide_gate({"unsupported_format": True}, None)
    assert gate.decision == "keep"
    assert "format de persistance non reconnu" in gate.reason
    assert "mono-classe" not in gate.reason
