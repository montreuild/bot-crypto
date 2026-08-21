"""Gardes des constats de audit/2026-08-18-revue-complete."""
import polars as pl

from app.core.is_oos import default_purge_embargo, split_is_oos
from app.engine.opt_scoring import beats_baseline, composite_score
from app.live.position_open_mixin import _order_failed
from app.ml.splitting import chrono_split, label_embargo


def test_order_failed_refuse_un_fill_nul_encore_ouvert():
    assert _order_failed(None) is True
    assert _order_failed({"status": "rejected"}) is True
    assert _order_failed({"status": "open", "filled": 0}) is True
    assert _order_failed({"status": "closed"}) is False
    assert _order_failed({"status": "closed", "filled": 1.0}) is False
    # LIVE-02 : un ordre sans statut ET sans fill était compté comme exécuté.
    # L'assertion inverse figurait ici ; elle décrivait le repli permissif, pas
    # un besoin réel. Aucun chemin de production ne produit un tel dict :
    # `Exchange.create_order` pose `status="closed"` en mode papier
    # (app/core/exchange.py) et ccxt renseigne toujours le statut en réel.
    # Ouvrir une position sans preuve d'exécution laissait le bot suivre un
    # fantôme, stop posé sur du vide.
    assert _order_failed({"price": 100.0}) is True


def test_beats_baseline_refuse_un_drawdown_beaucoup_pire():
    ok, reason = beats_baseline(
        20, 100.0, 55.0, 1.2,
        {"pnl": 50.0, "wr": 50.0, "sharpe": 1.0, "dd": -10.0},
        oos_dd=-20.0,
        oos_pf=1.5,
        oos_expectancy=2.0,
    )
    assert ok is False
    assert "drawdown" in reason


def test_beats_baseline_refuse_le_win_rate_seul():
    ok, reason = beats_baseline(
        20, 100.0, 70.0, 1.0,
        {"pnl": 50.0, "wr": 50.0, "sharpe": 1.2, "profit_factor": 1.8, "expectancy": 3.0},
        oos_pf=1.5,
        oos_expectancy=2.0,
    )
    assert ok is False
    assert "win-rate" in reason.lower() or "seul" in reason


def test_dd_factor_continue_de_mordre_au_dela_de_30():
    class _R:
        total_trades = 20
        sharpe = 1.0
        win_rate = 50
        profit_factor = 1.5
        net_profit = 100
        total_pnl = 100
        max_drawdown = -40
        expectancy = 1.0
        alpha = 0
        initial_capital = 1000

    s32 = composite_score(_R())
    _R.max_drawdown = -80
    s80 = composite_score(_R())
    assert s80 < s32


def test_split_is_oos_signale_un_historique_trop_court():
    df = pl.DataFrame({"close": list(range(100))})
    is_, oos, split = split_is_oos(df, warmup=210)
    assert split >= 310
    assert len(oos) == 0
    assert len(is_) == 100


def test_chrono_split_embargo_au_moins_un_pourcent():
    n = 2000
    plan = chrono_split(n, label_horizons=[1, 3])
    assert plan is not None
    _, embargo = default_purge_embargo(n, label_embargo([1, 3]))
    assert plan.embargo == embargo
    assert plan.embargo >= int(n * 0.01)


def test_auc_ci_bloque_une_estimation_compatible_avec_le_hasard():
    from app.ml.overfitting_gate import auc_hanley_ci_low, validate_model_quality
    # AUC 0,56 sur 30 obs : IC 95 % recouvre 0,50.
    assert auc_hanley_ci_low(0.56, 30) < 0.50
    r = validate_model_quality(0.56, "dummy", 30)
    assert r["ok"] is False
    assert r["auc_ci_low"] < 0.50


def test_auc_ci_laisse_passer_un_echantillon_large():
    from app.ml.overfitting_gate import validate_model_quality
    r = validate_model_quality(0.70, "dummy", 400)
    assert r["ok"] is True
    assert r["auc_ci_low"] > 0.50


def test_fallback_to_inline_est_au_niveau_racine():
    from app.engine.backtest_result import BacktestResult
    r = BacktestResult([], [1000.0], 1000.0)
    r.ml_info = {"models": {"x": {"fallback_to_inline": True}}}
    d = r.to_dict()
    assert d["fallback_to_inline"] is True


def test_close_pnl_sans_venue_n_emprunte_pas_un_long():
    from app.core.execution import close_pnl
    _, _, borrow = close_pnl(
        side="long", entry=100.0, exit_price=110.0, size=1.0,
        notional=100.0, fee_rate=0.001, daily_rate=0.0002, hours_held=24.0,
    )
    assert borrow == 0.0


def test_detect_ohlcv_gaps_trouve_un_trou():
    from datetime import datetime, timedelta

    import polars as pl

    from app.core.ohlcv_gaps import detect_ohlcv_gaps
    start = datetime(2024, 1, 1)
    times = [start + timedelta(hours=i) for i in range(5)]
    times.append(start + timedelta(hours=20))
    df = pl.DataFrame({"time": times})
    gaps = detect_ohlcv_gaps(df, "1h")
    assert len(gaps) == 1
    assert gaps[0]["gap_bars"] >= 10
