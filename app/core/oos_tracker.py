"""Forward-test glissant & contrat Monte-Carlo glissant (Phase 0 — observationnel).

Ce module ne prend **aucune** décision de trading. Une fois par jour, pour chaque
slot actif ``strategy::timeframe`` :

1. **Forward-test glissant** — on re-backteste les *params figés* du slot sur les
   ``lookback_days`` derniers jours de données fraîches (réutilisation du
   ``Backtester`` existant). C'est la photo « non périmée » du comportement de la
   stratégie, par opposition à l'OOS figé à la création.
2. **Contrat Monte-Carlo glissant** — on branche le ``MonteCarlo`` existant (mais
   jusqu'ici inutilisé hors de l'API) sur les trades simulés pour obtenir une
   **fourchette** d'issues plausibles, recalculée à chaque exécution (donc
   *glissante*, jamais figée).
3. **Comparaison réel vs simulation** — on récupère les trades réels du slot sur
   la même fenêtre et on vérifie si leur rendement moyen par trade tombe dans la
   fourchette Monte-Carlo. C'est la donnée « le live confirme-t-il la
   simulation ? » qui rendra fiables les décisions d'allocation des phases
   suivantes.

Toutes les grandeurs comparées sont **budget-indépendantes** (rendement % par
trade), cohérent avec le score budget-indépendant (``opt_scoring.py``).

Persistance : ``data/oos_tracker.json`` — un dict ``slot_key → enregistrement``,
le dernier écrasant le précédent (même esprit que ``backtest_history.py``).
"""
import importlib
import json
import logging
import math
import os
import threading
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger(__name__)

_TRACKER_PATH = os.path.join("data", "oos_tracker.json")
_lock = threading.Lock()

# Timeframe → minutes (aligné sur app/engine/backtest.py)
_TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480,
    "12h": 720, "1d": 1440,
}

# Bougies de chauffe pour les indicateurs (EMA/ADX longues) avant la fenêtre utile.
_WARMUP_BARS = 250
# Garde-fou : on ne demande jamais plus que ça de bougies pour un forward-test.
_MAX_BARS = 4000


# ── Persistance ────────────────────────────────────────────────────────────
def _path() -> str:
    os.makedirs(os.path.dirname(_TRACKER_PATH), exist_ok=True)
    return _TRACKER_PATH


