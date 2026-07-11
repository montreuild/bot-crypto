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
    res = MonteCarlo(n_runs=100).run(_trades([10, -5, 8, -4]), 1000.0)
    assert res["max_dd_p95"] >= 0.0
    assert 0.0 <= res["prob_ruin_10pct"] <= 100.0


def test_no_trades_error():
    assert "error" in MonteCarlo().run([], 1000.0)
