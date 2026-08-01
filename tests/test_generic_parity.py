"""S2-05 — golden test de parité : le chantier de généralisation multi-actifs
(Sprint 2 : providers.py, Venue étendue, alias min_volume_quote_24h, filtre
asset_classes) ne doit produire AUCUN changement de comportement en crypto.

Données synthétiques déterministes (seed fixe) plutôt que le parquet BTC/USDC
réel : un golden test doit rester stable même si les données de marché
persistées sont régénérées/mises à jour — seule la LOGIQUE de calcul doit
être verrouillée ici.

Valeurs de référence capturées en comparant, bit à bit, la sortie AVANT et
APRÈS l'intégralité du Sprint 2 (git stash sur les 3 stratégies trend_rider/
pullback_trend/breakout, données réelles BTC_USDC/1h — résultats identiques).
"""
import numpy as np
import polars as pl

from app.engine.backtest import Backtester
from app.engine.engine import Engine


def _make_ohlcv(n: int = 600, seed: int = 42) -> pl.DataFrame:
    """OHLCV synthétique déterministe (marché en range + tendance modérée)."""
    rng = np.random.default_rng(seed)
    base = 30_000.0
    closes = [base]
    for i in range(n - 1):
        drift = 0.03 * np.sin(i / 40.0)
        step = rng.standard_normal() * 60.0 + drift * base / 100
        closes.append(max(closes[-1] + step, 1.0))
    closes = np.array(closes)
    highs = closes * (1 + np.abs(rng.standard_normal(n) * 0.004))
    lows = closes * (1 - np.abs(rng.standard_normal(n) * 0.004))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = rng.uniform(500, 2000, n)
    from datetime import datetime, timedelta
    start = datetime(2025, 1, 1)
    times = [start + timedelta(hours=i) for i in range(n)]
    return pl.DataFrame({
        "time": times, "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    })


def _cfg(strategy: str) -> dict:
    return {
        "trading": {
            "capital": 1000.0, "risk_per_trade": 0.01, "timeframe": "1h",
            "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
            "score_threshold": 0.55, "borrow_rate_daily": 0.0002,
            "max_notional_pct": 0.20,
        },
        "backtest": {
            "spread_pct": 0.0005, "latency_ms": 0,
            "partial_fill_pct": 1.0, "monte_carlo_runs": 50,
            "walk_forward_folds": 3,
            "trail_wide": 2.5, "trail_normal": 2.0, "trail_lock": 1.5,
            "trail_tight": 1.0, "grace_bars": 4, "breakeven_r": 1.2,
            "lock_r": 2.5, "tight_r": 4.0, "lock_ratio": 0.60,
            "use_swing": False, "max_notional_pct": 0.20,
        },
        "strategies": {"enabled": [strategy]},
        "strategy_params": {},
    }


def _run(strategy_name: str) -> dict:
    import importlib
    mod = importlib.import_module(f"app.strategies.{strategy_name}")
    inst = mod.Strategy()
    eng = Engine()
    eng.register(inst, silent=True)
    cfg = _cfg(strategy_name)
    bt = Backtester(eng, cfg)
    df = _make_ohlcv()
    res = bt.run(df, "BTC/USDC", timeframe="1h")
    d = res.to_dict()
    strat_key = next(iter(res.by_strategy.keys()), strategy_name) if res.by_strategy else strategy_name
    sd = res.by_strategy.get(strat_key, {})
    return {
        "total_trades": sd.get("total_trades", d["total_trades"]),
        "win_rate": round(sd.get("win_rate", d["win_rate"]), 4),
        "total_pnl": round(sd.get("total_pnl", d["total_pnl"]), 4),
        "final_equity": round(sd.get("final_equity", d["final_equity"]), 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Valeurs témoins — mises à jour une fois, pour une raison nommée
# ─────────────────────────────────────────────────────────────────────────────
# Ces deux stratégies lisent l'ADX/ATR de ``indicators_core``. La correction du
# lissage (§8ter : α = 2/(n+1) → α = 1/n, la définition de Wilder) change donc
# légitimement leurs signaux, et ces témoins ont été recapturés à cette
# occasion — c'est le SEUL motif de changement admis ici.
#
#   trend_rider     3 trades / pnl −5.1440  →  2 trades / pnl −2.3265
#   pullback_trend  9 trades / pnl −16.9821 →  8 trades / pnl −14.4664
#
# S11 — coût d'emprunt porté par la venue (2e recapture, motif nommé). Cette
# config ne déclare ni `venues` ni `exchange.margin` : la venue résolue est donc
# du SPOT, qui n'emprunte pas. Le `borrow_rate_daily: 0.0002` ci-dessus était
# jusqu'ici facturé quand même, sur chaque trade. Les signaux, le nombre de
# trades et le win rate sont INCHANGÉS — seul le PnL remonte, exactement du
# montant des intérêts fictifs supprimés :
#
#   trend_rider     pnl −2.3265  →  −2.2815   (Δ +0.0450)
#   pullback_trend  pnl −14.4664 →  −14.2505  (Δ +0.2159)
#
# Un futur écart sur ces chiffres n'est PAS à réaligner sans avoir identifié la
# cause : le rôle de ce test est de rendre visible un changement de
# comportement, pas de le suivre.
def test_trend_rider_backtest_parity_on_synthetic_btc_data():
    result = _run("trend_rider")
    assert result == {
        "total_trades": 2,
        "win_rate": 0.0,
        "total_pnl": -2.2815,
        "final_equity": 997.7185,
    }


def test_pullback_trend_backtest_parity_on_synthetic_btc_data():
    result = _run("pullback_trend")
    assert result == {
        "total_trades": 8,
        "win_rate": 0.0,
        "total_pnl": -14.2505,
        "final_equity": 985.7495,
    }
