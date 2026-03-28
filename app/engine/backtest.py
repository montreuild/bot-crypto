"""Backtester, WalkForwardAnalyzer et MonteCarlo. Stop vérifié intrabar, trailing dynamique."""
import logging
import math
import threading
from typing import List, Dict, Optional, Tuple
import numpy as np
import polars as pl

from app.engine.engine import Engine
from app.core.trailing import TrailingStopManager
from app.live.utils import resolve_strategy_params


def _sf(v, fallback=None):
    """Safe float : convertit nan/inf en fallback pour JSON."""
    try:
        f = float(v)
        return fallback if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return fallback

logger = logging.getLogger(__name__)


# Timeframe → minutes mapping (crypto markets: 365 days/year, 24h/day)
_TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "1d": 1440,
}


def _bar_to_days(tf: str) -> float:
    return _TF_MINUTES.get(tf, 15) / 1440.0


def _bars_per_year(tf: str) -> float:
    """Number of bars in a trading year for the given timeframe."""
    minutes = _TF_MINUTES.get(tf, 60)
    # Crypto markets trade 365 days/year, 24h/day
    return 365 * 24 * 60 / minutes


# ── BacktestResult ──
class BacktestResult:
    def __init__(self, trades: List[dict], equity_curve: List[float],
                 initial_capital: float, timestamps: List[str] = None,
                 timeframe: str = "1d"):
        self.trades          = trades
        self.equity_curve    = equity_curve
        self.initial_capital = initial_capital
        self.timestamps      = timestamps or []
        self._timeframe      = timeframe
        self._compute_metrics()

    def _compute_metrics(self):
        closed = [t for t in self.trades if t.get("status", "").startswith("closed")]
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
            drawdowns         = (eq - peak) / np.where(peak > 0, peak, 1.0) * 100
            self.max_drawdown = _sf(float(drawdowns.min()), 0.0)
            returns           = np.diff(eq) / np.where(eq[:-1] > 0, eq[:-1], 1.0)
            std               = float(returns.std())
            # Annualization factor based on timeframe (crypto: 365×24h)
            ann_factor        = np.sqrt(_bars_per_year(self._timeframe))
            raw_sharpe        = float(returns.mean() / std * ann_factor) if std > 0 else 0.0
            self.sharpe       = _sf(raw_sharpe, 0.0)
        else:
            self.max_drawdown = 0.0
            self.sharpe       = 0.0

        self.avg_win  = _sf(float(np.mean(wins)),   0.0) if wins   else 0.0
        self.avg_loss = _sf(float(np.mean(losses)), 0.0) if losses else 0.0

        self.expectancy = (
            len(wins) / len(closed) * self.avg_win +
            len(losses) / len(closed) * self.avg_loss
        ) if closed else 0.0

        win_sum  = sum(wins)
        loss_sum = abs(sum(losses))
        self.profit_factor = win_sum / loss_sum if loss_sum > 0 else (999.0 if win_sum > 0 else 0.0)

        maes = [t.get("mae", 0) for t in closed if t.get("mae") is not None]
        mfes = [t.get("mfe", 0) for t in closed if t.get("mfe") is not None]
        self.avg_mae = _sf(float(np.mean(maes)), 0.0) if maes else 0.0
        self.avg_mfe = _sf(float(np.mean(mfes)), 0.0) if mfes else 0.0

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

            d["win_rate"]     = round(d["wins"] / d["trades"] * 100, 1) if d["trades"] else 0.0
            d["pnl"]          = round(d["pnl"], 4)
            d["fees"]         = round(d["fees"], 4)
            d["avg_win"]      = round(_sf(float(np.mean(wins_s)), 0.0), 4) if wins_s else 0.0
            d["avg_loss"]     = round(_sf(float(np.mean(loss_s)), 0.0), 4) if loss_s else 0.0
            _loss_sum = abs(sum(loss_s))
            d["profit_factor"] = round(sum(wins_s) / _loss_sum, 3) if _loss_sum > 0 else (999.0 if wins_s else 0.0)
            d["expectancy"]   = round(
                len(wins_s) / len(sd_pnls) * d["avg_win"] +
                len(loss_s) / len(sd_pnls) * d["avg_loss"], 4
            ) if sd_pnls else 0.0
            d["total_trades"] = d["trades"]
            d["total_pnl"]    = d["pnl"]
            d["total_fees"]   = d["fees"]

            eq_s  = [self.initial_capital]
            cap   = self.initial_capital
            for t in [x for x in closed if x.get("strategy") == s]:
                cap += t["pnl"]
                eq_s.append(round(cap, 4))
            d["equity_curve"]    = eq_s
            d["initial_capital"] = self.initial_capital
            d["final_equity"]    = round(eq_s[-1], 4)

            eq_arr = np.array(eq_s, dtype=float)
            peak_s = np.maximum.accumulate(eq_arr)
            if len(eq_arr) > 1:
                dd_arr = (eq_arr - peak_s) / np.where(peak_s > 0, peak_s, 1.0) * 100
                d["max_drawdown"] = round(_sf(float(dd_arr.min()), 0.0), 2)
            else:
                d["max_drawdown"] = 0.0

            if len(eq_arr) > 1:
                denom  = np.where(eq_arr[:-1] > 0, eq_arr[:-1], 1.0)
                rets_s = np.diff(eq_arr) / denom
            else:
                rets_s = np.array([0.0])
            ann_s  = np.sqrt(_bars_per_year(self._timeframe))
            std_s  = float(rets_s.std())
            if std_s > 0:
                d["sharpe"] = round(_sf(float(rets_s.mean() / std_s * ann_s), 0.0), 3)
            else:
                d["sharpe"] = 0.0

            d["trades"] = [t for t in closed if t.get("strategy") == s]

    def to_dict(self) -> dict:
        pf = self.profit_factor
        pf_safe = round(min(pf, 999.0), 3) if math.isfinite(pf) else 999.0
        return {
            "initial_capital":    self.initial_capital,
            "final_equity":       round(_sf(self.final_equity, 0.0), 4),
            "total_pnl":          round(_sf(self.total_pnl, 0.0), 4),
            "total_fees":         round(_sf(self.total_fees, 0.0), 4),
            "total_trades":       self.total_trades,
            "win_rate":           round(_sf(self.win_rate, 0.0), 2),
            "max_drawdown":       round(_sf(self.max_drawdown, 0.0), 2),
            "sharpe":             round(_sf(self.sharpe, 0.0), 3),
            "expectancy":         round(_sf(self.expectancy, 0.0), 4),
            "avg_mae":            round(_sf(self.avg_mae, 0.0), 4),
            "avg_mfe":            round(_sf(self.avg_mfe, 0.0), 4),
            "avg_win":            round(_sf(self.avg_win, 0.0), 4),
            "avg_loss":           round(_sf(self.avg_loss, 0.0), 4),
            "profit_factor":      pf_safe,
            "buy_and_hold_pnl":   round(_sf(getattr(self, "buy_and_hold_pnl", 0), 0.0), 4),
            "buy_and_hold_pct":   round(_sf(getattr(self, "buy_and_hold_pct", 0), 0.0), 3),
            "alpha":              round(_sf(getattr(self, "alpha", 0), 0.0), 4),
            "equity_curve":       [round(_sf(e, 0.0), 4) for e in self.equity_curve],
            "timestamps":         self.timestamps,
            "by_strategy":        self.by_strategy,
            "trades":             self.trades,
        }