def load_oos_tracker() -> dict:
    """Retourne le dict ``slot_key → enregistrement`` (vide si absent/illisible)."""
    try:
        with open(_TRACKER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        logger.warning(f"[oos_tracker] lecture KO : {e}")
        return {}


def _save_record(slot_key: str, record: dict) -> None:
    try:
        with _lock:
            data = load_oos_tracker()
            data[slot_key] = record
            with open(_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[oos_tracker] écriture KO ({slot_key}) : {e}")


# ── Helpers ────────────────────────────────────────────────────────────────
def _bars_for_lookback(tf: str, lookback_days: int) -> int:
    minutes = _TF_MINUTES.get(tf, 60)
    bars = int(math.ceil(lookback_days * 1440 / minutes)) + _WARMUP_BARS
    return max(_WARMUP_BARS + 30, min(bars, _MAX_BARS))


def _closed_trades(trades: list) -> list:
    return [t for t in (trades or []) if str(t.get("status", "")).startswith("closed")]


def _per_trade_returns_pct(trades: list) -> list:
    """Rendements par trade en % (budget-indépendant). Utilise ``pnl_pct`` quand
    présent (trades simulés et live le portent), sinon retombe sur 0."""
    out = []
    for t in trades:
        v = t.get("pnl_pct")
        if v is None:
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _mc_contract(sim_returns_pct: list, n_live: int,
                 runs: int = 2000, conf: float = 0.90) -> dict:
    """Fourchette Monte-Carlo glissante sur le **rendement moyen par trade**.

    On rééchantillonne (bootstrap) ``n_live`` rendements parmi les rendements
    simulés et on prend la distribution de leur moyenne. La fourchette
    [p_low, p_high] est l'intervalle de confiance ``conf`` de ce que la
    simulation prédit pour ``n_live`` trades — exactement le nombre de trades
    réels observés, d'où une comparaison apples-to-apples.
    """
    if not sim_returns_pct or n_live <= 0:
        return {"available": False}
    arr = np.asarray(sim_returns_pct, dtype=float)
    rng = np.random.default_rng(42)
    means = rng.choice(arr, size=(runs, n_live), replace=True).mean(axis=1)
    lo = (1.0 - conf) / 2.0 * 100.0
    hi = (1.0 + conf) / 2.0 * 100.0
    return {
        "available":      True,
        "runs":           runs,
        "confidence":     conf,
        "n_live":         n_live,
        "sim_mean_pct":   round(float(arr.mean()), 4),
        "band_low_pct":   round(float(np.percentile(means, lo)), 4),
        "band_high_pct":  round(float(np.percentile(means, hi)), 4),
    }


def _verdict(contract: dict, live_mean_pct):
    """Compare le rendement réel moyen par trade à la fourchette MC.

    Retourne (in_band: bool|None, verdict: str). ``in_band`` vaut None tant
    qu'on n'a pas de données live exploitables.
    """
    if not contract.get("available") or live_mean_pct is None:
        return None, "pas_assez_de_trades_reels"
    lo = contract["band_low_pct"]
    hi = contract["band_high_pct"]
    if live_mean_pct < lo:
        return False, "sous_la_simulation"   # le live sous-performe la sim
    if live_mean_pct > hi:
        return False, "au_dessus_de_la_simulation"  # rare : à investiguer aussi
    return True, "conforme"


# ── Forward-test d'un slot ─────────────────────────────────────────────────
def _forward_test_slot(strategy: str, timeframe: str, symbol: str,
                       cfg: dict, fetch_ohlcv, session_factory,
                       lookback_days: int) -> dict | None:
    """Re-backteste un slot sur données fraîches, construit le contrat MC et
    compare aux trades réels. Retourne l'enregistrement (ou None si données
    insuffisantes)."""
    from app.engine.engine import Engine
    from app.engine.backtest import Backtester, MonteCarlo

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
    bt = Backtester(eng, cfg)
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
            rows = get_closed_trades_for_slot(sess, strategy, timeframe, days=lookback_days)
            live_returns = [float(r.pnl_pct) for r in rows if r.pnl_pct is not None]
    except Exception as e:
        logger.debug(f"[ForwardTest] lecture trades réels {strategy}@{timeframe} KO : {e}")

    live_mean = round(float(np.mean(live_returns)), 4) if live_returns else None

    # ── Contrat MC glissant (rendement moyen par trade) + verdict ──
    contract = _mc_contract(sim_returns, n_live=len(live_returns))
    in_band, verdict = _verdict(contract, live_mean)

    return {
        "slot_key":      f"{strategy}::{timeframe}",
        "strategy":      strategy,
        "timeframe":     timeframe,
        "symbol":        symbol,
        "run_date":      datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "lookback_days": lookback_days,
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
                     session_factory, symbol: str = "BTC/USDC",
                     lookback_days: int = 45) -> dict:
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
    logger.info(
        f"[ForwardTest] Forward-test glissant : {n_slots} slot(s), "
        f"fenêtre {lookback_days} j, symbole {symbol}."
    )
    for tf, slots in (active_per_tf or {}).items():
        for slot in slots:
            strategy = slot.get("name")
            if not strategy:
                continue
            try:
                rec = _forward_test_slot(
                    strategy, tf, symbol, cfg, fetch_ohlcv,
                    session_factory, lookback_days,
                )
            except Exception as e:
                logger.error(
                    f"[ForwardTest] {strategy}@{tf} : erreur — {e}", exc_info=True
                )
                continue
            if rec is None:
                continue
            _save_record(rec["slot_key"], rec)
            results[rec["slot_key"]] = rec
            c = rec["contract"]
            logger.info(
                f"[ForwardTest] {rec['slot_key']} : sim {rec['sim']['total_trades']} tr "
                f"({rec['sim']['return_pct']:+.2f}%) | live {rec['live']['n_trades']} tr "
                f"| verdict={c['verdict']}"
            )
    return results
