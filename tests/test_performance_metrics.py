"""Métriques de performance étendues (S3-07) — échelles et annualisation.

Ce module n'avait AUCUN test malgré la mention « écrit et testé unitairement »
dans les documents d'audit. Il produisait des valeurs absurdes en production :
CAGR de 3 809 %/an et Calmar de 3 961 sur un backtest à +9,5 % en 5,5 ans.

La cause tient en une phrase : ``equity_curve`` ne reçoit un point qu'à chaque
trade clôturé, alors que tout le calcul la traitait comme une série par bougie.
Les tests ci-dessous verrouillent la distinction entre les trois fréquences en
jeu — durée réelle, bougies par an, trades par an.
"""
import math

import pytest

from app.core.performance_metrics import (
    alpha_vs_buy_hold,
    annualized_excess_vs_buy_hold,
    compute_cagr,
    compute_extended_metrics,
    sortino_ratio,
)

BARS_PER_YEAR_1D = 365.0

# Run réel observé : 8 trades sur 2 000 bougies journalières, 1000 → 1094,6.
EQUITY_8_TRADES = [1000.0, 1010, 1005, 1040, 1030, 1060, 1050, 1080, 1094.6]
ANNEES_REELLES = 2000 / BARS_PER_YEAR_1D  # ≈ 5,48 ans


def test_sortino_uses_full_sample_downside_dev():
    """F-09 : dénominateur = N total, pas n_négatifs − 1."""
    rets = [0.02, -0.01, 0.03, -0.02, 0.01]
    s = sortino_ratio(rets, periods_per_year=1)
    n = len(rets)
    downside_ss = sum(min(r, 0.0) ** 2 for r in rets)
    expected = (sum(rets) / n) / (downside_ss / n) ** 0.5
    assert s == pytest.approx(round(expected, 4))


def test_sortino_without_losses_is_nan_not_one_hundred():
    """F-10 : sentinelle 100 remplacée par NaN."""
    assert math.isnan(sortino_ratio([0.01, 0.02, 0.03], periods_per_year=1))


def test_alpha_vs_buy_hold_n_est_pas_on2():
    """F-13 : mean() hors des boucles — le résultat reste défini."""
    strat = [0.01, -0.005, 0.02, 0.0, 0.015]
    bench = [0.008, 0.002, 0.01, -0.001, 0.012]
    a = alpha_vs_buy_hold(strat, bench, periods_per_year=365)
    assert isinstance(a, float)


def test_cagr_utilise_la_duree_reelle_et_non_le_nombre_de_trades():
    """Le défaut d'origine, isolé.

    L'appelant calculait ``years = len(equity_curve) / bars_per_year``, soit
    9 / 365 ≈ 0,025 an pour un backtest de 5,5 ans. Élever 1,0946 à la
    puissance 1/0,025 donne un CAGR de 3 809 %.
    """
    m = compute_extended_metrics(
        [], EQUITY_8_TRADES, 1000.0,
        years=ANNEES_REELLES, periods_per_year=BARS_PER_YEAR_1D,
    )
    # +9,46 % en 5,48 ans → un peu moins de 2 %/an.
    assert 1.0 < m["cagr"] < 2.5, f"CAGR hors de toute plausibilité : {m['cagr']}"

    faux = compute_extended_metrics(
        [], EQUITY_8_TRADES, 1000.0,
        years=len(EQUITY_8_TRADES) / BARS_PER_YEAR_1D,
        periods_per_year=BARS_PER_YEAR_1D,
    )
    assert faux["cagr"] > 1000, "témoin : l'ancien calcul produisait bien l'absurdité"


def test_calmar_suit_le_cagr_corrige():
    m = compute_extended_metrics(
        [], EQUITY_8_TRADES, 1000.0,
        years=ANNEES_REELLES, periods_per_year=BARS_PER_YEAR_1D,
    )
    # Max DD ≈ 1 % → Calmar de l'ordre de l'unité, pas du millier.
    assert 0 < m["calmar"] < 10, f"Calmar implausible : {m['calmar']}"


def test_sans_duree_les_metriques_valent_zero_plutot_qu_un_nombre_invente():
    """Un zéro visible vaut mieux qu'une valeur fausse d'apparence crédible.

    L'ancien code se rabattait sur ``len(equity_curve) / periods_per_year`` ;
    ce repli EST le bug. En son absence, on n'invente plus de durée.
    """
    m = compute_extended_metrics([], EQUITY_8_TRADES, 1000.0, years=None)
    assert m["cagr"] == 0.0
    assert m["calmar"] == 0.0


