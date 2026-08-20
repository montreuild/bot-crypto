"""Backtester — stop vérifié intrabar, trailing dynamique.

``WalkForwardAnalyzer`` et ``MonteCarlo`` sont ré-exportés en fin de module
(``from app.engine.backtest import WalkForwardAnalyzer``).
"""
import logging
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.core.config import DEFAULT_MAKER_FEE, DEFAULT_TAKER_FEE
from app.core.execution import cost_model as _cost_model
from app.core.execution import format_cost_model as _format_cost_model
from app.core.execution import size_impact_cost as _size_impact_cost
from app.core.execution import venue_trade_cost as _venue_trade_cost
from app.core.log_throttle import log_throttled
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL, resolve_strategy_params
from app.core.rejections import RejectionCounter
from app.core.risk.envelope import trade_risk_pct as _trade_risk_pct
from app.core.risk.envelope import with_reference_envelope
from app.core.trade_economics import funding_cost as _funding_cost
from app.core.trailing import TrailingStopManager
from app.engine.backtest_result import BacktestResult
from app.engine.engine import Engine
from app.engine.position_lifecycle import PositionLifecycleMixin

logger = logging.getLogger(__name__)


def _iso_of(df: pl.DataFrame, idx: int) -> Optional[str]:
    """``time`` de la barre ``idx`` en ISO (borne ``as_of`` du registre ML)."""
    if df is None or len(df) == 0 or "time" not in df.columns:
        return None
    from app.ml.model_registry import to_iso
    return to_iso(df["time"][idx])


def _resolve_frozen_ml_model(strat, symbol: Optional[str], tf: Optional[str],
                             window_start: Optional[str], window_end: Optional[str]) -> dict:
    """Charge le modèle figé ``as_of=window_start``. Ne lève jamais :
    introuvable / illisible / chevauchement → ``fallback_to_inline``.
    """
    import app.ml.model_registry as ml_registry

    entry: Dict = {"resolved": False, "fallback_to_inline": True}
    if not tf:
        return entry
    base_dir = getattr(strat, "model_dir", "models") or "models"
    try:
        from app.ml.scoring import resolve_recipe_name
        recipe = resolve_recipe_name(strat)
        art = ml_registry.resolve(tf, recipe, as_of=window_start, base_dir=base_dir)
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
        # M-06 : un modèle qui a vu la fenêtre évaluée n'est pas utilisable.
        logger.warning(
            f"[Backtest] ml_mode=frozen : {strat.name}/{tf} — la fenêtre d'entraînement "
            f"de {art.version_id} ({art.train_start}..{art.train_end}) chevauche la "
            f"fenêtre backtestée ({window_start}..{window_end}) : modèle invalidé, "
            f"repli inline."
        )
        return {
            "resolved": False, "fallback_to_inline": True,
            "overlap_warning": True, "invalidated": True,
            "version_id": art.version_id,
            "train_start": art.train_start, "train_end": art.train_end,
        }
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
        "undated": not art.train_end, "overlap_warning": overlap,
    })
    return entry


def run_dual_pass(engine: Engine, cfg: dict, df, envelope, *,
                  symbol: str = DEFAULT_CONFIG_SYMBOL, timeframe: str = "1d",
                  engine_factory=None,
                  **run_kwargs) -> dict:
    """``live`` = enveloppe du slot (promouvable ?) ;
    ``reference`` = même enveloppe à échelle fixe (la stratégie vaut-elle ?).
    Un écart de PnL % vient des contraintes absolues (min notional, lot, frais).

    ``engine_factory`` : si fourni, une Engine **neuve** par passe. Sans ça,
    l'état runtime de la stratégie (cooldown, ``_call_count``) de la passe
    live empoisonne la passe d'étude — 0 trade alors que le réel en a.
    """
    reference_capital = float((cfg.get("backtest") or {}).get("reference_envelope", 1000.0))
    out = {}
    for pass_name, env in (("live", envelope),
                           ("reference", with_reference_envelope(envelope, reference_capital))):
        eng = engine_factory() if callable(engine_factory) else engine
        df_pass = _clone_frame(df)
        bt = Backtester(eng, cfg, envelope=env, **run_kwargs)
        out[pass_name] = bt.run(df_pass, symbol=symbol, timeframe=timeframe)
    return out


