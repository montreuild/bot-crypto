"""Étape C — entraîner depuis la seule recette, sans classe Strategy.

``features.catalog`` était déclaré par les recettes depuis l'étape B mais
n'était dispatché nulle part : il n'entrait que dans ``Recipe.hash()``. Seule
une classe ``Strategy`` savait construire des features, d'où l'asymétrie
relevée par §2 de la conception — lecture pilotée par la recette
(``build_predictor``), écriture pilotée par la stratégie (``Strategy().fit``).
"""
import datetime as dt
import os

import numpy as np
import polars as pl
import pytest

pytest.importorskip("lightgbm")

import app.ml.features_catalog as fc
import app.ml.labelling as lb
import app.ml.policy as policy
from app.ml import recipe_trainer as rt
from app.ml.recipe import load_recipe


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
#  Catalogues de features
# ─────────────────────────────────────────────────────────────────────────────
class TestFeaturesCatalog:
    def test_every_recipe_catalog_is_registered(self):
        """Une recette qui déclare un catalogue inconnu ne serait entraînable
        par personne — le contrat doit être vérifié pour tout le dépôt."""
        from app.ml.recipe import available_recipes
        known = set(fc.available_catalogs()) | {"none"}
        for name in available_recipes():
            assert load_recipe(name).features_catalog in known, name

    def test_unknown_catalog_raises_rather_than_defaulting(self):
        with pytest.raises(KeyError, match="inconnu"):
            fc.build("nexiste_pas@1", pl.DataFrame({"close": [1.0]}))

    @pytest.mark.parametrize("catalog", ["v4_polars@1", "stat48@1", "dyn_threshold@1"])
    def test_frame_is_row_aligned_and_carries_close(self, catalog, ohlcv):
        """Le labelleur travaille sur les mêmes lignes que les features : sans
        ``close`` dans le frame, le réalignement retomberait sur l'appelant —
        c'est là que se logent les décalages d'un indice."""
        fs = fc.build(catalog, ohlcv, {"adx_threshold": 20.0})
        assert fs is not None and len(fs) == len(ohlcv)
        assert "close" in fs.frame.columns
        assert fs.matrix().shape == (len(ohlcv), len(fs.names))

    def test_stat48_names_match_the_builder_column_for_column(self, ohlcv):
        """L'ordre est le contrat : la matrice est empilée par column_stack, un
        nom mal placé associerait une importance à la mauvaise feature."""
        from app.strategies.scoring_statistique_opus_v4 import _build_features
        raw = _build_features(ohlcv, adx_threshold=20.0)
        fs = fc.build("stat48@1", ohlcv, {"adx_threshold": 20.0})
        assert raw.shape[1] == len(fs.names)
        assert np.array_equal(np.nan_to_num(raw), np.nan_to_num(fs.matrix()))

    def test_stat48_really_has_56_columns_not_48(self, ohlcv):
        """La docstring d'origine annonce 48 (et les recettes s'appellent
        stat48_*), mais le bloc BB — largeur + rang centile × 4 lags — a été
        ajouté sans que le décompte suive."""
        assert len(fc.stat48_feature_names()) == 56
        assert fc.build("stat48@1", ohlcv).matrix().shape[1] == 56

    def test_feature_names_are_unique(self):
        names = fc.stat48_feature_names()
        assert len(set(names)) == len(names)


# ─────────────────────────────────────────────────────────────────────────────
#  Schémas de labellisation
# ─────────────────────────────────────────────────────────────────────────────
class TestLabelling:
    def test_declared_scheme_produces_the_declared_heads(self, ohlcv):
        for name in ("omnibus_v4_multi", "stat48_v5", "dyn_threshold_v1"):
            r = load_recipe(name)
            fs = fc.build(r.features_catalog, ohlcv, r.features_params)
            params = {"label_horizons": r.label_horizons,
                      "amp_top_pct": r.amp_top_pct, **r.features_params}
            lab = lb.build(r.label_scheme, fs.frame, params)
            assert lab is not None and lab.n > 0, name
            assert set(r.heads) <= set(lab.heads), name

    def test_labels_are_shorter_than_the_frame(self, ohlcv):
        """Labelliser la barre t demande t+h : les dernières barres ne sont pas
        labellisables, et c'est au labelleur d'annoncer sa longueur."""
        fs = fc.build("v4_polars@1", ohlcv)
        lab = lb.build("amp_dir_quantile", fs.frame, {"label_horizons": [1, 3, 6]})
        assert 0 < lab.n < len(fs)

    def test_vol_adaptive_dir_is_single_headed(self, ohlcv):
        fs = fc.build("dyn_threshold@1", ohlcv)
        lab = lb.build("vol_adaptive_dir", fs.frame, {"lookahead": 3})
        assert lab.heads == ["dir"]

    def test_unknown_scheme_raises(self, ohlcv):
        with pytest.raises(KeyError, match="inconnu"):
            lb.build("nexiste_pas", pl.DataFrame({"close": [1.0, 2.0]}))

    def test_frame_without_close_is_refused(self):
        with pytest.raises(ValueError, match="close"):
            lb.build("amp_dir_quantile", pl.DataFrame({"x": [1.0]}))

    def test_scheme_is_read_from_the_recipe_and_enters_its_hash(self):
        import dataclasses
        assert load_recipe("dyn_threshold_v1").label_scheme == "vol_adaptive_dir"
        assert load_recipe("stat48_v5").label_scheme == "amp_dir_quantile"
        r = load_recipe("stat48_v5")
        assert r.hash() != dataclasses.replace(r, label_scheme="vol_adaptive_dir").hash()


