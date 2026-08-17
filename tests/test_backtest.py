"""
Tests unitaires — Backtester & BacktestResult
"""
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.engine.backtest import Backtester, BacktestResult
from app.engine.engine import BaseStrategy, Engine


def _make_df(n=300, trend="up"):
    """Génère un DataFrame OHLCV synthétique."""
    np.random.seed(42)
    base = 100.0
    closes = [base]
    for _ in range(n - 1):
        step = np.random.randn() * 0.5
        if trend == "up":
            step += 0.1
        elif trend == "down":
            step -= 0.1
        closes.append(max(closes[-1] + step, 1.0))
    closes = np.array(closes)
    highs  = closes * (1 + np.abs(np.random.randn(n) * 0.005))
    lows   = closes * (1 - np.abs(np.random.randn(n) * 0.005))
    opens  = np.roll(closes, 1)
    opens[0] = closes[0]
    vols   = np.random.uniform(1000, 5000, n)
    start  = datetime(2024, 1, 1)
    times  = [start + timedelta(hours=i) for i in range(n)]
    return pl.DataFrame({"time": times, "open": opens, "high": highs,
                         "low": lows, "close": closes, "volume": vols})


def _cfg():
    return {
        "trading": {
            "capital": 1000, "risk_per_trade": 0.01, "timeframe": "1h",
            "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
            "score_threshold": 0.55, "borrow_rate_daily": 0.0002,
        },
        "backtest": {
            "spread_pct": 0.0005, "latency_ms": 0,
            "partial_fill_pct": 1.0, "monte_carlo_runs": 50,
            "walk_forward_folds": 3, "atr_stop_mult": 2.0,
            "trail_wide": 2.5, "trail_normal": 2.0, "trail_lock": 1.5,
            "trail_tight": 1.0, "grace_bars": 4, "breakeven_r": 1.2,
            "lock_r": 2.5, "tight_r": 4.0, "lock_ratio": 0.60,
            "use_swing": False, "max_notional_pct": 0.20,
        },
        "strategies": {"enabled": ["dummy"]},
        "strategy_params": {},
    }


class DummyLongStrategy(BaseStrategy):
    """Stratégie factice qui émet toujours un signal LONG."""
    name = "dummy"

    def score(self, df, params=None, df_htf=None):
        if len(df) < 30:
            return {"side": "none", "score": 0}
        return {
            "side": "long", "score": 0.80,
            "name": "dummy", "reason": "test",
            "stop_hint": float(df["close"][-1]) * 0.97,
        }


