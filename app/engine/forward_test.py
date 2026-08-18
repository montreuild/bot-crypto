"""Forward-test glissant d'un slot (exécution) — Phase 0, observationnel.

Ce module orchestre le **re-backtest** des params figés d'un slot sur données
fraîches (réutilisation du ``Backtester``), branche le ``MonteCarlo`` existant
et assemble l'enregistrement persisté dans ``data/oos_tracker.json``.

Séparation des responsabilités (V4-E / ARCH-09) :

- **app/engine/forward_test.py** (ici) : tout ce qui a besoin du moteur
  (``Engine``, ``Backtester``, ``MonteCarlo``) — exécution des re-backtests,
  boucle sur les slots actifs, écriture des enregistrements.
- **app/core/oos_tracker.py** : persistance (``load_oos_tracker``) et
  analytique pure (contrats Monte-Carlo, cône d'edge, verdict de conformité) —
  aucune dépendance vers app/engine.

Toutes les grandeurs comparées restent **budget-indépendantes** (rendement %
par trade), cohérent avec le score budget-indépendant (``opt_scoring.py``).
"""
import importlib
import logging
import math
from datetime import datetime, timezone

import numpy as np

from app.core.bot_identity import build_slot_key
from app.core.is_oos import WARMUP_BARS_DEFAULT as _WARMUP_BARS
from app.core.oos_tracker import (
    _closed_trades,
    _edge_contract,
    _mc_contract,
    _per_trade_returns_pct,
    _verdict,
    save_records,
)
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL
from app.core.risk_envelope import envelope_base as _envelope_base
from app.core.risk_envelope import envelopes_for_active_slots
from app.core.timeframes import TF_MINUTES as _TF_MINUTES

logger = logging.getLogger(__name__)

# Bougies de chauffe : même constante que le split IS/OOS et le backtest.
# Garde-fou : on ne demande jamais plus que ça de bougies pour un forward-test.
_MAX_BARS = 4000
# Plafond plus large pour le backtest d'edge (fenêtre longue, ex. 365 j :
# ~9000 bougies en 1h). Évite de tronquer la fenêtre d'edge.
_MAX_EDGE_BARS = 12000


def _bars_for_lookback(tf: str, lookback_days: int, max_bars: int = _MAX_BARS,
                        warn_truncation: bool = False,
                        slot_label: str = "") -> int:
    """Calcule le nombre de bougies pour `lookback_days` sur le TF `tf`.

    S3-08 : quand la demande dépasse `max_bars`, la fenêtre est tronquée
    silencieusement (l'edge est alors calculé sur moins de jours que demandé).
    Si `warn_truncation=True`, on émet un WARNING explicite — l'appelant sait
    qu'il doit soit baisser `edge_lookback_days`, soit monter `_MAX_EDGE_BARS`
    (configurable), soit accepter que l'edge soit calculé sur une fenêtre
    plus courte.
    """
    minutes = _TF_MINUTES.get(tf, 60)
    bars = int(math.ceil(lookback_days * 1440 / minutes)) + _WARMUP_BARS
    actual = max(_WARMUP_BARS + 30, min(bars, max_bars))
    if warn_truncation and bars > max_bars:
        actual_days = max(0, (actual - _WARMUP_BARS) * minutes / 1440)
        logger.warning(
            f"[ForwardTest] {slot_label or 'slot'} : edge_lookback_days={lookback_days} "
            f"exige {bars} bougies en {tf}, plafonné à {max_bars} "
            f"(≈{actual_days:.0f} jours réels). L'edge est calculé sur "
            f"une fenêtre PLUS COURTE que demandé. Solutions : (a) baisser "
            f"edge_lookback_days dans config.yaml → lifecycle, (b) monter "
            f"_MAX_EDGE_BARS dans app/engine/forward_test.py, (c) accepter."
        )
    return actual


