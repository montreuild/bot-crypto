"""Cycle de vie d'une position de backtest — mixin de ``Backtester``.

Même calcul de risque que le live : ``RiskLedger.reserve`` dans ``_try_enter``
et le scale-in.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from app.core.bot_identity import build_pos_key as _bpk_enter
from app.core.bot_identity import build_slot_key
from app.core.execution import apply_exit_mode as _apply_exit_mode
from app.core.execution import close_pnl as _close_pnl
from app.core.execution import plan_partial_targets as _plan_partial_targets
from app.core.execution import quantize_size as _quantize_size
from app.core.risk.curve import risk_multiplier as _risk_multiplier
from app.core.risk.sizer import _floor_to
from app.core.timeframes import bar_to_days as _bar_to_days
from app.live.protocols import LifecycleHost

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RaisonDeSortie:
    """Résultat de ``_evaluer_sorties`` — clôture totale, pas une jambe."""
    reason: str
    exec_price: float
    ref_price: Optional[float]
    maker: bool


class PositionLifecycleMixin(LifecycleHost):
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
        hours_held = bars_held * _bar_to_days(ctx.timeframe) * 24.0
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
        bars_held  = i - position["bar"]
        hours_held = bars_held * _bar_to_days(ctx.timeframe) * 24.0
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

    def _manage_open_position(self, ctx, position: dict, i: int):
        """Orchestre sorties / jambes / trailing / pyramidage (ARCH-02)."""
        ctx.diag["bars_in_position"] += 1
        ctx.bars_current_position += 1
        sortie = self._evaluer_sorties(ctx, position, i)
        if sortie is not None:
            self._close_at(ctx, position, i, sortie.exec_price, sortie.reason,
                           maker=sortie.maker, ref_price=sortie.ref_price)
            return None
        if self._appliquer_jambes(ctx, position, i):
            return None
        self._mettre_a_jour_trailing(ctx, position, i)
        side = position["side"]
        c_close = ctx.close_arr[i]

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
                # B-06 : le pyramidage passe par le même gate que l'entrée.
                gate = getattr(ctx, "risk_gate", None)
                if gate is not None:
                    _sk = build_slot_key(position.get("strategy", ""),
                                         ctx.timeframe, ctx.symbol)
                    try:
                        _ts = ctx.df["time"][i]
                        _day = str(_ts)[:10] if _ts is not None else ""
                    except Exception:
                        _day = ""
                    _ok, _why = gate.can_slot_trade(
                        i, _sk, _day, ctx.capital,
                        getattr(ctx, "peak_capital", ctx.capital),
                    )
                    if not _ok:
                        logger.debug(f"[Backtest] bar {i} : scale-in refusé — {_why}")
                        scale = None
            if scale:
                add_price = c_close * (1 + self.spread_pct) if side == "long" \
                            else c_close * (1 - self.spread_pct)
                stop_dist = abs(add_price - position["stop"])
                if stop_dist > 0:
                    sf = max(0.0, min(float(scale.get("size_factor", 1.0)), 2.0))
                    _base = self._sizing_base(ctx)
                    peak = getattr(ctx, "peak_capital", ctx.capital) or ctx.capital
                    dd = max(0.0, (peak - ctx.capital) / peak) if peak > 0 else 0.0
                    add_size = (_base * ctx.risk / stop_dist * sf * self.partial_fill
                                * _risk_multiplier(dd))
                    _gate = getattr(ctx, "risk_gate", None)
                    if _gate is not None:
                        add_size *= _gate.volatility_brake_factor
                    add_notional = add_size * add_price
                    # Cap : le notional total reste sous l'enveloppe × levier
                    room = _base * max(self._leverage(), 1.0) - position["notional"]
                    if add_notional > room:
                        add_notional = max(room, 0.0)
                        add_size = add_notional / add_price
                    add_size = _quantize_size(add_size, self._venue)   # G2
                    add_notional = add_size * add_price
                    _ledger = getattr(ctx, "ledger", None)
                    _env = getattr(ctx, "ledger_env", None)
                    _pk = position.get("_pos_key")
                    if (_ledger is not None and _env is not None and _pk
                            and add_notional >= 1.0 and add_size > 0):
                        from dataclasses import replace
                        _env = replace(
                            _env,
                            slot_key=build_slot_key(
                                position.get("strategy", ""),
                                ctx.timeframe, ctx.symbol,
                            ),
                        )
                        _inc_key = f"{_pk}:scale:{position.get('scale_ins', 0)}"
                        _inc_risk = abs(add_price - float(position["stop"])) * add_size
                        _dec = _ledger.reserve(
                            _env, risk=_inc_risk, notional=add_notional,
                            pos_key=_inc_key)
                        if not _dec.allowed:
                            logger.debug(
                                f"[Backtest] bar {i} : scale-in refusé par "
                                f"RiskLedger ({_dec.reason_code})"
                            )
                            add_size = 0.0
                            add_notional = 0.0
                        else:
                            _new_n = float(position["notional"]) + add_notional
                            _new_r = float(position.get("_reserved_risk") or 0.0) + _inc_risk
                            _ledger.resize(_pk, risk=_new_r, notional=_new_n)
                            _ledger.release(_inc_key)
                            position["_reserved_risk"] = _new_r
                    if add_notional >= 1.0 and add_size > 0:
                        add_fees = self._fees(add_price, add_size, maker=False,
                                              side=position["side"], is_entry=True)
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
                        # FIN-02 : `add_fees` est un frais d'ENTRÉE, débité de
                        # ctx.capital ici. Sans cette ligne, `_close_at` ne le
                        # retranche pas du PnL journalisé et la somme des PnL
                        # de trades diverge de la courbe d'équité.
                        position["entry_fees"] = round(
                            position.get("entry_fees", 0.0) + add_fees, 6)
                        position["scale_ins"] = position.get("scale_ins", 0) + 1
                        ctx.diag["scale_ins"] = ctx.diag.get("scale_ins", 0) + 1

        return position

    def _try_enter(self, ctx, signal: dict, i: int):
        """Ouvre une position ou None. Slot key = ``name`` (pas ``strategy``, souvent vide)."""
        _mode = str(signal.get("exit_mode") or self.exit_mode)
        if _mode != "as_declared":
            signal = _apply_exit_mode(signal, _mode, self.exit_mode_params)
        df   = ctx.df
        diag = ctx.diag

        gate = getattr(ctx, "risk_gate", None)
        if gate is not None:
            strat_name = signal.get("name") or signal.get("strategy") or ""
            slot_key = build_slot_key(strat_name, ctx.timeframe, ctx.symbol)
            # day_key depuis le timestamp de la bougie
            try:
                ts = df["time"][i]
                day_key = str(ts)[:10] if ts is not None else ""
            except Exception:
                day_key = ""
            ok, reason = gate.can_slot_trade(
                i, slot_key, day_key, ctx.capital,
                getattr(ctx, "peak_capital", ctx.capital),
            )
            if not ok:
                diag["rejected_circuit_breaker"] = diag.get("rejected_circuit_breaker", 0) + 1
                self.rejections.record("circuit_breaker", symbol=ctx.symbol)
                logger.debug(f"[Backtest] bar {i} : trade rejeté (circuit_breaker) — {reason}")
                return None

        # Un événement de liquidité = un trade (cooldown événementiel, pas temporel).
        event_id = signal.get("market_event_id")
        if event_id and bool(self.cfg.get("trading", {}).get("dedup_events", True)):
            vus = getattr(ctx, "events_traded", None)
            if vus is None:
                vus = ctx.events_traded = set()
            if event_id in vus:
                diag["rejected_dedup"] = diag.get("rejected_dedup", 0) + 1
                self.rejections.record("evenement_deja_trade", symbol=ctx.symbol)
                return None

        atr_v = float(ctx.atr_arr[i])
        if atr_v <= 0:
            diag["rejected_atr_zero"] += 1
            logger.debug(f"[Backtest] bar {i} : trade rejeté (ATR<=0)")
            return None

        if signal["side"] == "short" and self._venue is not None \
                and not self._venue.allow_short:
            diag["rejected_venue"] = diag.get("rejected_venue", 0) + 1
            return None

        raw_price  = float(df["open"][i + 1])           # L0 — avant spread
        exec_price = raw_price
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
        if stop_dist <= 0:
            diag["rejected_stop"] = diag.get("rejected_stop", 0) + 1
            diag["rejected_notional"] += 1
            self.rejections.record("stop_invalide", symbol=ctx.symbol)
            return None
        peak = getattr(ctx, "peak_capital", ctx.capital) or ctx.capital
        dd   = max(0.0, (peak - ctx.capital) / peak) if peak > 0 else 0.0
        size_factor  = max(0.0, min(float(signal.get("size_factor", 1.0)), 2.0))
        base         = self._sizing_base(ctx)
        risk_amount  = base * ctx.risk * size_factor * _risk_multiplier(dd)
        _gate = getattr(ctx, "risk_gate", None)
        if _gate is not None:
            risk_amount *= _gate.volatility_brake_factor
        # Plafond notionnel exprimé sur la base du bot, pas sur un pourcentage
        # global d'un capital qui n'existe plus.
        max_notional = base * max(self._leverage(), 1.0)
        size = _floor_to(risk_amount / stop_dist, 6)
        if size * exec_price > max_notional:
            size = _floor_to(max_notional / exec_price, 6)
        notional = _floor_to(size * exec_price, 4)

        if size <= 0:
            diag["rejected_size"] = diag.get("rejected_size", 0) + 1
            diag["rejected_notional"] += 1
            self.rejections.record("notionnel_min", symbol=ctx.symbol)
            return None

        size       *= self.partial_fill
        q_size = _quantize_size(size, self._venue)
        if q_size <= 0:
            diag["rejected_venue"] = diag.get("rejected_venue", 0) + 1
            diag["rejected_notional"] += 1
            self.rejections.record("venue", symbol=ctx.symbol)
            logger.debug(
                f"[Backtest] bar {i} : trade rejeté (taille {size:.6f} < 1 unité "
                f"négociable sur la venue)"
            )
            return None
        size        = q_size
        notional    = size * exec_price
        pos_key = _bpk_enter(
            ctx.symbol,
            signal.get("name") or signal.get("strategy") or "",
            ctx.timeframe,
        )
        ledger = getattr(ctx, "ledger", None)
        env = getattr(ctx, "ledger_env", None)
        if ledger is not None and env is not None:
            from dataclasses import replace
            env = replace(
                env,
                slot_key=build_slot_key(
                    signal.get("name") or signal.get("strategy") or "",
                    ctx.timeframe, ctx.symbol,
                ),
            )
            risk_amt = abs(float(exec_price) - float(stop)) * float(size)
            dec = ledger.reserve(env, risk=risk_amt, notional=notional, pos_key=pos_key)
            if not dec.allowed:
                code = dec.reason_code or "notionnel_min"
                if code == "notionnel_min":
                    diag["rejected_min_notional"] = diag.get("rejected_min_notional", 0) + 1
                elif code == "enveloppe_venue":
                    diag["rejected_venue"] = diag.get("rejected_venue", 0) + 1
                diag["rejected_notional"] = diag.get("rejected_notional", 0) + 1
                self.rejections.record(code, symbol=ctx.symbol)
                logger.debug(
                    f"[Backtest] bar {i} : trade rejeté par RiskLedger "
                    f"({code} — {dec.detail})"
                )
                return None
        else:
            min_notional = self._min_notional()
            if notional < min_notional:
                diag["rejected_min_notional"] = diag.get("rejected_min_notional", 0) + 1
                diag["rejected_notional"] += 1
                self.rejections.record("notionnel_min", symbol=ctx.symbol)
                return None
        entry_fees  = self._fees(exec_price, size, maker=False,
                                 side=signal["side"], is_entry=True)
        _impact_in  = self._impact_cost(ctx, i, notional)   # BT-10
        entry_fees += _impact_in
        ctx.capital -= entry_fees
        slip_entry  = abs(exec_price - raw_price) * size + _impact_in   # L0
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
            # §65 — module SMC/ICT du setup, pour des statistiques séparées.
            "module":          signal.get("module"),
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
            "entry_fees":      round(entry_fees, 6),
            "slippage_cost":   round(slip_entry, 6),
            "funding_cost":    0.0,
            "session":         signal.get("session"),
            "htf_bias":        signal.get("htf_bias"),
            "structure_state": signal.get("structure_state"),
            "sequence_type":   signal.get("sequence_type"),
            "sequence_id":     signal.get("sequence_id"),
            "market_event_id": signal.get("market_event_id"),
            "tier":            signal.get("tier"),
            "liquidity_swept": signal.get("liquidity_swept"),
            "pd_zone":         (signal.get("indicators") or {}).get("pd_zone"),
            "gross_rr":        signal.get("gross_rr"),
            "net_rr":          signal.get("net_rr"),
            "score_breakdown": signal.get("score_breakdown"),
            "planned_stop":    round(stop, 6),
            "planned_tp":      round(tp_init, 6) if tp_init is not None else None,
            "size_initial":    round(size, 6),
            "_pos_key":        pos_key,
            "_reserved_risk":  abs(float(exec_price) - float(stop)) * float(size),
            "_targets":        _plan_partial_targets(signal, exec_price, stop),
            "be_after_partial": bool(signal.get("be_after_partial", True)),
            "_trail_activate_r": signal.get("_trail_activate_r"),
        }
        diag["trades_opened"] += 1
        diag["last_trade_bar"] = i
        ctx.bars_current_position = 0
        vus = getattr(ctx, "events_traded", None)
        if event_id and vus is not None:
            vus.add(event_id)
        return position
