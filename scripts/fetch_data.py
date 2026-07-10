#!/usr/bin/env python3
"""Récupère des données OHLCV pour l'analyse (scripts/analyze_indicators.py).

Deux sources :
  • CRYPTO via ccxt (okx/binance…) → data/ohlcv/<SYM>/<tf>.parquet
  • ACTIONS/ETF via l'API chart de Yahoo Finance → data/stocks/<TICKER>_<iv>.csv

⚠ Nécessite un accès réseau sortant vers l'exchange / Yahoo. Dans l'environnement
Claude Code managé par défaut, ces hôtes sont bloqués par la politique réseau
(403) — lancez ce script en local, ou faites autoriser okx.com /
query1.finance.yahoo.com dans les réglages d'environnement.

Exemples :
  python scripts/fetch_data.py --crypto ETH/USDC SOL/USDC XRP/USDC --tf 4h --bars 6000
  python scripts/fetch_data.py --stocks ETL.PA ALTBG.PA CAC.PA --interval 1d --range 5y

Tickers utiles (Euronext Paris) : Eutelsat=ETL.PA, Capital B (ex The Blockchain
Group)=ALTBG.PA, ETF CAC 40=CAC.PA ou C40.PA (indice nu=^FCHI).
"""
import argparse
import os
import time

import polars as pl

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


# ── Crypto via ccxt (pagination arrière) ─────────────────────────────────────
def fetch_crypto(symbol: str, timeframe: str, bars: int, exchange: str):
    import ccxt
    ex = getattr(ccxt, exchange)({"enableRateLimit": True})
    tf_ms = ex.parse_timeframe(timeframe) * 1000
    since = ex.milliseconds() - bars * tf_ms
    out, cursor = [], since
    while len(out) < bars:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=300)
        if not batch:
            break
        out += batch
        cursor = batch[-1][0] + tf_ms
        if len(batch) < 300:
            break
        time.sleep(ex.rateLimit / 1000)
    # dédoublonnage + tri
    seen, rows = set(), []
    for r in sorted(out, key=lambda x: x[0]):
        if r[0] in seen:
            continue
        seen.add(r[0]); rows.append(r)
    df = pl.DataFrame(rows, schema=["ts", "open", "high", "low", "close", "volume"],
                      orient="row").with_columns(
        pl.from_epoch("ts", time_unit="ms").alias("time")).drop("ts")
    sym_dir = symbol.replace("/", "_")
    path = os.path.join(ROOT, "data", "ohlcv", sym_dir, f"{timeframe}.parquet")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.write_parquet(path)
    return path, df.height


# ── Actions/ETF via Yahoo Finance ────────────────────────────────────────────
def fetch_stock(ticker: str, interval: str, rng: str):
    import requests
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    r = requests.get(url, params={"range": rng, "interval": interval},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    df = pl.DataFrame({
        "time": [t * 1000 for t in ts],
        "open": q["open"], "high": q["high"], "low": q["low"],
        "close": q["close"], "volume": q["volume"],
    }).with_columns(pl.from_epoch("time", time_unit="ms")).drop_nulls(
        ["open", "high", "low", "close"])
    path = os.path.join(ROOT, "data", "stocks", f"{ticker}_{interval}.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.write_csv(path)
    return path, df.height


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crypto", nargs="*", default=[], help="paires ccxt, ex. ETH/USDC")
    ap.add_argument("--tf", default="4h", help="timeframe crypto (défaut 4h)")
    ap.add_argument("--bars", type=int, default=6000, help="nb de bougies crypto (défaut 6000)")
    ap.add_argument("--exchange", default="okx", help="exchange ccxt (défaut okx)")
    ap.add_argument("--stocks", nargs="*", default=[], help="tickers Yahoo, ex. ETL.PA")
    ap.add_argument("--interval", default="1d", help="intervalle actions (défaut 1d)")
    ap.add_argument("--range", dest="rng", default="5y", help="plage actions (défaut 5y)")
    a = ap.parse_args()

    for sym in a.crypto:
        try:
            p, n = fetch_crypto(sym, a.tf, a.bars, a.exchange)
            print(f"✔ crypto {sym} {a.tf} : {n} bougies → {p}")
        except Exception as e:
            print(f"✗ crypto {sym} KO : {repr(e)[:160]}")
    for tk in a.stocks:
        try:
            p, n = fetch_stock(tk, a.interval, a.rng)
            print(f"✔ action {tk} {a.interval} : {n} barres → {p}")
        except Exception as e:
            print(f"✗ action {tk} KO : {repr(e)[:160]}")

    if not a.crypto and not a.stocks:
        ap.print_help()


if __name__ == "__main__":
    main()
