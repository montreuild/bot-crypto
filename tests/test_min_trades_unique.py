"""Un seul plancher de trades dans le dépôt.

Le module en portait deux : `MIN_TRADES_DEGENERATE = 2` pour la SÉLECTION et
`MIN_SIGNIFICANT_TRADES = 10` pour la DÉCISION. La distinction se défendait sur
le papier — « classer n'est pas décider » — mais c'est le classement qui
désigne le jeu retenu, et un plancher de 2 laissait gagner des Sharpe de 7,83
sur deux trades face à des configurations à trente
(`docs/SUITE_ABLATION_V3.md` §1).

Ces tests verrouillent la fusion et sa conséquence : une optimisation dont
aucun essai n'atteint le seuil ne retourne plus de gagnant, plutôt qu'un
gagnant fabriqué.
"""
import pytest

from app.core.stats_thresholds import MIN_SIGNIFICANT_TRADES
from app.engine.opt_scoring import beats_baseline, composite_score


def _resultat(n_trades: int, **surcharges) -> dict:
    """Résultat de backtest flatteur — sauf sur le nombre de trades."""
    base = {
        "total_trades": n_trades, "sharpe": 8.0, "win_rate": 100.0,
        "profit_factor": 999.0, "total_pnl": 500.0, "max_drawdown": -1.0,
        "expectancy": 50.0, "alpha": 40.0, "initial_capital": 10000,
    }
    base.update(surcharges)
    return base


def test_le_seuil_de_degenerescence_n_existe_plus():
    """S'il revenait, l'écart entre sélection et décision se rouvrirait."""
    import app.core.stats_thresholds as seuils

    assert not hasattr(seuils, "MIN_TRADES_DEGENERATE"), (
        "deux planchers = deux politiques ; le dépôt n'en veut qu'une"
    )


def test_un_paramétrage_hyper_selectif_est_refuse_par_defaut():
    """LE cas mesuré : deux trades, tous les ratios saturés. Il gagnait."""
    assert composite_score(_resultat(2)) == -999.0
    assert composite_score(_resultat(MIN_SIGNIFICANT_TRADES - 1)) == -999.0


def test_le_meme_paramétrage_passe_des_le_seuil():
    assert composite_score(_resultat(MIN_SIGNIFICANT_TRADES)) > -999.0


def test_selection_et_decision_partagent_le_seuil():
    """C'était l'anomalie : le dépôt refusait de promouvoir sous dix trades
    tout en sélectionnant avec un plancher de deux."""
    n = MIN_SIGNIFICANT_TRADES - 1
    refuse_a_la_selection = composite_score(_resultat(n)) == -999.0
    ok, _ = beats_baseline(n, 500.0, 1.0, 8.0,
                           {"pnl": 0.0, "win_rate": 0.5, "sharpe": 0.0})
    assert refuse_a_la_selection and not ok, (
        "les deux étages doivent refuser le même échantillon"
    )


@pytest.mark.parametrize("plancher", [2, 5, 10])
def test_le_plancher_reste_surchargeable(plancher):
    """Sortie de secours pour les études exploratoires : classer à deux trades
    est légitime pour explorer, jamais pour décider."""
    res = _resultat(plancher)
    assert composite_score(res, min_trades=plancher) > -999.0
    assert composite_score(res, min_trades=plancher + 1) == -999.0


def test_un_resultat_vide_reste_ecarte():
    """La protection anti-dégénérescence d'origine tient toujours — elle est
    juste devenue un cas particulier du seuil unique."""
    assert composite_score(_resultat(0)) == -999.0
