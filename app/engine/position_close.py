"""Clôture d'une position de backtest, totale ou partielle (DETTE-04c).

Extrait de `position_lifecycle.py` (741 lignes) — le fichier qui portait
`FIN-01` et `FIN-02`, tous deux nés d'un découpage qui avait déplacé la
comptabilité des frais. Le déplacement est ici littéral, et deux invariants
comptables le verrouillent (`test_execution_parity`).
"""
import logging
from typing import Optional

from app.core.execution import close_pnl as _close_pnl
from app.core.execution import quantize_size as _quantize_size
from app.core.risk.sizer import _floor_to
from app.engine.position_hours import _heures_detenues
from app.live.protocols import LifecycleHost

logger = logging.getLogger(__name__)


class PositionCloseMixin(LifecycleHost):
    def _close_at(self, ctx, position: dict, i: int, exec_price: float,
                  exit_reason: str, *, maker: bool, status: str = "closed",
                  append_ts: bool = True, ref_price: Optional[float] = None) -> float:
        """Clôture commune. ``ref_price`` = prix avant spread (sinon slip_exit = 0)."""
        df         = ctx.df
        side       = position["side"]
        entry      = position["entry"]
        # size déjà post-partial_fill : ne pas réappliquer à la sortie.
        fill_size  = position["size"]
        bars_held  = i - position["bar"]
        hours_held = _heures_detenues(ctx, position["bar"], i)
        pnl, fees, borrow = _close_pnl(
            side=side, entry=entry, exit_price=exec_price, size=fill_size,
            notional=position["notional"],
            fee_rate=(self.maker_fee if maker else self.taker_fee),
            daily_rate=self.borrow_rate, hours_held=hours_held,
            periods_per_day=self.borrow_periods,
            venue=self._venue,
        )
        impact = self._impact_cost(ctx, i, position["notional"])
        if impact:
            pnl -= impact
            fees += impact
        funding = self._funding_cost(ctx, position, i, hours_held)
        if funding:
            pnl -= funding
            position["funding_cost"] = round(
                position.get("funding_cost", 0.0) + funding, 8)
        slip_exit = (abs(exec_price - ref_price) * fill_size
                     if ref_price is not None else 0.0) + impact
        # FIN-01/FIN-02 — deux accumulateurs distincts, à ne pas confondre :
        #   `fees`       = TOUS les frais déjà prélevés (entrée initiale, jambes
        #                  partielles, pyramidages) ;
        #   `entry_fees` = le seul côté ENTRÉE (initiale + pyramidages), qu'il
        #                  faut retrancher du PnL journalisé car il a été
        #                  débité de `ctx.capital` à l'ouverture et à chaque
        #                  scale-in, sans jamais passer par `_close_pnl`.
        # `close_pnl` ne rend que les frais de la sortie finale : on les ajoute
        # au cumul, on ne l'écrase pas (sinon jambes et pyramidages disparaissent).
        frais_cumules = float(position.get("fees", 0.0) or 0.0)
        entry_fees = float(position.get("entry_fees", position.get("fees", 0.0)) or 0.0)
        fees = frais_cumules + fees
        ctx.capital += pnl
        realized = position.pop("_realized_pnl", 0.0)
        gross_realized = position.pop("_gross_realized", 0.0)
        # BT-09 : plus-haut d'équité pour la courbe de dé-risquage en drawdown.
        ctx.peak_capital = max(getattr(ctx, "peak_capital", ctx.capital), ctx.capital)
        ts = str(df["time"][i]) if "time" in df.columns else str(i)
        position.update({
            # Frais d'entrée déjà prélevés sur capital : les retrancher du PnL journalisé.
            "pnl":           round(pnl + realized - entry_fees, 6),
            "entry_fees":    round(entry_fees, 6),
            "fees":          round(fees, 6),
            "borrow_cost":   round(position.get("borrow_cost", 0.0) + borrow, 6),
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
            # L0 (§99/§49) — coûts ventilés. `fees` agrège entrée+sortie+impact ;
            # `gross_pnl` vient des PRIX, pas d'une somme de composantes : ⚠ `pnl`
            # ne retranche pas les frais d'entrée (déjà prélevés sur ctx.capital
            # à l'ouverture), donc `pnl + fees + borrow` ne les reconstituerait pas.
            "slippage_cost": round(position.get("slippage_cost", 0.0) + slip_exit, 6),
            "funding_cost":  round(position.get("funding_cost", 0.0), 6),
            "gross_pnl":     round(gross_realized + (exec_price - entry) * fill_size *
                                   (1 if side == "long" else -1), 6),
            # L1 — jambes réalisées avant la clôture finale (§29). Liste vide
            # pour toute stratégie qui ne demande pas de sorties partielles.
            "exits":         position.pop("_exits", []),
            "size_initial":  position.pop("size_initial", fill_size),
        })
        position.pop("_trailing", None)
        position.pop("_targets", None)
        position.pop("_be_done", None)
        ctx.trades.append(position)
        ctx.equity_curve.append(round(ctx.capital, 4))
        _ledger = getattr(ctx, "ledger", None)
        _pk = position.get("_pos_key")
        if _ledger is not None and _pk:
            _ledger.release(_pk)
        if append_ts:
            ctx.timestamps.append(ts)

        # QW-6 (étape 6) — notifier le risk gate du résultat du trade
        # (pour incrémenter les compteurs consecutive_losses, daily_pnl, etc.)
        gate = getattr(ctx, "risk_gate", None)
        if gate is not None:
            from app.core.bot_identity import build_slot_key
            strat_name = position.get("strategy", "")
            slot_key = build_slot_key(strat_name, ctx.timeframe, ctx.symbol)
            try:
                close_ts = df["time"][i]
                day_key = str(close_ts)[:10] if close_ts is not None else ""
            except Exception:
                day_key = ""
            # B-11 : ctx.capital inclut déjà les jambes partielles (`realized`).
            # Soustraire seulement `pnl` décalait le DD journalier du slot.
            capital_before = ctx.capital - pnl - realized
            gate.record_trade_result(i, slot_key, pnl + realized, day_key,
                                     capital_before)

        return pnl + realized

    def _close_partial_at(self, ctx, position: dict, i: int, exec_price: float,
                          fraction: float, reason: str, *, maker: bool,
                          ref_price: Optional[float] = None) -> float:
        """Réalise ``fraction`` de la taille initiale. Pas de point d'équité
        (un par trade — sinon l'annualisation du Sharpe change)."""
        size0 = position.get("size_initial", position["size"])
        part = _quantize_size(min(_floor_to(size0 * fraction, 6),
                                  position["size"]), self._venue)
        if part <= 0 or position["size"] <= 0:
            return 0.0
        # Prorata du notionnel : le reliquat doit rester cohérent avec la taille.
        notional_part = position["notional"] * (part / position["size"])
        hours_held = _heures_detenues(ctx, position["bar"], i)
        side = position["side"]
        pnl, fees, borrow = _close_pnl(
            side=side, entry=position["entry"], exit_price=exec_price, size=part,
            notional=notional_part,
            fee_rate=(self.maker_fee if maker else self.taker_fee),
            daily_rate=self.borrow_rate, hours_held=hours_held,
            periods_per_day=self.borrow_periods, venue=self._venue,
        )
        impact = self._impact_cost(ctx, i, notional_part)
        if impact:
            pnl -= impact
            fees += impact
        slip = (abs(exec_price - ref_price) * part
                if ref_price is not None else 0.0) + impact

        ctx.capital += pnl
        ctx.peak_capital = max(getattr(ctx, "peak_capital", ctx.capital), ctx.capital)
        position["size"]     = round(position["size"] - part, 8)
        position["notional"] = round(position["notional"] - notional_part, 6)
        position["fees"]     = round(position.get("fees", 0.0) + fees, 8)
        position["borrow_cost"]   = round(position.get("borrow_cost", 0.0) + borrow, 8)
        position["slippage_cost"] = round(position.get("slippage_cost", 0.0) + slip, 8)
        position["_realized_pnl"] = position.get("_realized_pnl", 0.0) + pnl
        position["_gross_realized"] = position.get("_gross_realized", 0.0) + \
            (exec_price - position["entry"]) * part * (1 if side == "long" else -1)
        position.setdefault("_exits", []).append({
            "bar": i, "price": round(exec_price, 6), "size": round(part, 8),
            "fraction": round(part / size0, 4) if size0 else 0.0,
            "reason": str(reason), "pnl": round(pnl, 6),
        })
        ctx.diag["partial_exits"] = ctx.diag.get("partial_exits", 0) + 1
        _ledger = getattr(ctx, "ledger", None)
        _pk = position.get("_pos_key")
        if _ledger is not None and _pk:
            _ledger.resize(
                _pk,
                risk=abs(float(position["entry"]) - float(position.get("stop") or position["entry"]))
                     * float(position["size"]),
                notional=float(position["notional"]),
            )
        return pnl

