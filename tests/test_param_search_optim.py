"""Param Search Optim : OPTION (activée par défaut) appliquée PENDANT
random_search/bayesian_search/grid_search — PAS un 4e mode de recherche, PAS
un dépistage à part sur une fenêtre séparée. Les premiers essais de la
recherche elle-même (fenêtre complète, budget demandé) servent de dépistage ;
un checkpoint fige ensuite les paramètres à faible impact pour le reste de
l'appel, sur le MÊME pool de process. Tests de contrôle avec ``_eval`` stubbé
(rapide, pas de vrai Backtester)."""
import math
import random as _random

import polars as pl

from app.engine.optimizer import StrategyOptimizer


def _tiny_df(n=200):
    from datetime import datetime, timedelta
    t0 = datetime(2024, 1, 1)
    return pl.DataFrame({
        "time": [t0 + timedelta(hours=i) for i in range(n)],
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.0] * n, "volume": [10.0] * n,
    })


def _fake_eval_result(params: dict, score: float) -> dict:
    return {
        "params": dict(params), "is_score": score, "oos_score": score, "overfit": 1.0,
        "is_pnl": 0.0, "oos_pnl": 0.0, "is_sharpe": 0.0, "oos_sharpe": 0.0,
        "is_trades": 1, "oos_trades": 1, "is_wr": 50.0, "oos_wr": 50.0,
        "oos_dd": 0.0, "oos_alpha": 0.0, "final_score": score,
    }


def _make_opt(param_space, n=200):
    df = _tiny_df(n)
    return StrategyOptimizer(
        "opus_omnibus_v11", {"trading": {}}, df[:int(n * 0.7)], df[int(n * 0.7):],
        param_space=param_space, symbol="BTC/USDC", timeframe="1h",
    )


# Espace à 6 paramètres, cardinalité 3**6=729, choisi pour dépasser le seuil
# de déclenchement (_should_reduce_space : card > n_trials*200) avec un
# n_trials modeste dans les tests.
def _wide_space():
    return {f"p{i}": [0, 1, 2] for i in range(6)}


# Espace plus large (6 valeurs/param) pour donner assez de signal au
# dépistage EN BUDGET (échantillon plus petit que l'ancien dépistage à part).
def _wide_space_6vals():
    return {f"p{i}": list(range(6)) for i in range(6)}


class TestShouldReduceSpace:
    def test_small_space_never_reduced(self):
        opt = _make_opt({"a": [0, 1, 2], "b": [0, 1, 2]})  # 2 params < 6
        assert opt._should_reduce_space(n_trials=1) is False

    def test_well_covered_wide_space_not_reduced(self):
        opt = _make_opt(_wide_space())  # 729 combos
        assert opt._should_reduce_space(n_trials=1000) is False  # 1000*200 >> 729

    def test_poorly_covered_wide_space_is_reduced(self):
        opt = _make_opt(_wide_space())
        assert opt._should_reduce_space(n_trials=1) is True  # 729 > 1*200


class TestImpactScores:
    def test_important_param_gets_higher_impact_than_noise(self):
        opt = _make_opt({"important": [0, 1, 2], "noise": [0, 1, 2]})
        results = [
            _fake_eval_result({"important": 0, "noise": 0}, 0.0),
            _fake_eval_result({"important": 1, "noise": 1}, 1.0),
            _fake_eval_result({"important": 2, "noise": 2}, 2.0),
            _fake_eval_result({"important": 0, "noise": 1}, 0.1),
            _fake_eval_result({"important": 1, "noise": 0}, 1.1),
            _fake_eval_result({"important": 2, "noise": 2}, 2.1),
        ]
        impacts, best = opt._impact_scores(results, ["important", "noise"])
        assert impacts["important"] > impacts["noise"]
        assert best["important"] == 2

    def test_all_degenerate_scores_give_zero_impact(self):
        """Essais tous à -999 (0 trade OOS, ex: opus_omnibus_v12 sur une
        fenêtre de test trop courte) : aucun signal, impact nul partout —
        pas une erreur."""
        opt = _make_opt(_wide_space())
        results = [_fake_eval_result({f"p{j}": i % 3 for j in range(6)}, -999.0)
                  for i in range(12)]
        impacts, _ = opt._impact_scores(results, list(opt.param_space.keys()))
        assert all(v == 0.0 for v in impacts.values())

    def test_param_observed_at_a_single_value_gives_nan_not_zero(self):
        """Un paramètre jamais tiré qu'à UNE seule valeur dans le dépistage
        (donnée insuffisante) doit rendre NaN, PAS 0.0 — 0.0 signifierait
        « mesuré et plat », ce qui est un signal différent (et exploitable
        pour geler) de « je n'ai aucune idée ». Confondre les deux a mené à
        geler 20 paramètres sur 21 pour opus_omnibus_v9 à partir de 8 essais
        de dépistage seulement (régression détectée en vérification réelle)."""
        opt = _make_opt({"always_same": [0, 1, 2], "varies": [0, 1, 2]})
        results = [
            _fake_eval_result({"always_same": 0, "varies": 0}, 1.0),
            _fake_eval_result({"always_same": 0, "varies": 1}, 2.0),
            _fake_eval_result({"always_same": 0, "varies": 2}, 3.0),
        ]
        impacts, _ = opt._impact_scores(results, ["always_same", "varies"])
        assert math.isnan(impacts["always_same"])
        assert impacts["varies"] > 0


