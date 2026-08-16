#!/usr/bin/env python3
"""BT-05/STRAT-03 — Audit de la couverture de l'espace de recherche.

Pour chaque stratégie enregistrée (``app.engine.registry.get_param_spaces``),
calcule la cardinalité de son ``param_space`` (produit du nombre de choix par
paramètre) et la compare à ``optimizer.n_trials`` (config.yaml). Avertit si la
couverture (``n_trials / cardinalité``) est inférieure à 1e-4 : l'optimiseur
n'explore alors qu'une fraction infinitésimale de l'espace déclaré, signe que
soit ``n_trials`` est trop bas, soit ``param_space`` est surdimensionné.

Mesure seule — ne modifie ni la config ni le comportement de l'optimiseur.

Usage :
    python scripts/audit_param_space.py [--config config.yaml] [--strict]

    --strict : sort avec un code de retour non-nul si au moins une stratégie
               est sous le seuil de couverture (utile en CI).
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import load_config
from app.engine.registry import get_param_spaces

COVERAGE_WARN_THRESHOLD = 1e-4


def cardinality(param_space: dict) -> int:
    """Produit du nombre de choix par paramètre (grille discrète — cf. registry.py)."""
    if not param_space:
        return 0
    return math.prod(len(choices) for choices in param_space.values())


def audit(cfg: dict) -> list:
    """Retourne une liste de dicts {name, n_params, cardinality, n_trials,
    coverage, warn} triée par couverture croissante (pire en premier).

    ``n_trials`` est le budget EFFECTIF de chaque stratégie
    (``app/engine/opt_budget.py``), pas la valeur brute de config : depuis que
    le budget est proportionné à l'espace, afficher 40 partout décrirait une
    optimisation qui n'a plus lieu.
    """
    from app.engine.opt_budget import effective_n_trials

    base = int(cfg.get("optimizer", {}).get("n_trials", 50))
    rows = []
    for name, space in sorted(get_param_spaces().items()):
        card = cardinality(space)
        n_trials, detail = effective_n_trials(space, base, cfg)
        coverage = (n_trials / card) if card > 0 else 1.0
        rows.append({
            "name": name,
            "n_params": len(space),
            "cardinality": card,
            "n_trials": n_trials,
            "base": base,
            "raison": detail.get("raison", ""),
            "coverage": coverage,
            "warn": coverage < COVERAGE_WARN_THRESHOLD,
        })
    rows.sort(key=lambda r: r["coverage"])
    return rows


def print_report(rows: list) -> None:
    header = (f"{'stratégie':<32} {'#params':>7} {'cardinalité':>14} "
              f"{'demandé':>8} {'effectif':>9} {'couverture':>11}")
    print(header)
    print("-" * len(header))
    for r in rows:
        flag = " ⚠" if r["warn"] else ""
        print(f"{r['name']:<32} {r['n_params']:>7} {r['cardinality']:>14,} "
              f"{r.get('base', r['n_trials']):>8} {r['n_trials']:>9} "
              f"{r['coverage']:>10.2%}{flag}")
    n_warn = sum(1 for r in rows if r["warn"])
    print("-" * len(header))
    print(f"{len(rows)} stratégies auditées, {n_warn} sous le seuil de couverture "
          f"({COVERAGE_WARN_THRESHOLD:.0%}).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--strict", action="store_true",
                        help="Code de retour non-nul si une stratégie est sous le seuil.")
    args = parser.parse_args()

    cfg = load_config(args.config)
    rows = audit(cfg)
    print_report(rows)

    if args.strict and any(r["warn"] for r in rows):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
