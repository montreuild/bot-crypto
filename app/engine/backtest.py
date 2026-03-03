"""
Backtest Engine Pro — V2

Améliorations :
  - Stop loss vérifié sur HIGH/LOW intrabar (réaliste) + open de la barre suivante
  - Trailing stop dynamique par stratégie (pas global)
  - ATR mult configurable par stratégie
  - Score threshold plus strict (configurable)
  - Profit target optionnel (take profit fixe en % ou ATR mult)
  - Warmup adapté à la stratégie (EMA200 = 210 barres minimum)
  - Slippage asymétrique : stop market = prix défavorable
"""
import logging
from typing import List, Dict, Optional, Tuple
import numpy as np
import pandas as pd

from app.engine.engine import Engine
from app.core.trailing import TrailingStopManager

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────
def _atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return 0.0
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    tr  = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    val = float(tr.rolling(period).mean().iloc[-1])
    return val if val > 0 else 0.0

def _bar_to_days(tf: str) -> float:
    m = {"1m":1,"3m":3,"5m":5,"15m":15,"30m":30,"1h":60,"2h":120,"4h":240,"1d":1440}
    return m.get(tf, 15) / 1440.0


# ══════════════════════════════════════════════════════════════════════════════
#  BacktestResult
# ══════════════════════════════════════════════════════════════════════════════
class BacktestResult:
    def __init__(self, trades: List[dict], equity_curve: List[float],
                 initial_capital: float, timestamps: List[str] = None):
        self.trades          = trades
        self.equity_curve    = equity_curve
        self.initial_capital = initial_capital
        self.timestamps      = timestamps or []
        self._compute_metrics()

    def _compute_metrics(self):
        closed = [t for t in self.trades if t.get("status","").startswith("closed")]
        pnls   = [t["pnl"] for t in closed]
        fees   = [t.get("fees", 0) for t in closed]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        self.total_trades = len(closed)
        self.win_rate     = len(wins) / len(closed) * 100 if closed else 0.0
        self.total_pnl    = sum(pnls)
        self.total_fees   = sum(fees)
        self.final_equity = self.equity_curve[-1] if self.equity_curve else self.initial_capital

        eq = np.array(self.equity_curve, dtype=float)
        if len(eq) > 1:
            peak              = np.maximum.accumulate(eq)
            drawdowns         = (eq - peak) / np.where(peak > 0, peak, 1) * 100
            self.max_drawdown = float(drawdowns.min())
            returns           = np.diff(eq) / np.where(eq[:-1] > 0, eq[:-1], 1)
            std               = returns.std()
            self.sharpe       = float(returns.mean() / std * np.sqrt(252)) if std > 0 else 0.0
        else:
            self.max_drawdown = 0.0
            self.sharpe       = 0.0

        self.avg_win  = float(np.mean(wins))   if wins   else 0.0
        self.avg_loss = float(np.mean(losses)) if losses else 0.0

        self.expectancy = (
            len(wins)/len(closed)*self.avg_win +
            len(losses)/len(closed)*self.avg_loss
        ) if closed else 0.0

        win_sum  = sum(wins)
        loss_sum = abs(sum(losses))
        self.profit_factor = win_sum / loss_sum if loss_sum > 0 else float("inf")

        maes = [t.get("mae", 0) for t in closed if t.get("mae") is not None]
        mfes = [t.get("mfe", 0) for t in closed if t.get("mfe") is not None]
        self.avg_mae = float(np.mean(maes)) if maes else 0.0
        self.avg_mfe = float(np.mean(mfes)) if mfes else 0.0

        # ── Métriques par stratégie ───────────────────────────────────────────
        self.by_strategy: Dict[str, dict] = {}
        for t in closed:
            s = t.get("strategy", "unknown")
            if s not in self.by_strategy:
                self.by_strategy[s] = {"trades": 0, "wins": 0, "pnl": 0.0, "fees": 0.0}
            d = self.by_strategy[s]
            d["trades"] += 1
            d["pnl"]    += t["pnl"]
            d["fees"]   += t.get("fees", 0)
            if t["pnl"] > 0:
                d["wins"] += 1

        for s, d in self.by_strategy.items():
            sd_pnls = [t["pnl"] for t in closed if t.get("strategy") == s]
            wins_s  = [p for p in sd_pnls if p > 0]
            loss_s  = [p for p in sd_pnls if p <= 0]

            d["win_rate"]     = round(d["wins"] / d["trades"] * 100, 1) if d["trades"] else 0
            d["pnl"]          = round(d["pnl"], 4)
            d["fees"]         = round(d["fees"], 4)
            d["avg_win"]      = round(float(np.mean(wins_s)), 4) if wins_s else 0
            d["avg_loss"]     = round(float(np.mean(loss_s)), 4) if loss_s else 0
            d["profit_factor"]= round(sum(wins_s) / abs(sum(loss_s)), 3) if loss_s and sum(loss_s) else "∞"
            d["expectancy"]   = round(
                len(wins_s)/len(sd_pnls)*d["avg_win"] +
                len(loss_s)/len(sd_pnls)*d["avg_loss"], 4
            ) if sd_pnls else 0
            d["total_trades"] = d["trades"]
            d["total_pnl"]    = d["pnl"]
            d["total_fees"]   = d["fees"]

            # Equity curve individuelle
            eq_s  = [self.initial_capital]
            cap   = self.initial_capital
            for t in [x for x in closed if x.get("strategy") == s]:
                cap += t["pnl"]
                eq_s.append(round(cap, 4))
            d["equity_curve"]   = eq_s
            d["initial_capital"]= self.initial_capital
            d["final_equity"]   = round(eq_s[-1], 4)

            eq_arr  = np.array(eq_s)
            peak_s  = np.maximum.accumulate(eq_arr)
            dd_arr  = (eq_arr - peak_s) / np.where(peak_s > 0, peak_s, 1) * 100
            d["max_drawdown"] = round(float(dd_arr.min()), 2) if len(eq_arr) > 1 else 0

            rets_s  = np.diff(eq_arr) / np.where(eq_arr[:-1] > 0, eq_arr[:-1], 1) if len(eq_arr) > 1 else np.array([0])
            d["sharpe"] = round(float(rets_s.mean() / rets_s.std() * np.sqrt(252)), 3) if rets_s.std() > 0 else 0

            # Trades complets (avec bar/exit_bar) pour le graphique
            d["trades"] = [t for t in closed if t.get("strategy") == s]

    def to_dict(self) -> dict:
        return {
            "initial_capital": self.initial_capital,
            "final_equity":    round(self.final_equity, 4),
            "total_pnl":       round(self.total_pnl, 4),
            "total_fees":      round(self.total_fees, 4),
            "total_trades":    self.total_trades,
            "win_rate":        round(self.win_rate, 2),
            "max_drawdown":    round(self.max_drawdown, 2),
            "sharpe":          round(self.sharpe, 3),
            "expectancy":      round(self.expectancy, 4),
            "avg_mae":         round(self.avg_mae, 4),
            "avg_mfe":         round(self.avg_mfe, 4),
            "avg_win":         round(self.avg_win, 4),
            "avg_loss":        round(self.avg_loss, 4),
            "profit_factor":   round(self.profit_factor, 3) if self.profit_factor != float("inf") else "∞",
            "equity_curve":    [round(e, 4) for e in self.equity_curve],
            "timestamps":      self.timestamps,
            "by_strategy":     self.by_strategy,
            "trades":          self.trades,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  Backtester V2
# ══════════════════════════════════════════════════════════════════════════════
class Backtester:
    """
    Backtester V6 — Trailing stop dynamique multi-phases, sans TP fixe.
    Le TP fixe est supprimé. Les gains courent jusqu'au retournement naturel.
    """
    def __init__(self, engine: Engine, cfg: dict):
        self.engine = engine
        self.cfg    = cfg
        bcfg = cfg.get("backtest", {})
        tcfg = cfg.get("trading",  {})

        self.atr_stop_mult = float(bcfg.get("atr_stop_mult", 2.5))

        # V6 : TP FIXE SUPPRIMÉ — trailing dynamique multi-phases
        self.atr_tp_mult  = None

        # Paramètres des 4 phases du trailing
        self.trail_wide   = float(bcfg.get("trail_wide",   2.5))   # Phase 1
        self.trail_normal = float(bcfg.get("trail_normal", 2.0))   # Phase 2 (breakeven)
        self.trail_lock   = float(bcfg.get("trail_lock",   1.5))   # Phase 3 (lock 60%)
        self.trail_tight  = float(bcfg.get("trail_tight",  1.0))   # Phase 4 (serré)
        self.grace_bars   = int(bcfg.get("grace_bars",     4))
        self.breakeven_r  = float(bcfg.get("breakeven_r",  1.2))
        self.lock_r       = float(bcfg.get("lock_r",       2.5))
        self.tight_r      = float(bcfg.get("tight_r",      4.0))
        self.lock_ratio   = float(bcfg.get("lock_ratio",   0.60))
        self.use_swing    = bool(bcfg.get("use_swing",     True))

        self.taker_fee    = tcfg.get("taker_fee",         0.001)
        self.maker_fee    = tcfg.get("maker_fee",        0.0004)
        self.borrow_rate  = tcfg.get("borrow_rate_daily", 0.0002)
        self.spread_pct   = bcfg.get("spread_pct",        0.0005)
        self.partial_fill = bcfg.get("partial_fill_pct",  0.95)
        self.max_notional_pct = float(bcfg.get("max_notional_pct", 0.50))

    def _make_trailing(self):
        """Crée une instance de trailing V6 propre à chaque trade ouvert."""
        return TrailingStopManager(
            mult             = self.trail_wide,
            grace_bars       = self.grace_bars,
            breakeven_r      = self.breakeven_r,
            trail_tight_mult = self.trail_tight,
            lock_r           = self.lock_r,
            tight_r          = self.tight_r,
            lock_ratio       = self.lock_ratio,
            use_swing        = self.use_swing,
        )

    # ── run ───────────────────────────────────────────────────────────────────
    def run(self, df: pd.DataFrame, symbol: str = "BTC/USDC") -> "BacktestResult":
        capital      = self.cfg["trading"].get("capital", 1000.0)
        risk         = self.cfg["trading"]["risk_per_trade"]
        threshold    = self.cfg["trading"].get("score_threshold", 0.60)
        strat_params = self.cfg.get("strategy_params", {})
        trades       = []
        equity_curve = [capital]
        timestamps   = [str(df["time"].iloc[0]) if "time" in df.columns else "0"]
        position     = None
        trade_id     = 0
        # Warmup adapté : EMA200 a besoin de 210+ barres pour être fiable
        warmup       = 210

        for i in range(warmup, len(df) - 1):
            window       = df.iloc[:i + 1].copy()
            current_bar  = df.iloc[i]
            c_high       = float(current_bar["high"])
            c_low        = float(current_bar["low"])
            c_open       = float(current_bar["open"])

            # ── Gestion de la position ouverte (V6 — trailing multi-phases) ────
            if position is not None:
                side    = position["side"]
                entry   = position["entry"]
                stop    = position["stop"]
                c_close = float(current_bar["close"])

                # MAE / MFE intrabar
                if side == "long":
                    position["mae"] = min(position.get("mae", 0.0), c_low  - entry)
                    position["mfe"] = max(position.get("mfe", 0.0), c_high - entry)
                else:
                    position["mae"] = min(position.get("mae", 0.0), entry - c_high)
                    position["mfe"] = max(position.get("mfe", 0.0), entry - c_low)

                # ── Stop touché sur le low/high intrabar ─────────────────────
                stop_hit = (side == "long"  and c_low  <= stop) or \
                           (side == "short" and c_high >= stop)

                if stop_hit:
                    exec_price  = stop * (1 - self.spread_pct) if side == "long" \
                                  else stop * (1 + self.spread_pct)

                    # Phase du trailing au moment de la sortie
                    trail_phase = "unknown"
                    _tr = position.get("_trailing")
                    if _tr and hasattr(_tr, "_dts") and _tr._dts:
                        trail_phase = _tr._dts.phase_name

                    fill_size   = position["size"] * self.partial_fill
                    fees        = self._fees(exec_price, fill_size, maker=False)
                    bars_held   = i - position["bar"]
                    days_held   = bars_held * _bar_to_days(
                        self.cfg["trading"].get("timeframe", "1h"))
                    borrow_cost = position["notional"] * self.borrow_rate * days_held

                    gross = (exec_price - entry) * fill_size * (1 if side == "long" else -1)
                    pnl   = gross - fees - borrow_cost
                    capital += pnl

                    ts = str(df["time"].iloc[i]) if "time" in df.columns else str(i)
                    position.update({
                        "pnl":           round(pnl, 6),
                        "fees":          round(fees, 6),
                        "borrow_cost":   round(borrow_cost, 6),
                        "exit":          round(exec_price, 6),
                        "status":        "closed",
                        "exit_bar":      i,
                        "exit_time":     ts,
                        "exit_reason":   "trailing_stop",
                        "trail_phase":   trail_phase,
                        "pnl_pct":       round((exec_price - entry) / entry * 100 *
                                               (1 if side == "long" else -1), 3),
                        "duration_bars": bars_held,
                        "fill_pct":      self.partial_fill,
                    })
                    position.pop("_trailing", None)
                    trades.append(position)
                    equity_curve.append(round(capital, 4))
                    timestamps.append(ts)
                    position = None

                else:
                    # ── Mise à jour trailing stop V6 ─────────────────────────
                    atr           = _atr(window)
                    bars_held_now = i - position["bar"]
                    _tr           = position.get("_trailing")
                    recent_lows   = window["low"].iloc[-20:].tolist()
                    recent_highs  = window["high"].iloc[-20:].tolist()
                    ema_fast_val  = float(
                        window["close"].ewm(span=21, adjust=False).mean().iloc[-1])

                    if _tr:
                        new_stop = _tr.update_stop(
                            current_price = c_close,
                            current_stop  = stop,
                            atr           = atr, side = side, entry = entry,
                            bars_held     = bars_held_now,
                            recent_lows   = recent_lows,
                            recent_highs  = recent_highs,
                        )
                        position["stop"] = new_stop
                        if hasattr(_tr, "_dts") and _tr._dts:
                            position["trail_phase"] = _tr._dts.phase_name

                continue  # Position traitée

            # ── Cherche un signal ─────────────────────────────────────────────
            signal = self.engine.best_signal(window, strat_params)
            if signal["side"] == "none" or signal["score"] < threshold:
                continue

            atr = _atr(window)
            if atr <= 0:
                continue

            # ── Prix d'entrée : open de la barre suivante + spread ───────────
            exec_price = float(df["open"].iloc[i + 1])
            if signal["side"] == "long":
                exec_price *= (1 + self.spread_pct)
            else:
                exec_price *= (1 - self.spread_pct)

            # ── Stop initial via trailing V6 ──────────────────────────────
            _trailing = self._make_trailing()
            stop      = _trailing.initial_stop(exec_price, atr, signal["side"])

            # V6 : PAS DE TP FIXE — le trailing stop gère tout
            # Le trailing multi-phases laisse courir les gains sans plafond.

            # ── Sizing : risque fixé en USDC ──────────────────────────────────
            stop_dist    = abs(exec_price - stop)
            risk_amount  = capital * risk
            size         = risk_amount / stop_dist if stop_dist > 0 else 0
            notional     = size * exec_price
            max_notional = capital * self.max_notional_pct
            if notional > max_notional:
                size     = max_notional / exec_price
                notional = max_notional
            if notional < 1.0 or size <= 0:
                continue

            size       *= self.partial_fill
            notional    = size * exec_price
            entry_fees  = self._fees(exec_price, size, maker=False)
            capital    -= entry_fees
            trade_id   += 1
            ts = str(df["time"].iloc[i]) if "time" in df.columns else str(i)

            position = {
                "id":           trade_id,
                "symbol":       symbol,
                "side":         signal["side"],
                "strategy":     signal.get("name", ""),
                "score":        round(signal.get("score", 0), 3),
                "entry":        round(exec_price, 6),
                "stop":         round(stop, 6),
                "take_profit":  None,
                "size":         round(size, 6),
                "notional":     round(notional, 4),
                "bar":          i + 1,
                "entry_time":   ts,
                "fees":         round(entry_fees, 6),
                "borrow_cost":  0.0,
                "status":       "open",
                "pnl":          None,
                "exit":         None,
                "trail_phase":  "grace",
                "_trailing":    _trailing,
                "reason":       signal.get("reason", ""),
                "conditions":   signal.get("conditions", []),
                "indicators":   signal.get("indicators", {}),
                "mae":          0.0,
                "mfe":          0.0,
            }


        # ── Clôture forcée en fin de série ───────────────────────────────────
        if position is not None:
            last_price  = float(df["close"].iloc[-1])
            fill_size   = position["size"] * self.partial_fill
            fees        = self._fees(last_price, fill_size, maker=True)
            bars_held   = len(df) - 1 - position["bar"]
            days_held   = bars_held * _bar_to_days(self.cfg["trading"].get("timeframe", "1h"))
            borrow_cost = position["notional"] * self.borrow_rate * days_held
            gross = (last_price - position["entry"]) * fill_size * (1 if position["side"] == "long" else -1)
            pnl   = gross - fees - borrow_cost
            capital += pnl
            position.update({
                "pnl":          round(pnl, 6),
                "fees":         round(fees, 6),
                "borrow_cost":  round(borrow_cost, 6),
                "exit":         round(last_price, 6),
                "status":       "closed_eod",
                "exit_bar":     len(df) - 1,
                "exit_time":    str(df["time"].iloc[-1]) if "time" in df.columns else str(len(df)-1),
                "exit_reason":  "end_of_data",
                "pnl_pct":      round((last_price - position["entry"]) / position["entry"] * 100 *
                                       (1 if position["side"] == "long" else -1), 3),
                "duration_bars": bars_held,
                "fill_pct":     self.partial_fill,
            })
            trades.append(position)
            equity_curve.append(round(capital, 4))

        return BacktestResult(trades, equity_curve, self.initial_capital(self.cfg), timestamps)

    def initial_capital(self, cfg: dict) -> float:
        return cfg["trading"].get("capital", 1000.0)

    def _fees(self, price: float, size: float, maker: bool = False) -> float:
        rate = self.maker_fee if maker else self.taker_fee
        return price * size * rate


# ══════════════════════════════════════════════════════════════════════════════
#  Walk-Forward
# ══════════════════════════════════════════════════════════════════════════════
class WalkForwardAnalyzer:
    def __init__(self, engine: Engine, cfg: dict, n_folds: int = 5):
        self.engine  = engine
        self.cfg     = cfg
        self.n_folds = n_folds

    def run(self, df: pd.DataFrame, symbol: str = "BTC/USDC") -> dict:
        n      = len(df)
        fold_n = n // (self.n_folds + 1)
        if fold_n < 60:
            return {"error": "Données insuffisantes pour Walk-Forward"}

        in_sample_results  = []
        out_sample_results = []

        for k in range(self.n_folds):
            is_end  = fold_n * (k + 1)
            oos_end = min(fold_n * (k + 2), n)
            df_is   = df.iloc[:is_end].copy().reset_index(drop=True)
            df_oos  = df.iloc[is_end:oos_end].copy().reset_index(drop=True)
            if len(df_oos) < 30:
                continue
            try:
                bt_is  = Backtester(self.engine, self.cfg)
                bt_oos = Backtester(self.engine, self.cfg)
                r_is   = bt_is.run(df_is,  symbol).to_dict()
                r_oos  = bt_oos.run(df_oos, symbol).to_dict()
                in_sample_results.append(r_is)
                out_sample_results.append(r_oos)
            except Exception as e:
                logger.error(f"[WF] Fold {k} : {e}")

        if not out_sample_results:
            return {"error": "Aucun fold OOS valide"}

        oos_pnl    = [r["total_pnl"]  for r in out_sample_results]
        oos_sharpe = [r["sharpe"]     for r in out_sample_results]
        oos_wr     = [r["win_rate"]   for r in out_sample_results]

        return {
            "n_folds":        len(out_sample_results),
            "avg_oos_pnl":    round(float(np.mean(oos_pnl)),    4),
            "avg_oos_sharpe": round(float(np.mean(oos_sharpe)), 3),
            "avg_oos_wr":     round(float(np.mean(oos_wr)),     2),
            "consistency":    round(sum(1 for p in oos_pnl if p > 0) / len(oos_pnl) * 100, 1),
            "in_sample":      in_sample_results,
            "out_of_sample":  out_sample_results,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  Monte-Carlo
# ══════════════════════════════════════════════════════════════════════════════
class MonteCarlo:
    def __init__(self, n_runs: int = 200, confidence: float = 0.95):
        self.n_runs    = n_runs
        self.confidence = confidence

    def run(self, trades: List[dict], initial_capital: float) -> dict:
        closed = [t for t in trades if t.get("status", "").startswith("closed")]
        if not closed:
            return {"error": "Aucun trade fermé"}

        pnls   = np.array([t["pnl"] for t in closed])
        finals = []
        max_dds= []
        rng    = np.random.default_rng(42)

        for _ in range(self.n_runs):
            shuffled = rng.permutation(pnls)
            equity   = np.concatenate([[initial_capital], initial_capital + np.cumsum(shuffled)])
            finals.append(float(equity[-1]))
            peak = np.maximum.accumulate(equity)
            dd   = (equity - peak) / np.where(peak > 0, peak, 1) * 100
            max_dds.append(float(dd.min()))

        alpha = 1 - self.confidence
        return {
            "runs":               self.n_runs,
            "confidence":         self.confidence,
            "final_equity_mean":  round(float(np.mean(finals)),  2),
            "final_equity_p5":    round(float(np.percentile(finals, 5)),  2),
            "final_equity_p95":   round(float(np.percentile(finals, 95)), 2),
            "max_dd_p95":         round(abs(float(np.percentile(max_dds, 95))), 2),
            "prob_profit":        round(sum(1 for f in finals if f > initial_capital) / self.n_runs * 100, 1),
            "prob_ruin_10pct":    round(sum(1 for d in max_dds if d < -10) / self.n_runs * 100, 1),
        }