class TestFreezeParams:
    def test_freezes_low_impact_keeps_high_impact(self):
        opt = _make_opt({"important": [0, 1, 2], "noise": [0, 1, 2]})
        impacts = {"important": 2.0, "noise": 0.0}
        best_value = {"important": 2, "noise": 1}
        frozen, kept = opt._freeze_params(impacts, best_value, ["important", "noise"])
        assert "noise" in frozen
        assert "important" not in frozen
        assert kept == ["important"]

    def test_no_signal_freezes_nothing(self):
        opt = _make_opt(_wide_space())
        keys = list(opt.param_space.keys())
        impacts = {k: 0.0 for k in keys}
        best_value = {k: 0 for k in keys}
        frozen, kept = opt._freeze_params(impacts, best_value, keys)
        assert frozen == {}
        assert kept == keys

    def test_never_freezes_every_param(self):
        """Un espace de recherche totalement gelé (0 dimension restante)
        rendrait grid_search/random_search inutiles — jamais permis, même
        avec un seuil d'importance absurdement permissif."""
        opt = _make_opt(_wide_space())
        keys = list(opt.param_space.keys())
        impacts = {k: float(i) for i, k in enumerate(keys)}
        best_value = {k: 0 for k in keys}
        frozen, kept = opt._freeze_params(impacts, best_value, keys, min_impact_share=1.1)
        assert len(kept) >= 1

    def test_max_cardinality_mode_reduces_below_target(self):
        opt = _make_opt({f"p{i}": [0, 1, 2, 3, 4] for i in range(6)})  # 5**6=15625
        keys = list(opt.param_space.keys())
        impacts = {k: float(i) for i, k in enumerate(keys)}
        best_value = {k: 0 for k in keys}
        frozen, kept = opt._freeze_params(impacts, best_value, keys, max_cardinality=5000)
        remaining_card = 1
        for k in kept:
            remaining_card *= len(opt.param_space[k])
        assert remaining_card <= 5000
        assert len(kept) >= 1

    def test_nan_impact_params_never_frozen_in_optional_mode(self):
        """Régression : un dépistage en budget court (ex. 8 essais pour un
        espace à 21 paramètres, cas réel opus_omnibus_v9) laisse la plupart
        des paramètres avec une seule valeur observée -> impact NaN, PAS un
        signal de faible impact. Ils ne doivent JAMAIS être gelés en mode
        random/bayesian (contrairement au mode grid, où le gel est
        obligatoire) — seul un paramètre effectivement MESURÉ à faible
        impact (relativement aux autres impacts mesurés) est gelable."""
        opt = _make_opt({f"p{i}": [0, 1, 2] for i in range(6)})
        keys = list(opt.param_space.keys())
        # p0 : mesuré, impact dominant. p1 : mesuré, faible impact relatif.
        # p2..p5 : donnée insuffisante (NaN) -> jamais gelables ici.
        impacts = {"p0": 10.0, "p1": 0.1, **{f"p{i}": float("nan") for i in range(2, 6)}}
        best_value = {k: 0 for k in keys}
        frozen, kept = opt._freeze_params(impacts, best_value, keys)
        assert frozen == {"p1": 0}
        assert set(kept) == {"p0", "p2", "p3", "p4", "p5"}

    def test_nan_impact_params_frozen_as_last_resort_in_grid_mode(self):
        """En mode grid (réduction obligatoire), si les impacts mesurés ne
        suffisent pas à atteindre la cardinalité cible, les paramètres à
        donnée insuffisante (NaN) sont gelés en dernier recours — sinon la
        réduction resterait bloquée au-dessus du seuil."""
        opt = _make_opt({f"p{i}": [0, 1, 2, 3, 4] for i in range(6)})  # 5**6=15625
        keys = list(opt.param_space.keys())
        impacts = {k: float("nan") for k in keys}  # aucun signal mesuré du tout
        best_value = {k: 0 for k in keys}
        frozen, kept = opt._freeze_params(impacts, best_value, keys, max_cardinality=5000)
        remaining_card = 1
        for k in kept:
            remaining_card *= len(opt.param_space[k])
        assert remaining_card <= 5000
        assert frozen  # a dû geler malgré l'absence totale de signal
        assert frozen  # réduction obligatoire pour grid : gèle même sur signal faible


