#!/usr/bin/env python3
"""Le pack V4 legacy gagne-t-il encore contre un ré-entraînement ?

Comparaison LÉGITIME : les deux artefacts sont scorés sur le MÊME holdout,
avec la MÊME convention de labels (celle déclarée par la recette V4 :
label_horizons=[1], amp_top_pct=0.30). Le candidat est entraîné strictement
avant le holdout — aucune fuite.

C'est exactement ce que fait ``policy.decide_gate`` en production ; ce script
l'exécute hors gate pour trancher une question d'architecture : le pack figé
de mai 2026 mérite-t-il encore le code qui le maintient en vie ?

**AUC par régime.** L'AUC directionnelle globale masque le seul endroit où V4
avait montré un gain : le régime. Un modèle à 0.48 global peut valoir 0.58 en
Trend Down et 0.44 ailleurs — ce qui justifierait de le garder pour ce régime.
Le script ventile donc ``auc_dir`` par régime (Range / Trend Up / Trend Down /
Choppy, classification ``features.classify_regime``) pour les DEUX artefacts.
Sans cette ventilation, la comparaison globale ne suffirait pas à conclure.

**Robustesse à la fenêtre** (``--sweep``). Le candidat est entraîné par défaut
sur tout l'historique disponible moins le holdout (~50 000 barres). Si son
avantage ne tenait qu'à ce volume, la conclusion serait un artefact du
protocole ; ``--sweep`` rejoue donc le même holdout contre des fenêtres de
5 000 à 40 000 barres.

Usage ::

    PYTHONPATH=. python scripts/compare_legacy_vs_retrained.py [--sweep]

Cf. docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §1.5.
"""
import os
import shutil
import sys
import tempfile

import numpy as np

import app.ml.model_registry as registry
import app.ml.policy as policy
import app.strategies.opus_stat_retrained_v4 as retr
from app.core.candle_store import get_store
from app.ml.backend.features import (
    REGIME_LABELS,
    build_features,
    regime_history,
    single_horizon_labels,
)
from app.ml.backend.persistence import load_amp_dir_bundle
from app.ml.backend.predictor import predict_batch_raw
from app.ml.scoring import rank_auc

HOLDOUT = 1500
TFS = ("15m", "30m", "1h")
ADX_THRESHOLD = 20.0
DI_RESCUE = 10.0
MIN_REGIME_N = 30          # sous ce seuil l'AUC d'un régime n'est pas interprétable


def dir_auc_by_regime(path_prefix: str, holdout_df, amp_top_pct: float) -> dict:
    """AUC directionnelle globale + ventilée par régime pour UN artefact.

    Reproduit exactement le chemin de ``score_amp_dir_bundle`` (mêmes features,
    mêmes labels, mêmes médianes d'imputation) puis ventile par régime au lieu
    d'agréger. Retourne ``{}`` si l'artefact est illisible.
    """
    bundle = load_amp_dir_bundle(path_prefix)
    if bundle is None or bundle.get("dir_model") is None or not bundle.get("features"):
        return {}
    feats = build_features(holdout_df)
    if feats is None or len(feats) < 50:
        return {}

    close = feats["close"].to_numpy().astype(np.float64)
    _, y_dir, n, _ = single_horizon_labels(close, amp_top_pct)
    if n < 30:
        return {}

    scored = feats.head(n)
    # V4 legacy : amp/dir peuvent porter des features/médianes distinctes.
    dfeat = bundle.get("dir_features") if bundle.get("dir_features") is not None else bundle["features"]
    dmed = bundle.get("dir_medians") if bundle.get("dir_medians") is not None else bundle["medians"]
    scores = predict_batch_raw(bundle.get("dir_model"), bundle.get("dir_cal"),
                               dfeat, dmed, scored)
    if scores is None:
        return {}

    regimes = np.asarray(
        regime_history(scored, n, ADX_THRESHOLD, DI_RESCUE)[0], dtype=int)
    out = {"global": rank_auc(y_dir, scores), "n": int(n), "by_regime": {},
           "y": y_dir, "scores": scores, "regimes": regimes}
    for code, label in REGIME_LABELS.items():
        m = regimes == code
        k = int(m.sum())
        out["by_regime"][label] = {
            "n": k,
            "auc": rank_auc(y_dir[m], scores[m]) if k >= MIN_REGIME_N else None,
        }
    return out


