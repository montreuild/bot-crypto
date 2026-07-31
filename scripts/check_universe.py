#!/usr/bin/env python3
"""Valide un univers d'instruments contre son provider de données (G2).

Un fichier ``data/universe/*.yaml`` est un instantané écrit à la main : les
indices sont révisés trimestriellement, les sociétés fusionnent, les
mnémoniques changent (Faurecia → Forvia, Orpea → Emeis…). Un ticker mort ne
lève aucune erreur — il produit juste, silencieusement, un symbole qui ne
score jamais. Ce script rend ce silence visible.

Pour chaque membre : une requête OHLCV courte, puis un verdict.

    python scripts/check_universe.py sbf120
    python scripts/check_universe.py sbf120 --tf 1d --limit 10 --json rapport.json

Sortie : ``OK`` (données reçues), ``VIDE`` (ticker probablement radié ou
renommé) ou ``ERREUR`` (échec réseau/provider). Code de retour 1 si au moins
un membre n'est pas OK — utilisable en pré-vol avant d'activer l'univers.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import load_config  # noqa: E402
from app.core.universe import universe_members, universe_path  # noqa: E402


def _build_provider(cfg: dict, name: str):
    from app.core.provider_router import _instantiate
    return _instantiate(name, cfg)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("universe", help="nom de l'univers (ex. sbf120)")
    parser.add_argument("--provider", default="yfinance",
                        help="provider de données à interroger (défaut : yfinance)")
    parser.add_argument("--tf", default="1d", help="timeframe de test (défaut : 1d)")
    parser.add_argument("--limit", type=int, default=5,
                        help="bougies demandées par symbole (défaut : 5)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--json", dest="json_out", default=None,
                        help="écrit le rapport détaillé dans ce fichier")
    args = parser.parse_args()

    members = universe_members(args.universe)
    if not members:
        print(f"✗ Univers '{args.universe}' vide ou introuvable "
              f"({universe_path(args.universe)})")
        return 1

    cfg = load_config(args.config)
    try:
        provider = _build_provider(cfg, args.provider)
    except Exception as e:
        print(f"✗ Provider '{args.provider}' indisponible : {e}")
        return 1

    print(f"Univers '{args.universe}' — {len(members)} membres, "
          f"provider={args.provider}, tf={args.tf}\n")

    rows, failures = [], 0
    for idx, member in enumerate(members, 1):
        symbol = member["symbol"]
        status, detail = "OK", ""
        try:
            bars = provider.fetch_ohlcv(symbol, args.tf, limit=args.limit) or []
            if not bars:
                status, detail = "VIDE", "aucune bougie retournée"
                failures += 1
            else:
                last = bars[-1]
                stamp = datetime.fromtimestamp(last[0] / 1000, tz=timezone.utc)
                detail = f"{len(bars)} bougies, dernier close {last[4]:.4f} " \
                         f"le {stamp:%Y-%m-%d}"
        except Exception as e:
            status, detail = "ERREUR", str(e)[:120]
            failures += 1
        mark = {"OK": "✓", "VIDE": "∅", "ERREUR": "✗"}[status]
        print(f"  {mark} [{idx:3d}/{len(members)}] {symbol:<12} "
              f"{member.get('name', ''):<28} {detail}")
        rows.append({"symbol": symbol, "name": member.get("name", ""),
                     "status": status, "detail": detail})

    print(f"\n{len(members) - failures}/{len(members)} membres exploitables.")
    if failures:
        print(f"⚠ {failures} à corriger dans {universe_path(args.universe)} "
              f"(radiés, renommés, ou mnémonique erroné).")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({"universe": args.universe, "provider": args.provider,
                       "checked_at": datetime.now(timezone.utc).isoformat(),
                       "results": rows}, fh, ensure_ascii=False, indent=2)
        print(f"Rapport écrit : {args.json_out}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
