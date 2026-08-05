#!/usr/bin/env python3
"""ML-10 — Campagne de mesure + recalibrage des seuils ADX (Wilder).

Contexte (docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §8ter, PLAN ML-10) :
les seuils ``adx_min`` / ``adx_threshold`` / ``adx_thresh`` / etc. des YAML
ont été choisis face à un ADX EWM (échelle ~35) ; le moteur utilise désormais
Wilder (échelle ~28). Ce script :

1. **Mesure** l'échelle ADX actuelle (Wilder) sur BTC/USDC par TF.
2. **Réoptimise** chaque cible sous Wilder (défaut production).
3. **Persiste** ``optimizer_results`` + met à jour les clés ADX des ``params``
   top-level du YAML stratégie.
4. Écrit un rapport JSON dans ``research/ml10_adx_recalibration.json``.

Usage ::

    PYTHONPATH=. python scripts/recalibrate_adx_thresholds.py
    PYTHONPATH=. python scripts/recalibrate_adx_thresholds.py --trials 40 --jobs 4
    PYTHONPATH=. python scripts/recalibrate_adx_thresholds.py --only pullback_trend
    PYTHONPATH=. python scripts/recalibrate_adx_thresholds.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.core.indicators_precompute as ip  # noqa: E402
from app.core.candle_store import get_store  # noqa: E402
from app.core.config import load_config  # noqa: E402
from app.core.yaml_io import dump_yaml, load_yaml  # noqa: E402
from app.engine.backtest import Backtester  # noqa: E402
from app.engine.engine import Engine  # noqa: E402
from app.engine.opt_persistence import save_optimizer_results  # noqa: E402
from app.engine.opt_scoring import composite_score  # noqa: E402
from app.engine.optimizer_search import OptimizerSearchEngine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("ml10")

SYMBOL = "BTC/USDC"
FRAC_IS, FRAC_OOS = 0.60, 0.20
MIN_VAL_TRADES = 15

# Stratégies ADX rules/proxy (pas les ML qui réentraînent à chaque barre :
# v4/v5/ml_dynamic_threshold → trop lents pour random_search).
_ADX_STRATS = [
    "pullback_trend",
    "scoring_statistique_opus",
    "scoring_statistique_opus_v2",
    "multi_tf_sr",
    "momentum_blitz",
    "harmonic_regime",
    "volatility_squeeze",
    "smart_trend_adx",
    "dynamic_threshold_no_ml",
    "trend",
    "trend_rider",
    "supertrend_macd",
]
# Vague 1 : 1h/4h/1d — Vague 2 : 15m/30m (cf. --tfs)
_DEFAULT_TFS = ["15m", "30m", "1h", "4h", "1d"]

TARGETS: List[Tuple[str, str]] = [
    (s, tf) for s in _ADX_STRATS for tf in _DEFAULT_TFS
]

ADX_KEY_SUBSTR = ("adx",)


def _is_adx_key(k: str) -> bool:
    kl = k.lower()
    return any(s in kl for s in ADX_KEY_SUBSTR)


def split_three(df):
    n = len(df)
    a = int(n * FRAC_IS)
    b = int(n * (FRAC_IS + FRAC_OOS))
    return df.slice(0, a), df.slice(a, b - a), df.slice(b, n - b)


def measure_adx_scale(tf: str) -> Dict[str, Any]:
    """Statistiques de l'ADX Wilder (défaut) sur le cache local."""
    ip.set_wilder_atr_adx(True)
    df = get_store().load_cached(SYMBOL, tf)
    if df is None or len(df) < 50:
        return {"tf": tf, "n": 0}
    from app.core.indicators import precompute_df
    df = precompute_df(df)
    if "_pre_adx14" not in df.columns:
        return {"tf": tf, "n": len(df), "error": "no _pre_adx14"}
    s = df["_pre_adx14"].drop_nulls()
    if len(s) == 0:
        return {"tf": tf, "n": len(df), "error": "empty adx"}
    import numpy as np
    arr = s.to_numpy().astype(float)
    return {
        "tf": tf,
        "n": int(len(arr)),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "p25": float(np.percentile(arr, 25)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
    }


def run_val(strategy_name: str, cfg: dict, params: dict, df_val, tf: str) -> dict:
    import importlib
    mod = importlib.import_module(f"app.strategies.{strategy_name}")
    eng = Engine()
    eng.register(mod.Strategy(), silent=True)
    c = deepcopy(cfg)
    c.setdefault("strategy_params", {})[strategy_name] = params
    c.get("optimizer_results", {}).pop(strategy_name, None)
    res = Backtester(eng, c).run(df_val, SYMBOL, timeframe=tf)
    return {
        "score": composite_score(res),
        "pnl": res.total_pnl,
        "sharpe": res.sharpe,
        "trades": res.total_trades,
        "wr": res.win_rate,
        "dd": res.max_drawdown,
    }


def optimize_wilder(strategy_name: str, cfg: dict, df_is, df_oos,
                    tf: str, trials: int, jobs: int) -> dict:
    ip.set_wilder_atr_adx(True)
    c = deepcopy(cfg)
    c.setdefault("indicators", {})["wilder_atr_adx"] = True
    opt = OptimizerSearchEngine(strategy_name, c, df_is, df_oos,
                                symbol=SYMBOL, timeframe=tf)
    if not opt.param_space:
        return {"error": "espace de params vide"}
    # Garde les ADX dans l'espace ; si aucun, on optimise quand même le reste
    # (recalibrage global sous Wilder utile), mais on le note.
    adx_keys = [k for k in opt.param_space if _is_adx_key(k)]
    best = opt.random_search(trials, n_jobs=jobs, param_search_optim=False)
    best["adx_keys_in_space"] = adx_keys
    return best


def update_yaml_adx_defaults(strategy_name: str, best_params: dict,
                             dry_run: bool) -> Dict[str, Any]:
    """Met à jour les clés ADX dans ``params:`` top-level du YAML."""
    path = ROOT / "strategies" / f"{strategy_name}.yaml"
    if not path.exists():
        return {"updated": False, "reason": "yaml missing"}
    data = load_yaml(str(path), default={})
    if not isinstance(data, dict):
        return {"updated": False, "reason": "invalid yaml"}
    params = data.setdefault("params", {})
    if not isinstance(params, dict):
        params = {}
        data["params"] = params
    changes = {}
    for k, v in best_params.items():
        if not _is_adx_key(k):
            continue
        old = params.get(k)
        if old != v:
            changes[k] = {"old": old, "new": v}
            params[k] = v
    if not changes:
        return {"updated": False, "reason": "no adx keys in best_params or no change"}
    if not dry_run:
        dump_yaml(str(path), data)
    return {"updated": not dry_run, "dry_run": dry_run, "changes": changes}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--only", default=None, help="filtre nom de stratégie")
    ap.add_argument("--tfs", default=None,
                    help="filtre TF, ex: 15m,30m (défaut : tous les TF de TARGETS)")
    ap.add_argument("--dry-run", action="store_true",
                    help="optimise et mesure, n'écrit ni YAML ni optimizer_results")
    ap.add_argument("--report", default=str(ROOT / "research" / "ml10_adx_recalibration.json"))
    args = ap.parse_args()

    t0 = time.time()
    # Wilder = convention de production.
    ip.set_wilder_atr_adx(True)

    tf_filter = None
    if args.tfs:
        tf_filter = {t.strip() for t in args.tfs.split(",") if t.strip()}

    log.info("=== ML-10 mesure échelle ADX (Wilder) ===")
    scales = []
    tfs_measure = sorted(tf_filter) if tf_filter else sorted({t for _, t in TARGETS})
    for tf in tfs_measure:
        m = measure_adx_scale(tf)
        scales.append(m)
        if "mean" in m:
            log.info("  %s n=%s mean=%.1f median=%.1f p25=%.1f p75=%.1f p90=%.1f",
                     tf, m["n"], m["mean"], m["median"], m["p25"], m["p75"], m["p90"])
        else:
            log.info("  %s %s", tf, m)

    cfg = load_config()
    targets = [t for t in TARGETS if args.only in (None, t[0])]
    if tf_filter:
        targets = [t for t in targets if t[1] in tf_filter]
    log.info("Cibles : %d (strats×TF)", len(targets))
    campaigns = []

    for name, tf in targets:
        log.info("=" * 72)
        log.info("Cible %s / %s", name, tf)
        df = get_store().load_cached(SYMBOL, tf)
        if df is None or len(df) < 400:
            log.warning("  historique insuffisant (%s)", 0 if df is None else len(df))
            campaigns.append({
                "strategy": name, "tf": tf, "status": "skipped",
                "reason": "insufficient_history",
            })
            continue
        df = df.sort("time")
        df_is, df_oos, df_val = split_three(df)
        log.info("  IS=%d OOS=%d VAL=%d", len(df_is), len(df_oos), len(df_val))

        # Baseline : params YAML actuels sous Wilder
        import importlib
        try:
            mod = importlib.import_module(f"app.strategies.{name}")
            base_params = dict(getattr(mod.Strategy(), "_DEFAULTS", {}) or {})
            # merge YAML defaults
            ypath = ROOT / "strategies" / f"{name}.yaml"
            if ypath.exists():
                yd = load_yaml(str(ypath), default={}) or {}
                if isinstance(yd.get("params"), dict):
                    base_params.update(yd["params"])
                or_tf = (yd.get("optimizer_results") or {}).get(tf) or {}
                if isinstance(or_tf.get("params"), dict):
                    base_params.update(or_tf["params"])
        except Exception as e:
            log.warning("  import baseline KO: %s", e)
            base_params = {}

        baseline_val = None
        if base_params:
            try:
                baseline_val = run_val(name, cfg, base_params, df_val, tf)
                log.info("  baseline VAL score=%.3f pnl=%.2f trades=%s",
                         baseline_val["score"], baseline_val["pnl"],
                         baseline_val["trades"])
            except Exception as e:
                log.warning("  baseline VAL KO: %s", e)

        best = optimize_wilder(name, cfg, df_is, df_oos, tf, args.trials, args.jobs)
        if best.get("error") or best.get("failed"):
            log.warning("  optim KO: %s", best.get("error") or best)
            campaigns.append({
                "strategy": name, "tf": tf, "status": "optim_failed",
                "detail": {k: best.get(k) for k in ("error", "failed")},
            })
            continue

        params = best.get("best_params") or {}
        oos = best.get("best_oos_score")
        ip.set_wilder_atr_adx(True)
        c = deepcopy(cfg)
        c.setdefault("indicators", {})["wilder_atr_adx"] = True
        try:
            val = run_val(name, c, params, df_val, tf)
        except Exception as e:
            log.warning("  VAL KO: %s", e)
            val = {"score": None, "trades": 0, "error": str(e)}

        adx_params = {k: v for k, v in params.items() if _is_adx_key(k)}
        log.info("  best OOS=%.4f VAL score=%s trades=%s ADX=%s",
                 float(oos or 0), val.get("score"), val.get("trades"), adx_params)

        persisted = False
        yaml_update: Dict[str, Any] = {}
        if not args.dry_run and params and oos is not None:
            try:
                save_optimizer_results(name, tf, params, float(oos))
                persisted = True
            except Exception as e:
                log.warning("  save_optimizer_results KO: %s", e)
            yaml_update = update_yaml_adx_defaults(name, params, dry_run=False)
        else:
            yaml_update = update_yaml_adx_defaults(name, params, dry_run=True)

        campaigns.append({
            "strategy": name,
            "tf": tf,
            "status": "ok",
            "adx_keys_in_space": best.get("adx_keys_in_space"),
            "oos_score": oos,
            "val": val,
            "baseline_val": baseline_val,
            "best_params": params,
            "adx_params": adx_params,
            "persisted_optimizer_results": persisted,
            "yaml_adx_update": yaml_update,
            "low_trades_warning": (val.get("trades") or 0) < MIN_VAL_TRADES,
        })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "wilder": True,
        "trials": args.trials,
        "dry_run": args.dry_run,
        "adx_scale": scales,
        "campaigns": campaigns,
        "elapsed_s": round(time.time() - t0, 1),
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                           encoding="utf-8")
    log.info("Rapport → %s (%.1fs)", report_path, report["elapsed_s"])

    # Synthèse stdout
    print("\n=== ML-10 synthèse ===")
    print(f"{'stratégie':<28} {'TF':>4} {'OOS':>8} {'VAL':>8} {'trades':>6}  ADX params")
    for c in campaigns:
        if c.get("status") != "ok":
            print(f"{c['strategy']:<28} {c['tf']:>4}  SKIP ({c.get('status')})")
            continue
        v = c.get("val") or {}
        print(f"{c['strategy']:<28} {c['tf']:>4} "
              f"{float(c.get('oos_score') or 0):>8.3f} "
              f"{float(v.get('score') or 0):>8.3f} "
              f"{int(v.get('trades') or 0):>6}  {c.get('adx_params')}")
    print("\nÉchelles ADX (mean): " +
          ", ".join(f"{s['tf']}={s['mean']:.1f}" for s in scales if "mean" in s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