# ── Backtester ──
class Backtester:
    """Backtester trailing stop multi-phases, sans TP fixe.
    use_pretrained_ml=False force le réentraînement inline (walk-forward/optimiseur).
    """
    def __init__(self, engine: Engine, cfg: dict,
                 cancel_event: Optional[threading.Event] = None,
                 use_pretrained_ml: bool = True):
        self.engine             = engine
        self.cfg                = cfg
        self._cancel_event      = cancel_event
        self.use_pretrained_ml  = use_pretrained_ml
        bcfg = cfg.get("backtest", {})
        tcfg = cfg.get("trading",  {})

        self.atr_stop_mult = float(bcfg.get("atr_stop_mult", 2.5))
        self.atr_tp_mult   = None  # TP fixe supprimé — trailing gère les sorties

        self.trail_wide   = float(bcfg.get("trail_wide",   2.5))
        self.trail_normal = float(bcfg.get("trail_normal", 2.0))
        self.trail_lock   = float(bcfg.get("trail_lock",   1.5))
        self.trail_tight  = float(bcfg.get("trail_tight",  1.0))
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
    def run(self, df: pl.DataFrame, symbol: str = "BTC/USDC",
            timeframe: str = None) -> "BacktestResult":
        import os
        from app.engine.engine import BaseStrategyML
        for strat in self.engine.strategies:
            if not isinstance(strat, BaseStrategyML):
                continue
            strat.reset_model()
            strat._cancel_event = self._cancel_event
            if self.use_pretrained_ml and timeframe:
                # Backtest standard : utilise le modèle pré-entraîné du timeframe.
                # Pas de réentraînement inline → rapide et déterministe.
                path = os.path.join(strat.model_dir, f"{strat.name}_{timeframe}.pkl")
                if strat.load_model(path):
                    strat.managed_externally = True
                    logger.debug(f"[Backtest] ML '{strat.name}' : modèle {timeframe} chargé")
                else:
                    logger.info(
                        f"[Backtest] ML '{strat.name}' : aucun modèle pour {timeframe} "
                        "— entraînement inline activé (lancez d'abord un cycle live ou l'optimiseur)"
                    )

        capital      = self.cfg["trading"].get("capital", 1000.0)
        risk         = self.cfg["trading"]["risk_per_trade"]
        threshold    = self.cfg["trading"].get("score_threshold", 0.60)

        # Résolution des paramètres : base (strategy_params) + overlay optimizer_results
        # via la même logique que le live trader — garantit la cohérence backtest/live.
        strat_params = resolve_strategy_params(self.cfg, timeframe)
        trades       = []
        equity_curve = [capital]
        timestamps   = [str(df["time"][0]) if "time" in df.columns else "0"]
        position     = None
        trade_id     = 0

        # Warmup dynamique : prend le max parmi les stratégies actives.
        # Chaque stratégie peut déclarer `warmup_bars` (attribut de classe ou d'instance).
        # Valeur minimale garantie : 210 barres (couvre EMA200 + ADX + ATR14).
        _MIN_WARMUP = 210
        warmup = _MIN_WARMUP
        for _s in self.engine.strategies:
            _wb = getattr(_s, "warmup_bars", None) or getattr(_s, "min_bars", None)
            if _wb is not None:
                try:
                    warmup = max(warmup, int(_wb))
                except (TypeError, ValueError):
                    pass
        if warmup > _MIN_WARMUP:
            logger.debug(f"[Backtest] Warmup dynamique : {warmup} barres")

        # ── Pré-calculs vectorisés O(n) ───────────────────────────────────────
        from app.core.indicators import precompute_df as _precompute
        df = _precompute(df)

        # Arrays numpy pour accès O(1) dans la boucle
        atr_arr   = df["_pre_atr14"].to_numpy().astype(float)
        low_arr   = df["low"].to_numpy().astype(float)
        high_arr  = df["high"].to_numpy().astype(float)
        close_arr = df["close"].to_numpy().astype(float)
        open_arr  = df["open"].to_numpy().astype(float)

        for i in range(warmup, len(df) - 1):
            if self._cancel_event is not None and i % 100 == 0 and self._cancel_event.is_set():
                raise InterruptedError("Backtest annulé")
            window  = df[:i + 1]
            c_high  = high_arr[i]
            c_low   = low_arr[i]
            c_open  = open_arr[i]

            # ── Gestion de la position ouverte ────────────────────────────────
            if position is not None:
                side    = position["side"]
                entry   = position["entry"]
                stop    = position["stop"]
                c_close = close_arr[i]

                if side == "long":
                    mae_pts = c_low  - entry
                    mfe_pts = c_high - entry
                else:
                    mae_pts = entry - c_high
                    mfe_pts = entry - c_low
                if entry > 0:
                    position["mae"] = min(position.get("mae", 0.0), mae_pts / entry * 100)
                    position["mfe"] = max(position.get("mfe", 0.0), mfe_pts / entry * 100)

                stop_hit = (side == "long"  and c_low  <= stop) or \
                           (side == "short" and c_high >= stop)

                if stop_hit:
                    exec_price  = stop * (1 - self.spread_pct) if side == "long" \
                                  else stop * (1 + self.spread_pct)
                    trail_phase = "unknown"
                    _tr = position.get("_trailing")
                    if _tr and hasattr(_tr, "_dts") and _tr._dts:
                        trail_phase = _tr._dts.phase_name

                    # position["size"] est déjà la taille post-partial_fill (appliquée à l'entrée).
                    # Ne pas appliquer partial_fill une seconde fois à la sortie.
                    fill_size   = position["size"]
                    fees        = self._fees(exec_price, fill_size, maker=False)
                    bars_held   = i - position["bar"]
                    days_held   = bars_held * _bar_to_days(
                        self.cfg["trading"].get("timeframe", "1h"))
                    borrow_cost = position["notional"] * self.borrow_rate * days_held

                    gross = (exec_price - entry) * fill_size * (1 if side == "long" else -1)
                    pnl   = gross - fees - borrow_cost
                    capital += pnl

                    ts = str(df["time"][i]) if "time" in df.columns else str(i)
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
                                               (1 if side == "long" else -1), 3) if entry else 0.0,
                        "duration_bars": bars_held,
                        "fill_pct":      self.partial_fill,
                        "stop_trail":    position.pop("_stop_trail", []),
                    })
                    position.pop("_trailing", None)
                    trades.append(position)
                    equity_curve.append(round(capital, 4))
                    timestamps.append(ts)
                    position = None

                else:
                    atr_v         = float(atr_arr[i]) or 1e-8
                    bars_held_now = i - position["bar"]
                    _tr           = position.get("_trailing")
                    lo20 = low_arr[max(0, i - 19):i + 1].tolist()
                    hi20 = high_arr[max(0, i - 19):i + 1].tolist()

                    if _tr:
                        new_stop = _tr.update_stop(
                            current_price = c_close,
                            current_stop  = stop,
                            atr           = atr_v, side = side, entry = entry,
                            bars_held     = bars_held_now,
                            recent_lows   = lo20,
                            recent_highs  = hi20,
                        )
                        position["stop"] = new_stop
                        if hasattr(_tr, "_dts") and _tr._dts:
                            position["trail_phase"] = _tr._dts.phase_name
                        if i % 3 == 0:
                            position["_stop_trail"].append({"bar": i, "stop": round(new_stop, 6)})

                continue

            # ── Cherche un signal ─────────────────────────────────────────────
            signal = self.engine.best_signal(window, strat_params, threshold=threshold)
            if signal["side"] == "none":
                continue

            atr_v = float(atr_arr[i])
            if atr_v <= 0:
                continue

            exec_price = float(df["open"][i + 1])
            if signal["side"] == "long":
                exec_price *= (1 + self.spread_pct)
            else:
                exec_price *= (1 - self.spread_pct)

            _trailing = self._make_trailing()
            stop      = _trailing.initial_stop(exec_price, atr_v, signal["side"])

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
            ts = str(df["time"][i]) if "time" in df.columns else str(i)

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
                "_stop_trail":  [{"bar": i + 1, "stop": round(stop, 6)}],
            }

        # ── Clôture forcée en fin de série ────────────────────────────────────
        if position is not None:
            last_price  = float(df["close"][-1])
            # position["size"] est déjà la taille post-partial_fill (appliquée à l'entrée).
            # Ne pas appliquer partial_fill une seconde fois à la sortie finale.
            fill_size   = position["size"]
            fees        = self._fees(last_price, fill_size, maker=True)
            bars_held   = len(df) - 1 - position["bar"]
            days_held   = bars_held * _bar_to_days(self.cfg["trading"].get("timeframe", "1h"))
            borrow_cost = position["notional"] * self.borrow_rate * days_held
            gross = (last_price - position["entry"]) * fill_size * (1 if position["side"] == "long" else -1)
            pnl   = gross - fees - borrow_cost
            capital += pnl
            position.update({
                "pnl":           round(pnl, 6),
                "fees":          round(fees, 6),
                "borrow_cost":   round(borrow_cost, 6),
                "exit":          round(last_price, 6),
                "status":        "closed_eod",
                "exit_bar":      len(df) - 1,
                "exit_time":     str(df["time"][-1]) if "time" in df.columns else str(len(df) - 1),
                "exit_reason":   "end_of_data",
                "pnl_pct":       round((last_price - position["entry"]) / position["entry"] * 100 *
                                       (1 if position["side"] == "long" else -1), 3) if position["entry"] else 0.0,
                "duration_bars": bars_held,
                "fill_pct":      self.partial_fill,
                "stop_trail":    position.pop("_stop_trail", []),
            })
            position.pop("_trailing", None)
            trades.append(position)
            equity_curve.append(round(capital, 4))

        _tf = timeframe or self.cfg["trading"].get("timeframe", "1h")
        result = BacktestResult(trades, equity_curve, self.initial_capital(self.cfg), timestamps, timeframe=_tf)
        return self._add_buy_and_hold(result, df)

    def _add_buy_and_hold(self, result: "BacktestResult", df: pl.DataFrame) -> "BacktestResult":
        """Calcule le benchmark Buy & Hold sur la même période que le backtest."""
        try:
            warmup = 210
            if len(df) <= warmup:
                return result
            first_price = float(df["close"][warmup])
            last_price  = float(df["close"][-1])
            if first_price <= 0:
                return result
            bnh_pct = (last_price - first_price) / first_price * 100
            bnh_pnl = result.initial_capital * bnh_pct / 100
            result.buy_and_hold_pnl = round(bnh_pnl, 4)
            result.buy_and_hold_pct = round(bnh_pct, 3)
            result.alpha            = round(result.total_pnl - bnh_pnl, 4)
        except Exception as e:
            logger.debug(f"[BnH] Calcul benchmark KO : {e}")
        return result

    def initial_capital(self, cfg: dict) -> float:
        return cfg["trading"].get("capital", 1000.0)

    def _fees(self, price: float, size: float, maker: bool = False) -> float:
        rate = self.maker_fee if maker else self.taker_fee
        return price * size * rate


