#!/usr/bin/env python3
"""Récupère des données OHLCV pour l'analyse (scripts/analyze_indicators.py) et
pour alimenter le cache du bot.

  • CRYPTO via le CandleStore du bot (ccxt, pagination robuste, schéma canonique)
    → data/ohlcv/<SYM>/<tf>.parquet — MÊME format que le live (colonnes
    time/open/high/low/close/volume). Réutilise la machinerie qui marche déjà
    pour BTC (évite le bug « 0 bougie » du fetch ccxt brut).
  • ACTIONS/ETF via l'API chart de Yahoo Finance → data/stocks/<TICKER>_<iv>.csv.

⚠ Nécessite un accès réseau. Dans l'environnement Claude Code managé par défaut,
ces hôtes sont bloqués (403) — lancez ce script en local.

Exemples :
  python scripts/fetch_data.py --crypto ETH/USDC SOL/USDC XRP/USDC --tf 4h --bars 6000
  python scripts/fetch_data.py --stocks ETL.PA CAC.PA --interval 1d --range 5y

Tickers Euronext : Eutelsat=ETL.PA, TotalEnergies=TTE.PA, ETF CAC 40=CAC.PA
(indice nu=^FCHI). ⚠ Vérifiez le ticker EXACT sur finance.yahoo.com (ex.
Capital B / The Blockchain Group : le symbole Yahoo peut différer → 404 sinon).
"""
import argparse
import os
import sys

import polars as pl

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)

# Intervalles intraday Yahoo : historique plafonné (1m→7j, le reste→730j).
_YF_INTRADAY = {"1m": "7d", "2m": "60d", "5m": "60d", "15m": "60d",
                "30m": "60d", "60m": "730d", "90m": "60d", "1h": "730d"}


# ── Crypto via le CandleStore du bot (schéma canonique garanti) ──────────────
def fetch_crypto(symbol: str, timeframe: str, bars: int, exchange: str):
    import ccxt
    from app.core.candle_store import get_store
    ex = getattr(ccxt, exchange)({"enableRateLimit": True})
    store = get_store(os.path.join(ROOT, "data", "ohlcv"))
    df = store.fetch(ex, symbol, timeframe, total=bars)
    path = store._path(symbol, timeframe)
    return str(path), (df.height if df is not None else 0)


# ── Actions/ETF via Yahoo Finance ────────────────────────────────────────────
def fetch_stock(ticker: str, interval: str, rng: str):
    import requests
    # Yahoo refuse un range long avec un intervalle intraday (HTTP 422) → on cape.
    eff_range = rng
    if interval in _YF_INTRADAY:
        eff_range = _YF_INTRADAY[interval]
        if eff_range != rng:
            print(f"  ↳ {ticker} : intervalle {interval} intraday → range plafonné "
                  f"à {eff_range} (Yahoo refuse {rng}).")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    r = requests.get(url, params={"range": eff_range, "interval": interval},
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
            status = "✔" if n > 0 else "⚠ 0 bougie (paire absente de l'exchange ?)"
            print(f"{status} crypto {sym} {a.tf} : {n} bougies → {p}")
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
