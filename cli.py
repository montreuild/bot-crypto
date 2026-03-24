"""
Crypto Bot V11 — Point d'entrée principal.
"""
import argparse
import importlib
import logging
import sys
import threading

import polars as pl
import uvicorn

from app.core.config        import load_config
from app.core.logger        import setup_logging
from app.core.exchange      import create_exchange
from app.core.candle_store  import get_store
from app.engine.engine      import Engine
from app.engine.backtest    import Backtester, WalkForwardAnalyzer, MonteCarlo
from app.api.main           import app as fastapi_app, init_app


# Nombre de minutes par timeframe — utilisé pour convertir des mois en nombre de bougies
_TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720, "1d": 1440,
}

def _months_to_limit(months: float, tf: str) -> int:
    """Convertit un nombre de mois en nombre de bougies pour un timeframe donné."""
    minutes_per_bar = _TF_MINUTES.get(tf, 60)
    total_minutes   = months * 30 * 24 * 60
    return max(100, int(total_minutes / minutes_per_bar))


def parse_args():
    p = argparse.ArgumentParser(description="Crypto Bot V11")
    p.add_argument("--config",       default="config.yaml", help="Fichier de config")
    p.add_argument("--backtest",     metavar="SYMBOL",       help="Lancer un backtest CLI")
    p.add_argument("--timeframe",    default="",             help="Timeframe (défaut=config)")
    p.add_argument("--timeframes",   default="",             help="Multi-timeframes séparés par virgule (ex: 1h,4h,1d)")
    p.add_argument("--limit",        type=int, default=0,    help="Nombre de bougies (prioritaire sur --months)")
    p.add_argument("--months",       type=float, default=6.0,help="Période en mois (défaut=6, converti en bougies par TF)")
    p.add_argument("--walk-forward", action="store_true",    help="Activer Walk-Forward")
    p.add_argument("--monte-carlo",  action="store_true",    help="Activer Monte-Carlo")
    p.add_argument("--optimize",     metavar="STRATEGY",     help="Optimiser une stratégie")
    p.add_argument("--opt-method",   default="bayesian",     help="grid|random|bayesian")
    p.add_argument("--scan",         action="store_true",    help="Scanner les marchés")
    p.add_argument("--live",         action="store_true",    help="Démarrer en mode live réel (désactive paper mode)")
    p.add_argument("--paper",        action="store_true",    help="Forcer le mode paper trading")
    p.add_argument("--web",          action="store_true",    help="Serveur web seul (sans démarrer le bot de trading)")
    p.add_argument("--host",         default=None,           help="Adresse d'écoute (écrase config)")
    p.add_argument("--port",         type=int, default=None, help="Port du serveur web (écrase config)")
    return p.parse_args()


def _run_backtest_one_tf(cfg, args, exchange, symbol: str, tf: str,
                         mc_runner, summary_rows: list) -> None:
    """Exécute le backtest pour un symbol/tf et affiche les résultats."""
    # Calcul du nombre de bougies
    if args.limit > 0:
        total = args.limit
        period_label = f"{total} bougies"
    else:
        total = _months_to_limit(args.months, tf)
        period_label = f"{args.months:.0f} mois (~{total} bougies)"

    print(f"\n{'='*60}")
    print(f"  BACKTEST : {symbol} | TF={tf} | {period_label}")
    print(f"{'='*60}")

    df = get_store().fetch(exchange, symbol, tf, total=total)
    if df is None or len(df) == 0:
        print(f"  Erreur : aucune donnée disponible pour {symbol}/{tf}")
        return

    actual_bars = len(df)
    date_from   = str(df["time"].min())[:10] if actual_bars > 0 else "?"
    date_to     = str(df["time"].max())[:10] if actual_bars > 0 else "?"
    print(f"  Données   : {actual_bars} bougies du {date_from} au {date_to}")

    for name in cfg["strategies"]["enabled"]:
        print(f"\n  -- Stratégie : {name}")
        mod = importlib.import_module(f"app.strategies.{name}")
        eng = Engine(); eng.register(mod.Strategy())
        bt  = Backtester(eng, cfg)
        res = bt.run(df, symbol)
        d   = res.to_dict()
        print(f"  Trades     : {d['total_trades']}")
        print(f"  Win Rate   : {d['win_rate']:.1f}%")
        print(f"  PnL net    : {d['total_pnl']:+.4f} USDC")
        print(f"  Frais      : -{d['total_fees']:.4f} USDC")
        print(f"  Sharpe     : {d['sharpe']:.3f}")
        print(f"  Expectancy : {d['expectancy']:+.4f}")
        print(f"  Max DD     : {d['max_drawdown']:.2f}%")
        print(f"  Profit Fct : {d['profit_factor']}")

        summary_rows.append({
            "tf": tf, "strategy": name,
            "trades": d["total_trades"], "wr": d["win_rate"],
            "pnl": d["total_pnl"], "sharpe": d["sharpe"],
            "dd": d["max_drawdown"], "pf": d["profit_factor"],
        })

        if args.walk_forward and actual_bars >= 200:
            print(f"\n  Walk-Forward ({cfg.get('backtest',{}).get('walk_forward_folds', 5)} folds)...")
            wf  = WalkForwardAnalyzer(eng, cfg)
            wfr = wf.run(df, symbol)
            print(f"  OOS PnL moy   : {wfr['avg_oos_pnl']:+.4f}")
            print(f"  OOS Sharpe    : {wfr['avg_oos_sharpe']:.3f}")
            print(f"  OOS Win Rate  : {wfr['avg_oos_wr']:.1f}%")
            print(f"  Consistance   : {wfr['consistency']:.0f}% des folds OOS profitables")

        if mc_runner and d.get("trades"):
            print(f"\n  Monte-Carlo ({cfg.get('backtest',{}).get('monte_carlo_runs', 200)} runs)...")
            mc  = mc_runner.run(d["trades"], cfg["trading"]["capital"])
            print(f"  Equity moy.   : ${mc['final_equity_mean']:.2f}")
            print(f"  Equity p5-p95 : ${mc['final_equity_p5']:.2f} -- ${mc['final_equity_p95']:.2f}")
            print(f"  DD max p95    : {mc['max_dd_p95']:.2f}%")
            print(f"  Prob profit   : {mc['prob_profit']:.1f}%")
            print(f"  Prob ruine -10%:{mc['prob_ruin_10pct']:.1f}%")