def test_sortino_annualise_avec_la_frequence_des_trades_pas_des_bougies():
    """Annualiser des rendements PAR TRADE avec « bougies par an » gonfle le
    ratio d'un facteur sqrt(bougies / trades) — ici environ ×15."""
    m = compute_extended_metrics(
        [], EQUITY_8_TRADES, 1000.0,
        years=ANNEES_REELLES, periods_per_year=BARS_PER_YEAR_1D,
    )
    # 8 rendements sur 5,48 ans ≈ 1,46 trade/an, contre 365 bougies/an.
    gonfle = compute_extended_metrics(
        [], EQUITY_8_TRADES, 1000.0,
        years=ANNEES_REELLES, periods_per_year=BARS_PER_YEAR_1D,
        equity_periods_per_year=BARS_PER_YEAR_1D,   # l'ancien comportement
    )
    assert abs(m["sortino"]) < abs(gonfle["sortino"]), (
        "le Sortino doit être annualisé sur la fréquence des trades"
    )


def test_frequence_de_l_equity_explicite_est_respectee():
    """Une équité échantillonnée par bougie doit pouvoir le déclarer."""
    m = compute_extended_metrics(
        [], EQUITY_8_TRADES, 1000.0,
        years=ANNEES_REELLES, periods_per_year=BARS_PER_YEAR_1D,
        equity_periods_per_year=BARS_PER_YEAR_1D,
    )
    assert math.isfinite(m["sortino"])


# ── Alpha vs Buy & Hold ──────────────────────────────────────────────────────

def test_alpha_compare_des_rendements_annualises_quand_les_axes_different():
    """Le CAPM exige deux séries alignées période par période.

    Ici l'équité a 8 rendements (un par trade) et le benchmark 1 999 (un par
    bougie) : `zip` apparierait le 3ᵉ trade avec la 3ᵉ bougie, et le bêta qui
    en sort ne mesure rien. On tombe alors sur la différence de rendements
    annualisés, qui reste bien définie.
    """
    # Benchmark qui double sur la période → ~13 %/an sur 5,48 ans.
    prices = [100.0 * (1 + i / 1999) for i in range(2000)]
    m = compute_extended_metrics(
        [], EQUITY_8_TRADES, 1000.0, prices=prices,
        years=ANNEES_REELLES, periods_per_year=BARS_PER_YEAR_1D,
    )
    bh_cagr = compute_cagr(prices[0], prices[-1], ANNEES_REELLES)
    assert m["alpha_vs_bh"] == pytest.approx(m["cagr"] - bh_cagr, abs=1e-3)
    # La stratégie (+1,7 %/an) sous-performe nettement ce benchmark.
    assert m["alpha_vs_bh"] < 0


def test_alpha_reste_un_capm_quand_les_series_sont_alignees():
    """Si l'équité est échantillonnée comme les prix, le bêta a un sens et on
    conserve le CAPM — la correction ne dégrade pas le cas correct."""
    prices = [100.0 + i for i in range(50)]
    equity = [1000.0 + 8 * i for i in range(50)]
    m = compute_extended_metrics(
        [], equity, 1000.0, prices=prices,
        years=50 / BARS_PER_YEAR_1D, periods_per_year=BARS_PER_YEAR_1D,
    )
    assert math.isfinite(m["alpha_vs_bh"])


def test_excess_vs_buy_hold_est_neutre_sans_donnees():
    assert annualized_excess_vs_buy_hold([], 10.0, 1.0) == 0.0
    assert annualized_excess_vs_buy_hold([100.0, 110.0], 10.0, 0) == 0.0
    assert annualized_excess_vs_buy_hold([100.0], 10.0, 1.0) == 0.0


def test_un_benchmark_plat_laisse_l_alpha_egal_au_cagr():
    prices = [100.0] * 2000
    m = compute_extended_metrics(
        [], EQUITY_8_TRADES, 1000.0, prices=prices,
        years=ANNEES_REELLES, periods_per_year=BARS_PER_YEAR_1D,
    )
    assert m["alpha_vs_bh"] == pytest.approx(m["cagr"], abs=1e-3)


# ── Cadence d'une série de rendements ────────────────────────────────────────

def test_returns_per_year_mesure_la_cadence_reelle():
    """8 trades en 5,48 ans → ~1,5 observation par an, pas 365."""
    from app.core.performance_metrics import returns_per_year
    assert returns_per_year(8, ANNEES_REELLES, BARS_PER_YEAR_1D) == pytest.approx(1.46, abs=0.01)


def test_returns_per_year_est_plafonne_par_la_cadence_des_bougies():
    """Une série issue des trades ne peut pas battre celle des bougies.

    Trois trades en deux heures donneraient 13 140 observations par an — un
    Sharpe qui explose sur un échantillon minuscule. Le plafond exprime un
    fait : il n'y a pas plus de clôtures possibles que de bougies.
    """
    from app.core.performance_metrics import returns_per_year
    deux_heures = 2 / (365 * 24)
    assert returns_per_year(3, deux_heures, max_per_year=8760.0) == 8760.0


def test_returns_per_year_retombe_sur_le_plafond_sans_duree():
    from app.core.performance_metrics import returns_per_year
    assert returns_per_year(10, None, 365.0) == 365.0
    assert returns_per_year(10, 0, 365.0) == 365.0
    assert returns_per_year(0, 5.0, 365.0) == 365.0