class TestFreezeFromResults:
    def test_freezes_and_mutates_param_space_in_place(self):
        opt = _make_opt({"important": [0, 1, 2], **{f"noise{i}": [0, 1, 2] for i in range(5)}})
        rng = _random.Random(0)
        results = []
        for i in range(30):
            important = i % 3
            # Le score ne dépend QUE de "important" — les "noise" sont tirés
            # indépendamment, sans aucun lien avec le score.
            params = {"important": important,
                      **{f"noise{j}": rng.choice([0, 1, 2]) for j in range(5)}}
            results.append(_fake_eval_result(params, score=float(important)))
        diag = opt._freeze_from_results(results, list(opt.param_space.keys()))

        assert diag is not None
        assert "important" not in diag["frozen_params"]
        assert "important" in diag["kept_params"]
        assert set(diag["frozen_params"]).issubset({f"noise{i}" for i in range(5)})
        for k in diag["frozen_params"]:
            assert len(opt.param_space[k]) == 1
        assert opt.param_space["important"] == [0, 1, 2]

    def test_restore_reverts_mutation(self):
        opt = _make_opt(_wide_space())
        original = {k: list(v) for k, v in opt.param_space.items()}
        rng = _random.Random(0)
        # score corrélé uniquement à p0 -> p0 seul "important", les autres
        # tirés indépendamment.
        results = []
        for i in range(30):
            p0 = i % 3
            params = {"p0": p0, **{f"p{j}": rng.choice([0, 1, 2]) for j in range(1, 6)}}
            results.append(_fake_eval_result(params, score=float(p0)))

        diag = opt._freeze_from_results(results, list(opt.param_space.keys()))
        assert diag is not None
        assert any(len(v) == 1 for v in opt.param_space.values())

        opt._restore_param_space()
        assert opt.param_space == original

    def test_degenerate_results_freeze_nothing_and_no_mutation(self):
        opt = _make_opt(_wide_space())
        original = {k: list(v) for k, v in opt.param_space.items()}
        results = [_fake_eval_result({f"p{j}": i % 3 for j in range(6)}, score=-999.0)
                  for i in range(12)]
        diag = opt._freeze_from_results(results, list(opt.param_space.keys()))
        assert diag is None
        assert opt.param_space == original


class TestRandomSearchIntegration:
    def test_no_param_space_returns_error(self):
        opt = _make_opt({"x": [1]})
        opt.param_space = {}
        assert opt.random_search(n_trials=5) == {
            "error": "Aucun espace de params pour opus_omnibus_v11"}

    def test_small_space_skips_reduction(self, monkeypatch):
        """Espace à 2 paramètres : jamais réduit, param_search_optim=True
        (défaut) ne doit rien changer au comportement historique."""
        opt = _make_opt({"a": [0, 1, 2], "b": [0, 1, 2]})
        calls = {"freeze": 0}
        monkeypatch.setattr(opt, "_freeze_from_results",
                            lambda *a, **kw: calls.__setitem__("freeze", calls["freeze"] + 1))
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(params, 1.0))

        result = opt.random_search(n_trials=5, n_jobs=1)
        assert calls["freeze"] == 0
        assert "param_search_optim" not in result

    def test_wide_space_triggers_reduction_and_restores_after(self, monkeypatch):
        opt = _make_opt(_wide_space_6vals())  # 6**6=46656
        original = {k: list(v) for k, v in opt.param_space.items()}
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(
            params, score=float(params["p0"]) * 10 + sum(params.values())))
        _random.seed(0)

        result = opt.random_search(n_trials=20, n_jobs=1)  # 46656 > 20*200 -> réduit

        assert "param_search_optim" in result
        assert len(result["param_search_optim"]["frozen_params"]) >= 1
        # Le dépistage EN BUDGET ne dépense jamais plus que n_trials au total.
        assert result["n_trials"] <= 20
        # self.param_space restauré après coup (n'affecte pas un run suivant).
        assert opt.param_space == original

    def test_disabling_the_toggle_matches_legacy_behaviour(self, monkeypatch):
        opt = _make_opt(_wide_space_6vals())
        calls = {"freeze": 0}
        monkeypatch.setattr(opt, "_freeze_from_results",
                            lambda *a, **kw: calls.__setitem__("freeze", calls["freeze"] + 1))
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(params, 1.0))

        result = opt.random_search(n_trials=20, n_jobs=1, param_search_optim=False)
        assert calls["freeze"] == 0
        assert "param_search_optim" not in result
        assert result["n_trials"] == 20  # aucun essai de dépistage retranché

    def test_degenerate_screening_does_not_crash_and_freezes_nothing(self, monkeypatch):
        """Tous les essais scorent -999 (ex: OOS sans trade) : la recherche
        se termine normalement, sans geler sur du bruit."""
        opt = _make_opt(_wide_space_6vals())
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(params, -999.0))

        result = opt.random_search(n_trials=20, n_jobs=1)
        assert "error" not in result
        assert "param_search_optim" not in result


