"""Budget d'essais proportionné à l'espace de recherche.

`n_trials: 40` valait pour `signal_consensus` (27 combinaisons — 40 essais
dépassent la grille entière) comme pour `smart_money` (58 paramètres). Les
quatre règles se testent séparément, et surtout dans leur ORDRE : c'est lui qui
décide, par exemple, qu'un espace minuscule ne se voit pas gonfler par le
plancher par dimension.
"""
import pytest

from app.engine.opt_budget import cardinality, effective_n_trials


def _espace(n_params: int, choix: int = 4) -> dict:
    return {f"p{i}": list(range(choix)) for i in range(n_params)}


class TestCardinalite:
    def test_produit_des_choix(self):
        assert cardinality({"a": [1, 2], "b": [1, 2, 3]}) == 6
        assert cardinality({}) == 0
        assert cardinality(None) == 0

    def test_parametre_non_enumerable(self):
        assert cardinality({"a": 3}) == 0


class TestBudget:
    def test_plancher_par_dimension(self):
        n, d = effective_n_trials(_espace(11, choix=8), 40, {})
        assert n == 165                      # 15 × 11
        assert d["raison"] == "proportionné au nombre de paramètres"

    def test_petit_espace_garde_le_budget_demande(self):
        n, d = effective_n_trials(_espace(2, choix=30), 40, {})
        assert n == 40                       # 15 × 2 < 40
        assert d["raison"] == "budget demandé"

    def test_plafond(self):
        n, d = effective_n_trials(_espace(58, choix=6), 40, {})
        assert n == 400
        assert d["raison"] == "plafonné (max_trials)"

    def test_jamais_plus_dessais_que_de_combinaisons(self):
        """Tirer 40 fois dans 27 combinaisons, c'est payer des doublons."""
        n, d = effective_n_trials({"a": [1, 2, 3], "b": [1, 2, 3], "c": [1, 2, 3]},
                                  40, {})
        assert n == 27
        assert d["raison"] == "borné par la cardinalité de l'espace"

    def test_la_cardinalite_gagne_sur_le_plancher(self):
        """L'ordre des règles compte : la borne dure passe en dernier."""
        n, _ = effective_n_trials({"a": [1, 2], "b": [1, 2], "c": [1, 2],
                                   "d": [1, 2], "e": [1, 2]}, 40, {})
        assert n == 32                       # 2^5, et non 15 × 5 = 75


class TestConfiguration:
    def test_desactivation(self):
        cfg = {"optimizer": {"trials_per_param": 0}}
        n, d = effective_n_trials(_espace(58), 40, cfg)
        assert n == 40
        assert d["raison"] == "mise à l'échelle désactivée"

    def test_reglages_personnalises(self):
        cfg = {"optimizer": {"trials_per_param": 5, "max_trials": 60}}
        assert effective_n_trials(_espace(9, choix=9), 40, cfg)[0] == 45
        assert effective_n_trials(_espace(30, choix=9), 40, cfg)[0] == 60

    def test_espace_vide(self):
        n, d = effective_n_trials({}, 40, {})
        assert n == 40
        assert d["n_params"] == 0


class TestSurLesVraiesStrategies:
    """Les stratégies du dépôt, pas des espaces fabriqués."""

    @pytest.fixture(scope="class")
    @classmethod
    def espaces(cls):
        from app.engine.registry import get_param_spaces
        return get_param_spaces()

    def test_smart_money_recoit_plus_dessais_et_garde_son_espace(self, espaces):
        """Ses setups doivent rester testables : plus d'essais, pas moins d'espace."""
        space = espaces.get("smart_money")
        if space is None:
            pytest.skip("smart_money absente du registre")
        avant = len(space)
        n, _ = effective_n_trials(space, 40, {})
        assert n > 40, "un espace à 58 paramètres mérite plus que le budget de base"
        assert len(space) == avant, "l'espace de smart_money ne doit pas être élagué"

    def test_aucune_strategie_ne_perd_de_couverture(self, espaces):
        for name, space in espaces.items():
            n, _ = effective_n_trials(space, 40, {})
            card = cardinality(space)
            assert n >= min(40, card or 40), \
                f"{name} : budget effectif {n} sous le budget demandé sans raison"
