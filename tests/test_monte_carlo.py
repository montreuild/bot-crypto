"""BT-02 — MonteCarlo : l'équité finale doit avoir une vraie distribution.

Avant le correctif, la permutation (sans remise) rendait l'équité finale
identique à chaque run : ``final_equity_p5 == final_equity_p95`` et
``prob_profit`` ne pouvait valoir que 0 ou 100. Le bootstrap avec remise
rétablit un intervalle de confiance réel ; la permutation reste utilisée
pour les statistiques d'ORDRE (drawdown, ruine), où elle est correcte.
"""
from app.engine.backtest import MonteCarlo


def _trades(pnls):
    return [{"status": "closed", "pnl": p} for p in pnls]


def test_final_equity_has_real_distribution():
    # PnL non tous égaux → le bootstrap doit produire une fourchette.
    res = MonteCarlo(n_runs=300).run(_trades([50, -30, 20, -10, 40, -25, 15]), 1000.0)
    assert "error" not in res
    assert res["final_equity_p5"] < res["final_equity_p95"]
    assert res["final_equity_p5"] <= res["final_equity_mean"] <= res["final_equity_p95"]


def test_prob_profit_is_a_real_probability():
    # Mélange gains/pertes équilibré → prob_profit strictement entre 0 et 100.
    res = MonteCarlo(n_runs=300).run(_trades([100, -100, 90, -90, 80, -80, 5]), 1000.0)
    assert 0.0 < res["prob_profit"] < 100.0


def test_drawdown_stats_still_present():
    """T-01 : le p95 de DD est un quantile de risque, pas seulement un champ ≥ 0."""
    pnls = [10, -5, 8, -4, 12, -7]
    res = MonteCarlo(n_runs=200).run(_trades(pnls), 1000.0)
    assert res["max_dd_p95"] >= 0.0
    assert 0.0 <= res["prob_ruin_10pct"] <= 100.0
    realized = _realized_max_dd_pct(pnls, 1000.0)
    assert res["max_dd_p95"] + 1e-9 >= realized


def _realized_max_dd_pct(pnls, initial_capital):
    """Drawdown de la série d'origine — une permutation parmi d'autres."""
    import numpy as np
    equity = np.concatenate([[initial_capital], initial_capital + np.cumsum(pnls)])
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / np.where(peak > 0, peak, 1) * 100
    return abs(float(dd.min()))


def test_max_dd_p95_is_the_adverse_tail_not_the_best_case():
    """F-03 : max_dd_p95 est un quantile de risque, donc ≥ au DD réalisé.

    L'ancien calcul prenait le 95ᵉ percentile de drawdowns *négatifs* — le
    meilleur cas — puis ``abs()``. Sur 145/155 slots persistés, le p95 publié
    était *inférieur* au drawdown réellement observé.
    """
    pnls = [50, -30, 20, -10, 40, -25, 15, -8, 12, -18]
    capital = 1000.0
    realized = _realized_max_dd_pct(pnls, capital)
    res = MonteCarlo(n_runs=500).run(_trades(pnls), capital)
    assert "error" not in res
    assert res["max_dd_p95"] >= realized
    assert res.get("mc_version") == 2


def test_no_trades_error():
    assert "error" in MonteCarlo().run([], 1000.0)
