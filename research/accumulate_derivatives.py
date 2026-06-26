"""Accumulation « au fil de l'eau » des dérivés (funding/OI/long-short/taker).

Remplit data/derivatives/*.parquet (incrémental, dédupliqué) — comme CandleStore
pour l'OHLCV. À lancer en one-shot (seed/backfill) ou en boucle (cron / --loop).
Indépendant du bot : utile pour constituer un historique avant calibration.

Usage :
  python research/accumulate_derivatives.py --symbols BTC/USDC,ETH/USDC --period 1h
  python research/accumulate_derivatives.py --loop 300        # toutes les 5 min
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.derivatives import DerivativesStore, to_perp_symbol


def _exchange():
    """Client ccxt OKX public (sans clé) pour funding/OI. None si indispo."""
    try:
        import ccxt
        return ccxt.okx({"enableRateLimit": True, "timeout": 30000})
    except Exception as e:
        print(f"[accumulate] ccxt indisponible : {e}")
        return None


def run_once(store: DerivativesStore, ex, symbols, period):
    for sym in symbols:
        # min_interval=0 → force le fetch à chaque passage (le throttle vit dans le bot)
        store.refresh(ex, sym, period, min_interval=0.0)
        sizes = {m: len(store._load(store._path(sym, m)))
                 for m in ("funding", "oi", "lsratio", "taker")}
        print(f"[accumulate] {to_perp_symbol(sym):10} period={period} | "
              f"funding={sizes['funding']} oi={sizes['oi']} "
              f"lsr={sizes['lsratio']} taker={sizes['taker']} points en cache")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTC/USDC")
    ap.add_argument("--period", default="1h")
    ap.add_argument("--loop", type=float, default=0, help="intervalle en s (0 = one-shot)")
    ap.add_argument("--base-dir", default="data/derivatives")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    store = DerivativesStore(base_dir=args.base_dir)
    ex = _exchange()

    run_once(store, ex, symbols, args.period)
    while args.loop > 0:
        time.sleep(args.loop)
        run_once(store, ex, symbols, args.period)


if __name__ == "__main__":
    main()
