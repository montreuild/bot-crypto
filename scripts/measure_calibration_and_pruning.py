#!/usr/bin/env python3
"""Calibration isotone et élagage de features apportent-ils quelque chose ?

Les deux sont actifs par défaut depuis V11 et coûtent 169 lignes
(``app/ml/backend/isotonic.py``) plus la logique ``kept_features`` du trainer.
Aucune mesure du dépôt ne comparait leur présence à leur absence.

**Piège à éviter — l'AUC ne peut pas juger la calibration.** L'AUC est
invariante par transformation monotone croissante ; la régression isotone EST
une transformation monotone croissante. Mesurer la calibration à l'AUC
répondrait donc « aucun effet » par construction, et ce non-résultat serait
pris pour un résultat. Le critère pertinent est l'**erreur de calibration** :
quand le modèle annonce 0.60, observe-t-on 60 % d'événements ? C'est ce qui
donne un sens aux seuils des setups (``amp_min: 0.50``).

Critères de décision, fixés AVANT la mesure pour ne pas les ajuster au
résultat :

  * élagage      — gain d'AUC < 0.005 sur les 3 TF ⇒ retirer le flag ;
  * calibration  — réduction d'ECE < 20 % ⇒ retirer ; sinon garder et le
                   figer dans la recette ;
  * si l'effet dépend du TF ⇒ rester un paramètre de recette, documenté.

**Limite connue du protocole — l'élagage n'est PAS mesurable ici.** Il retire
les features à gain nul *au cycle d'entraînement SUIVANT* ; ce script ne fait
qu'un ``fit()``, donc il n'y a pas de cycle suivant. Vérifié :
``kept_features`` vaut 324/437 que le flag soit à True ou False, et
``feature_cols`` reste à 437 dans les deux cas. La colonne ``n_feats`` et le
verdict d'élagage sont donc affichés pour information, mais ne tranchent
rien : conclure « à retirer » sur cette base serait prendre l'absence de
mesure pour une absence d'effet. Trancher demande un protocole walk-forward à
au moins deux retrains successifs.

Usage ::

    PYTHONPATH=. python scripts/measure_calibration_and_pruning.py
"""
import os
import shutil
import sys
import tempfile

import numpy as np

import app.strategies.opus_omnibus_v11 as v11
from app.core.candle_store import get_store
from app.ml.backend.features import build_features, multi_horizon_labels
from app.ml.backend.persistence import load_amp_dir_bundle
from app.ml.backend.predictor import predict_batch_raw
from app.ml.scoring import rank_auc

HOLDOUT = 1500
TFS = ("15m", "30m", "1h")
N_BINS = 10
BASE = {"warmup_bars": 750, "retrain_every": 10 ** 9, "log_training": False,
        "enable_hour_filter": False, "label_horizons": [1, 3, 6]}


def expected_calibration_error(y: np.ndarray, p: np.ndarray, n_bins: int = N_BINS):
    """ECE : |fréquence observée − probabilité annoncée|, moyennée par bin,
    pondérée par l'effectif. C'est la question « quand le modèle dit 0.60,
    observe-t-on 60 % ? », que l'AUC ne pose jamais.

    Retourne ``(ece, max_ecart, n_bins_utilisés)``.
    """
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    ece = 0.0
    worst = 0.0
    used = 0
    for b in range(n_bins):
        m = idx == b
        k = int(m.sum())
        if k == 0:
            continue
        gap = abs(float(y[m].mean()) - float(p[m].mean()))
        ece += gap * k / len(y)
        worst = max(worst, gap)
        used += 1
    return ece, worst, used


def train_and_measure(tf: str, params: dict) -> dict:
    """Entraîne V11 avec ``params`` et mesure AUC + ECE sur le holdout."""
    df = get_store().load_cached("BTC/USDC", tf).sort("time")
    n = len(df)
    train_df = df.slice(0, n - HOLDOUT)
    holdout_df = df.tail(HOLDOUT + 210)

    strat = v11.Strategy()
    strat.fit(train_df, params={"opus_omnibus_v11": params})
    if not strat.is_trained:
        return {}

    tmp = tempfile.mkdtemp(prefix="calib_")
    try:
        prefix = os.path.join(tmp, f"opus_omnibus_v11_{tf}")
        strat.save_model(prefix)
        bundle = load_amp_dir_bundle(prefix)
        feats = build_features(holdout_df)
        if bundle is None or feats is None:
            return {}
        close = feats["close"].to_numpy().astype(np.float64)
        y_amp, y_dir, k, _, _ = multi_horizon_labels(close, params["label_horizons"], 0.30)
        scored = feats.head(k)
        p_amp = predict_batch_raw(bundle["amp_model"], bundle.get("amp_cal"),
                                  bundle["features"], bundle["medians"], scored)
        p_dir = predict_batch_raw(bundle.get("dir_model"), bundle.get("dir_cal"),
                                  bundle["features"], bundle["medians"], scored)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    meta = strat.ml.train_meta.get(tf, {})
    ece_amp, worst_amp, _ = expected_calibration_error(y_amp, p_amp)
    return {
        "auc_amp": rank_auc(y_amp, p_amp),
        "auc_dir": rank_auc(y_dir, p_dir),
        "ece_amp": ece_amp,
        "ece_amp_worst": worst_amp,
        "n_features": len(meta.get("kept_features") or bundle.get("features") or []),
    }