# ─────────────────────────────────────────────────────────────────────────────
#  Entraînement piloté par la recette
# ─────────────────────────────────────────────────────────────────────────────
class TestSupports:
    def test_accepts_the_production_recipe(self):
        assert rt.supports("omnibus_v4_multi") is None

    def test_refuses_recipes_without_artifact(self):
        assert "proxy" in (rt.supports("proxy_indicators") or "")

    def test_refuses_an_unknown_recipe(self):
        assert rt.supports("nexiste_pas") is not None

    def test_accepts_the_recipes_it_can_actually_train(self):
        assert rt.supports("stat48_v5") is None
        assert rt.supports("dyn_threshold_v1") is None


class TestTrain:
    def test_no_strategy_module_is_imported_to_train(self, ohlcv, monkeypatch):
        """Le point de tout le chantier : entraîner ne doit plus dépendre d'une
        classe Strategy. On casse l'import de app.strategies.* pour le prouver."""
        import importlib
        real = importlib.import_module

        def _guard(name, *a, **k):
            if name.startswith("app.strategies.") and "scoring_statistique_opus_v4" not in name:
                raise AssertionError(f"import interdit pendant l'entraînement : {name}")
            return real(name, *a, **k)

        monkeypatch.setattr(importlib, "import_module", _guard)
        out = rt.train("stat48_v5", ohlcv, "1h", params={"n_estimators": 20, "num_leaves": 7})
        assert out is not None

    def test_bundle_recipe_writes_the_three_files(self, ohlcv, tmp_path):
        out = rt.train("stat48_v5", ohlcv, "1h", params={"n_estimators": 20, "num_leaves": 7})
        assert out is not None and sorted(out.boosters) == ["amp", "dir"]
        prefix = str(tmp_path / "stat48_v5_1h")
        assert out.save(prefix)
        for sfx in (".amp.lgb", ".dir.lgb", ".meta.json"):
            assert os.path.exists(prefix + sfx)

    def test_single_head_recipe_writes_one_booster(self, ohlcv, tmp_path):
        out = rt.train("dyn_threshold_v1", ohlcv, "1h", params={"n_estimators": 20})
        assert out is not None and list(out.boosters) == ["dir"]
        prefix = str(tmp_path / "dyn_threshold_v1_1h")
        assert out.save(prefix)
        assert os.path.exists(prefix + ".lgb")
        assert not os.path.exists(prefix + ".amp.lgb")

    def test_artifact_layout_matches_what_the_registry_expects(self, ohlcv, tmp_path):
        import app.ml.model_registry as registry
        for name in ("stat48_v5", "dyn_threshold_v1"):
            prefix = str(tmp_path / f"{name}_1h")
            rt.train(name, ohlcv, "1h", params={"n_estimators": 20}).save(prefix)
            assert registry.missing_artifacts(name, prefix) == [], name

    def test_diagnostics_are_produced_for_every_head(self, ohlcv):
        out = rt.train("stat48_v5", ohlcv, "1h", params={"n_estimators": 20, "num_leaves": 7})
        for head in ("amp", "dir"):
            assert f"auc_{head}" in out.train_meta
            top = out.train_meta[f"feature_importance_{head}"]
            assert top and set(top[0]) == {"feature", "gain"}
            assert top[0]["feature"] in out.feature_names

    def test_stat48_artifact_becomes_scoreable_by_the_generic_gate(self, ohlcv, tmp_path):
        """LE gain de l'étape C sur cette recette.

        ``save_lgb_with_scaler`` ne sérialise ni features ni médianes : le
        scorer générique ne peut pas reconstruire la matrice d'entrée et
        rapporte ``unsupported_format``, ce qui fait conclure « keep » au gate
        quoi qu'il arrive — stat48_v4/v5 n'étaient donc JAMAIS promouvables.
        L'artefact produit ici porte ses noms de colonnes, donc se score.
        """
        train_df, holdout = ohlcv.slice(0, 3000), ohlcv.tail(1200)
        prefix = str(tmp_path / "stat48_v5_1h")
        rt.train("stat48_v5", train_df, "1h",
                 params={"n_estimators": 20, "num_leaves": 7}).save(prefix)

        metrics = policy.score_holdout(prefix, holdout, strategy="stat48_v5")
        assert not metrics.get("unsupported_format"), metrics
        assert metrics.get("auc_amp") is not None, metrics

        # …et le gate peut donc rendre autre chose qu'un « keep » par défaut.
        gate = policy.decide_gate(metrics, None, auc_floor=0.0, metric="auc_amp")
        assert gate.decision == "initial", gate.reason

    def test_old_path_is_still_unscoreable_which_is_why_this_matters(self, ohlcv, tmp_path):
        """Contrôle : sans ce chantier, le même artefact reste illisible."""
        from app.strategies.scoring_statistique_opus_v5 import Strategy
        s = Strategy()
        s.fit(ohlcv.slice(0, 3000), params={s.name: {}})
        prefix = str(tmp_path / "old_stat48_v5_1h")
        s.save_model(prefix)
        metrics = policy.score_holdout(prefix, ohlcv.tail(1200), strategy="stat48_v5")
        assert metrics.get("unsupported_format") is True