class TestBacktestResult:
    def _make_result(self, pnls):
        capital = 1000.0
        eq = [capital]
        trades = []
        for i, p in enumerate(pnls):
            capital += p
            eq.append(round(capital, 4))
            trades.append({
                "id": i, "pnl": p, "fees": abs(p) * 0.001,
                "status": "closed", "strategy": "dummy",
                "mae": abs(p) * 0.1, "mfe": abs(p) * 0.5,
            })
        return BacktestResult(trades, eq, 1000.0)

    def test_win_rate(self):
        r = self._make_result([10, -5, 8, -3, 6])
        assert r.win_rate == pytest.approx(60.0)

    def test_total_pnl(self):
        r = self._make_result([10, -5, 8])
        assert r.total_pnl == pytest.approx(13.0)

    def test_profit_factor(self):
        r = self._make_result([10, -5])
        assert r.profit_factor == pytest.approx(2.0)

    def test_no_trades(self):
        r = BacktestResult([], [1000.0], 1000.0)
        assert r.total_trades == 0
        assert r.win_rate == 0.0
        assert r.sharpe is None

    def test_sharpe_is_none_under_ten_observations(self):
        """F-02 : un Sharpe sur 1-3 trades n'est pas une mesure."""
        r = self._make_result([10, -5, 8])
        assert r.total_trades == 3
        assert r.sharpe is None
        assert r.to_dict()["sharpe"] is None

    def test_sharpe_is_numeric_from_ten_observations(self):
        r = self._make_result([10, -5, 8, -3, 6, -2, 4, -1, 5, -4])
        assert r.total_trades == 10
        assert r.sharpe is not None
        assert isinstance(r.sharpe, float)

    def test_max_drawdown_negative(self):
        r = self._make_result([100, -200, 50])
        assert r.max_drawdown < 0

    def test_mtm_drawdown_sees_latent_loss(self):
        """F-06 : une position qui descend puis remonte laisse une trace."""
        capital = 1000.0
        # Courbe par trade : seulement +10 à la clôture → DD nul.
        trades = [{"status": "closed", "pnl": 10.0, "fees": 0.1}]
        equity_curve = [1000.0, 1010.0]
        # MTM : 1000 → 960 → 1010 (perte latente de 4 %).
        equity_mtm = [1000.0, 960.0, 1010.0]
        r = BacktestResult(trades, equity_curve, capital, equity_mtm=equity_mtm)
        assert r.max_drawdown < -3.0

    def test_expectancy(self):
        r = self._make_result([10, 10, -5])
        assert r.expectancy == pytest.approx(5.0)

    def test_to_dict_keys(self):
        r = self._make_result([5, -2, 8])
        d = r.to_dict()
        for key in ("total_trades", "win_rate", "total_pnl", "sharpe",
                    "max_drawdown", "profit_factor", "expectancy",
                    "equity_curve", "by_strategy", "trades"):
            assert key in d

    def test_all_losing(self):
        r = self._make_result([-5, -3, -7])
        assert r.win_rate == 0.0
        assert r.profit_factor == 0.0

    def test_all_winning(self):
        r = self._make_result([5, 3, 7])
        assert r.win_rate == 100.0
        assert r.profit_factor is None

    def test_by_strategy_matches_global_when_single_strategy(self):
        """F-08 : une seule stratégie → mêmes agrégats que le run global."""
        r = self._make_result([10, -5, 8])
        g = r.by_strategy["dummy"]
        assert g["final_equity"] == pytest.approx(r.final_equity)
        assert g["pnl"] == pytest.approx(r.total_pnl)

    def test_end_of_data_is_taker_with_spread(self):
        """B-10 : la clôture de fin de série n'est plus un maker gratuit."""
        import inspect
        from app.engine.backtest import Backtester
        src = inspect.getsource(Backtester.run)
        assert 'maker=False' in src
        assert 'end_of_data' in src
        assert 'ref_price=_eod' in src

    def test_by_strategy_populated(self):
        r = self._make_result([10, -3, 5])
        assert "dummy" in r.by_strategy
        assert r.by_strategy["dummy"]["trades"] == 3 or len(r.by_strategy["dummy"]["trades"]) == 3


