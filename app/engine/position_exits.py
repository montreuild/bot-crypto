"""Décision de sortie d'une position de backtest (DETTE-04c).

Quelle sortie se déclenche à cette barre, dans quel ordre, et à quel prix de
remplissage. Extrait de `position_lifecycle.py`, littéralement.
"""
import logging
from typing import Any, Optional

from app.engine.position_exit_reason import RaisonDeSortie
from app.live.protocols import LifecycleHost

logger = logging.getLogger(__name__)


class _ExitsHost(LifecycleHost):
    """Fourni par `PositionCloseMixin`, mélangé au même hôte."""

    _close_at: Any
    _close_partial_at: Any


class PositionExitsMixin(_ExitsHost):
    def _fill_at_level(self, side: str, level: float, open_px: float,
                       *, stop: bool) -> tuple:
        """B-01 : fill au gap si la bougie ouvre au-delà du niveau.

        Un stop-market réel se remplit à l'ouverture, pas au niveau, quand
        celle-ci a déjà franchi le seuil. Symétrique pour un TP favorable.
        Retourne ``(exec_price, ref_price, gapped)``.
        """
        if stop:
            gapped = (side == "long" and open_px < level) or \
                     (side == "short" and open_px > level)
        else:
            gapped = (side == "long" and open_px > level) or \
                     (side == "short" and open_px < level)
        ref = open_px if gapped else level
        exec_price = ref * (1 - self.spread_pct) if side == "long" \
            else ref * (1 + self.spread_pct)
        return exec_price, ref, gapped

    def _evaluer_sorties(self, ctx, position: dict, i: int) -> Optional[RaisonDeSortie]:
        """Décide d'une clôture totale sur la barre ``i``, ou None.

        Early / time / TP / stop. Ne mute pas le capital — l'appelant clôture.
        """
        side, entry, stop = position["side"], position["entry"], position["stop"]
        c_high, c_low, c_close = ctx.high_arr[i], ctx.low_arr[i], ctx.close_arr[i]
        c_open = ctx.open_arr[i] if getattr(ctx, "open_arr", None) is not None else c_close
        if side == "long":
            mae_pts, mfe_pts = c_low - entry, c_high - entry
        else:
            mae_pts, mfe_pts = entry - c_high, entry - c_low
        if entry > 0:
            position["mae"] = min(position.get("mae", 0.0), mae_pts / entry * 100)
            position["mfe"] = max(position.get("mfe", 0.0), mfe_pts / entry * 100)
        exit_after = position.get("exit_after_bars")
        time_exit = (exit_after is not None
                     and (i - position["bar"]) >= int(exit_after))
        stop_hit = ((side == "long" and c_low <= stop)
                    or (side == "short" and c_high >= stop))
        early_exit_reason = None
        if not stop_hit and not time_exit:
            strat = self._find_strategy(position.get("strategy", ""))
            if strat is not None:
                try:
                    early_exit_reason = strat.check_early_exit(
                        ctx.window, position, ctx.strat_params)
                except Exception as _ee:
                    logger.warning(
                        f"[Backtest] check_early_exit({position.get('strategy', '')}) "
                        f"KO : {_ee}")
        tp_val = position.get("take_profit")
        tp_hit = False
        if tp_val is not None and not stop_hit:
            tp_hit = ((side == "long" and c_high >= tp_val)
                      or (side == "short" and c_low <= tp_val))
        elif tp_val is not None and stop_hit:
            would_tp = ((side == "long" and c_high >= tp_val)
                        or (side == "short" and c_low <= tp_val))
            if would_tp:
                ctx.diag["tp_sl_ambiguous_bars"] += 1
        if early_exit_reason and not stop_hit and not tp_hit:
            px = c_close * (1 - self.spread_pct) if side == "long" \
                else c_close * (1 + self.spread_pct)
            return RaisonDeSortie(early_exit_reason, px, c_close, False)
        if time_exit and not stop_hit and not tp_hit:
            px = c_close * (1 - self.spread_pct) if side == "long" \
                else c_close * (1 + self.spread_pct)
            return RaisonDeSortie("exit_after_bars", px, c_close, False)
        if tp_hit and tp_val is not None:
            exec_price, ref, gapped = self._fill_at_level(
                side, float(tp_val), c_open, stop=False)
            return RaisonDeSortie(
                "gap" if gapped else "take_profit", exec_price, ref, not gapped)
        if stop_hit:
            exec_price, ref, gapped = self._fill_at_level(
                side, stop, c_open, stop=True)
            _tr = position.get("_trailing")
            if _tr and hasattr(_tr, "_dts") and _tr._dts:
                position["trail_phase"] = _tr._dts.phase_name
            else:
                position["trail_phase"] = "unknown"
            reason = "gap" if gapped else (
                "stop_loss" if position.get("disable_trailing") else "trailing_stop")
            return RaisonDeSortie(reason, exec_price, ref, False)
        return None

    def _appliquer_jambes(self, ctx, position: dict, i: int) -> bool:
        """Exécute les cibles partielles. True si la position est soldée."""
        cibles = position.get("_targets")
        if not cibles:
            return False
        side, entry = position["side"], position["entry"]
        c_high, c_low, c_close = ctx.high_arr[i], ctx.low_arr[i], ctx.close_arr[i]
        c_open = ctx.open_arr[i] if getattr(ctx, "open_arr", None) is not None else c_close
        atteintes = [c for c in cibles
                     if (side == "long" and c_high >= c["price"])
                     or (side == "short" and c_low <= c["price"])]
        for cible in atteintes:
            cibles.remove(cible)
            px, ref, gapped = self._fill_at_level(
                side, cible["price"], c_open, stop=False)
            self._close_partial_at(ctx, position, i, px, cible["fraction"],
                                   "gap" if gapped else cible["reason"],
                                   maker=not gapped, ref_price=ref)
            if position.get("be_after_partial") and not position.get("_be_done"):
                from app.core.execution import venue_trade_cost
                _sz = float(position.get("size") or 0.0) or 1.0
                _fin = venue_trade_cost(entry, _sz, self.taker_fee, side=side,
                                        venue=getattr(self, "_venue", None), is_entry=True)
                _fout = venue_trade_cost(entry, _sz, self.taker_fee, side=side,
                                         venue=getattr(self, "_venue", None), is_entry=False)
                cout = (_fin + _fout) / max(entry * _sz, 1e-12) + self.spread_pct
                be = entry * (1 + cout) if side == "long" else entry * (1 - cout)
                if (side == "long" and be > position["stop"]) or \
                        (side == "short" and be < position["stop"]):
                    position["stop"] = round(be, 6)
                position["_be_done"] = True
        if atteintes:
            if position["size"] <= 0 or \
                    position["size"] * c_close < self._min_notional():
                self._close_at(ctx, position, i, c_close, "partial_final", maker=True)
                return True
        return False

    def _mettre_a_jour_trailing(self, ctx, position: dict, i: int) -> None:
        """Remonte le stop logiciel et libère le risque au registre."""
        if position.get("disable_trailing"):
            return
        _tr = position.get("_trailing")
        if not _tr:
            return
        side, entry, stop = position["side"], position["entry"], position["stop"]
        c_close = ctx.close_arr[i]
        _act_r = position.get("_trail_activate_r")
        if _act_r is not None:
            _risque = abs(entry - position.get("planned_stop", stop))
            _profit = (c_close - entry) if side == "long" else (entry - c_close)
            if _risque <= 0 or _profit < float(_act_r) * _risque:
                return
        atr_v = float(ctx.atr_arr[i]) or 1e-8
        lo20 = ctx.low_arr[max(0, i - 19):i + 1].tolist()
        hi20 = ctx.high_arr[max(0, i - 19):i + 1].tolist()
        new_stop = _tr.update_stop(
            current_price=c_close, current_stop=stop,
            atr=atr_v, side=side, entry=entry,
            bars_held=i - position["bar"],
            recent_lows=lo20, recent_highs=hi20,
        )
        position["stop"] = new_stop
        _ledger = getattr(ctx, "ledger", None)
        if _ledger is not None and (_pk := position.get("_pos_key")):
            _ledger.update_risk(_pk, abs(entry - new_stop) * position["size"])
        if hasattr(_tr, "_dts") and _tr._dts:
            position["trail_phase"] = _tr._dts.phase_name
        if i % 3 == 0:
            position["_stop_trail"].append({"bar": i, "stop": round(new_stop, 6)})

