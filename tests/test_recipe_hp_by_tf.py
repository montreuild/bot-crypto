"""ML-11 — surcharges d'hyperparamètres PAR TIMEFRAME (``hp_by_tf``).

Le constat qui a produit ce bloc : la calibration isotone dégrade l'ECE de
+461 % en 1h alors qu'elle l'améliore en 15m/30m (mesure de
``scripts/measure_calibration_and_pruning.py``). La conclusion était écrite
depuis longtemps et n'avait jamais été appliquée — ``omnibus_v4_multi``
portait ``calibrate: true`` pour tous les TF, sans moyen de déroger.

Ce que ces tests verrouillent, et POURQUOI chacun :

1. La précédence est inversée par rapport au reste de la recette : ``hp_by_tf``
   gagne sur les ``params`` reçus. Sans cela le réglage serait inerte — chaque
   stratégie recopie ``hp`` dans ses ``_DEFAULTS`` et ses ``fixed_params``,
   valeurs sans timeframe qui arrivent donc toujours dans ``params``.
2. Les DEUX chemins d'entraînement l'appliquent. Le chemin recette
   (``recipe_trainer``) et le chemin stratégie (``MLBackend``) ont des sites
   d'application différents ; n'en câbler qu'un rendait la mesure vraie dans
   les scripts et fausse en production.
3. Le hash de recette bouge quand ``hp_by_tf`` bouge, sinon deux lignées de
   modèles différentes partageraient une identité.
"""
import datetime as dt

import numpy as np
import polars as pl
import pytest

pytest.importorskip("lightgbm")

from app.ml import recipe_trainer as rt
from app.ml.recipe import Recipe, load_recipe, tf_hp_overrides


@pytest.fixture(scope="module")
def ohlcv():
    from app.core.indicators import precompute_df
    rng = np.random.RandomState(9)
    n = 4000
    times = [dt.datetime(2023, 1, 1) + dt.timedelta(hours=i) for i in range(n)]
    close = 100 * np.cumprod(1 + rng.normal(0.0002, 0.012, n))
    open_ = np.concatenate([[100.0], close[:-1]])
    return precompute_df(pl.DataFrame({
        "time": times, "open": open_, "close": close,
        "high": np.maximum(open_, close) * 1.004,
        "low": np.minimum(open_, close) * 0.996,
        "volume": rng.uniform(100, 1000, n),
    }))


# ─────────────────────────────────────────────────────────────────────────────
#  La déclaration
# ─────────────────────────────────────────────────────────────────────────────
class TestDeclaration:
    def test_omnibus_v4_multi_disables_calibration_in_1h_only(self):
        """ML-11 lui-même : l'écart entre ce que le doc affirmait et ce que le
        code faisait. Ce test EST la décision — le supprimer la révoque."""
        r = load_recipe("omnibus_v4_multi")
        assert r.hp["calibrate"] is True, "le défaut multi-TF reste calibré"
        assert r.hp_for_tf("1h") == {"calibrate": False}
        for tf in ("15m", "30m", "4h", "1d"):
            assert r.hp_for_tf(tf) == {}, f"{tf} ne doit pas être touché"

    def test_absent_block_yields_no_override(self):
        r = Recipe(name="x")
        assert r.hp_for_tf("1h") == {}
        assert r.hp_for_tf(None) == {}

    def test_hash_moves_when_a_tf_block_moves(self):
        """Sans cela, un modèle 1h non calibré et un modèle 1h calibré
        porteraient la même identité de recette."""
        base = Recipe(name="x", hp={"calibrate": True})
        derived = Recipe(name="x", hp={"calibrate": True},
                         hp_by_tf={"1h": {"calibrate": False}})
        assert base.hash() != derived.hash()

    def test_tf_hp_overrides_never_raises(self):
        """Pendant tolérant : MLBackend s'entraîne aussi là où ``recipes/``
        n'existe pas (tests, scripts hors dépôt)."""
        assert tf_hp_overrides("recette_qui_nexiste_pas", "1h") == {}
        assert tf_hp_overrides(None, "1h") == {}
        assert tf_hp_overrides("omnibus_v4_multi", None) == {}


# ─────────────────────────────────────────────────────────────────────────────
#  Chemin recette — app.ml.recipe_trainer
# ─────────────────────────────────────────────────────────────────────────────
class TestRecipePath:
    def test_1h_trains_uncalibrated(self, ohlcv):
        out = rt.train("omnibus_v4_multi", ohlcv.slice(0, 3000), "1h")
        assert out is not None
        assert out.train_meta["calibrated"] is False
        assert out.calibrators == {}

    def test_other_tfs_keep_calibration(self, ohlcv):
        out = rt.train("omnibus_v4_multi", ohlcv.slice(0, 3000), "15m")
        assert out is not None
        assert out.train_meta["calibrated"] is True

    def test_tf_block_beats_an_explicit_param(self, ohlcv):
        """La précédence inversée, mesurée là où elle compte : un appelant qui
        passe ``calibrate=True`` en 1h — ce que font tous les ``_DEFAULTS`` du
        dépôt — n'obtient PAS de calibration."""
        out = rt.train("omnibus_v4_multi", ohlcv.slice(0, 3000), "1h",
                       params={"calibrate": True})
        assert out.train_meta["calibrated"] is False

    def test_train_multi_applies_the_same_block(self, ohlcv):
        frames = {"A": ohlcv.slice(0, 2000), "B": ohlcv.slice(1000, 2000)}
        out = rt.train_multi("omnibus_v4_multi", frames, "1h",
                             params={"calibrate": True})
        assert out is not None
        assert out.train_meta["calibrated"] is False, (
            "le pooling passe par le même _fit_heads — le rater ici ferait "
            "diverger le futur modèle actions du modèle crypto")


# ─────────────────────────────────────────────────────────────────────────────
#  Chemin stratégie — app.ml.backend.MLBackend
# ─────────────────────────────────────────────────────────────────────────────
class TestStrategyPath:
    def test_mixin_hands_its_recipe_to_the_backend(self):
        """Sans cette liaison, le backend ne saurait pas quoi surcharger."""
        import importlib
        strat = importlib.import_module("app.strategies.opus_omnibus_v11").Strategy()
        assert strat.ml.recipe == "omnibus_v4_multi"

    def test_backend_train_is_uncalibrated_in_1h(self, ohlcv):
        """Le chemin réellement emprunté en production (retrain live et
        ``score()`` du backtest) — celui que ML-11 devait atteindre."""
        import importlib
        strat = importlib.import_module("app.strategies.opus_omnibus_v11").Strategy()
        assert strat._train(ohlcv.slice(0, 3000), "1h",
                            {"calibrate": True, "warmup_bars": 750})
        meta = strat.ml.train_meta.get("1h", {})
        assert meta, "pas de train_meta — l'entraînement n'a pas eu lieu"
        assert not strat.ml.state.amp_cal.get("1h")
        assert not strat.ml.state.dir_cal.get("1h")
