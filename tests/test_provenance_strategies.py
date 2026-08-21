"""TEST-02 — la provenance ML-02 traverse aussi les chemins « stratégie ».

``BaseStrategyML.save_model`` déclare ``extra_meta`` (bloc provenance + verdict
de gate) depuis ML-02, mais quatre stratégies l'avaient redéclaré sans lui :
`opus_omnibus_v11`, `scoring_statistique_opus_v4/v5`, `ml_dynamic_threshold`.
Un appelant qui le passait recevait un ``TypeError`` ; personne ne le passait,
donc l'artefact n'a jamais porté sa provenance sur ces chemins.

Le contrat tient à deux endroits : la SIGNATURE (l'appel ne lève pas) et
l'ÉCRITURE (le bloc arrive dans le meta.json). Vérifier la première seule
laisserait passer un paramètre accepté puis jeté.
"""
import inspect
import json
import os

import pytest

from app.engine.engine import BaseStrategyML

_EXTRA = {"provenance": {"train_symbol": "ETH/USDC", "trained_at": "2026-08-22"},
          "gate": {"verdict": "promote"}}


def _strategies_ml():
    import importlib
    import pkgutil

    import app.strategies as pkg
    out = []
    for m in pkgutil.iter_modules(pkg.__path__):
        try:
            mod = importlib.import_module(f"app.strategies.{m.name}")
        except Exception:
            continue
        S = getattr(mod, "Strategy", None)
        if isinstance(S, type) and issubclass(S, BaseStrategyML):
            out.append((m.name, S))
    return out


def test_toutes_les_strategies_ml_acceptent_extra_meta():
    strategies = _strategies_ml()
    assert strategies, "aucune stratégie ML découverte — test vacant"
    manquants = [
        nom for nom, S in strategies
        if "extra_meta" not in inspect.signature(S.save_model).parameters
    ]
    assert not manquants, (
        f"save_model sans extra_meta : {manquants}. Le bloc de provenance "
        f"ML-02 déclaré par BaseStrategyML n'atteint pas ces artefacts."
    )


@pytest.mark.parametrize("recette", ["stat48_v5", "dyn_threshold_v1"])
def test_la_provenance_arrive_dans_le_meta_json(recette, tmp_path):
    """Les deux formats d'artefact (bundle amp+dir, booster unique)."""
    pytest.importorskip("lightgbm")
    import datetime as dt

    import numpy as np
    import polars as pl

    from app.core.indicators import precompute_df
    from app.ml import recipe_trainer as rt

    rng = np.random.RandomState(3)
    n = 4000
    times = [dt.datetime(2023, 1, 1) + dt.timedelta(hours=i) for i in range(n)]
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    df = precompute_df(pl.DataFrame({
        "time": times, "open": close, "high": close * 1.002,
        "low": close * 0.998, "close": close,
        "volume": rng.uniform(10, 100, n),
    }))

    out = rt.train(recette, df, "1h", params={"n_estimators": 20, "num_leaves": 7})
    assert out is not None
    prefix = str(tmp_path / f"{recette}_1h")
    assert out.save(prefix, _EXTRA)

    meta_path = prefix + ".meta.json"
    assert os.path.exists(meta_path)
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    assert meta.get("provenance") == _EXTRA["provenance"]
    assert meta.get("gate") == _EXTRA["gate"]


def test_sans_extra_meta_le_meta_json_ne_porte_aucun_bloc(tmp_path):
    """Le passe-plat ne doit rien inventer : pas de provenance vide."""
    pytest.importorskip("lightgbm")
    import datetime as dt

    import numpy as np
    import polars as pl

    from app.core.indicators import precompute_df
    from app.ml import recipe_trainer as rt

    rng = np.random.RandomState(4)
    n = 4000
    times = [dt.datetime(2023, 1, 1) + dt.timedelta(hours=i) for i in range(n)]
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    df = precompute_df(pl.DataFrame({
        "time": times, "open": close, "high": close * 1.002,
        "low": close * 0.998, "close": close,
        "volume": rng.uniform(10, 100, n),
    }))
    out = rt.train("stat48_v5", df, "1h", params={"n_estimators": 20, "num_leaves": 7})
    prefix = str(tmp_path / "stat48_v5_1h")
    assert out.save(prefix)
    with open(prefix + ".meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert "provenance" not in meta and "gate" not in meta


def test_managed_externally_atteint_le_backend_ml():
    """`backtest.py` pose `strat.managed_externally = True` pour couper le
    réentraînement inline. Sur les stratégies à `MLBackendMixin`, seule la
    propriété du mixin route vers le backend — l'attribut simple que
    `BaseStrategyML` déclarait à côté était mort, et rien ne le disait."""
    from app.ml.backend.mixin import MLBackendMixin

    mixtes = [(nom, S) for nom, S in _strategies_ml()
              if issubclass(S, MLBackendMixin)]
    assert mixtes, "aucune stratégie MLBackendMixin — test vacant"

    for nom, S in mixtes:
        strat = S()
        assert strat.managed_externally is False, nom
        strat.managed_externally = True
        assert strat.ml.managed_externally is True, nom
        assert strat.managed_externally is True, nom
        strat.managed_externally = False
        assert strat.ml.managed_externally is False, nom


def test_managed_externally_reste_un_drapeau_sans_le_mixin():
    """Les stratégies ML sans backend partagé gardent le drapeau local."""
    from app.ml.backend.mixin import MLBackendMixin

    simples = [(nom, S) for nom, S in _strategies_ml()
               if not issubclass(S, MLBackendMixin)]
    assert simples, "aucune stratégie ML hors mixin — test vacant"

    for nom, S in simples:
        strat = S()
        assert strat.managed_externally is False, nom
        strat.managed_externally = True
        assert strat.managed_externally is True, nom
