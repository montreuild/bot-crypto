"""Performance de la stratégie par régime — `strategy_performance_by_regime`.

`POST /api/optimize/validate?method=regime` relançait un backtest complet avec
les `best_params` puis n'en gardait que le NOMBRE de trades : le résultat
renvoyé (`regime_summary`) décrit le rendement, le Sharpe et le drawdown du
PRIX, pas ceux de la stratégie. Deux paramétrages opposés auraient produit
exactement la même réponse, et le backtest n'aurait servi à rien.

Ces tests verrouillent la fonction qui répond vraiment à la question :
rattacher chaque trade au régime dans lequel il a été ouvert.
"""
from app.engine.regime_stress_test import RegimeSegment, strategy_performance_by_regime

SEGMENTS = [
    RegimeSegment(regime="bull", start_time="2024-01-01", end_time="2024-03-31",
                  n_bars=90, metrics={}),
    RegimeSegment(regime="bear", start_time="2024-06-01", end_time="2024-08-31",
                  n_bars=92, metrics={}),
]


def _trade(entry: str, pnl: float, status: str = "closed"):
    return {"entry_time": entry, "pnl": pnl, "status": status}


def test_les_trades_sont_rattaches_a_leur_regime_d_entree():
    trades = [
        _trade("2024-02-10 04:00:00", 30.0),
        _trade("2024-03-02 12:00:00", -10.0),
        _trade("2024-07-15 08:00:00", -25.0),
    ]
    out = strategy_performance_by_regime(SEGMENTS, trades)

    assert out["bull"]["n_trades"] == 2
    assert out["bull"]["pnl"] == 20.0
    assert out["bull"]["win_rate"] == 50.0
    assert out["bear"]["n_trades"] == 1
    assert out["bear"]["pnl"] == -25.0
    assert out["bear"]["win_rate"] == 0.0


def test_un_trade_hors_segment_est_compte_et_non_perdu():
    """Les segments ne couvrent pas toute la série : ceux plus courts que
    `min_segment_bars` sont écartés. Un trade tombé dans un de ces trous doit
    apparaître, sinon la somme des PnL par régime ne vaut plus le PnL total et
    l'écart est inexplicable."""
    trades = [
        _trade("2024-02-10 00:00:00", 10.0),
        _trade("2024-05-01 00:00:00", -5.0),   # entre les deux segments
    ]
    out = strategy_performance_by_regime(SEGMENTS, trades)

    assert out["unassigned"]["n_trades"] == 1
    assert out["unassigned"]["pnl"] == -5.0
    total = sum(v["pnl"] for v in out.values())
    assert total == 5.0, "la ventilation doit conserver le PnL total"


def test_les_trades_ouverts_sont_ignores():
    trades = [_trade("2024-02-10 00:00:00", 0.0, status="open"),
              _trade("2024-02-11 00:00:00", 12.0)]
    out = strategy_performance_by_regime(SEGMENTS, trades)
    assert out["bull"]["n_trades"] == 1


def test_bornes_de_segment_inclusives():
    trades = [_trade("2024-01-01 00:00:00", 1.0), _trade("2024-03-31 23:00:00", 2.0)]
    out = strategy_performance_by_regime(SEGMENTS, trades)
    assert out["bull"]["n_trades"] == 2


def test_sans_trade_le_resultat_est_vide_et_non_une_erreur():
    assert strategy_performance_by_regime(SEGMENTS, []) == {}


def test_extremes_reportes_par_regime():
    trades = [_trade("2024-02-01 00:00:00", 50.0),
              _trade("2024-02-02 00:00:00", -20.0),
              _trade("2024-02-03 00:00:00", 5.0)]
    out = strategy_performance_by_regime(SEGMENTS, trades)
    assert out["bull"]["best"] == 50.0
    assert out["bull"]["worst"] == -20.0
    assert out["bull"]["avg_pnl"] == round(35.0 / 3, 4)
