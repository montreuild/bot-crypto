#!/usr/bin/env python3
"""CLI d'entraînement ML committé et reproductible (ML-02 §3.3, tâche E3).

Wrapper argparse autour de ``app.ml.train_runner`` — logique réutilisable
sans CLI (tests, page « Modèles » de l'UI à venir).

Exemples ::

    # Dry-run : entraîne, compare au sortant publié, n'écrit rien.
    python -m scripts.train_model --strategy opus_omnibus_v11 --symbol BTC/USDC --tf 1h

    # Publication réelle (gate de promotion, même chemin que le live).
    python -m scripts.train_model --strategy opus_omnibus_v11 --symbol BTC/USDC --tf 1h --publish

    # Entraînement sur l'historique tel qu'il était à une date donnée
    # (reproduit ce qu'un modèle live aurait vu).
    python -m scripts.train_model --strategy opus_omnibus_v11 --symbol BTC/USDC --tf 1h \\
        --as-of 2026-06-01T00:00:00 --window-bars 40000

    # Window sweep : compare 10k/20k/40k barres sur un holdout commun,
    # publie uniquement le meilleur (gaté contre le sortant).
    python -m scripts.train_model --strategy opus_omnibus_v11 --symbol BTC/USDC --tf 1h \\
        --windows 10000,20000,40000 --publish-best
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(message)s")


def _parse_params(raw: str) -> dict:
    if not raw:
        return {}
    return json.loads(raw)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strategy", required=True, help="ex: opus_omnibus_v11")
    parser.add_argument("--symbol", default="BTC/USDC")
    parser.add_argument("--tf", required=True, help="ex: 1h")
    parser.add_argument("--as-of", default=None,
                        help="ISO (YYYY-MM-DDTHH:MM:SS) — n'utilise que l'historique jusqu'à cette date")
    parser.add_argument("--window-bars", type=int, default=None,
                        help="fenêtre d'entraînement (barres) — défaut : tout l'historique disponible")
    parser.add_argument("--windows", default=None,
                        help="ex: 10000,20000,40000 — déclenche un window sweep sur holdout commun")
    parser.add_argument("--params", default=None,
                        help='JSON de params stratégie, ex: \'{"n_estimators": 500}\'')
    parser.add_argument("--publish", action="store_true",
                        help="publie réellement (gate de promotion) — sans ce flag : dry-run, rien n'est écrit")
    parser.add_argument("--publish-best", action="store_true",
                        help="(--windows uniquement) publie le meilleur candidat du sweep, gaté")
    parser.add_argument("--base-dir", default="models")
    args = parser.parse_args()

    from app.ml.train_runner import train_and_publish, window_sweep

    params = _parse_params(args.params)

    if args.windows:
        windows = [int(w) for w in args.windows.split(",") if w.strip()]
        result = window_sweep(args.strategy, args.symbol, args.tf, windows,
                              as_of=args.as_of, params=params,
                              publish_best=args.publish_best, base_dir=args.base_dir)
    else:
        result = train_and_publish(args.strategy, args.symbol, args.tf,
                                   as_of=args.as_of, window_bars=args.window_bars,
                                   params=params, publish=args.publish,
                                   base_dir=args.base_dir)

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
