"""Backtester (BacktestResult) — stop vérifié intrabar, trailing dynamique.

ARCH-010 : ``WalkForwardAnalyzer`` et ``MonteCarlo`` ont été extraits vers
``app/engine/walk_forward.py`` et ``app/engine/monte_carlo.py``. Ils sont
ré-exportés en fin de module pour préserver la compatibilité ascendante
(``from app.engine.backtest import WalkForwardAnalyzer`` continue de fonctionner).
"""
import logging
import math
import threading
import time
from typing import Dict, List, Optional

import numpy as np
import polars as pl

from app.core.config import DEFAULT_MAKER_FEE, DEFAULT_TAKER_FEE
from app.core.execution import close_pnl as _close_pnl
from app.core.execution import size_impact_cost as _size_impact_cost
from app.core.execution import trade_fees as _trade_fees
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL, resolve_strategy_params
from app.core.risk_curve import risk_multiplier as _risk_multiplier
from app.core.timeframes import TF_MINUTES as _TF_MINUTES
from app.core.timeframes import bars_per_year as _bars_per_year
from app.core.trailing import TrailingStopManager
from app.engine.engine import Engine


def _sf(v, fallback=None):
    """Safe float : convertit nan/inf en fallback pour JSON."""
    try:
        f = float(v)
        return fallback if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return fallback

logger = logging.getLogger(__name__)


# Timeframe → minutes — source unique (V4-A). L'ancienne table locale (9 clés)
# renvoyait le défaut pour 6h/8h/12h ; la canonique les couvre.
# S4-01/S4-02 : bars_per_year — facteur d'annualisation partagé avec le Sharpe
# live (health_mixin.py) — sans source unique, les deux Sharpe n'étaient pas
# comparables (cf. app/core/timeframes.py::bars_per_year).


def _bar_to_days(tf: str) -> float:
    return _TF_MINUTES.get(tf, 15) / 1440.0


def _iso_of(df: pl.DataFrame, idx: int) -> Optional[str]:
    """``time`` de la barre ``idx`` en ISO — borne ``as_of``/anti-chevauchement
    du registre ML (``app.ml.model_registry.to_iso``, importé localement pour
    éviter un import module-wide non nécessaire au reste de ce fichier)."""
    if df is None or len(df) == 0 or "time" not in df.columns:
        return None
    from app.ml.model_registry import to_iso
    return to_iso(df["time"][idx])


def _resolve_frozen_ml_model(strat, symbol: Optional[str], tf: Optional[str],
                             window_start: Optional[str], window_end: Optional[str]) -> dict:
    """Résout et charge le modèle figé pour ``strat`` via le registre ML
    (``ml_mode="frozen"``, ou repli d'une stratégie sans cadence de
    réentraînement configurée en ``ml_mode="simulated_live"``).

    ``as_of=window_start`` : ne retient qu'un modèle entraîné AVANT le début
    de la fenêtre backtestée — c'est la garde qui empêche un backtest de
    charger un modèle qui a vu des données de la période évaluée. Un
    chevauchement résiduel (modèle legacy sans date, ou fenêtre de train
    partiellement postérieure) est signalé (log + ``overlap_warning``), pas
    silencieusement ignoré.

    Ne lève jamais : un modèle introuvable/illisible reste un repli sur
    l'entraînement inline (comportement historique), mais ce repli est
    désormais TOUJOURS visible dans l'entrée retournée (``fallback_to_inline``)
    au lieu d'un simple log — c'est le changement demandé par ML-02 §4.1
    (« ce switch silencieux peut fausser une comparaison sans qu'on le voie »).
    """
    import app.ml.model_registry as ml_registry

    entry: Dict = {"resolved": False, "fallback_to_inline": True}
    if not tf:
        return entry
    base_dir = getattr(strat, "model_dir", "models") or "models"
    try:
        art = ml_registry.resolve(symbol, tf, strat.name, as_of=window_start, base_dir=base_dir)
    except Exception as e:
        logger.warning(f"[Backtest] ml_mode=frozen : resolve() KO pour {strat.name}/{tf} : {e}")
        return entry
    if art is None:
        logger.warning(
            f"[Backtest] ml_mode=frozen : aucun modèle résoluble pour {strat.name}/{tf} "
            f"(symbole={symbol}) — entraînement inline activé (lancez d'abord un cycle "
            f"live, le runner CLI, ou un optimiseur pour publier un modèle)."
        )
        return entry
    overlap = ml_registry.overlaps(art, window_start, window_end)
    if overlap:
        logger.warning(
            f"[Backtest] ml_mode=frozen : {strat.name}/{tf} — la fenêtre d'entraînement "
            f"de {art.version_id} ({art.train_start}..{art.train_end}) chevauche la "
            f"fenêtre backtestée ({window_start}..{window_end}) : fuite potentielle."
        )
    if not strat.load_model(art.path_prefix):
        logger.warning(
            f"[Backtest] ml_mode=frozen : {strat.name}/{tf} — modèle {art.version_id} "
            f"résolu mais illisible — entraînement inline activé."
        )
        return entry
    strat.managed_externally = True
    logger.debug(f"[Backtest] ml_mode=frozen : {strat.name}/{tf} -> {art.version_id} chargé")
    entry.update({
        "resolved": True, "fallback_to_inline": False,
        "version_id": art.version_id, "train_start": art.train_start,
        "train_end": art.train_end, "auc": round(float(art.auc), 4),
        "legacy": art.legacy, "overlap_warning": overlap,
    })
    return entry


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
        # S4-05 : pré-groupement en une passe — l'ancien code refiltrait
        # `closed` (par ex. `[t for t in closed if t.get("strategy") == s]`)
        # TROIS fois par stratégie, soit O(n×k) sur n trades / k stratégies.
        # `trades_by_strategy` préserve l'ordre chronologique (celui de
        # `closed`), requis par la courbe d'équité et le Sharpe ci-dessous.
        from collections import defaultdict as _defaultdict
        trades_by_strategy: Dict[str, list] = _defaultdict(list)
        for t in closed:
            trades_by_strategy[t.get("strategy", "unknown")].append(t)

        self.by_strategy: Dict[str, dict] = {}
        for s, strat_trades in trades_by_strategy.items():
            d = {"trades": 0, "wins": 0, "pnl": 0.0, "fees": 0.0}
            for t in strat_trades:
                d["trades"] += 1
                d["pnl"]    += t["pnl"]
                d["fees"]   += t.get("fees", 0)
                if t["pnl"] > 0:
                    d["wins"] += 1
            self.by_strategy[s] = d

        for s, d in self.by_strategy.items():
            strat_trades = trades_by_strategy[s]
            sd_pnls = [t["pnl"] for t in strat_trades]
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
            for t in strat_trades:
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

            d["trades"] = strat_trades

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
            "diagnostics":        getattr(self, "diagnostics", None),
            "ml_info":            getattr(self, "ml_info", None),
        }


