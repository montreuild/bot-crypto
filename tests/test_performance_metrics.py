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
    annualized_excess_vs_buy_hold,
    compute_cagr,
    compute_extended_metrics,
)

BARS_PER_YEAR_1D = 365.0

# Run réel observé : 8 trades sur 2 000 bougies journalières, 1000 → 1094,6.
EQUITY_8_TRADES = [1000.0, 1010, 1005, 1040, 1030, 1060, 1050, 1080, 1094.6]
ANNEES_REELLES = 2000 / BARS_PER_YEAR_1D  # ≈ 5,48 ans


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