def run_backtest_cli(cfg, args):
    symbol = args.backtest

    # Résolution des timeframes à tester
    if args.timeframes:
        timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    elif args.timeframe:
        timeframes = [args.timeframe]
    else:
        timeframes = [cfg["trading"]["timeframe"]]

    exchange  = create_exchange(cfg)
    mc_runner = MonteCarlo(n_runs=cfg.get("backtest",{}).get("monte_carlo_runs", 200)) if args.monte_carlo else None
    summary_rows: list = []

    for tf in timeframes:
        _run_backtest_one_tf(cfg, args, exchange, symbol, tf, mc_runner, summary_rows)

    # Tableau récapitulatif multi-TF
    if len(timeframes) > 1 and summary_rows:
        print(f"\n{'='*80}")
        print(f"  RECAP MULTI-TIMEFRAME : {symbol}")
        print(f"{'='*80}")
        print(f"  {'TF':<6} {'Stratégie':<25} {'Trades':>6} {'WR%':>6} {'PnL':>10} {'Sharpe':>7} {'DD%':>7} {'PF':>6}")
        print(f"  {'-'*75}")
        for r in summary_rows:
            pnl_str = f"{r['pnl']:+.2f}"
            pf_str  = f"{r['pf']:.2f}" if isinstance(r['pf'], float) else str(r['pf'])
            print(f"  {r['tf']:<6} {r['strategy']:<25} {r['trades']:>6} "
                  f"{r['wr']:>5.1f}% {pnl_str:>10} {r['sharpe']:>7.3f} "
                  f"{r['dd']:>6.2f}% {pf_str:>6}")
        print(f"{'='*80}\n")


def run_optimizer_cli(cfg, args):
    from app.engine.optimizer import StrategyOptimizer, DEFAULT_SPACES
    strategy = args.optimize
    exchange = create_exchange(cfg)
    tf       = cfg["trading"]["timeframe"]
    df       = get_store().fetch(exchange, "BTC/USDC", tf, total=1000)
    if df is None or len(df) == 0:
        print(f"  Erreur : aucune donnée disponible pour BTC/USDC/{tf}")
        return
    split    = int(len(df) * 0.7)
    opt      = StrategyOptimizer(strategy, cfg, df[:split], df[split:],
                                  DEFAULT_SPACES.get(strategy, {}))
    method = args.opt_method
    if method == "grid":       result = opt.grid_search()
    elif method == "bayesian": result = opt.bayesian_search(30)
    else:                      result = opt.random_search(30)
    best = result.get("best", {})
    print(f"\n  Meilleurs parametres ({method}) :")
    print(f"  Params   : {best.get('params')}")
    print(f"  IS score : {best.get('is_score')}")
    print(f"  OOS score: {best.get('oos_score')}")


def main():
    args = parse_args()
    cfg  = load_config(args.config)

    if args.live:
        # --live force le mode réel (désactive paper trading)
        cfg["trading"]["paper_mode"] = False
    elif args.paper:
        cfg["trading"]["paper_mode"] = True

    setup_logging(cfg)
    logger = logging.getLogger("bot")

    if args.backtest:
        run_backtest_cli(cfg, args)
        return

    if args.optimize:
        run_optimizer_cli(cfg, args)
        return

    if args.scan:
        exchange = create_exchange(cfg)
        from app.engine.scanner import MarketScanner
        scanner = MarketScanner(exchange, cfg)
        results = scanner.screen(cfg["trading"]["timeframe"])
        print(f"\n  {len(results)} paires scannees :")
        for r in sorted(results, key=lambda x: x.get("indicators", {}).get("adx", 0), reverse=True)[:10]:
            ind = r.get("indicators", {})
            print(f"  {r['symbol']:15} ADX={ind.get('adx',0):.1f} RSI={ind.get('rsi',0):.1f} "
                  f"Regime={r['regime']:6} Vol24h=${r['volume_24h']/1e6:.1f}M")
        return

    # Mode live / paper / web
    trader = None

    if not args.web:
        try:
            from app.live.live_trader import LiveTrader
            exchange = create_exchange(cfg)
            trader   = LiveTrader(cfg, exchange)
        except Exception as e:
            logger.error(
                f"[Main] Erreur initialisation LiveTrader : {e} "
                f"-- démarrage en mode serveur web seul.",
                exc_info=True,
            )
            trader = None

    init_app(cfg, trader)

    web_cfg = cfg.get("web", {})
    host    = args.host or web_cfg.get("host", "127.0.0.1")
    port    = args.port or int(web_cfg.get("port", 8000))

    if trader:
        t = threading.Thread(target=trader.start, daemon=True)
        t.start()
        mode = "PAPER" if cfg["trading"].get("paper_mode", True) else "LIVE"
        logger.info(f"[Main] Trading demarre ({mode}) -- Dashboard : http://{host}:{port}")
    else:
        logger.info(f"[Main] Serveur web seul -- http://{host}:{port}")

    print(f"\n  Crypto Bot V11 -- http://{host}:{port}\n")
    uvicorn.run(fastapi_app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
