"""``save_model()`` doit écrire les artefacts que le registre attend.

Régression du cycle d'entraînement ML observé en production : les stratégies
« bespoke » indexent leur magasin de modèles selon l'appelant — ``fit()``
(chemin registre/gate) écrit sous ``"default"``, ``score()`` sous le SYMBOLE,
``ml_dynamic_threshold`` sous le TF INFÉRÉ des bougies — alors que
``save_model()`` déduit le TF du nom de fichier. Quand les deux ne
coïncidaient pas, l'écriture était un no-op silencieux et l'échec ne se
manifestait que deux couches plus loin, sous deux messages trompeurs :

    [MLPolicy] 1h/stat48_v4 : keep — candidat : auc_amp indisponible
               (labels mono-classe / holdout dégénéré)
    [ModelRegistry] publish : artefacts absents pour …/stat48_v5_30m

— deux diagnostics qui accusaient les données alors que rien n'avait été écrit.
"""
import datetime as dt
import os

import numpy as np
import polars as pl
import pytest

pytest.importorskip("lightgbm")

import app.ml.model_registry as registry


def _ohlcv(n: int, seed: int = 7, tf_minutes: int = 60) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    start = dt.datetime(2024, 1, 1)
    times = [start + dt.timedelta(minutes=tf_minutes * i) for i in range(n)]
    close = 100.0 * np.cumprod(1 + rng.normal(0, 0.01, n))
    open_ = np.concatenate([[100.0], close[:-1]])
    return pl.DataFrame({
        "time": times, "open": open_, "close": close,
        "high": np.maximum(open_, close) * 1.003,
        "low":  np.minimum(open_, close) * 0.997,
        "volume": rng.uniform(100, 1000, n),
    })


@pytest.mark.parametrize("module_name, recipe", [
    ("scoring_statistique_opus_v4", "stat48_v4"),
    ("scoring_statistique_opus_v5", "stat48_v5"),
    ("ml_dynamic_threshold",        "dyn_threshold_v1"),
])
def test_fit_then_save_model_produces_the_artifacts_publish_expects(
        module_name, recipe, tmp_path):
    import importlib

    from app.core.indicators import precompute_df
    strat = importlib.import_module(f"app.strategies.{module_name}").Strategy()

    # Mêmes colonnes qu'en live (scanner.fetch_ohlcv) et que ce que
    # train_runner.load_offline_ohlcv fournit désormais au chemin UI.
    strat.fit(precompute_df(_ohlcv(3000)), params={strat.name: {}})
    if not getattr(strat, "is_trained", False):
        pytest.skip(f"{module_name} n'a pas produit de modèle sur ces bougies")

    # Le préfixe se termine par "_{tf}" : c'est le contrat de policy.maybe_refresh.
    prefix = str(tmp_path / f"{recipe}_1h")
    strat.save_model(prefix)

    assert registry.missing_artifacts(recipe, prefix) == [], (
        f"{module_name}.save_model() n'a pas écrit les fichiers attendus par "
        f"la recette {recipe}"
    )


def test_save_model_without_training_warns_instead_of_silent_noop(tmp_path, caplog):
    """Sans modèle entraîné, ne rien écrire est correct — mais le dire.
    Le ``logger.debug`` d'origine rendait l'échec invisible en exploitation."""
    import importlib
    strat = importlib.import_module("app.strategies.scoring_statistique_opus_v4").Strategy()

    prefix = str(tmp_path / "stat48_v4_1h")
    with caplog.at_level("WARNING"):
        strat.save_model(prefix)

    assert not os.path.exists(f"{prefix}.amp.lgb")
    assert "aucun modèle entraîné à persister" in caplog.text
