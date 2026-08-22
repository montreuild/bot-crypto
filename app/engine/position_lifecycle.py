"""Cycle de vie d'une position de backtest — mixin de ``Backtester``.

Même calcul de risque que le live : ``RiskLedger.reserve`` dans ``_try_enter``
et le scale-in.
"""
import logging

from app.core.bot_identity import build_pos_key as _bpk_enter
from app.core.bot_identity import build_slot_key
from app.core.execution import apply_exit_mode as _apply_exit_mode
from app.core.execution import plan_partial_targets as _plan_partial_targets
from app.core.execution import quantize_size as _quantize_size
from app.core.risk.curve import risk_multiplier as _risk_multiplier
from app.core.risk.sizer import _floor_to
from app.engine.position_close import PositionCloseMixin

# Ré-exports : `_heures_detenues` et `RaisonDeSortie` sont importés d'ici.
from app.engine.position_exit_reason import RaisonDeSortie  # noqa: F401
from app.engine.position_exits import PositionExitsMixin
from app.engine.position_hours import _heures_detenues  # noqa: F401
from app.live.protocols import LifecycleHost

logger = logging.getLogger(__name__)



class PositionLifecycleMixin(PositionExitsMixin, PositionCloseMixin,
                             LifecycleHost):
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
