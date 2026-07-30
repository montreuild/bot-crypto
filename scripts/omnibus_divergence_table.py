#!/usr/bin/env python3
"""Table de divergence des générations omnibus — à lire AVANT de fusionner.

Le chantier §7 fusionne les routings omnibus en un seul catalogue de setups.
Sa règle : **aucune ligne de code avant que les divergences soient posées
noir sur blanc**, parce que c'est la seule étape du chantier ML qui change
des backtests. Ce script produit cette table mécaniquement, à partir des
``_DEFAULT_SETUPS`` réellement déclarés — pas d'une lecture à l'œil qui
raterait un champ.

Trois vues :

1. **Couverture** — quel setup existe dans quelle génération.
2. **Divergences de valeurs** — pour chaque setup présent dans plusieurs
   générations, les champs dont la valeur diffère. C'est ici que se joue la
   fusion : un champ identique partout est un défaut du catalogue, un champ
   qui diverge doit devenir un paramètre de preset.
3. **Mécanique de sélection** — signature de ``_evaluate_setup`` par
   génération : si elles diffèrent, le catalogue ne suffit pas et la fusion
   demande du code, pas seulement des valeurs.

Usage ::

    PYTHONPATH=. python scripts/omnibus_divergence_table.py

Cf. docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §7.
"""
import importlib
import inspect
import sys

MODULES = {
    "v7":          "app.strategies.opus_omnibus_v7",
    "v11":         "app.strategies.opus_omnibus_v11",
    "followsetup": "app.strategies.opus_omnibus_v11_followsetup",
}

# Champs dont la divergence est structurelle (change QUI peut tirer) vs
# cosmétique (change COMMENT on sort). Les deux comptent, mais pas au même
# titre dans une fusion.
GATING = ("enabled", "priority", "regime", "amp_min", "dir_min", "dir_max",
          "needs_exit_td_window", "needs_bearish_excess", "needs_rsi_below",
          "needs_adx_above")
SIZING = ("direction", "tp_mult", "sl_mult", "max_bars", "size_factor")


def load():
    out = {}
    for tag, path in MODULES.items():
        mod = importlib.import_module(path)
        setups = getattr(mod, "_DEFAULT_SETUPS", None)
        if setups is None:
            print(f"  ({tag} : pas de _DEFAULT_SETUPS)", file=sys.stderr)
            continue
        out[tag] = {s["name"]: dict(s) for s in setups}
    return out


def main() -> int:
    gens = load()
    tags = list(gens)
    names = []
    for t in tags:
        for n in gens[t]:
            if n not in names:
                names.append(n)

    print("=" * 78)
    print("1. COUVERTURE — quel setup existe dans quelle génération")
    print("=" * 78)
    hdr = "  ".join(f"{t:>12}" for t in tags)
    print(f"{'setup':<20} {hdr}")
    for n in names:
        cells = []
        for t in tags:
            s = gens[t].get(n)
            cells.append("—" if s is None
                         else ("actif" if s.get("enabled", True) else "désactivé"))
        print(f"{n:<20} " + "  ".join(f"{c:>12}" for c in cells))
    print(f"\nUnion : {len(names)} setups. "
          f"Par génération : " + ", ".join(f"{t}={len(gens[t])}" for t in tags))

    print("\n" + "=" * 78)
    print("2. DIVERGENCES DE VALEURS — ce qui doit devenir paramètre de preset")
    print("=" * 78)
    n_gate = n_size = 0
    for n in names:
        present = [t for t in tags if n in gens[t]]
        if len(present) < 2:
            continue
        rows = []
        for field in GATING + SIZING:
            vals = {t: gens[t][n].get(field) for t in present}
            if len({repr(v) for v in vals.values()}) > 1:
                kind = "SÉLECTION" if field in GATING else "sizing/sortie"
                n_gate += field in GATING
                n_size += field in SIZING
                rows.append((field, kind, vals))
        if rows:
            print(f"\n  {n}  (présent dans : {', '.join(present)})")
            for field, kind, vals in rows:
                detail = "  ".join(f"{t}={vals[t]!r}" for t in present)
                print(f"    {field:<22} [{kind:<13}] {detail}")
    print(f"\n  Total : {n_gate} divergences de SÉLECTION, "
          f"{n_size} de sizing/sortie.")

    print("\n" + "=" * 78)
    print("3. MÉCANIQUE DE SÉLECTION — le catalogue suffit-il ?")
    print("=" * 78)
    sigs = {}
    for t, path in MODULES.items():
        mod = importlib.import_module(path)
        fn = getattr(mod, "_evaluate_setup", None)
        sigs[t] = str(inspect.signature(fn)) if fn else "(absent)"
        print(f"  {t:<12} _evaluate_setup{sigs[t]}")
    distinct = len(set(sigs.values()))
    print(f"\n  {distinct} signature(s) distincte(s).")
    if distinct > 1:
        print("  → les générations ne sélectionnent pas sur les mêmes entrées :")
        print("    la fusion demande du CODE (unifier la mécanique), pas")
        print("    seulement des valeurs de catalogue.")
    else:
        print("  → mécanique identique : la fusion est un travail de VALEURS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