def bootstrap_delta(y, s_leg, s_new, n_boot: int = 2000, seed: int = 0):
    """IC 95 % bootstrap de (AUC_legacy − AUC_retrain) sur un sous-échantillon.

    Ré-échantillonne les mêmes indices pour les deux modèles (comparaison
    APPARIÉE : les deux voient exactement les mêmes barres à chaque tirage),
    ce qui isole l'écart entre modèles du bruit d'échantillonnage commun.
    Un IC qui contient 0 = écart indiscernable du bruit.
    """
    rng = np.random.RandomState(seed)
    k = len(y)
    if k < MIN_REGIME_N:
        return None
    deltas = []
    for _ in range(n_boot):
        idx = rng.randint(0, k, k)
        a = rank_auc(y[idx], s_leg[idx])
        b = rank_auc(y[idx], s_new[idx])
        if a is not None and b is not None:
            deltas.append(a - b)
    if len(deltas) < n_boot // 2:
        return None
    d = np.asarray(deltas)
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def fmt(v, w=6):
    return f"{v:>{w}.3f}" if isinstance(v, float) else f"{'n/m':>{w}}"


# La convention de labels vient de la RECETTE (gate_spec), pas d'un défaut :
# scorer le pack V4 contre des labels multi-horizon qu'il n'a jamais appris
# fausserait la comparaison dans son sens défavorable.
gc_ = policy.GateConfig.from_params(policy.recipe_gate_defaults(retr.Strategy()))
print(f"# recette : label_horizons={gc_.label_horizons} amp_top_pct={gc_.amp_top_pct} "
      f"metric={gc_.metric} | holdout={HOLDOUT} | régime : ADX>{ADX_THRESHOLD:.0f}, "
      f"di_rescue={DI_RESCUE:.0f}\n", file=sys.stderr)

print("=" * 78)
print("1. GLOBAL — le gate promouvrait-il le candidat ?")
print("=" * 78)
print(f"{'TF':>4} {'n_train':>8} {'LEGACY amp':>11} {'RETRAIN amp':>12} "
      f"{'LEGACY dir':>11} {'RETRAIN dir':>12}  verdict (métrique de gate)")

per_tf = {}
wins = 0
for tf in TFS:
    df = get_store().load_cached("BTC/USDC", tf).sort("time")
    n = len(df)
    holdout_df = df.tail(HOLDOUT + 210)
    train_df = df.slice(0, n - HOLDOUT)

    # ── Sortant : le pack legacy tel qu'il est résolu en production
    art = registry.resolve("BTC/USDC", tf, "opus_stat_pretrained_v4", pin="legacy")
    leg = policy.score_holdout(art.path_prefix, holdout_df,
                               strategy="opus_stat_pretrained_v4", gate_cfg=gc_)
    leg_reg = dir_auc_by_regime(art.path_prefix, holdout_df, gc_.amp_top_pct)

    # ── Candidat : même recette, ré-entraînée sur tout l'historique disponible
    strat = retr.Strategy()
    p = {"warmup_bars": 750, "retrain_every": 10 ** 9}
    strat.fit(train_df, params={"opus_stat_retrained_v4": p})
    tmp = tempfile.mkdtemp(prefix="cmp_")
    try:
        prefix = os.path.join(tmp, f"opus_stat_retrained_v4_{tf}")
        strat.save_model(prefix)
        new = policy.score_holdout(prefix, holdout_df, strategy=strat, gate_cfg=gc_, params=p)
        new_reg = dir_auc_by_regime(prefix, holdout_df, gc_.amp_top_pct)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    la, na = leg.get("auc_amp"), new.get("auc_amp")
    ok = isinstance(la, float) and isinstance(na, float) and na >= la - gc_.epsilon
    wins += bool(ok)
    print(f"{tf:>4} {len(train_df):>8} {fmt(la, 11)} {fmt(na, 12)} "
          f"{fmt(leg.get('auc_dir'), 11)} {fmt(new.get('auc_dir'), 12)}  "
          f"{'RETRAIN promu' if ok else 'LEGACY tient'}")
    per_tf[tf] = (leg_reg, new_reg)

print(f"\nLe gate promouvrait le ré-entraînement sur {wins}/{len(TFS)} timeframes.")

print("\n" + "=" * 78)
print("2. AUC DIRECTIONNELLE PAR RÉGIME — le pack V4 sauve-t-il un régime ?")
print("=" * 78)
print(f"{'TF':>4} {'régime':<12} {'n':>6} {'LEGACY dir':>11} {'RETRAIN dir':>12} "
      f"{'écart':>8}   IC95 % de (legacy − retrain)")

