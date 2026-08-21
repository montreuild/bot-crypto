"""DOWN-01/02/03 — le moteur cesse de confondre nombre de barres et durée.

Les trois constats ont la même racine : une durée déduite d'un compte de
barres, exacte sur un marché continu sans trou — le cas pour lequel le moteur
a été écrit — et fausse partout ailleurs.
"""
import datetime as dt
import math

import polars as pl
import pytest

from app.core.timeframes import bars_per_year
from app.engine.backtest_result import BacktestResult

UTC = dt.timezone.utc


# ── DOWN-01 : l'annualisation dépend de la place ────────────────────────────

@pytest.mark.parametrize("tf", ["15m", "1h", "4h"])
def test_une_action_a_moins_de_barres_par_an_qu_un_marche_continu(tf):
    """Une action ne cote que ~8,5 h sur 24 et 5 jours sur 7."""
    continu = bars_per_year(tf)
    action = bars_per_year(tf, "BNP.PA")
    assert action < continu
    assert 3.5 < continu / action < 4.5, "≈ 24/8,5 × 7/5"


@pytest.mark.parametrize("symbole", ["BTC/USDC", "XRP/USDC", ""])
def test_le_crypto_garde_la_convention_continue(symbole):
    assert bars_per_year("1h", symbole) == bars_per_year("1h")


def test_le_facteur_de_sharpe_des_actions_etait_surestime_du_double():
    """Mesure d'origine : Sharpe annualisé ×2,0 sur BNP.PA 15 m. Le seuil
    ABSOLU du gate Deflated Sharpe s'en trouvait franchi à tort."""
    ratio = bars_per_year("15m") / bars_per_year("15m", "BNP.PA")
    assert 1.9 < math.sqrt(ratio) < 2.1


def test_le_journalier_compte_des_seances_pas_des_jours():
    seances = bars_per_year("1d", "AC.PA")
    assert 240 < seances < 262, f"~252 séances par an, obtenu {seances}"
    assert bars_per_year("1d") == 365


@pytest.mark.parametrize("a,b", [("AC.PA", "SOLB.BR"), ("AC.PA", "APAM.AS")])
def test_deux_places_europeennes_donnent_le_meme_ordre(a, b):
    assert bars_per_year("1h", a) == pytest.approx(bars_per_year("1h", b), rel=0.05)


def test_le_resultat_utilise_le_symbole_pour_annualiser():
    """Sans le symbole, `BacktestResult` retombait sur la convention 24/7."""
    import inspect
    src = inspect.getsource(BacktestResult)
    assert "_bars_per_year(self._timeframe, self._symbol)" in src
    assert "_bars_per_year(self._timeframe)" not in src.replace(
        "_bars_per_year(self._timeframe, self._symbol)", "")


# ── DOWN-02 : la complétude accompagne le résultat ──────────────────────────

def _resultat(completude):
    return BacktestResult([], [1000.0], 1000.0, timeframe="15m",
                          symbol="X", data_completeness=completude)


@pytest.mark.parametrize("c", [None, 100.0, 99.5, 99.0])
def test_une_serie_saine_ne_declenche_aucun_avertissement(c):
    assert _resultat(c).data_warning() is None


@pytest.mark.parametrize("c,mot", [(94.2, "partiellement"), (55.2, "sévèrement")])
def test_une_serie_trouee_avertit_et_dit_l_impact(c, mot):
    """L'avertissement doit nommer les CONSÉQUENCES, pas répéter le chiffre :
    c'est ce qui permet à l'utilisateur de décider s'il fait confiance."""
    w = _resultat(c).data_warning()
    assert w is not None and mot in w
    assert f"{c:.1f}" in w
    assert "indicateurs" in w and "durée" in w


def test_la_completude_est_portee_dans_le_payload():
    """DOWN-02 : l'indicateur existait mais n'atteignait personne."""
    d = _resultat(88.0).to_dict()
    assert d["data_completeness"] == 88.0
    assert d["data_warning"] is not None


def test_la_completude_ne_bloque_rien():
    """Choix assumé : c'est un avertissement, pas un gate. Une série à 5 %
    produit un résultat complet, simplement accompagné du message."""
    d = _resultat(5.0).to_dict()
    assert "error" not in d
    assert d["data_warning"] is not None


def test_le_schema_api_expose_la_completude():
    from app.api.schemas import StrategyStats
    champs = StrategyStats.model_fields
    assert "data_completeness" in champs and "data_warning" in champs


# ── DOWN-03 : la durée vient des horodatages ────────────────────────────────

class _Ctx:
    timeframe = "1h"
    def __init__(self, temps):
        self.df = pl.DataFrame({"time": temps})


def test_la_duree_vient_des_horodatages_pas_du_compte_de_barres():
    """Sur une série trouée, deux barres consécutives dans le tableau peuvent
    être séparées de bien plus qu'un timeframe."""
    from app.engine.position_lifecycle import _heures_detenues
    debut = dt.datetime(2026, 7, 1, tzinfo=UTC)
    # 3 barres « consécutives » mais espacées de 1 h, puis 10 h.
    ctx = _Ctx([debut, debut + dt.timedelta(hours=1), debut + dt.timedelta(hours=11)])
    assert _heures_detenues(ctx, 0, 2) == pytest.approx(11.0)
    assert _heures_detenues(ctx, 0, 1) == pytest.approx(1.0)


def test_sans_horodatage_exploitable_on_retombe_sur_le_compte():
    """Le repli doit rester le comportement historique, pas une erreur."""
    from app.engine.position_lifecycle import _heures_detenues

    class _Vide:
        timeframe = "1h"
        df = pl.DataFrame({"time": []})

    assert _heures_detenues(_Vide(), 0, 5) == pytest.approx(5.0)


def test_une_serie_reguliere_donne_le_meme_resultat_qu_avant():
    """Garde-fou de non-régression : sur un marché continu sans trou, le
    nouveau calcul doit coïncider avec l'ancien."""
    from app.core.timeframes import bar_to_days
    from app.engine.position_lifecycle import _heures_detenues
    debut = dt.datetime(2026, 7, 1, tzinfo=UTC)
    ctx = _Ctx([debut + dt.timedelta(hours=i) for i in range(20)])
    for n in (1, 5, 19):
        assert _heures_detenues(ctx, 0, n) == pytest.approx(
            n * bar_to_days("1h") * 24.0)