def _clone_frame(df):
    """Copie du frame par passe : prepare_for_backtest / colonnes _pre_* ne
    doivent pas muter le df partagé entre live et étude."""
    if hasattr(df, "clone"):
        return df.clone()
    if hasattr(df, "copy"):
        return df.copy()
    return df


_ML_MODES = ("frozen", "inline", "simulated_live")


class Backtester(PositionLifecycleMixin):
    """Trailing stop multi-phases.

    ``ml_mode`` : ``frozen`` (défaut, ``as_of`` début de fenêtre), ``inline``
    (réentraînement WF), ``simulated_live`` (``maybe_refresh`` aux cadences).
    """
    def __init__(self, engine: Engine, cfg: dict,
                 cancel_event: Optional[threading.Event] = None,
                 ml_mode: str = "frozen",
                 envelope=None,
                 realistic_risk: bool = False):
        self.engine             = engine
        self.cfg                = cfg
        self._cancel_event      = cancel_event
        # Sans enveloppe (études libres) : capital de la venue par défaut.
        self.envelope           = envelope
        self.rejections         = RejectionCounter()
        if ml_mode not in _ML_MODES:
            raise ValueError(f"ml_mode invalide : {ml_mode!r} (attendu parmi {_ML_MODES})")
        self.ml_mode            = ml_mode
        # Circuit breakers opt-in — off pour préserver la parité des backtests existants.
        self.realistic_risk     = bool(realistic_risk)
        self._risk_gate: Any    = None
        bcfg = cfg.get("backtest", {})
        tcfg = cfg.get("trading",  {})

        self.atr_stop_mult = float(bcfg.get("atr_stop_mult", 2.5))
        self.atr_tp_mult   = None

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
        self.slippage_model = str(bcfg.get("slippage_model", "static"))
        self.slippage_k     = float(bcfg.get("slippage_k", 1.0))
        self.partial_fill = bcfg.get("partial_fill_pct",  0.95)
        # as_declared : la stratégie garde la main — aucun backtest existant ne change.
        self.exit_mode = str(bcfg.get("exit_mode", "as_declared"))
        self.exit_mode_params = dict(bcfg.get("exit_mode_params") or {})
        self._venue: Any = None
        self._cost_model: dict | None = None

    def _sizing_base(self, ctx) -> float:
        """Enveloppe fixe si fournie — pas ``ctx.capital`` (sinon DD pénalisé deux fois)."""
        return self.envelope.slot_envelope if self.envelope is not None else ctx.capital

    def _leverage(self) -> float:
        if self.envelope is not None:
            return max(self.envelope.max_leverage, 1.0)
        return max(float(getattr(self._venue, "max_leverage", 1.0) or 1.0), 1.0)

    def _min_notional(self) -> float:
        if self.envelope is not None and self.envelope.min_notional > 0:
            return self.envelope.min_notional
        return float(getattr(self._venue, "min_notional", 0.0) or 0.0)

    def _ledger_envelope(self, ctx):
        """Envelope RiskLedger. Sans slot : budgets symbole/venue = base×1e6
        (ne lient pas — B-02 multi-stratégies). ``slot_key`` est un repli ;
        ``_try_enter`` le rebind par stratégie avant ``reserve``.
        """
        if self.envelope is not None:
            return self.envelope
        from app.core.risk.envelope import Envelope
        base = float(self._sizing_base(ctx) or 0.0) or float(ctx.capital or 0.0)
        venue = self._venue
        wide = max(base, 1.0) * 1e6
        return Envelope(
            venue=getattr(venue, "name", None) or "default",
            symbol=getattr(ctx, "symbol", "") or "",
            slot_key="backtest",
            currency=getattr(venue, "quote_currency", None) or "USDC",
            venue_envelope=wide,
            venue_risk_budget=wide,
            symbol_envelope=wide,
            symbol_risk_budget=wide,
            slot_envelope=base,
            slot_risk_amount=base,
            max_leverage=self._leverage(),
            min_notional=self._min_notional(),
            trade_risk_pct=1.0,
            weight=1.0,
        )

    def _find_strategy(self, name: str):
        if not name:
            return None
        cache = getattr(self, "_strat_by_name", None)
        if cache is None:
            cache = {
                getattr(s, "name", None): s
                for s in self.engine.strategies
                if getattr(s, "name", None)
            }
            self._strat_by_name = cache
        return cache.get(name)

    def _make_trailing(self, override: dict | None = None):
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
            struct_buffer_atr = float(ov.get("struct_buffer_atr", 0.25)),
            struct_pivot_k    = int(ov.get("struct_pivot_k", 2)),
        )

    def run(self, df: pl.DataFrame, symbol: str = DEFAULT_CONFIG_SYMBOL,
            timeframe: str | None = None) -> "BacktestResult":
        import app.ml.policy as _ml_policy
        from app.engine.engine import BaseStrategyML
        # ``_bt_params`` avant prepare_for_backtest : les hooks lisent le paramétrage résolu.
        strat_params = resolve_strategy_params(self.cfg, timeframe, symbol)
        # Un même Backtester sert IS puis OOS : reset sinon les rejets IS polluent l'OOS.
        self.rejections = RejectionCounter()

        from app.core.bot_identity import resolve_venue as _resolve_venue
        self._venue = _resolve_venue(self.cfg, tf=timeframe, symbol=symbol)

        self._cost_model = _cost_model(self.cfg, self._venue)
        _key = f"cost_model:{symbol}:{timeframe}:{sorted(self._cost_model.items())}"
        log_throttled(
            logger, _key,
            _format_cost_model(self._cost_model, symbol or "", timeframe or ""),
            level=logging.INFO, ttl=3600.0,
        )

        # Relu ici : un appelant peut poser ``bt.ml_mode = "inline"`` entre deux ``run()``.
        ml_mode = self.ml_mode
        symbol_key      = symbol or DEFAULT_CONFIG_SYMBOL
        window_start_iso = _iso_of(df, 0)
        window_end_iso   = _iso_of(df, -1)
        ml_info: Dict[str, Any] = {"mode": ml_mode, "symbol": symbol_key,
                                   "timeframe": timeframe, "models": {}}
        sim_live_entries: List[Dict] = []
        # ML-03 : compter les fit() inline et la longueur de fenêtre reçue.
        from app.ml.fit_trace import start as _fit_start
        _fit_start()

        for strat in self.engine.strategies:
            strat._bt_params = strat_params
            if isinstance(strat, BaseStrategyML):
                strat.reset_model()
                strat._cancel_event = self._cancel_event
                sp = strat_params.get(strat.name, {})
                cadence_bars = int(sp.get("retrain_every") or 0)

                if ml_mode == "inline":
                    pass
                elif ml_mode == "simulated_live" and cadence_bars > 0 and timeframe:
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
                    entry = _resolve_frozen_ml_model(strat, symbol_key, timeframe,
                                                     window_start_iso, window_end_iso)
                    entry["requested_mode"] = ml_mode
                    ml_info["models"][strat.name] = entry
            strat._bt_symbol = symbol
            strat._bt_tf = timeframe or self.cfg["trading"].get("timeframe", "1h")
            # Dual-pass / WF : un cooldown ou un call_count d'une passe
            # précédente ne doit pas bloquer la suivante.
            for attr in ("_call_count", "_last_signal"):
                val = getattr(strat, attr, None)
                if isinstance(val, dict):
                    val.clear()
            prep = getattr(strat, "prepare_for_backtest", None)
            if callable(prep):
                try:
                    prep(df)
                except Exception as e:
                    logger.warning(
                        f"[Backtest] prepare_for_backtest('{strat.name}') KO : {e}"
                    )

        capital      = self.initial_capital(self.cfg)
        risk         = _trade_risk_pct(self.cfg)
        threshold    = self.cfg["trading"].get("score_threshold", 0.60)

        trades: list[dict] = []
        equity_curve = [capital]
        equity_mtm   = [capital]
        timestamps   = [str(df["time"][0]) if "time" in df.columns else "0"]
        positions: Dict[str, dict] = {}
        trade_id     = 0

        diag: dict[str, Any] = {
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
            "partial_exits":         0,   # L1 — jambes sorties avant la clôture
        }
        per_strategy_stats: Dict[str, Dict[str, int]] = {}
        _bars_since_signal     = 0
        _bars_current_position = 0
        _prev_in_position      = False

        # Warmup dynamique : prend le max parmi les stratégies actives.
        # Chaque stratégie peut déclarer `warmup_bars` (attribut de classe ou d'instance).
        # Valeur minimale garantie : WARMUP_BARS_DEFAULT (EMA200 + ADX + ATR14).
        from app.core.is_oos import WARMUP_BARS_DEFAULT as _MIN_WARMUP
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
            close_arr=close_arr, open_arr=open_arr,
            bars_current_position=_bars_current_position,
            # QW-6 (étape 6) — risk gate pour le mode realistic_risk.
            # Initialisé plus bas (après know si realistic_risk=True).
            risk_gate=None,
        )
        # R-02 : un seul RiskLedger pour le run — plus de plafonds recopiés.
        from app.core.risk.ledger import RiskLedger
        ctx.ledger = RiskLedger()
        ctx.ledger_env = self._ledger_envelope(ctx)
        # L2 (§27) — série de funding pour les venues perp. Absente = pas de
        # facturation (et non une estimation inventée) : mieux vaut un coût
        # manquant et signalé qu'un coût faux et silencieux.
        ctx.funding_arr = None
        if getattr(self._venue, "market_type", "spot") == "perp" \
                and "funding_rate" in df.columns:
            ctx.funding_arr = df["funding_rate"].fill_null(0.0) \
                .to_numpy().astype(float)

        # BT-10 : volume quote moyen (20 barres, causal) pour le modèle "size".
        if self.slippage_model == "size" and "volume" in df.columns:
            ctx.qvol_arr = (df["volume"] * df["close"]).rolling_mean(20) \
                .fill_null(0.0).to_numpy().astype(float)
        else:
            ctx.qvol_arr = None

        # QW-6 (étape 6) — initialiser le risk gate si realistic_risk=True
        if self.realistic_risk:
            from app.engine.backtest_risk_gate import BacktestRiskGate
            # `timeframe` est requis : il convertit la durée de pause du live
            # (en secondes) en nombre de bougies.
            self._risk_gate = BacktestRiskGate.from_config(
                self.cfg, timeframe=timeframe or "1h")
            ctx.risk_gate = self._risk_gate
            logger.info(
                f"[Backtest] Mode realistic_risk ACTIF — circuit breakers "
                f"(consec_loss={self._risk_gate.consec_loss_limit} → pause "
                f"{self._risk_gate.pause_bars} bougies, "
                f"slot_daily_dd={self._risk_gate.slot_daily_dd_limit:.1%}, "
                f"max_trades/day={self._risk_gate.max_trades_per_day}, "
                f"daily_dd={self._risk_gate.daily_dd_limit:.1%}, "
                f"global_dd={self._risk_gate.global_dd_limit:.1%}, "
                f"vol_brake={self._risk_gate.volatility_threshold:.1%})"
            )

        def _mark_mtm(bar_i: int) -> None:
            # F-06 : équité mark-to-market à chaque barre — le drawdown
            # ne voit plus seulement les clôtures. B-02 : somme de toutes
            # les positions ouvertes.
            u = 0.0
            _c = float(close_arr[bar_i])
            for _pos in positions.values():
                if int(_pos.get("bar") or 0) > bar_i:
                    continue
                _e = float(_pos.get("entry") or 0.0)
                _sz = float(_pos.get("size") or 0.0)
                u += ((_e - _c) if _pos.get("side") == "short" else (_c - _e)) * _sz
            equity_mtm.append(round(ctx.capital + u, 4))

        for i in range(warmup, len(df) - 1):
            diag["bars_total"] += 1
            # QW-6 — alimenter le volatility brake AVANT toute décision de la
            # bougie : sans cet appel, `volatility_brake_factor` restait à 1.0
            # pour toujours et le breaker n'existait que dans la documentation.
            if self._risk_gate is not None:
                _px = float(close_arr[i])
                if _px > 0:
                    self._risk_gate.update_volatility(float(atr_arr[i]) / _px)
            _had_position_at_start = bool(positions)
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
            # P-05 : slice zéro-copie (pas une reconstruction Python df[:i+1]).
            ctx.window = df.slice(0, i + 1)
            ctx.bar_index = i

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
                        params=sle["params"],   # recipe dérivé de la liaison
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

            # ── Gestion des positions ouvertes (B-02 : plusieurs à la fois) ──
            if positions:
                _kept: Dict[str, dict] = {}
                for _pk, _pos in list(positions.items()):
                    _managed = self._manage_open_position(ctx, _pos, i)
                    if _managed is not None:
                        _kept[_pk] = _managed
                positions = _kept

            # ── Cherche des signaux même si d'autres slots sont ouverts (B-02)
            diag["bars_seeking_signal"] += 1
            diag["signal_calls"] += 1
            _cands = self.engine.passing_signals(
                ctx.window, strat_params, threshold=threshold,
                stats=per_strategy_stats,
            )
            if not _cands:
                _bars_since_signal += 1
                if _bars_since_signal > diag["max_bars_no_signal"]:
                    diag["max_bars_no_signal"] = _bars_since_signal
                _mark_mtm(i)
                continue

            from app.core.bot_identity import build_pos_key as _bpk
            _opened_any = False
            for signal in _cands:
                _new_key = _bpk(
                    ctx.symbol,
                    signal.get("name") or signal.get("strategy") or "",
                    ctx.timeframe,
                )
                if _new_key in positions:
                    continue
                diag["signal_accepted"] += 1
                diag["last_signal_bar"] = i
                _bars_since_signal = 0
                _opened_any = True
                logger.debug(
                    f"[Backtest] bar {i} : signal accepté — {signal.get('name')} "
                    f"{signal.get('side')} score={signal.get('score', 0):.3f}"
                )
                _entered = self._try_enter(ctx, signal, i)
                if _entered is not None:
                    positions[_new_key] = _entered
            if not _opened_any:
                _bars_since_signal += 1
            _mark_mtm(i)

        capital                = ctx.capital
        trade_id               = ctx.trade_id
        _bars_current_position = ctx.bars_current_position

        # ── Clôture forcée en fin de série ────────────────────────────────────
        if positions:
            # B-10 : une liquidation forcée est taker, avec spread — pas un
            # maker gratuit. ref_price isole le coût de spread (L0).
            _eod = float(df["close"][-1])
            for _pos in list(positions.values()):
                _side = _pos["side"]
                _eod_exec = _eod * (1 - self.spread_pct) if _side == "long" \
                    else _eod * (1 + self.spread_pct)
                self._close_at(ctx, _pos, len(df) - 1, _eod_exec,
                               "end_of_data", maker=False, status="closed_eod",
                               append_ts=False, ref_price=_eod)
            positions.clear()
            capital  = ctx.capital
            equity_mtm.append(round(capital, 4))

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
        result = BacktestResult(trades, equity_curve, self.initial_capital(self.cfg),
                                timestamps, timeframe=_tf,
                                rejections=self.rejections.as_dict(),
                                # Durée réelle du run : les bougies parcourues,
                                # hors warmup. Indispensable à toute
                                # annualisation (cf. BacktestResult._years).
                                n_bars=max(0, len(df) - warmup),
                                equity_mtm=equity_mtm)
        result.diagnostics = diag
        from app.ml.fit_trace import stop as _fit_stop
        ml_info["fit_trace"] = _fit_stop(series_len=len(df))
        result.ml_info = ml_info
        # §5 — le mode de sortie appliqué fait partie du contexte qui produit le
        # PnL, au même titre que la venue et le levier : deux runs qui ne
        # sortent pas de la même façon ne sont pas comparables de bonne foi.
        result.exit_mode = self.exit_mode
        # S11 : le résultat porte le contexte qui l'a produit — sans quoi un
        # PnL n'est pas interprétable (spot ou margin ? quel levier ? quels
        # frais ?), et deux runs ne sont pas comparables de bonne foi.
        result.cost_model = self._cost_model
        # QW-6 (étape 6) — diagnostics du risk gate si realistic_risk=True
        if self._risk_gate is not None:
            result.realistic_risk_diagnostics = self._risk_gate.to_diagnostics()
            result.realistic_risk = True
        else:
            result.realistic_risk = False
        return self._add_buy_and_hold(result, df, warmup)

    def _add_buy_and_hold(self, result: "BacktestResult", df: pl.DataFrame,
                          warmup: int = 210) -> "BacktestResult":
        """Calcule le benchmark Buy & Hold sur la MÊME fenêtre que le backtest.

        FIN-04 : ``warmup`` doit être le warmup dynamique réellement utilisé par
        la boucle de trading (``run()``, potentiellement > 210 si une stratégie
        déclare un ``warmup_bars``/``min_bars`` plus grand) — un warmup figé à
        210 désynchronisait le prix de départ du Buy & Hold de la première
        barre réellement tradée, faussant l'alpha calculé.

        QW-1 : stocke aussi la série des prix close (post-warmup) sur le
        résultat pour que ``_compute_metrics`` puisse calculer l'alpha vs
        Buy & Hold annualisé via ``compute_extended_metrics``.
        """
        try:
            if len(df) <= warmup:
                return result
            # B-13 : même première barre que le bot (entrée à l'open warmup+1).
            # Repli sur close[warmup] si la colonne open manque (tests unitaires).
            if "open" in df.columns and warmup + 1 < len(df):
                first_price = float(df["open"][warmup + 1])
            else:
                first_price = float(df["close"][warmup])
            last_price  = float(df["close"][-1])
            if first_price <= 0:
                return result
            bnh_pct = (last_price - first_price) / first_price * 100
            bnh_pnl = result.initial_capital * bnh_pct / 100
            result.buy_and_hold_pnl = round(bnh_pnl, 4)
            result.buy_and_hold_pct = round(bnh_pct, 3)
            result.alpha            = round(result.total_pnl - bnh_pnl, 4)
            # QW-1 : série des close post-warmup pour compute_extended_metrics.
            # `_compute_metrics()` a déjà tourné (dans `__init__`) sans ces prix :
            # on recalcule les métriques étendues maintenant, sinon `alpha_vs_bh`
            # resterait à 0 pour tous les backtests.
            try:
                result._close_prices = [float(x) for x in df["close"][warmup + 1:].to_list()]
                result._compute_extended_metrics()
            except Exception:
                result._close_prices = []
        except Exception as e:
            logger.debug(f"[BnH] Calcul benchmark KO : {e}")
        return result

    def initial_capital(self, cfg: dict) -> float:
        """Base économique du backtest.

        S12 : l'enveloppe du slot quand elle est fournie — c'est elle qui rend
        le PnL comparable au live. Sinon le capital de la venue par défaut
        (études libres, walk-forward historique)."""
        if self.envelope is not None:
            return self.envelope.slot_envelope
        from app.core.risk.gate import _default_venue_capital
        return _default_venue_capital(cfg) or 1000.0

    def _funding_cost(self, ctx, position: dict, i: int,
                      hours_held: float) -> float:
        """L2 (§27) — funding d'un perpétuel sur la durée de détention.

        0.0 hors venue perp, ou quand la série de funding n'est pas disponible
        (``ctx.funding_arr``, alimentée par ``derivatives.align_to_ohlcv``).
        Un long paie quand le funding est positif, un short encaisse — d'où le
        signe porté par le sens de la position."""
        arr = getattr(ctx, "funding_arr", None)
        if arr is None or self._venue is None:
            return 0.0
        if getattr(self._venue, "market_type", "spot") != "perp":
            return 0.0
        debut = int(position.get("bar", i))
        if not (0 <= debut <= i < len(arr)):
            return 0.0
        taux_moyen = float(np.nanmean(arr[debut:i + 1]))
        if taux_moyen != taux_moyen:      # NaN
            return 0.0
        cout = _funding_cost(position["notional"], taux_moyen, hours_held)
        return cout if position["side"] == "long" else -cout

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

    def _fees(self, price: float, size: float, maker: bool = False,
              side: str = "long", is_entry: bool = True) -> float:
        """Coût d'un fill. Passe par le modèle de la venue quand il y en a une
        (G2 : commission fixe, plancher, TTF) — sinon frais proportionnels,
        strictement comme avant."""
        rate = self.maker_fee if maker else self.taker_fee
        return _venue_trade_cost(price, size, rate, side=side,
                                 venue=self._venue, is_entry=is_entry)


# Imports en fin de module : walk_forward importe Backtester en lazy dans run().
from app.engine.monte_carlo import MonteCarlo  # noqa: E402,F401
from app.engine.walk_forward import WalkForwardAnalyzer  # noqa: E402,F401
