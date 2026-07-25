#!/usr/bin/env python3
"""Ampleur du réalignement des variantes « sans ML » sur leur routing.

Les fichiers ``*_no_ml`` étaient des forks autonomes de leur jumeau ML. Ils
avaient dérivé : sur les 9 fonctions communes à ``opus_omnibus_v11`` et
``opus_omnibus_v11_no_ml``, 8 divergeaient — ``needs_bearish_excess`` et
``needs_rsi_below`` perdues, test de régime fusionné dans la garde ``exit_td``.

Les transformer en presets du routing partagé **corrige cette dérive**, donc
**change leur comportement**. Ce n'est pas une refactorisation neutre et ne
doit pas être présenté comme telle : ce script mesure l'ampleur du changement
au lieu de l'affirmer.

Usage — l'original doit être fourni comme fichier de sauvegarde ::

    PYTHONPATH=. python scripts/compare_no_ml_preset.py <chemin_original.py>
"""
import importlib.util
import sys

from app.core.candle_store import get_store
from app.core.indicators_precompute import precompute_df

import app.strategies.opus_omnibus_v11_no_ml as preset  # isort: skip

TF = "1h"
N_WINDOW = 600
DECISION_KEYS = ("side", "score", "setup", "regime", "p_event", "p_up",
                 "tp_mult", "sl_mult", "size_factor")
PARAMS = {"enable_hour_filter": False}


def load_original(path: str):
    spec = importlib.util.spec_from_file_location("_orig_no_ml", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def decision(sig):
    if not isinstance(sig, dict):
        return {"__err__": str(sig)}
    ind = sig.get("indicators") or {}
    return {k: (round(v, 6) if isinstance(v, float) else v)
            for k, v in ((k, sig.get(k, ind.get(k))) for k in DECISION_KEYS)}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    orig = load_original(sys.argv[1])

    df = precompute_df(get_store().load_cached("BTC/USDC", TF).sort("time"))
    ends = list(range(len(df) - 30000, len(df), 250))

    same = fired_a = fired_b = 0
    changed_side = []
    for e in ends:
        win = df.slice(max(0, e - N_WINDOW), min(N_WINDOW, e))
        a = decision(orig.Strategy().score(win, params={orig.Strategy.name: PARAMS}))
        b = decision(preset.Strategy().score(win, params={preset.Strategy.name: PARAMS}))
        same += (a == b)
        sa = a.get("side") not in (None, "none")
        sb = b.get("side") not in (None, "none")
        fired_a += sa
        fired_b += sb
        if a.get("side") != b.get("side") or a.get("setup") != b.get("setup"):
            changed_side.append((e, a.get("side"), a.get("setup"),
                                 b.get("side"), b.get("setup")))

    n = len(ends)
    print("=" * 78)
    print(f"Barres de décision : {n}   décisions identiques : {same} "
          f"({100 * same / n:.1f} %)")
    print(f"Signaux émis — fork original : {fired_a}   preset : {fired_b}")
    print(f"Décisions dont le SIDE ou le SETUP change : {len(changed_side)}")
    print("=" * 78)
    for e, sa, ua, sb, ub in changed_side[:15]:
        print(f"  @{e}: fork {sa}/{ua}  ->  preset {sb}/{ub}")
    if len(changed_side) > 15:
        print(f"  … et {len(changed_side) - 15} autres")
    print("\nLecture : un écart est ATTENDU — le fork avait perdu des conditions")
    print("de setup que le routing partagé rétablit. Ce chiffre mesure ce que la")
    print("bascule change, il ne valide ni n'invalide la bascule à lui seul.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