# ── Backtester ──
_ML_MODES = ("frozen", "inline", "simulated_live")


class Backtester:
    """Backtester trailing stop multi-phases, sans TP fixe.

    ``ml_mode`` (ML-02) pilote le comportement des stratégies ``BaseStrategyML`` :

    - ``"frozen"`` (défaut) : résout le dernier modèle promu au registre
      antérieur au début de la fenêtre backtestée (``as_of``) — rapide,
      déterministe, sans fuite temporelle. Repli visible sur l'entraînement
      inline si aucun modèle n'est résoluble (cf. ``result.ml_info``).
    - ``"inline"`` : réentraînement walk-forward par la stratégie elle-même
      (comportement historique de ``use_pretrained_ml=False``) — utilisé par
      l'optimiseur et les tests qui évaluent le comportement réel de la ML.
    - ``"simulated_live"`` : rejoue la politique de rafraîchissement complète
      (``app.ml.policy.maybe_refresh`` — entraînement + gate + registre) aux
      frontières de cadence de chaque stratégie, comme si le backtest était
      vécu en live. Pour une stratégie sans cadence configurée (modèle figé
      pour toujours), se comporte comme ``"frozen"``.

    ``use_pretrained_ml`` (bool) reste accepté pour compatibilité ascendante :
    ``True``/``False`` équivalent à ``ml_mode="frozen"``/``"inline"``. Certains
    appelants mutent l'attribut ``use_pretrained_ml`` après construction (avant
    ``run()``) — ce chemin continue de fonctionner : ``ml_mode`` explicite est
    prioritaire uniquement s'il a été passé au constructeur.
    """
    def __init__(self, engine: Engine, cfg: dict,
                 cancel_event: Optional[threading.Event] = None,
                 use_pretrained_ml: bool = True,
                 ml_mode: Optional[str] = None):
        self.engine             = engine
        self.cfg                = cfg
        self._cancel_event      = cancel_event
        self.use_pretrained_ml  = use_pretrained_ml
        if ml_mode is not None and ml_mode not in _ML_MODES:
            raise ValueError(f"ml_mode invalide : {ml_mode!r} (attendu parmi {_ML_MODES})")
        self._ml_mode_explicit  = ml_mode
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

        self.taker_fee    = tcfg.get("taker_fee", DEFAULT_TAKER_FEE)
        self.maker_fee    = tcfg.get("maker_fee", DEFAULT_MAKER_FEE)
        self.borrow_rate  = tcfg.get("borrow_rate_daily", 0.0002)
        self.borrow_periods = int(tcfg.get("borrow_periods_per_day", 24))
        self.spread_pct   = bcfg.get("spread_pct",        0.0005)
        # BT-10 : modèle de slippage dépendant de la taille (off par défaut).
        # "size" → coût d'impact additionnel notional × spread_pct × k ×
        # (notional / volume quote moyen 20 barres), appliqué à l'entrée, aux
        # scale-ins et à la sortie. "static" (défaut) = byte-identique.
        self.slippage_model = str(bcfg.get("slippage_model", "static"))
        self.slippage_k     = float(bcfg.get("slippage_k", 1.0))
        self.partial_fill = bcfg.get("partial_fill_pct",  0.95)
        self.max_notional_pct = float(bcfg.get("max_notional_pct", 0.20))  # BT-03 : aligné sur le live (risk.py)

    def _find_strategy(self, name: str):
        """Récupère l'instance Strategy par son nom (pour les hooks comme
        ``check_early_exit``). Retourne None si introuvable."""
        if not name:
            return None
        for s in self.engine.strategies:
            if getattr(s, "name", None) == name:
                return s
        return None

    def _make_trailing(self, override: dict = None):
        ov = override or {}
        return TrailingStopManager(
            mult             = float(ov.get("trail_wide",   self.trail_wide)),
            grace_bars       = int(ov.get("grace_bars",     self.grace_bars)),
            breakeven_r      = float(ov.get("breakeven_r",  self.breakeven_r)),
            trail_tight_mult = float(ov.get("trail_tight",  self.trail_tight)),
            lock_r           = float(ov.get("lock_r",       self.lock_r)),
            tight_r          = float(ov.get("tight_r",      self.tight_r)),
            lock_ratio       = float(ov.get("lock_ratio",   self.lock_ratio)),
            use_swing        = bool(ov.get("use_swing",     self.use_swing)),
            mode             = str(ov.get("mode",           "dynamic")),
        )

    # ── Cycle de vie d'une position (extrait de run() — V13) ──────────────────

    def _close_at(self, ctx, position: dict, i: int, exec_price: float,
                  exit_reason: str, *, maker: bool, status: str = "closed",
                  append_ts: bool = True) -> float:
        """Clôture commune à toutes les sorties (early-exit, time-exit, TP,
        stop, fin de série) : frais, coût d'emprunt (formule composée partagée
        avec le live — app/core/execution.py), PnL net, enregistrement du
        trade et de la courbe d'équité. Retourne le PnL net."""
        df         = ctx.df
        side       = position["side"]
        entry      = position["entry"]
        # position["size"] est déjà la taille post-partial_fill (appliquée à
        # l'entrée) : ne pas réappliquer partial_fill à la sortie.
        fill_size  = position["size"]
        bars_held  = i - position["bar"]
        hours_held = bars_held * _bar_to_days(ctx.timeframe) * 24.0
        pnl, fees, borrow = _close_pnl(
            side=side, entry=entry, exit_price=exec_price, size=fill_size,
            notional=position["notional"],
            fee_rate=(self.maker_fee if maker else self.taker_fee),
            daily_rate=self.borrow_rate, hours_held=hours_held,
            periods_per_day=self.borrow_periods,
        )
        impact = self._impact_cost(ctx, i, position["notional"])   # BT-10
        if impact:
            pnl -= impact
            fees += impact
        ctx.capital += pnl
        # BT-09 : plus-haut d'équité pour la courbe de dé-risquage en drawdown.
        ctx.peak_capital = max(getattr(ctx, "peak_capital", ctx.capital), ctx.capital)
        ts = str(df["time"][i]) if "time" in df.columns else str(i)
        position.update({
            "pnl":           round(pnl, 6),
            "fees":          round(fees, 6),
            "borrow_cost":   round(borrow, 6),
            "exit":          round(exec_price, 6),
            "status":        status,
            "exit_bar":      i,
            "exit_time":     ts,
            "exit_reason":   str(exit_reason),
            "pnl_pct":       round((exec_price - entry) / entry * 100 *
                                   (1 if side == "long" else -1), 3) if entry else 0.0,
            "duration_bars": bars_held,
            "fill_pct":      self.partial_fill,
            "stop_trail":    position.pop("_stop_trail", []),
        })
        position.pop("_trailing", None)
        ctx.trades.append(position)
        ctx.equity_curve.append(round(ctx.capital, 4))
        if append_ts:
            ctx.timestamps.append(ts)
        return pnl

    def _manage_open_position(self, ctx, position: dict, i: int):
        """Gère la position ouverte sur la barre ``i`` : MAE/MFE, sorties
        (early-exit stratégie, time-exit, TP, stop intrabar), sinon mise à
        jour du trailing et pyramidage. Retourne la position (None si close)."""
        diag    = ctx.diag
        diag["bars_in_position"] += 1
        ctx.bars_current_position += 1
        side    = position["side"]
        entry   = position["entry"]
        stop    = position["stop"]
        c_high  = ctx.high_arr[i]
        c_low   = ctx.low_arr[i]
        c_close = ctx.close_arr[i]

        if side == "long":
            mae_pts = c_low  - entry
            mfe_pts = c_high - entry
        else:
            mae_pts = entry - c_high
            mfe_pts = entry - c_low
        if entry > 0:
            position["mae"] = min(position.get("mae", 0.0), mae_pts / entry * 100)
            position["mfe"] = max(position.get("mfe", 0.0), mfe_pts / entry * 100)

        # ── Sortie temporelle (exit_after_bars) — prioritaire sur trailing.
        # Utilisée par les stratégies type rapport V4 : sortie à la clôture
        # de la barre suivante, sans SL/TP (mesure pure du signal directionnel).
        exit_after = position.get("exit_after_bars")
        time_exit  = (exit_after is not None
                      and (i - position["bar"]) >= int(exit_after))

        stop_hit = (side == "long"  and c_low  <= stop) or \
                   (side == "short" and c_high >= stop)

        # ── Sortie anticipée pilotée par la stratégie ────────────────────────
        # Hook BaseStrategy.check_early_exit (changement de régime, inversion
        # du signal…). Non priorisée sur le SL : si SL touché intrabar, le SL
        # l'emporte.
        early_exit_reason = None
        if not stop_hit and not time_exit:
            strat = self._find_strategy(position.get("strategy", ""))
            if strat is not None:
                try:
                    early_exit_reason = strat.check_early_exit(
                        ctx.window, position, ctx.strat_params
                    )
                except Exception as _ee:
                    logger.warning(
                        f"[Backtest] check_early_exit({position.get('strategy', '')}) "
                        f"KO : {_ee}"
                    )

        # ── Take-profit fixe (intrabar) — optionnel via signal["tp_hint"].
        # Vérifié seulement si stop NON touché (priorité conservative au stop
        # en cas d'ambiguïté intrabar high/low).
        tp_val = position.get("take_profit")
        tp_hit = False
        if tp_val is not None and not stop_hit:
            tp_hit = (side == "long"  and c_high >= tp_val) or \
                     (side == "short" and c_low  <= tp_val)
        elif tp_val is not None and stop_hit:
            # STRAT-06/BT-13 : diagnostic pur — le stop l'emporte toujours
            # (comportement inchangé), on compte seulement les barres où le TP
            # aurait AUSSI été touché (ambiguïté intrabar réelle, mesurable
            # seulement en high/low faute de données tick).
            would_tp_hit = (side == "long"  and c_high >= tp_val) or \
                           (side == "short" and c_low  <= tp_val)
            if would_tp_hit:
                diag["tp_sl_ambiguous_bars"] += 1

        if early_exit_reason and not stop_hit and not tp_hit:
            self._close_at(ctx, position, i, c_close, early_exit_reason, maker=True)
            return None

        if time_exit and not stop_hit and not tp_hit:
            self._close_at(ctx, position, i, c_close, "exit_after_bars", maker=True)
            return None

        if tp_hit:
            # TP fixe touché : sortie au prix TP (spread défavorable, côté maker)
            exec_price = tp_val * (1 - self.spread_pct) if side == "long" \
                         else tp_val * (1 + self.spread_pct)
            self._close_at(ctx, position, i, exec_price, "take_profit", maker=True)
            return None

        if stop_hit:
            exec_price = stop * (1 - self.spread_pct) if side == "long" \
                         else stop * (1 + self.spread_pct)
            _tr = position.get("_trailing")
            if _tr and hasattr(_tr, "_dts") and _tr._dts:
                position["trail_phase"] = _tr._dts.phase_name
            else:
                position["trail_phase"] = "unknown"
            self._close_at(ctx, position, i, exec_price,
                           ("stop_loss" if position.get("disable_trailing")
                            else "trailing_stop"), maker=False)
            return None

        # ── Position conservée : trailing + pyramidage ───────────────────────
        atr_v         = float(ctx.atr_arr[i]) or 1e-8
        bars_held_now = i - position["bar"]
        # Skip trailing si désactivé via signal["disable_trailing"]=True :
        # le stop reste fixe (V4 standalone style : SL = entry ∓ 1.5×ATR).
        _tr = (None if position.get("disable_trailing")
               else position.get("_trailing"))
        if _tr:
            lo20 = ctx.low_arr[max(0, i - 19):i + 1].tolist()
            hi20 = ctx.high_arr[max(0, i - 19):i + 1].tolist()
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

        # ── Pyramidage (check_scale_in) ──────────────────────────────────────
        # La stratégie peut demander l'ajout d'une unité sur une position
        # gagnante. L'unité est sizée comme une entrée normale (risque %
        # capital / distance au stop courant), le prix d'entrée moyen et le
        # notional sont recalculés.
        strat_si = self._find_strategy(position.get("strategy", ""))
        if strat_si is not None:
            try:
                scale = strat_si.check_scale_in(ctx.window, position, ctx.strat_params)
            except Exception as _si:
                logger.warning(
                    f"[Backtest] check_scale_in({position.get('strategy', '')}) "
                    f"KO : {_si}"
                )
                scale = None
            if scale:
                add_price = c_close * (1 + self.spread_pct) if side == "long" \
                            else c_close * (1 - self.spread_pct)
                stop_dist = abs(add_price - position["stop"])
                if stop_dist > 0:
                    sf = max(0.0, min(float(scale.get("size_factor", 1.0)), 2.0))
                    add_size = ctx.capital * ctx.risk / stop_dist * sf * self.partial_fill
                    add_notional = add_size * add_price
                    # Cap : le notional total reste sous max_notional_pct
                    room = ctx.capital * self.max_notional_pct - position["notional"]
                    if add_notional > room:
                        add_notional = max(room, 0.0)
                        add_size = add_notional / add_price
                    if add_notional >= 1.0 and add_size > 0:
                        add_fees = self._fees(add_price, add_size, maker=False)
                        add_fees += self._impact_cost(ctx, i, add_notional)  # BT-10
                        ctx.capital -= add_fees
                        new_size = position["size"] + add_size
                        position["entry"] = round(
                            (position["entry"] * position["size"]
                             + add_price * add_size) / new_size, 6)
                        position["size"]     = round(new_size, 6)
                        position["notional"] = round(
                            position["notional"] + add_notional, 4)
                        position["fees"]     = round(
                            position.get("fees", 0.0) + add_fees, 6)
                        position["scale_ins"] = position.get("scale_ins", 0) + 1
                        diag["scale_ins"] = diag.get("scale_ins", 0) + 1

        return position

    def _try_enter(self, ctx, signal: dict, i: int):
        """Tente d'ouvrir une position depuis un signal accepté : stop/TP
        initiaux, sizing par risque (cap notional, size_factor, partial fill),
        frais d'entrée. Retourne le dict position ou None si rejeté."""
        df   = ctx.df
        diag = ctx.diag

        atr_v = float(ctx.atr_arr[i])
        if atr_v <= 0:
            diag["rejected_atr_zero"] += 1
            logger.debug(f"[Backtest] bar {i} : trade rejeté (ATR<=0)")
            return None

        exec_price = float(df["open"][i + 1])
        if signal["side"] == "long":
            exec_price *= (1 + self.spread_pct)
        else:
            exec_price *= (1 - self.spread_pct)

        _trailing = self._make_trailing(signal.get("trail_override"))
        # Stop initial : priorité au multiplicateur ATR (calé sur exec_price,
        # robuste aux gaps close→open) ; sinon stop_hint absolu ; sinon trailing.
        if signal.get("sl_atr_mult") is not None:
            _sl_mult = float(signal["sl_atr_mult"])
            stop = (exec_price - _sl_mult * atr_v) if signal["side"] == "long" \
                   else (exec_price + _sl_mult * atr_v)
        elif signal.get("stop_hint") is not None:
            stop = float(signal["stop_hint"])
        else:
            stop = _trailing.initial_stop(exec_price, atr_v, signal["side"])

        # TP fixe optionnel : priorité au multiplicateur ATR (calé sur
        # exec_price), sinon tp_hint absolu fourni par la stratégie.
        if signal.get("tp_atr_mult") is not None:
            _tp_mult = float(signal["tp_atr_mult"])
            tp_init = (exec_price + _tp_mult * atr_v) if signal["side"] == "long" \
                      else (exec_price - _tp_mult * atr_v)
        elif signal.get("tp_hint") is not None:
            tp_init = float(signal["tp_hint"])
        else:
            tp_init = None

        disable_trailing = bool(signal.get("disable_trailing", False))

        stop_dist    = abs(exec_price - stop)
        # BT-09 : même courbe de dé-risquage que le live (RiskManager.compute_risk)
        # — ×0.75 si drawdown > 5 %, ×0.5 si > 10 % (app/core/risk_curve.py).
        peak = getattr(ctx, "peak_capital", ctx.capital) or ctx.capital
        dd   = max(0.0, (peak - ctx.capital) / peak) if peak > 0 else 0.0
        risk_amount  = ctx.capital * ctx.risk * _risk_multiplier(dd)
        size         = risk_amount / stop_dist if stop_dist > 0 else 0
        notional     = size * exec_price
        max_notional = ctx.capital * self.max_notional_pct
        if notional > max_notional:
            size     = max_notional / exec_price
            notional = max_notional
        if notional < 1.0 or size <= 0:
            diag["rejected_notional"] += 1
            logger.debug(
                f"[Backtest] bar {i} : trade rejeté "
                f"(notional={notional:.4f} size={size:.6f} capital={ctx.capital:.2f})"
            )
            return None

        # Size factor (demi-Kelly côté stratégie — ex. ×confidence) :
        # appliqué après le cap notional pour permettre à la stratégie de
        # réduire la taille sans buter sur max_notional_pct.
        # Cap haut à 2.0 : autorise les boosts type V7 SHORT_TD_HIGH (×1.5)
        # tout en gardant max_notional_pct comme garde-fou de risque global.
        size_factor = float(signal.get("size_factor", 1.0))
        size_factor = max(0.0, min(size_factor, 2.0))
        size       *= size_factor

        size       *= self.partial_fill
        notional    = size * exec_price
        entry_fees  = self._fees(exec_price, size, maker=False)
        entry_fees += self._impact_cost(ctx, i, notional)   # BT-10
        ctx.capital -= entry_fees
        ctx.trade_id += 1
        ts = str(df["time"][i]) if "time" in df.columns else str(i)

        position = {
            "id":              ctx.trade_id,
            "symbol":          ctx.symbol,
            "side":            signal["side"],
            "strategy":        signal.get("name", ""),
            "score":           round(signal.get("score", 0), 3),
            "entry":           round(exec_price, 6),
            "stop":            round(stop, 6),
            "take_profit":     round(tp_init, 6) if tp_init is not None else None,
            "exit_after_bars": signal.get("exit_after_bars"),
            "disable_trailing": disable_trailing,
            "size":            round(size, 6),
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
            # Champs V7 / V4 — utilisés pour les colonnes 'Sortie' et 'Setup'
            # du tableau de trades et pour les statistiques par exit_reason.
            "setup":           signal.get("setup"),
            "setup_priority":  signal.get("setup_priority"),
            "regime":          signal.get("regime"),
            "regime_lbl":      signal.get("regime_lbl"),
            "sl_atr_mult":     signal.get("sl_atr_mult"),
            "tp_atr_mult":     signal.get("tp_atr_mult"),
            "size_factor":     signal.get("size_factor"),
            "tf_detected":     signal.get("tf_detected"),
            "mae":          0.0,
            "mfe":          0.0,
            "_stop_trail":  [{"bar": i + 1, "stop": round(stop, 6)}],
        }
        diag["trades_opened"] += 1
        diag["last_trade_bar"] = i
        ctx.bars_current_position = 0
        return position

    # ── run ───────────────────────────────────────────────────────────────────
    def run(self, df: pl.DataFrame, symbol: str = DEFAULT_CONFIG_SYMBOL,
            timeframe: str = None) -> "BacktestResult":
        import app.ml.policy as _ml_policy
        from app.engine.engine import BaseStrategyML
        # Résolution des paramètres en amont du hook ``prepare_for_backtest`` :
        # certaines stratégies pré-calculent leurs features/votes en fonction du
        # paramétrage résolu (ex: signal_consensus → votes des sous-stratégies).
        # On expose donc ``_bt_params`` avant l'appel à prepare.
        # ``symbol`` transmis : une config héritée (sans dimension symbole) reste
        # celle de BTC/USDC ; les autres symboles prennent leur config dédiée si
        # elle existe, sinon les params de base (séparation des configs).
        strat_params = resolve_strategy_params(self.cfg, timeframe, symbol)

        # ML-02 : mode effectif — l'attribut ``use_pretrained_ml`` est relu ICI
        # (pas caché à __init__) pour rester compatible avec les appelants qui
        # le mutent après construction (cf. docstring de la classe).
        ml_mode = self._ml_mode_explicit or ("frozen" if self.use_pretrained_ml else "inline")
        symbol_key      = symbol or DEFAULT_CONFIG_SYMBOL
        window_start_iso = _iso_of(df, 0)
        window_end_iso   = _iso_of(df, -1)
        ml_info: Dict[str, Dict] = {"mode": ml_mode, "symbol": symbol_key,
                                    "timeframe": timeframe, "models": {}}
        # (stratégie, cadence_bars, dernière barre rafraîchie, params) pour le
        # rafraîchissement périodique en ml_mode="simulated_live" — alimenté
        # ci-dessous, consommé dans la boucle bar-par-bar plus loin.
        sim_live_entries: List[Dict] = []

        for strat in self.engine.strategies:
            strat._bt_params = strat_params
            # ── Spécifique ML : reset + configuration selon ml_mode ────────────
            if isinstance(strat, BaseStrategyML):
                strat.reset_model()
                strat._cancel_event = self._cancel_event
                sp = strat_params.get(strat.name, {})
                cadence_bars = int(sp.get("retrain_every") or 0)

                if ml_mode == "inline":
                    pass  # comportement historique : la stratégie s'auto-entraîne (need_train interne)
                elif ml_mode == "simulated_live" and cadence_bars > 0 and timeframe:
                    # Pas de résolution figée ici : la politique de gate décide
                    # au fil de la boucle (cf. plus bas), à partir d'un état
                    # non entraîné (cold start, comme un live fraîchement déployé).
                    strat.managed_externally = True
                    entry = {"requested_mode": "simulated_live", "cadence_bars": cadence_bars,
                             "n_refreshes": 0, "decisions": []}
                    ml_info["models"][strat.name] = entry
                    sim_live_entries.append({
                        "strat": strat, "symbol": symbol_key, "tf": timeframe,
                        "cadence_bars": cadence_bars, "last_refresh_bar": -1,
                        "params": sp, "entry": entry,
                    })
                else:
                    # "frozen", ou "simulated_live" sans cadence configurée
                    # (modèle figé pour toujours, ex. familles V4 pretrained).
                    entry = _resolve_frozen_ml_model(strat, symbol_key, timeframe,
                                                     window_start_iso, window_end_iso)
                    entry["requested_mode"] = ml_mode
                    ml_info["models"][strat.name] = entry
            # ── Pré-calcul des features (optionnel, par stratégie) ───────────
            # Hook ouvert à TOUTES les stratégies (ML ou rule-based) : les
            # stratégies rule-based l'utilisent aussi pour réutiliser des séries
            # causales lourdes (ex: supertrend_macd → SuperTrend pré-calculé,
            # signal_consensus → propagation à ses sous-stratégies).
            # Les stratégies à features lourdes exposent un hook
            # ``prepare_for_backtest(df)`` qui construit toutes leurs features
            # sur la fenêtre complète, en une seule passe. Ensuite ``score()``
            # lit la dernière ligne du cache au lieu de rebuild.
            #
            # Stratégies actuellement instrumentées (voir leur ``__init__`` /
            # ``prepare_for_backtest`` pour les détails de cache) :
            #   - opus_stat_pretrained_v4     (pandas, ~462 features)
            #   - opus_stat_retrained_v4      (polars, ~462 features)
            #   - opus_omnibus_v7_pretrained  (pandas, ~462 features)
            #   - opus_omnibus_v7             (polars, ~462 features)
            #   - scoring_statistique_opus_v4 (numpy, ~48 features, cache par adx_thr)
            #   - scoring_statistique_opus_v5 (numpy, ~48 features, cache par adx_thr)
            #   - ml_dynamic_threshold        (polars, ~30 features)
            #   - supertrend_macd             (SuperTrend/MACD causaux, O(n²)→O(n))
            #   - signal_consensus            (propage le hook à ses sous-stratégies)
            #
            # Ce hook est invoqué par TOUS les chemins qui passent par
            # ``Backtester.run`` — y compris donc les workers d'optimisation
            # (``app.engine.optimizer._eval_worker``), les folds Walk-Forward
            # (``WalkForwardAnalyzer.run``) et le replay live
            # (``app.api.routes.replay``). Chaque trial subprocess en
            # bénéficie automatiquement (build × 1 puis ~N-1 lookups O(1) par
            # barre du backtest), sans modification supplémentaire.
            # Symbole/TF exposés à la stratégie pour le catalogue FeatureStore
            # (clé (symbol, tf) du cache disque de features pré-calculées).
            strat._bt_symbol = symbol
            strat._bt_tf = timeframe or self.cfg["trading"].get("timeframe", "1h")
            prep = getattr(strat, "prepare_for_backtest", None)
            if callable(prep):
                try:
                    prep(df)
                except Exception as e:
                    logger.warning(
                        f"[Backtest] prepare_for_backtest('{strat.name}') KO : {e}"
                    )

        capital      = self.cfg["trading"].get("capital", 1000.0)
        risk         = self.cfg["trading"]["risk_per_trade"]
        threshold    = self.cfg["trading"].get("score_threshold", 0.60)

        # ``strat_params`` déjà résolu plus haut (avant prepare_for_backtest) :
        # base (strategy_params) + overlay optimizer_results, même logique que le
        # live trader — garantit la cohérence backtest/live.
        trades       = []
        equity_curve = [capital]
        timestamps   = [str(df["time"][0]) if "time" in df.columns else "0"]
        position     = None
        trade_id     = 0

        # ── Diagnostics ──────────────────────────────────────────────────────
        # Compteurs alimentés à chaque barre pour répondre à la question
        # « pourquoi mon backtest s'arrête de trader ? ». Exposés dans le
        # dict retourné par to_dict() sous la clé ``diagnostics``.
        diag = {
            "bars_total":            0,   # barres parcourues après warmup
            "bars_in_position":      0,   # barres passées avec une position ouverte
            "bars_seeking_signal":   0,   # barres sans position (recherche active)
            "signal_calls":          0,   # appels à best_signal() (= bars_seeking_signal)
            "signal_accepted":       0,   # un signal a été retenu et a tenté d'ouvrir un trade
            "rejected_atr_zero":     0,   # ATR <= 0 → trade refusé
            "rejected_notional":     0,   # notional < 1.0 ou size <= 0 → trade refusé
            "trades_opened":         0,
            "trades_closed":         0,
            "last_signal_bar":       None,
            "last_trade_bar":        None,
            "max_bars_no_signal":    0,   # plus longue séquence sans signal accepté
            "max_bars_in_position":  0,   # plus longue position détenue
            # STRAT-06/BT-13 : barres où stop ET take-profit auraient TOUS DEUX
            # été touchés (ambiguïté intrabar high/low, mesure seule — le stop
            # continue de toujours l'emporter, cf. _manage_open_position).
            "tp_sl_ambiguous_bars":  0,
        }
        per_strategy_stats: Dict[str, Dict[str, int]] = {}
        _bars_since_signal     = 0
        _bars_current_position = 0
        _prev_in_position      = False

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

        # Libellé des stratégies actives — chaque backtest de l'UI tourne une
        # stratégie par Backtester, donc ce libellé identifie la stratégie
        # concernée dans les logs de progression et de fin.
        _strat_label = ",".join(s.name for s in self.engine.strategies) or "?"

        total_bars = len(df) - 1 - warmup
        _t_loop    = time.time()
        logger.info(
            f"[Backtest] [{_strat_label}] {symbol} {timeframe or '?'} : démarrage boucle — "
            f"{total_bars} barres à parcourir (warmup={warmup}, total={len(df)})"
        )
        from types import SimpleNamespace
        ctx = SimpleNamespace(
            df=df, window=None, symbol=symbol,
            # Timeframe effectif du run (et non cfg.trading.timeframe : les
            # coûts d'emprunt étaient calculés sur le mauvais TF quand le
            # backtest tournait sur un TF différent de la config).
            timeframe=timeframe or self.cfg["trading"].get("timeframe", "1h"),
            capital=capital, peak_capital=capital, risk=risk, trade_id=trade_id,
            trades=trades, equity_curve=equity_curve, timestamps=timestamps,
            diag=diag, strat_params=strat_params,
            atr_arr=atr_arr, low_arr=low_arr, high_arr=high_arr,
            close_arr=close_arr,
            bars_current_position=_bars_current_position,
        )
        # BT-10 : volume quote moyen (20 barres, causal) pour le modèle "size".
        if self.slippage_model == "size" and "volume" in df.columns:
            ctx.qvol_arr = (df["volume"] * df["close"]).rolling_mean(20) \
                .fill_null(0.0).to_numpy().astype(float)
        else:
            ctx.qvol_arr = None

        for i in range(warmup, len(df) - 1):
            diag["bars_total"] += 1
            _had_position_at_start = position is not None
            # Transition close : la barre précédente avait une position, plus
            # maintenant. On ferme le compteur de durée et on met à jour le max.
            # (La gestion de position fait ``continue`` après une clôture, donc
            # on ne détecte la transition qu'au début de l'itération suivante.)
            if _prev_in_position and not _had_position_at_start:
                diag["trades_closed"] += 1
                if ctx.bars_current_position > diag["max_bars_in_position"]:
                    diag["max_bars_in_position"] = ctx.bars_current_position
                ctx.bars_current_position = 0
            _prev_in_position = _had_position_at_start
            if i % 100 == 0:
                if self._cancel_event is not None and self._cancel_event.is_set():
                    raise InterruptedError("Backtest annulé")
                # Log de progression toutes les 500 barres (≈ visibilité utilisateur
                # sans noyer les logs sur des backtests courts).
                if total_bars > 1000 and i % 500 == 0 and i > warmup:
                    done = i - warmup
                    pct  = 100.0 * done / max(total_bars, 1)
                    rate = done / max(time.time() - _t_loop, 0.001)
                    eta  = (total_bars - done) / max(rate, 0.001)
                    in_pos_pct = 100.0 * diag["bars_in_position"] / max(diag["bars_total"], 1)
                    logger.info(
                        f"[Backtest] [{_strat_label}] {symbol} {timeframe or '?'} : "
                        f"{done}/{total_bars} barres ({pct:.0f}%) — "
                        f"{rate:.0f} bars/s, ETA {eta:.0f}s, "
                        f"{len(trades)} trades, capital={ctx.capital:.2f} "
                        f"· {in_pos_pct:.0f}% en position · "
                        f"sig_acc={diag['signal_accepted']} "
                        f"rej_notional={diag['rejected_notional']} "
                        f"rej_atr={diag['rejected_atr_zero']}"
                    )
            ctx.window = df[:i + 1]

            # ── ml_mode="simulated_live" : rafraîchissement aux frontières de
            # cadence, indépendamment de la gestion de position (comme le
            # thread du live trainer, qui tourne sans se soucier des positions
            # ouvertes). Coût borné : une entrée par stratégie ML concernée,
            # déclenchée seulement tous les ``cadence_bars`` (800-3000+
            # barres typiquement), pas à chaque itération.
            for sle in sim_live_entries:
                if i - sle["last_refresh_bar"] < sle["cadence_bars"]:
                    continue
                sle["last_refresh_bar"] = i
                try:
                    res = _ml_policy.maybe_refresh(
                        sle["strat"], sle["symbol"], sle["tf"], ctx.window,
                        params=sle["params"], recipe=sle["strat"].name,
                        source="backtest_sim",
                        base_dir=getattr(sle["strat"], "model_dir", "models") or "models",
                    )
                except Exception as e:
                    res = {"decision": "failed", "reason": f"maybe_refresh KO : {e}"}
                    logger.warning(
                        f"[Backtest] simulated_live : {sle['strat'].name}/{sle['tf']} "
                        f"@bar {i} : {e}"
                    )
                sle["entry"]["n_refreshes"] += 1
                sle["entry"]["decisions"].append({"bar": i, **res})

            # ── Gestion de la position ouverte ────────────────────────────────
            if position is not None:
                position = self._manage_open_position(ctx, position, i)
                continue

            # ── Cherche un signal ─────────────────────────────────────────────
            diag["bars_seeking_signal"] += 1
            diag["signal_calls"] += 1
            signal = self.engine.best_signal(
                ctx.window, strat_params, threshold=threshold,
                stats=per_strategy_stats,
            )
            if signal["side"] == "none":
                _bars_since_signal += 1
                if _bars_since_signal > diag["max_bars_no_signal"]:
                    diag["max_bars_no_signal"] = _bars_since_signal
                continue

            diag["signal_accepted"] += 1
            diag["last_signal_bar"] = i
            _bars_since_signal = 0
            logger.debug(
                f"[Backtest] bar {i} : signal accepté — {signal.get('name')} "
                f"{signal.get('side')} score={signal.get('score', 0):.3f}"
            )
            position = self._try_enter(ctx, signal, i)

        capital                = ctx.capital
        trade_id               = ctx.trade_id
        _bars_current_position = ctx.bars_current_position

        # ── Clôture forcée en fin de série ────────────────────────────────────
        if position is not None:
            self._close_at(ctx, position, len(df) - 1, float(df["close"][-1]),
                           "end_of_data", maker=True, status="closed_eod",
                           append_ts=False)
            position = None
            capital  = ctx.capital

        # Finalise les compteurs de fin de boucle : si la dernière position
        # a été fermée à l'avant-dernière barre, la transition est déjà
        # comptée ; sinon (position toujours ouverte ou close en fin de série)
        # on rattrape ici.
        if _bars_current_position > diag["max_bars_in_position"]:
            diag["max_bars_in_position"] = _bars_current_position
        diag["per_strategy"] = per_strategy_stats

        # Récap final — visible en INFO pour diagnostiquer un backtest qui
        # « s'arrête de trader » : ratio temps en position, signaux générés,
        # signaux sous seuil, rejets notional, etc.
        bt = max(diag["bars_total"], 1)
        in_pos_pct = 100.0 * diag["bars_in_position"] / bt
        logger.info(
            f"[Backtest] [{_strat_label}] {symbol} {timeframe or '?'} : terminé — "
            f"{diag['bars_total']} barres, {diag['trades_opened']} trades ouverts, "
            f"{in_pos_pct:.0f}% du temps en position "
            f"(max {diag['max_bars_in_position']} barres consécutives), "
            f"signaux acceptés={diag['signal_accepted']}, "
            f"rejets notional={diag['rejected_notional']}, "
            f"ATR<=0={diag['rejected_atr_zero']}, "
            f"max sans signal={diag['max_bars_no_signal']} barres"
        )
        for sname, s in per_strategy_stats.items():
            total_seen = s.get("evaluated", 0)
            if total_seen == 0:
                continue
            logger.info(
                f"[Backtest]   └─ {sname} : évalué×{total_seen}, "
                f"proposés={s.get('proposed', 0)}, "
                f"<seuil={s.get('below_threshold', 0)}, "
                f">=seuil={s.get('above_threshold', 0)}, "
                f"erreurs={s.get('errors', 0)}"
            )

        _tf = timeframe or self.cfg["trading"].get("timeframe", "1h")
        result = BacktestResult(trades, equity_curve, self.initial_capital(self.cfg), timestamps, timeframe=_tf)
        result.diagnostics = diag
        result.ml_info = ml_info
        return self._add_buy_and_hold(result, df, warmup)

    def _add_buy_and_hold(self, result: "BacktestResult", df: pl.DataFrame,
                          warmup: int = 210) -> "BacktestResult":
        """Calcule le benchmark Buy & Hold sur la MÊME fenêtre que le backtest.

        FIN-04 : ``warmup`` doit être le warmup dynamique réellement utilisé par
        la boucle de trading (``run()``, potentiellement > 210 si une stratégie
        déclare un ``warmup_bars``/``min_bars`` plus grand) — un warmup figé à
        210 désynchronisait le prix de départ du Buy & Hold de la première
        barre réellement tradée, faussant l'alpha calculé.
        """
        try:
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

    def _impact_cost(self, ctx, i: int, notional: float) -> float:
        """BT-10 : coût d'impact croissant avec la taille RELATIVE du trade
        (participation au volume) — 0.0 si le modèle est off ou volume absent.
        Formule partagée avec le paper trading live (FIN-07) : voir
        ``app.core.execution.size_impact_cost``."""
        if self.slippage_model != "size":
            return 0.0
        qv = getattr(ctx, "qvol_arr", None)
        if qv is None or not (0 <= i < len(qv)):
            return 0.0
        return _size_impact_cost(notional, self.spread_pct, self.slippage_k, float(qv[i]))

    def _fees(self, price: float, size: float, maker: bool = False) -> float:
        return _trade_fees(price, size, self.maker_fee if maker else self.taker_fee)


# ── Ré-exports (compat ascendante — ARCH-010) ────────────────────────────────
# ``WalkForwardAnalyzer`` et ``MonteCarlo`` ont été extraits vers leurs propres
# modules (walk_forward.py / monte_carlo.py). Ils restent importables depuis
# backtest.py pour ne pas casser les callers existants (api/routes, cli,
# research/*, auto_optimizer). Imports placés EN FIN de module pour éviter un
# cycle : walk_forward.py fait un import lazy de ``Backtester`` dans sa méthode
# ``run``, et ``Backtester`` doit donc être défini avant cet import.
from app.engine.monte_carlo import MonteCarlo  # noqa: E402,F401
from app.engine.walk_forward import WalkForwardAnalyzer  # noqa: E402,F401