def fmt(v, w=7, p=4):
    return f"{v:>{w}.{p}f}" if isinstance(v, float) else f"{'n/m':>{w}}"


GRID = [("T/T", True, True), ("T/F", True, False),
        ("F/T", False, True), ("F/F", False, False)]

print(f"# holdout={HOLDOUT} | labels={BASE['label_horizons']} | ECE sur {N_BINS} bins",
      file=sys.stderr)
print("=" * 96)
print("calibrate/prune × timeframe — AUC (que la calibration ne peut pas changer) "
      "et ECE (qu'elle vise)")
print("=" * 96)
print(f"{'TF':>4} {'cal/prune':>10} {'auc_amp':>9} {'auc_dir':>9} "
      f"{'ECE amp':>9} {'ECE max':>9} {'n_feats':>8}")

results = {}
for tf in TFS:
    for label, cal, prune in GRID:
        r = train_and_measure(tf, {**BASE, "calibrate": cal, "prune_features": prune})
        results[(tf, label)] = r
        if not r:
            print(f"{tf:>4} {label:>10}   (entraînement KO)")
            continue
        print(f"{tf:>4} {label:>10} {fmt(r['auc_amp'], 9)} {fmt(r['auc_dir'], 9)} "
              f"{fmt(r['ece_amp'], 9)} {fmt(r['ece_amp_worst'], 9)} {r['n_features']:>8}")
    print()

print("=" * 96)
print("VERDICTS (critères fixés avant la mesure)")
print("=" * 96)

# ── Élagage : gain d'AUC ────────────────────────────────────────────────────
gains = []
for tf in TFS:
    a, b = results.get((tf, "T/T"), {}), results.get((tf, "T/F"), {})
    if a.get("auc_amp") is not None and b.get("auc_amp") is not None:
        gains.append((tf, a["auc_amp"] - b["auc_amp"], a["n_features"], b["n_features"]))
print("\nÉLAGAGE (prune=True vs False, calibration constante) :")
for tf, g, na, nb in gains:
    print(f"  {tf:>4} : gain auc_amp {g:+.4f}   features {nb} -> {na}")
print("  !! NON CONCLUSIF : l'élagage agit au cycle d'entraînement SUIVANT et ce")
print("     protocole n'en fait qu'un. Un écart nul ici ne prouve rien —")
print("     il faut un walk-forward à >= 2 retrains pour trancher.")

# ── Calibration : réduction d'ECE ────────────────────────────────────────────
print("\nCALIBRATION (calibrate=True vs False, élagage constant) :")
reductions = []
for tf in TFS:
    a, b = results.get((tf, "T/T"), {}), results.get((tf, "F/T"), {})
    if a.get("ece_amp") is not None and b.get("ece_amp") is not None:
        red = (b["ece_amp"] - a["ece_amp"]) / b["ece_amp"] * 100 if b["ece_amp"] else 0.0
        reductions.append((tf, b["ece_amp"], a["ece_amp"], red,
                           (a["auc_amp"] or 0) - (b["auc_amp"] or 0)))
for tf, e0, e1, red, dauc in reductions:
    print(f"  {tf:>4} : ECE {e0:.4f} -> {e1:.4f}  ({red:+.1f} %)   "
          f"écart d'AUC {dauc:+.4f} (doit être ~0 : invariance monotone)")
helped = [tf for tf, _, _, r, _ in reductions if r >= 20.0]
hurt   = [tf for tf, _, _, r, _ in reductions if r < 0.0]
if hurt and helped:
    print(f"  => EFFET DÉPENDANT DU TF : aide sur {helped}, DÉGRADE sur {hurt}.")
    print("     Critère n°3 : garder en PARAMÈTRE de recette, et le désactiver")
    print("     là où il nuit — un p_event mal calibré fausse directement les")
    print("     seuils des setups (amp_min: 0.50 ne veut plus dire 50 %).")
elif not helped:
    print("  => réduction d'ECE < 20 % partout : RETIRER la calibration")
else:
    print("  => la calibration réduit réellement l'erreur partout : GARDER")
if reductions and any(abs(d) > 0.01 for *_, d in reductions):
    print("  !! écart d'AUC non nul : la calibration n'est PAS monotone ici —"
          " à investiguer avant toute conclusion")