survivors = []
for tf in TFS:
    leg_reg, new_reg = per_tf[tf]
    if not leg_reg or not new_reg:
        print(f"{tf:>4} artefact illisible")
        continue
    for code, label in REGIME_LABELS.items():
        lr = leg_reg["by_regime"].get(label, {})
        nr = new_reg["by_regime"].get(label, {})
        la, na = lr.get("auc"), nr.get("auc")
        if not (isinstance(la, float) and isinstance(na, float)):
            continue
        m = leg_reg["regimes"] == code
        ci = bootstrap_delta(leg_reg["y"][m], leg_reg["scores"][m], new_reg["scores"][m])
        if ci is None:
            verdict = "—"
        elif ci[0] > 0:
            verdict = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]  LEGACY meilleur (significatif)"
            survivors.append((tf, label, la, na, lr.get("n"), ci))
        elif ci[1] < 0:
            verdict = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]  RETRAIN meilleur (significatif)"
        else:
            verdict = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]  indiscernable du bruit"
        print(f"{tf:>4} {label:<12} {lr.get('n', 0):>6} {fmt(la, 11)} {fmt(na, 12)} "
              f"{na - la:>+8.3f}   {verdict}")

print("\n" + "-" * 78)
if survivors:
    print("Régimes où le pack figé est SIGNIFICATIVEMENT meilleur :")
    for tf, label, la, na, k, ci in survivors:
        util = "et utile (>0.52)" if la > 0.52 else "MAIS sous 0.52 : inutile dans l'absolu"
        print(f"  - {tf} / {label} : legacy {la:.3f} vs retrain {na:.3f} (n={k}) — {util}")
    print("→ à discuter avant de retirer le pack.")
else:
    print("AUCUN régime où l'avantage du pack figé survit au test d'échantillonnage.")
    print("→ rien à sauver, y compris pour p_dir.")
print(f"(n/m = moins de {MIN_REGIME_N} barres : AUC non interprétable ; "
      f"IC95 % = bootstrap apparié, 2000 tirages)")


# ─────────────────────────────────────────────────────────────────────────────
#  3. Robustesse à la fenêtre d'entraînement
# ─────────────────────────────────────────────────────────────────────────────
# Le candidat de §1 est entraîné sur TOUT l'historique disponible (~50 000
# barres). Si son avantage ne tenait qu'à ce volume, la conclusion serait un
# artefact du protocole et non une propriété du pack figé. On rejoue donc le
# même holdout contre des fenêtres croissantes : le verdict doit tenir bien
# avant d'avoir consommé tout l'historique.
if "--sweep" in sys.argv:
    print("\n" + "=" * 78)
    print("3. ROBUSTESSE À LA FENÊTRE — l'avantage tient-il sur moins de données ?")
    print("=" * 78)
    WINDOWS = [5000, 10000, 20000, 40000, None]   # None = tout l'historique
    print(f"{'TF':>4} {'fenêtre':>9} {'n_train':>8} {'LEGACY amp':>11} "
          f"{'RETRAIN amp':>12} {'écart':>8}  gate")
    for tf in TFS:
        df = get_store().load_cached("BTC/USDC", tf).sort("time")
        n = len(df)
        holdout_df = df.tail(HOLDOUT + 210)
        pre = df.slice(0, n - HOLDOUT)
        art = registry.resolve("BTC/USDC", tf, "opus_stat_pretrained_v4", pin="legacy")
        la = policy.score_holdout(art.path_prefix, holdout_df,
                                  strategy="opus_stat_pretrained_v4",
                                  gate_cfg=gc_).get("auc_amp")
        for w in WINDOWS:
            train_df = pre.tail(w) if w else pre
            if len(train_df) < gc_.min_window_bars:
                continue
            s = retr.Strategy()
            p = {"warmup_bars": 750, "retrain_every": 10 ** 9}
            s.fit(train_df, params={"opus_stat_retrained_v4": p})
            tmp = tempfile.mkdtemp(prefix="sweep_")
            try:
                pre_ = os.path.join(tmp, f"opus_stat_retrained_v4_{tf}")
                s.save_model(pre_)
                na = policy.score_holdout(pre_, holdout_df, strategy=s,
                                          gate_cfg=gc_, params=p).get("auc_amp")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            ok = isinstance(la, float) and isinstance(na, float) and na >= la - gc_.epsilon
            d = f"{na - la:+.3f}" if isinstance(la, float) and isinstance(na, float) else "n/m"
            print(f"{tf:>4} {str(w or 'tout'):>9} {len(train_df):>8} {fmt(la, 11)} "
                  f"{fmt(na, 12)} {d:>8}  {'promu' if ok else 'REJETÉ'}")
        print()
else:
    print("\n(ajouter --sweep pour la robustesse à la fenêtre d'entraînement)")