# ── Forward-test d'un slot ─────────────────────────────────────────────────
def _forward_test_slot(strategy: str, timeframe: str, symbol: str,
                       cfg: dict, fetch_ohlcv, session_factory,
                       lookback_days: int, edge_lookback_days: int = 100,
                       envelope=None) -> dict | None:
    """Re-backteste un slot sur données fraîches, construit le contrat MC et
    compare aux trades réels. Retourne l'enregistrement (ou None si données
    insuffisantes)."""
    from app.engine.backtest import Backtester, MonteCarlo
    from app.engine.engine import Engine

    bars = _bars_for_lookback(timeframe, lookback_days)
    df = fetch_ohlcv(symbol, timeframe, limit=bars)
    if df is None or len(df) < _WARMUP_BARS + 30:
        logger.info(
            f"[ForwardTest] {strategy}@{timeframe} : données insuffisantes "
            f"({0 if df is None else len(df)} bougies) — slot ignoré."
        )
        return None

    mod = importlib.import_module(f"app.strategies.{strategy}")
    eng = Engine()
    eng.register(mod.Strategy(), silent=True)
    bt = Backtester(eng, cfg, envelope=envelope, realistic_risk=True)
    res = bt.run(df, symbol, timeframe=timeframe)
    d = res.to_dict()

    sim_closed = _closed_trades(d.get("trades", []))
    sim_returns = _per_trade_returns_pct(sim_closed)
    capital = float(cfg.get("trading", {}).get("capital", d.get("initial_capital", 1000.0)))

    # ── Cône Monte-Carlo « historique » (absolu → rendement %) : on branche le
    #    MonteCarlo existant sur les trades simulés frais.
    mc_equity = {}
    if sim_closed:
        try:
            mc_runs = int(cfg.get("backtest", {}).get("monte_carlo_runs", 200))
            mc_raw = MonteCarlo(n_runs=mc_runs).run(sim_closed, capital)
            if "error" not in mc_raw and capital > 0:
                mc_equity = {
                    "runs":             mc_raw.get("runs"),
                    "return_p5_pct":    round((mc_raw["final_equity_p5"]  - capital) / capital * 100, 3),
                    "return_mean_pct":  round((mc_raw["final_equity_mean"] - capital) / capital * 100, 3),
                    "return_p95_pct":   round((mc_raw["final_equity_p95"] - capital) / capital * 100, 3),
                    "max_dd_p95_pct":   mc_raw.get("max_dd_p95"),
                    "prob_profit":      mc_raw.get("prob_profit"),
                }
        except Exception as e:
            logger.debug(f"[ForwardTest] MonteCarlo {strategy}@{timeframe} KO : {e}")

    # ── Trades réels sur la même fenêtre ──
    live_returns = []
    try:
        from app.core.database import get_closed_trades_for_slot, session_scope
        with session_scope(session_factory) as sess:
            rows = get_closed_trades_for_slot(sess, strategy, timeframe,
                                              days=lookback_days, symbol=symbol)
            live_returns = [float(r.pnl_pct) for r in rows if r.pnl_pct is not None]
    except Exception as e:
        logger.debug(f"[ForwardTest] lecture trades réels {strategy}@{timeframe} KO : {e}")

    live_mean = round(float(np.mean(live_returns)), 4) if live_returns else None

    # ── Contrat MC glissant (rendement moyen par trade) + verdict ──
    contract = _mc_contract(sim_returns, n_live=len(live_returns))
    in_band, verdict = _verdict(contract, live_mean)

    # ── Cône d'edge (IC de l'expectancy) — promotion ──
    # L'edge se juge sur une **fenêtre longue** (edge_lookback_days, défaut 100 j),
    # distincte de la fenêtre de fidélité (lookback_days, ~45 j) : une stratégie
    # peu fréquente accumule assez de trades sur l'historique pour être
    # significative, sans attendre des mois de live. Cf.
    # docs/CONCEPTION_PROMOTION_PAR_EDGE.md §2.1.
    edge_conf = float((cfg.get("lifecycle", {}) or {}).get("edge_conf", 0.90))
    edge_returns = sim_returns
    if edge_lookback_days and edge_lookback_days > lookback_days:
        try:
            slot_label = f"{strategy}@{timeframe}::{symbol or ''}"
            e_bars = _bars_for_lookback(timeframe, edge_lookback_days,
                                          max_bars=_MAX_EDGE_BARS,
                                          warn_truncation=True,
                                          slot_label=slot_label)
            edge_df = fetch_ohlcv(symbol, timeframe, limit=e_bars)
            if edge_df is not None and len(edge_df) >= _WARMUP_BARS + 30:
                e_eng = Engine()
                e_eng.register(mod.Strategy(), silent=True)
                e_res = Backtester(e_eng, cfg, envelope=envelope,
                                   realistic_risk=True).run(
                    edge_df, symbol, timeframe=timeframe)
                e_trades = _per_trade_returns_pct(_closed_trades(e_res.to_dict().get("trades", [])))
                if e_trades:
                    edge_returns = e_trades
        except Exception as e:
            logger.debug(f"[ForwardTest] edge backtest {strategy}@{timeframe} KO : {e}")
    edge = _edge_contract(edge_returns, conf=edge_conf)

    return {
        "slot_key":      build_slot_key(strategy, timeframe, symbol or ""),
        "strategy":      strategy,
        "timeframe":     timeframe,
        "symbol":        symbol,
        "run_date":      datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "lookback_days": lookback_days,
        "edge_lookback_days": edge_lookback_days,
        "n_bars":        len(df),
        "sim": {
            "total_trades":   d.get("total_trades", 0),
            "win_rate":       d.get("win_rate", 0.0),
            "total_pnl":      d.get("total_pnl", 0.0),
            "return_pct":     round(d.get("total_pnl", 0.0) / capital * 100, 3) if capital else 0.0,
            "avg_return_pct": round(float(np.mean(sim_returns)), 4) if sim_returns else 0.0,
            "sharpe":         d.get("sharpe", 0.0),
            "max_drawdown":   d.get("max_drawdown", 0.0),
        },
        "monte_carlo":  mc_equity,
        "edge":         edge,
        # S12 §5.2 — l'échelle économique sur laquelle cette edge a été
        # mesurée. Sans elle, une expectancy en % ne dit pas de quoi.
        "base":         _envelope_base(
            envelope, datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ) if envelope is not None else None,
        "live": {
            "n_trades":       len(live_returns),
            "avg_return_pct": live_mean,
        },
        "contract": {
            **contract,
            "live_mean_pct": live_mean,
            "in_band":       in_band,
            "verdict":       verdict,
        },
    }