class TestMLBackendEquivalence:
    """La condition qui rend la bascule d'une recette omnibus sûre.

    La conception exige que toute divergence soit énumérée AVANT la bascule.
    Pour ``v4_polars@1`` + ``amp_dir_quantile`` — la famille qui tourne en
    production — il n'y en a aucune : ``recipe_trainer`` reprend les réglages
    LightGBM de ``app.ml.backend.trainer`` à l'identique (``scale_pos_weight``,
    early stopping, absence de ``seed`` explicite, ``max_bin=63``).
    """

    def test_predictions_are_bit_identical_to_mlbackend(self, ohlcv):
        import app.strategies.opus_omnibus_v11 as v11

        train_df, holdout = ohlcv.slice(0, 3000), ohlcv.tail(900)
        strat = v11.Strategy()
        strat.fit(train_df, params={strat.name: {}})

        out = rt.train("omnibus_v4_multi", train_df, "1h")
        assert out is not None

        full = fc.build("v4_polars@1", holdout)
        idx = [full.names.index(c) for c in out.feature_names]
        X = full.matrix()[:, idx]

        for head, store in (("amp", strat.ml.state.amp_models),
                            ("dir", strat.ml.state.dir_models)):
            ref = store.get("1h")
            assert ref is not None, f"MLBackend n'a pas de modèle {head}"
            gap = float(np.max(np.abs(ref.predict(X) - out.boosters[head].predict(X))))
            assert gap == pytest.approx(0.0, abs=1e-9), (
                f"tête {head} : écart {gap} — la bascule changerait le modèle")

    def test_calibration_is_produced_and_persisted(self, ohlcv, tmp_path):
        """Un modèle calibré à l'entraînement mais servi non calibré serait
        pire qu'un modèle non calibré : le décalage serait invisible."""
        import json
        out = rt.train("omnibus_v4_multi", ohlcv.slice(0, 3000), "1h")
        assert out.train_meta["calibrated"] is True
        assert set(out.calibrators) == {"amp", "dir"}
        assert out.train_meta["cal_err"]

        prefix = str(tmp_path / "omnibus_v4_multi_1h")
        assert out.save(prefix)
        with open(prefix + ".meta.json", encoding="utf-8") as fh:
            meta = json.load(fh)
        assert meta.get("amp_cal") and meta.get("dir_cal")

    def test_pruning_memory_travels_through_the_artifact(self, ohlcv):
        """MLBackend garde les features retenues en mémoire de processus ; ici
        elles passent par l'artefact, donc restent inspectables."""
        first = rt.train("omnibus_v4_multi", ohlcv.slice(0, 3000), "1h")
        kept = first.train_meta["kept_features"]
        assert 0 < len(kept) < first.train_meta["n_features"]

        second = rt.train("omnibus_v4_multi", ohlcv.slice(0, 3000), "1h",
                          params={"prune_features": True, "kept_features": kept})
        assert second.train_meta["n_features"] == len(kept)
        assert set(second.feature_names) <= set(kept)

    def test_pruning_ignores_a_list_that_bit_too_far(self, ohlcv):
        """Sous 50 features gardées, MLBackend repart du catalogue complet —
        même garde ici, sinon un élagage dégénéré s'auto-entretiendrait."""
        out = rt.train("omnibus_v4_multi", ohlcv.slice(0, 3000), "1h",
                       params={"prune_features": True, "kept_features": ["ret", "ret_intra"]})
        assert out.train_meta["n_features"] > 50