# ── Walk-Forward ──
class WalkForwardAnalyzer:
    def __init__(self, engine: Engine, cfg: dict, n_folds: int = 5):
        self.engine  = engine
        self.cfg     = cfg
        self.n_folds = n_folds

    def run(self, df: pl.DataFrame, symbol: str = "BTC/USDC") -> dict:
        n      = len(df)
        fold_n = n // (self.n_folds + 1)
        WARMUP = 220
        MIN_IS = WARMUP + 50
        MIN_OOS = 40
        if fold_n < MIN_OOS:
            return {"error": f"Données insuffisantes pour Walk-Forward ({n} barres · min {MIN_OOS * (self.n_folds+1)})"}
        if fold_n < MIN_IS:
            return {
                "error": (
                    f"IS trop court pour les stratégies EMA ({fold_n} barres/fold · "
                    f"min {MIN_IS} requis). Augmentez les bougies à ≥{MIN_IS * (self.n_folds+1)} "
                    f"ou réduisez les folds."
                ),
                "n_bars": n,
                "fold_n": fold_n,
                "min_required": MIN_IS * (self.n_folds + 1),
            }

        in_sample_results  = []
        out_sample_results = []

        for k in range(self.n_folds):
            is_end  = fold_n * (k + 1)
            oos_end = min(fold_n * (k + 2), n)
            df_is   = df[:is_end]
            df_oos  = df[is_end:oos_end]
            if len(df_oos) < 30:
                continue
            try:
                import importlib as _imp
                fresh_strats = []
                for s in self.engine.strategies:
                    mod = _imp.import_module(f"app.strategies.{s.name}")
                    fresh_strats.append(mod.Strategy())
                eng_is  = Engine(); [eng_is.register(s, silent=True)  for s in fresh_strats]
                fresh_strats_oos = []
                for s in self.engine.strategies:
                    mod = _imp.import_module(f"app.strategies.{s.name}")
                    fresh_strats_oos.append(mod.Strategy())
                eng_oos = Engine(); [eng_oos.register(s, silent=True) for s in fresh_strats_oos]

                bt_is  = Backtester(eng_is,  self.cfg)
                bt_oos = Backtester(eng_oos, self.cfg)
                r_is   = bt_is.run(df_is,  symbol).to_dict()
                r_oos  = bt_oos.run(df_oos, symbol).to_dict()
                in_sample_results.append(r_is)
                out_sample_results.append(r_oos)
            except Exception as e:
                logger.error(f"[WF] Fold {k} : {e}", exc_info=True)

        if not out_sample_results:
            return {"error": "Aucun fold OOS valide"}

        oos_pnl    = [r["total_pnl"]  for r in out_sample_results]
        oos_sharpe = [r["sharpe"]     for r in out_sample_results]
        oos_wr     = [r["win_rate"]   for r in out_sample_results]

        return {
            "n_folds":        len(out_sample_results),
            "avg_oos_pnl":    round(_sf(float(np.mean(oos_pnl)),    0.0), 4),
            "avg_oos_sharpe": round(_sf(float(np.mean(oos_sharpe)), 0.0), 3),
            "avg_oos_wr":     round(_sf(float(np.mean(oos_wr)),     0.0), 2),
            "consistency":    round(sum(1 for p in oos_pnl if p > 0) / len(oos_pnl) * 100, 1),
            "in_sample":      in_sample_results,
            "out_of_sample":  out_sample_results,
        }


# ── Monte-Carlo ──
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

        return {
            "runs":               self.n_runs,
            "confidence":         self.confidence,
            "final_equity_mean":  round(_sf(float(np.mean(finals)),  0.0), 2),
            "final_equity_p5":    round(_sf(float(np.percentile(finals,  5)),  0.0), 2),
            "final_equity_p95":   round(_sf(float(np.percentile(finals, 95)),  0.0), 2),
            "max_dd_p95":         round(_sf(abs(float(np.percentile(max_dds, 95))), 0.0), 2),
            "prob_profit":        round(sum(1 for f in finals if f > initial_capital) / self.n_runs * 100, 1),
            "prob_ruin_10pct":    round(sum(1 for d in max_dds if d < -10) / self.n_runs * 100, 1),
        }