# ── Point d'entrée ─────────────────────────────────────────────────────────
def run_forward_test(cfg: dict, fetch_ohlcv, active_per_tf: dict,
                     session_factory, symbol: str = DEFAULT_CONFIG_SYMBOL,
                     lookback_days: int = 45, edge_lookback_days: int = 100) -> dict:
    """Exécute le forward-test glissant sur tous les slots actifs.

    Parameters
    ----------
    cfg            : config globale (params figés résolus par ``Backtester``).
    fetch_ohlcv    : callable ``(symbol, timeframe, limit) -> polars.DataFrame``
                     (typiquement ``scanner.fetch_ohlcv``).
    active_per_tf  : ``{tf: [{"name", "params", ...}, ...]}`` (slots actifs).
    session_factory: ``SessionLocal`` SQLAlchemy pour lire les trades réels.
    symbol         : paire de référence (alignée sur l'optimiseur).
    lookback_days  : fenêtre glissante du re-backtest (30–60 j recommandé).

    Retourne le dict ``slot_key → enregistrement`` produit (aussi persisté).
    """
    results = {}
    n_slots = sum(len(v) for v in (active_per_tf or {}).values())
    # S12 : chaque slot est re-backtesté sur SON enveloppe — c'est ce qui rend
    # l'expectancy mesurée ici comparable au live, et c'est cette base qui est
    # enregistrée à côté de l'edge pour la garde de dérive (§5.2).
    envelopes = envelopes_for_active_slots(cfg, active_per_tf, default_symbol=symbol)
    logger.info(
        f"[ForwardTest] Forward-test glissant : {n_slots} slot(s), "
        f"fenêtre {lookback_days} j, symbole {symbol}."
    )
    for tf, slots in (active_per_tf or {}).items():
        for slot in slots:
            strategy = slot.get("name")
            if not strategy:
                continue
            slot_sym = slot.get("symbol") or symbol   # config par symbole
            try:
                rec = _forward_test_slot(
                    strategy, tf, slot_sym, cfg, fetch_ohlcv,
                    session_factory, lookback_days, edge_lookback_days,
                    envelope=envelopes.get(build_slot_key(strategy, tf, slot_sym)),
                )
            except Exception as e:
                logger.error(
                    f"[ForwardTest] {strategy}@{tf} : erreur — {e}", exc_info=True
                )
                continue
            if rec is None:
                continue
            results[rec["slot_key"]] = rec
            c = rec["contract"]
            logger.info(
                f"[ForwardTest] {rec['slot_key']} : sim {rec['sim']['total_trades']} tr "
                f"({rec['sim']['return_pct']:+.2f}%) | live {rec['live']['n_trades']} tr "
                f"| verdict={c['verdict']}"
            )
    # D-05 : une seule écriture atomique en fin de passe, pas une par slot.
    if results:
        save_records(results)
    return results