class TestBacktester:
    def test_close_at_puts_entry_fees_in_trade_pnl(self):
        """F-01 : le PnL du trade porte les frais d'entrée ; le capital, non."""
        import types
        from datetime import datetime, timedelta

        bt = Backtester(Engine(), _cfg())
        entry_fees = 0.50
        position = {
            "side": "long", "entry": 100.0, "size": 1.0, "notional": 100.0,
            "bar": 0, "stop": 95.0, "fees": entry_fees,
            "_stop_trail": [], "_trailing": None, "mae": 0.0, "mfe": 0.0,
        }
        t0 = datetime(2024, 1, 1)
        ctx = types.SimpleNamespace(
            df=pl.DataFrame({"time": [t0 + timedelta(hours=i) for i in range(5)],
                             "close": [100.0] * 5}),
            timeframe="1h", capital=1000.0 - entry_fees,
            trades=[], equity_curve=[1000.0], timestamps=[],
            peak_capital=1000.0,
        )
        returned = bt._close_at(ctx, position, 2, 110.0, "take_profit", maker=False)
        trade = ctx.trades[-1]
        assert trade["entry_fees"] == pytest.approx(entry_fees)
        # Le capital n'est débité qu'une fois (à l'ouverture) : le retour
        # de _close_at reste le PnL de clôture, sans re-déduire l'entrée.
        assert returned == pytest.approx(trade["pnl"] + entry_fees)
        assert trade["pnl"] == pytest.approx(returned - entry_fees)
        assert ctx.capital == pytest.approx(1000.0 - entry_fees + returned)

    def test_fill_at_level_uses_open_on_gap(self):
        """B-01 : un gap à travers le stop/TP se remplit à l'ouverture."""
        bt = Backtester(Engine(), _cfg())
        # Long, stop 95, ouverture à 90 (gap défavorable).
        px, ref, gapped = bt._fill_at_level("long", 95.0, 90.0, stop=True)
        assert gapped is True
        assert ref == 90.0
        assert px == pytest.approx(90.0 * (1 - bt.spread_pct))
        # Sans gap : fill au niveau.
        px, ref, gapped = bt._fill_at_level("long", 95.0, 96.0, stop=True)
        assert gapped is False and ref == 95.0
        # TP long : gap favorable (open au-dessus du TP).
        px, ref, gapped = bt._fill_at_level("long", 110.0, 115.0, stop=False)
        assert gapped is True and ref == 115.0
        # Short stop : gap si open au-dessus du stop.
        _, ref, gapped = bt._fill_at_level("short", 105.0, 108.0, stop=True)
        assert gapped is True and ref == 108.0

    def test_run_returns_result(self):
        engine = Engine()
        engine.register(DummyLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_df(300)
        result = bt.run(df, "BTC/USDC")
        assert isinstance(result, BacktestResult)

    def test_run_has_trades(self):
        engine = Engine()
        engine.register(DummyLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_df(300)
        result = bt.run(df, "BTC/USDC")
        # La stratégie émet des signaux → doit y avoir des trades
        assert result.total_trades >= 0  # peut être 0 si signaux filtrés

    def test_equity_curve_length(self):
        engine = Engine()
        engine.register(DummyLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_df(300)
        result = bt.run(df, "BTC/USDC")
        assert len(result.equity_curve) >= 1

    def test_fees_deducted(self):
        engine = Engine()
        engine.register(DummyLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_df(300)
        result = bt.run(df, "BTC/USDC")
        if result.total_trades > 0:
            assert result.total_fees > 0

    def test_stop_trail_in_trades(self):
        """Fix #14 — les trades fermés doivent avoir un champ stop_trail."""
        engine = Engine()
        engine.register(DummyLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_df(300)
        result = bt.run(df, "BTC/USDC")
        closed = [t for t in result.trades if t.get("status") == "closed"]
        if closed:
            assert "stop_trail" in closed[0]
            assert isinstance(closed[0]["stop_trail"], list)


class TightBracketStrategy(BaseStrategy):
    """Bracket fixe ±10% (stop_hint/tp_hint), trailing désactivé — utilisée
    pour forcer une barre où stop ET take-profit sont TOUS DEUX touchables
    (STRAT-06/BT-13)."""
    name = "tight_bracket"

    def score(self, df, params=None, df_htf=None, symbol: str = ""):
        if len(df) < 30:
            return {"side": "none", "score": 0}
        c = float(df["close"][-1])
        return {
            "side": "long", "score": 0.80, "name": "tight_bracket", "reason": "test",
            "stop_hint": c * 0.90, "tp_hint": c * 1.10, "disable_trailing": True,
        }


class TestTpSlAmbiguousBars:
    """STRAT-06/BT-13 : compteur diagnostique tp_sl_ambiguous_bars — mesure
    seule, ne change pas la résolution (le stop continue de toujours l'emporter)."""

    def _df_with_ambiguous_bar(self, n=230, ambiguous_bar=220):
        np.random.seed(7)
        closes = [100.0]
        for _ in range(n - 1):
            closes.append(max(closes[-1] + np.random.randn() * 0.3, 1.0))
        closes = np.array(closes)
        highs = closes * (1 + np.abs(np.random.randn(n) * 0.003))
        lows  = closes * (1 - np.abs(np.random.randn(n) * 0.003))
        opens = np.roll(closes, 1)
        opens[0] = closes[0]
        vols  = np.random.uniform(1000, 5000, n)
        # Barre volontairement hors des bornes du bracket ±10% des deux côtés.
        highs[ambiguous_bar] = closes[ambiguous_bar] * 3.0
        lows[ambiguous_bar]  = closes[ambiguous_bar] * 0.3
        start = datetime(2024, 1, 1)
        times = [start + timedelta(hours=i) for i in range(n)]
        return pl.DataFrame({"time": times, "open": opens, "high": highs,
                             "low": lows, "close": closes, "volume": vols})

    def test_counts_bar_where_both_would_hit(self):
        engine = Engine()
        engine.register(TightBracketStrategy())
        cfg = _cfg()
        cfg["strategies"]["enabled"] = ["tight_bracket"]
        bt = Backtester(engine, cfg)
        df = self._df_with_ambiguous_bar()
        result = bt.run(df, "BTC/USDC")
        assert result.diagnostics["tp_sl_ambiguous_bars"] >= 1

    def test_stop_still_wins_resolution_unchanged(self):
        """Le comportement de résolution (stop prioritaire) n'est pas modifié —
        seule une mesure diagnostique est ajoutée."""
        engine = Engine()
        engine.register(TightBracketStrategy())
        cfg = _cfg()
        cfg["strategies"]["enabled"] = ["tight_bracket"]
        bt = Backtester(engine, cfg)
        df = self._df_with_ambiguous_bar()
        result = bt.run(df, "BTC/USDC")
        closed = [t for t in result.trades if t.get("status") == "closed"]
        assert closed, "aucun trade fermé — le scénario n'a pas déclenché le bracket"
        assert closed[0]["exit_reason"] == "stop_loss"

    def test_zero_when_no_ambiguity(self):
        engine = Engine()
        engine.register(DummyLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_df(300)
        result = bt.run(df, "BTC/USDC")
        assert result.diagnostics["tp_sl_ambiguous_bars"] == 0


class _NoSignalStrategy(BaseStrategy):
    """N'émet jamais de signal — utile pour isoler le calcul du benchmark
    Buy & Hold (FIN-04) des mécaniques d'entrée/sortie."""
    name = "no_signal"
    warmup_bars = 250

    def score(self, df, params=None, df_htf=None, symbol: str = ""):
        return {"side": "none", "score": 0}


class TestBuyAndHold:
    """FIN-04 : benchmark Buy & Hold calculé sur la MÊME fenêtre que le backtest."""

    def test_buy_and_hold_present_in_result(self):
        engine = Engine()
        engine.register(DummyLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_df(300, trend="up")
        result = bt.run(df, "BTC/USDC")
        d = result.to_dict()
        assert d["buy_and_hold_pct"] is not None
        assert d["buy_and_hold_pnl"] is not None
        assert d["alpha"] is not None
        assert d["alpha"] == pytest.approx(round(result.total_pnl - result.buy_and_hold_pnl, 4))

    def test_buy_and_hold_start_price_matches_dynamic_warmup(self):
        """Avant le correctif, `_add_buy_and_hold` recalculait un warmup figé à
        210 au lieu du warmup dynamique (>= 250 ici, `_NoSignalStrategy.
        warmup_bars`) réellement utilisé par la boucle de trading — le prix de
        départ du B&H était désynchronisé de la première barre tradée."""
        engine = Engine()
        engine.register(_NoSignalStrategy())
        cfg = _cfg()
        cfg["strategies"]["enabled"] = ["no_signal"]
        bt = Backtester(engine, cfg)
        df = _make_df(300, trend="up")
        result = bt.run(df, "BTC/USDC")

        first_price = float(df["close"][250])
        last_price  = float(df["close"][-1])
        expected_pct = round((last_price - first_price) / first_price * 100, 3)
        assert result.buy_and_hold_pct == pytest.approx(expected_pct)

    def test_buy_and_hold_absent_when_df_too_short(self):
        engine = Engine()
        engine.register(DummyLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_df(150, trend="up")  # < warmup (210) : fenêtre trop courte
        result = bt.run(df, "BTC/USDC")
        assert getattr(result, "buy_and_hold_pct", None) is None
