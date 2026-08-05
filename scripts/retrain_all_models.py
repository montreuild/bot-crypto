#!/usr/bin/env python3
"""Réentraîne et publie (gaté) tous les modèles ML connus sur BTC/USDC.

Parcourt les stratégies qui possèdent un ``fit()`` ML productif × timeframes
avec assez d'historique local, et appelle ``train_and_publish(..., publish=True)``.

Usage ::

    PYTHONPATH=. python scripts/retrain_all_models.py
    PYTHONPATH=. python scripts/retrain_all_models.py --dry-run
    PYTHONPATH=. python scripts/retrain_all_models.py --only opus_omnibus_v11 --tfs 1h,4h
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("retrain_all")

# Stratégies ML productives (registre / fit) × TF standards.
DEFAULT_STRATEGIES = [
    "opus_omnibus_v7",
    "opus_omnibus_v10_retrained",
    "opus_omnibus_v11",
    "opus_omnibus_v11_followsetup",
    "opus_omnibus_v12",
    "opus_stat_retrained_v4",
    "ml_dynamic_threshold",
    "scoring_statistique_opus_v4",
    "scoring_statistique_opus_v5",
]
DEFAULT_TFS = ["15m", "30m", "1h", "4h"]
SYMBOL = "BTC/USDC"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="entraîne + score holdout, n'écrit rien au registre")
    ap.add_argument("--only", default=None,
                    help="une seule stratégie (sinon toutes)")
    ap.add_argument("--tfs", default=",".join(DEFAULT_TFS),
                    help="liste de TF séparés par des virgules")
    ap.add_argument("--symbol", default=SYMBOL)
    ap.add_argument("--base-dir", default="models")
    args = ap.parse_args()

    strategies = [args.only] if args.only else list(DEFAULT_STRATEGIES)
    tfs = [t.strip() for t in args.tfs.split(",") if t.strip()]
    publish = not args.dry_run

    from app.ml.train_runner import train_and_publish

    results = []
    n = len(strategies) * len(tfs)
    i = 0
    t0 = time.time()
    for strat in strategies:
        for tf in tfs:
            i += 1
            log.info("[%d/%d] %s %s/%s publish=%s", i, n, strat, args.symbol, tf, publish)
            try:
                out = train_and_publish(
                    strat, args.symbol, tf,
                    publish=publish,
                    base_dir=args.base_dir,
                    source="retrain_all",
                )
            except Exception as e:
                out = {"decision": "failed", "reason": f"exception: {e}"}
                log.exception("échec %s/%s", strat, tf)
            decision = out.get("decision") or out.get("status") or "?"
            reason = out.get("reason") or ""
            log.info("  → %s %s", decision, reason)
            results.append({
                "strategy": strat, "tf": tf, "decision": decision,
                "reason": reason, "detail": {
                    k: out[k] for k in ("candidate", "incumbent", "n_bars",
                                        "incumbent_version", "version_id")
                    if k in out
                },
            })

    report = {
        "symbol": args.symbol,
        "publish": publish,
        "elapsed_s": round(time.time() - t0, 1),
        "results": results,
    }
    out_path = ROOT / "research" / "retrain_all_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                        encoding="utf-8")
    log.info("Rapport → %s (%.1fs)", out_path, report["elapsed_s"])

    ok = sum(1 for r in results if str(r["decision"]).lower() in
             ("promoted", "kept", "dry_run_would_promote", "dry_run_would_keep",
              "published", "no_change", "skipped_no_regression"))
    fail = sum(1 for r in results if "fail" in str(r["decision"]).lower()
               or "error" in str(r["decision"]).lower())
    log.info("Synthèse : %d jobs, ~ok=%d fail=%d", len(results), ok, fail)
    print(json.dumps(
        {"n": len(results), "elapsed_s": report["elapsed_s"],
         "decisions": {r["strategy"] + "/" + r["tf"]: r["decision"] for r in results}},
        indent=2, ensure_ascii=False,
    ))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