class TestBayesianSearchLegacy:
    def test_explore_phase_freeze_checkpoint_in_budget(self, monkeypatch):
        opt = _make_opt(_wide_space_6vals())
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(
            params, score=float(params["p0"]) * 10 + sum(params.values())))
        _random.seed(0)
        try:
            result = opt._bayesian_search_legacy(n_trials=20, n_jobs=1)
            assert "param_search_optim" in result
            n_explore = max(8, 20 // 3)
            assert result["param_search_optim"]["n_screen"] == n_explore
            assert result["n_trials"] <= 20
        finally:
            opt._restore_param_space()

    def test_toggle_off_skips_freeze(self, monkeypatch):
        opt = _make_opt(_wide_space_6vals())
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(params, 1.0))
        try:
            result = opt._bayesian_search_legacy(n_trials=20, n_jobs=1, param_search_optim=False)
            assert "param_search_optim" not in result
        finally:
            opt._restore_param_space()


class TestBayesianSearchOptuna:
    def test_freezes_via_partial_fixed_sampler_without_mutating_param_space(self, monkeypatch):
        opt = _make_opt(_wide_space_6vals())
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(
            params, score=float(params["p0"]) * 10 + sum(params.values())))

        result = opt.bayesian_search(n_trials=20, n_jobs=1)

        assert "param_search_optim" in result
        # Le chemin Optuna fige le SAMPLER, jamais self.param_space.
        assert all(len(v) == 6 for v in opt.param_space.values())

    def test_toggle_off_skips_freeze(self, monkeypatch):
        opt = _make_opt(_wide_space_6vals())
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(params, 1.0))

        result = opt.bayesian_search(n_trials=20, n_jobs=1, param_search_optim=False)
        assert "param_search_optim" not in result


class TestGridSearchIntegration:
    def test_small_grid_not_reduced(self, monkeypatch):
        opt = _make_opt({"a": [0, 1, 2], "b": [0, 1, 2]})
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(params, 1.0))
        result = opt.grid_search()
        assert "param_search_optim" not in result
        assert len(opt.results) == 9  # 3*3, grille intacte

    def test_huge_grid_is_reduced_before_exhaustive_run(self, monkeypatch):
        # 6 params à 5 choix = 5**6 = 15625 > seuil (5000).
        space = {f"p{i}": [0, 1, 2, 3, 4] for i in range(6)}
        opt = _make_opt(space)
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(
            params, score=float(params["p0"])))
        result = opt.grid_search(n_jobs=1)
        assert "param_search_optim" in result
        # La grille finale (post-gel) doit être bien plus petite que 15625 —
        # essais de dépistage inclus (rien n'est perdu, évalués sur la
        # fenêtre complète).
        assert len(opt.results) < 15625

    def test_toggle_off_skips_reduction(self, monkeypatch):
        space = {f"p{i}": [0, 1, 2, 3, 4] for i in range(6)}
        opt = _make_opt(space)
        monkeypatch.setattr(opt, "_eval", lambda params: _fake_eval_result(params, 1.0))
        result = opt.grid_search(n_jobs=1, param_search_optim=False)
        assert "param_search_optim" not in result
        assert len(opt.results) == 15625
