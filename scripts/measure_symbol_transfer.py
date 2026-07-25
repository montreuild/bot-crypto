#!/usr/bin/env python3
"""La dimension **symbole** des modèles vaut-elle son coût ?

Le registre range les artefacts par ``(symbole, TF, recette)``, mais le
trainer live n'entraîne QUE sur BTC (``MLStrategyTrainer._resolve_symbol``
préfère systématiquement le symbole contenant « BTC ») pendant que le
pipeline de signaux score CE modèle sur TOUS les symboles du scanner
(``signal_pipeline.collect`` → ``strategy.score(..., symbol=symbol)``, boucle
sur ``scanner.get_symbols()`` — BTC/USDC, ETH/USDC, XRP/USDC en config).

La dimension est donc portée à moitié : présente sur disque, écrasée en vol.
Deux issues cohérentes — l'exploiter (un modèle par symbole) ou la retirer
(un modèle par recette, assumé cross-symbole). Ce script mesure laquelle des
deux a un fondement, au lieu de trancher sur l'esthétique.

**Protocole.** Pour chaque TF, deux candidats entraînés avec la MÊME recette
et les MÊMES paramètres, l'un sur BTC, l'autre sur ETH, tous deux strictement
avant le holdout. Les deux sont ensuite scorés sur le holdout de CHAQUE
symbole tradé. La matrice qui en sort répond directement :

Le holdout est découpé sur une **date de coupure commune**, pas sur un
index. Découper chaque symbole à ``len - 1500`` donnerait des périodes
d'évaluation différentes par symbole : les séries n'ont ni la même longueur
ni la même densité (XRP/USDC 1h : 2 220 barres pour 7 300 heures, 30 % de
couverture). Sur cette version indexée, le holdout XRP 1h démarrait le
2025-12-10 alors que l'entraînement BTC allait jusqu'au 2026-05-03 — cinq
mois de fuite temporelle dans la colonne XRP. La coupure commune est la plus
tardive des coupures des symboles d'entraînement : aucun modèle ne voit une
barre postérieure au début d'un holdout, quel que soit le symbole évalué.

* diagonale ≫ hors-diagonale → le modèle est spécifique au symbole, la
  dimension doit être exploitée ;
* diagonale ≈ hors-diagonale → le modèle capture un signal cross-symbole, la
  dimension est du rangement sans effet et peut être retirée.

L'écart est accompagné d'un **IC 95 % bootstrap apparié** (mêmes barres
tirées pour les deux modèles) : sans lui, un écart de 0.01 d'AUC sur 1 500
barres n'est pas distinguable du bruit d'échantillonnage.

XRP n'apparaît qu'en colonne : son historique (~2 200 barres 1h) ne couvre
même pas le ``warmup_bars`` + holdout nécessaire pour entraîner. C'est un
résultat en soi — un tiers des symboles tradés ne PEUT pas avoir de modèle
propre.

Usage ::

    PYTHONPATH=. python scripts/measure_symbol_transfer.py [--tf 1h]

Cf. docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §12, décision 11.
"""
import os
import shutil
import sys
import tempfile

import numpy as np
import polars as pl

import app.ml.policy as policy
import app.strategies.opus_omnibus_v11 as v11
from app.core.candle_store import get_store
from app.ml.backend.features import (
    build_features,
    multi_horizon_labels,
    single_horizon_labels,
)
from app.ml.backend.persistence import load_amp_dir_bundle
from app.ml.backend.predictor import predict_batch_raw
from app.ml.scoring import rank_auc

HOLDOUT = 1500
FEATURE_WARMUP = 210          # barres consommées par build_features avant la 1re ligne
STRATEGY = "opus_omnibus_v11"
MIN_N = 100                   # sous ce seuil l'AUC d'un holdout n'est pas interprétable
MIN_TRAIN_BARS = 8000         # en dessous, un symbole ne peut servir qu'en colonne

#: Corrélation de rendements au-delà de laquelle deux actifs sont considérés
#: comme redondants. Le verdict de ce script n'a de portée que sur le panier
#: qu'on lui donne : trois cryptos large-cap corrélées à 0.8 ne disent RIEN de
#: ce que ferait un panier actions/ETF. Ce seuil sert à le rappeler dans la
#: sortie plutôt qu'à le laisser à la mémoire du lecteur.
CORR_REDUNDANT = 0.70


def discover_symbols(tf: str):
    """Symboles disponibles dans le store pour ``tf``, séparés en
    (entraînables, évaluables).

    Découverts, pas codés en dur : le jour où le bot trade des actions, des
    ETF ou un panier moins corrélé, rejouer ce script suffit — il n'y a rien
    à éditer. C'est la condition qui rend la conclusion révisable.
    """
    store = get_store()
    trainable, evaluable = [], []
    for row in store.all_stats():
        if row.get("tf") != tf or not row.get("symbol"):
            continue
        sym, n = row["symbol"], int(row.get("bars") or 0)
        if n < HOLDOUT + FEATURE_WARMUP:
            continue
        evaluable.append(sym)
        if n >= MIN_TRAIN_BARS:
            trainable.append(sym)
    return sorted(set(trainable)), sorted(set(evaluable))


def return_correlations(symbols, tf: str, cutoff):
    """Corrélation des log-rendements sur la fenêtre de holdout, par paire.

    Transforme « ce sont des cryptos très corrélées » d'une intuition en un
    nombre, et conditionne explicitement le verdict à ce nombre.
    """
    series = {}
    for sym in symbols:
        df = get_store().load_cached(sym, tf).sort("time")
        post = df.filter(pl.col("time") >= cutoff)
        if len(post) < MIN_N:
            continue
        s = post.select(["time", "close"]).with_columns(
            (pl.col("close") / pl.col("close").shift(1)).log().alias(sym)
        ).drop("close").drop_nulls()
        series[sym] = s
    out = {}
    names = list(series)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            j = series[a].join(series[b], on="time", how="inner")
            if len(j) < MIN_N:
                continue
            x, y = j[a].to_numpy(), j[b].to_numpy()
            out[(a, b)] = float(np.corrcoef(x, y)[0, 1])
    return out


def common_cutoff(tf: str, train_symbols):
    """Date de coupure partagée par tous les symboles.

    La plus TARDIVE des coupures ``len - HOLDOUT`` des symboles
    d'entraînement : chaque modèle s'entraîne donc strictement avant elle, et
    tous les holdouts (y compris ceux de symboles jamais entraînés) démarrent
    à cette date. C'est ce qui rend les colonnes de la matrice comparables et
    la colonne XRP non fuitée.
    """
    cuts = []
    for sym in train_symbols:
        df = get_store().load_cached(sym, tf).sort("time")
        cuts.append(df["time"][max(0, len(df) - HOLDOUT)])
    return max(cuts)


def train_candidate(symbol: str, tf: str, cutoff, gate_cfg, tmp_root: str):
    """Entraîne la recette de V11 sur ``symbol``/``tf``, strictement avant
    ``cutoff``.

    Retourne le préfixe de l'artefact, ou ``None`` si l'historique du symbole
    ne permet pas d'entraîner (cas XRP).
    """
    df = get_store().load_cached(symbol, tf).sort("time")
    train_df = df.filter(pl.col("time") < cutoff)
    if len(train_df) < gate_cfg.min_window_bars:
        return None, len(train_df)
    strat = v11.Strategy()
    p = {"warmup_bars": 750, "retrain_every": 10 ** 9, "log_training": False}
    strat.fit(train_df, params={STRATEGY: p})
    if not strat.is_trained:
        return None, len(train_df)
    prefix = os.path.join(tmp_root, f"{symbol.replace('/', '_')}_{tf}")
    strat.save_model(prefix)
    return prefix, len(train_df)


def holdout_for(symbol: str, tf: str, cutoff):
    """Barres à partir de ``cutoff``, précédées des ``FEATURE_WARMUP`` barres
    nécessaires à ``build_features``.

    Les barres de warmup sont antérieures à la coupure — elles alimentent le
    calcul des indicateurs mais ne sont jamais scorées : ``build_features``
    rend une table qui commence après elles.
    """
    df = get_store().load_cached(symbol, tf).sort("time")
    post = df.filter(pl.col("time") >= cutoff)
    if post.is_empty():
        return post, 0
    start = len(df) - len(post)
    return df.slice(max(0, start - FEATURE_WARMUP)), len(post)


def score_heads(path_prefix: str, holdout_df, gate_cfg):
    """AUC amp + dir ET scores bruts, pour permettre le bootstrap apparié.

    Reproduit le chemin de ``scoring.score_amp_dir_bundle`` (mêmes features,
    mêmes labels, mêmes médianes) mais retourne aussi ``(y, scores)`` par
    tête — ``score_holdout`` n'expose que les AUC agrégées, insuffisant pour
    comparer deux modèles sur les mêmes tirages.
    """
    bundle = load_amp_dir_bundle(path_prefix)
    if bundle is None or bundle.get("amp_model") is None or not bundle.get("features"):
        return None
    feats = build_features(holdout_df)
    if feats is None or len(feats) < 50:
        return None

    close = feats["close"].to_numpy().astype(np.float64)
    hs = list(gate_cfg.label_horizons or [1, 3, 6])
    if len(hs) > 1:
        y_amp, y_dir, n, _, _ = multi_horizon_labels(close, hs, gate_cfg.amp_top_pct)
    else:
        y_amp, y_dir, n, _ = single_horizon_labels(close, gate_cfg.amp_top_pct)
    if n < MIN_N:
        return None

    scored = feats.head(n)
    out = {"n": int(n)}
    for head, y, model, cal in (
        ("amp", y_amp, bundle["amp_model"], bundle.get("amp_cal")),
        ("dir", y_dir, bundle.get("dir_model"), bundle.get("dir_cal")),
    ):
        if model is None:
            continue
        s = predict_batch_raw(model, cal, bundle["features"], bundle["medians"], scored)
        if s is None:
            continue
        out[head] = {"y": y, "scores": s, "auc": rank_auc(y, s)}
    return out


def bootstrap_delta(y, s_a, s_b, n_boot: int = 2000, seed: int = 0):
    """IC 95 % de (AUC_a − AUC_b), mêmes indices tirés pour les deux modèles."""
    rng = np.random.RandomState(seed)
    k = len(y)
    if k < MIN_N:
        return None
    deltas = []
    for _ in range(n_boot):
        idx = rng.randint(0, k, k)
        a = rank_auc(y[idx], s_a[idx])
        b = rank_auc(y[idx], s_b[idx])
        if a is not None and b is not None:
            deltas.append(a - b)
    if len(deltas) < n_boot // 2:
        return None
    d = np.asarray(deltas)
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def fmt(v, w=7):
    return f"{v:>{w}.3f}" if isinstance(v, float) else f"{'n/m':>{w}}"


def main() -> int:
    tfs = ["1h"]
    if "--tf" in sys.argv:
        tfs = [sys.argv[sys.argv.index("--tf") + 1]]
    elif "--all-tfs" in sys.argv:
        tfs = ["15m", "30m", "1h"]

    gc_ = policy.GateConfig.from_params(policy.resolve_gate_spec(v11.Strategy()))
    print(f"# recette V11 : label_horizons={gc_.label_horizons} "
          f"amp_top_pct={gc_.amp_top_pct} | holdout={HOLDOUT} barres\n", file=sys.stderr)

    verdicts, corr_trainable, corr_eval_only = [], [], []
    for tf in tfs:
        print("=" * 78)
        print(f"TF {tf} — MATRICE DE TRANSFERT (entraîné sur ⇢ / évalué sur ⇣)")
        print("=" * 78)

        train_symbols, eval_symbols = discover_symbols(tf)
        if len(train_symbols) < 2:
            print(f"  moins de 2 symboles entraînables (≥{MIN_TRAIN_BARS} barres) "
                  f"— matrice non calculable. Disponibles : {eval_symbols}")
            continue
        print(f"  entraînables : {', '.join(train_symbols)}")
        print(f"  évaluables   : {', '.join(eval_symbols)}")

        cutoff = common_cutoff(tf, train_symbols)
        print(f"  coupure commune : {cutoff} — entraînement strictement avant, "
              f"holdouts strictement après")

        corr = return_correlations(eval_symbols, tf, cutoff)
        if corr:
            pretty = "  ".join(f"{a.split('/')[0]}~{b.split('/')[0]}={v:.2f}"
                               for (a, b), v in sorted(corr.items()))
            print(f"  corrélation des rendements sur le holdout : {pretty}")
            # Deux populations à ne pas confondre : les paires ENTRAÎNABLES
            # (celles sur lesquelles la question « son propre modèle ferait-il
            # mieux ? » est testable) et les autres (où l'on ne compare que des
            # modèles étrangers entre eux).
            for (a_, b_), v in corr.items():
                both = a_ in train_symbols and b_ in train_symbols
                (corr_trainable if both else corr_eval_only).append(((a_, b_), v))

        tmp = tempfile.mkdtemp(prefix="symxfer_")
        try:
            models = {}
            for sym in train_symbols:
                prefix, n_train = train_candidate(sym, tf, cutoff, gc_, tmp)
                if prefix is None:
                    print(f"  {sym} : entraînement impossible ({n_train} barres "
                          f"< {gc_.min_window_bars} requis)")
                    continue
                models[sym] = prefix
                print(f"  modèle {sym:<9} entraîné sur {n_train} barres", file=sys.stderr)

            if len(models) < 2:
                print("  moins de 2 modèles : matrice non calculable")
                continue

            # ── Scoring croisé
            cells, coverage = {}, {}
            for eval_sym in eval_symbols:
                holdout_df, n_post = holdout_for(eval_sym, tf, cutoff)
                coverage[eval_sym] = n_post
                for train_sym, prefix in models.items():
                    cells[(train_sym, eval_sym)] = (
                        score_heads(prefix, holdout_df, gc_) if n_post else None)
            span = " | ".join(f"{s.split('/')[0]} {coverage[s]}" for s in eval_symbols)
            print(f"  barres de holdout après coupure : {span}")

            # ── Tableau. L'IC compare les DEUX PREMIERS symboles entraînables
            # (paire de référence) ; les colonnes supplémentaires, s'il y en a,
            # restent affichées mais sans test apparié — comparer N modèles
            # deux à deux multiplierait les tests sans corriger le seuil.
            ref_a, ref_b = train_symbols[0], train_symbols[1]
            sa, sb = ref_a.split("/")[0], ref_b.split("/")[0]
            if len(train_symbols) > 2:
                print(f"\n  (IC calculé sur la paire de référence {sa}/{sb} ; "
                      f"{len(train_symbols) - 2} colonne(s) en plus, sans test)")
            for head in ("amp", "dir"):
                print(f"\n  AUC {head}")
                hdr = "  ".join(f"{'← ' + s.split('/')[0]:>13}" for s in train_symbols)
                print(f"  {'holdout':<12} {'n':>6}  {hdr}   écart ({sb}−{sa})  IC95 %")
                for eval_sym in eval_symbols:
                    row = [cells.get((s, eval_sym)) for s in train_symbols]
                    if any(r is None for r in row) or any(head not in r for r in row):
                        print(f"  {eval_sym:<12} {'—':>6}  holdout trop court "
                              f"ou tête absente")
                        continue
                    n = row[0]["n"]
                    aucs = [r[head]["auc"] for r in row]
                    y = row[0][head]["y"]
                    ci = bootstrap_delta(y, row[1][head]["scores"], row[0][head]["scores"])
                    delta = aucs[1] - aucs[0] if all(isinstance(a, float) for a in aucs) else None
                    if ci is None:
                        note = "—"
                    elif ci[0] > 0:
                        note = f"[{ci[0]:+.3f}, {ci[1]:+.3f}] {sb}-entraîné meilleur"
                    elif ci[1] < 0:
                        note = f"[{ci[0]:+.3f}, {ci[1]:+.3f}] {sa}-entraîné meilleur"
                    else:
                        note = f"[{ci[0]:+.3f}, {ci[1]:+.3f}] indiscernable"
                    cellstr = "  ".join(fmt(a, 13) for a in aucs)
                    d = f"{delta:>+9.3f}" if delta is not None else f"{'n/m':>9}"
                    print(f"  {eval_sym:<12} {n:>6}  {cellstr}   {d}      {note}")
                    if head == "amp":
                        verdicts.append({"tf": tf, "sym": eval_sym,
                                         "a": aucs[0], "b": aucs[1], "ci": ci,
                                         "ref_a": ref_a, "ref_b": ref_b,
                                         "trainable": set(train_symbols)})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ── Lecture — deux questions distinctes, la décision tient aux DEUX
    print("\n" + "=" * 78)
    print("LECTURE — la dimension symbole est-elle exploitée utilement ?")
    print("=" * 78)

    # Q1 : les modèles sont-ils spécifiques au symbole ? (propriété du modèle)
    # « propre » = le modèle entraîné SUR le symbole évalué. L'IC porte sur
    # (ref_b − ref_a) : sur le holdout de ref_a, le propre gagne si l'IC est
    # entièrement négatif ; sur celui de ref_b, s'il est entièrement positif.
    specific = []
    for v in verdicts:
        if not v["ci"]:
            continue
        if v["sym"] == v["ref_a"] and v["ci"][1] < 0:
            specific.append((v, v["a"], v["b"]))
        elif v["sym"] == v["ref_b"] and v["ci"][0] > 0:
            specific.append((v, v["b"], v["a"]))
    print("\nQ1. Un modèle étranger perd-il quelque chose ?")
    if specific:
        for v, own, other in specific:
            print(f"  OUI sur {v['tf']} / {v['sym']} : propre {own:.3f} vs "
                  f"étranger {other:.3f} (IC95 % [{v['ci'][0]:+.3f}, {v['ci'][1]:+.3f}])")
        print("  → les modèles NE sont PAS interchangeables : la dimension a un sens.")
    else:
        print("  NON — aucun holdout où le modèle étranger perd significativement.")
        print("  → les modèles sont interchangeables : la dimension est du rangement.")

    # Q2 : le bot y gagnerait-il ? Le sortant est le modèle du PREMIER symbole
    # entraînable (celui que le trainer live produit). La question n'est donc
    # pas « ce modèle est-il bon chez lui » — c'est déjà le cas — mais « les
    # autres symboles gagneraient-ils le leur ».
    print("\nQ2. Les autres symboles gagneraient-ils leur propre modèle ?")
    gains = []
    for v in verdicts:
        if v["sym"] == v["ref_a"]:
            continue
        if v["sym"] == v["ref_b"] and v["ci"]:
            verdict = ("OUI" if v["ci"][0] > 0 else
                       "non (le modèle propre est MOINS bon)" if v["ci"][1] < 0 else
                       "non — écart indiscernable du bruit")
            if v["ci"][0] > 0:
                gains.append(v)
            print(f"  {v['tf']} / {v['sym']} : propre {v['b']:.3f} vs "
                  f"{v['ref_a'].split('/')[0]} {v['a']:.3f} → {verdict}")
        elif v["sym"] not in v["trainable"]:
            print(f"  {v['tf']} / {v['sym']} : pas de modèle propre possible "
                  f"(historique insuffisant) — sert forcément un modèle étranger")

    print("\n" + "-" * 78)
    if gains:
        print("Q1 et Q2 positives → EXPLOITER la dimension : entraîner par symbole.")
    elif specific:
        print("Q1 positive, Q2 négative → cas asymétrique. Les modèles sont bien")
        print("spécifiques, mais le sortant sert déjà les autres symboles sans perte")
        print("mesurable, et tous n'ont pas de quoi s'entraîner. Entraîner par")
        print("symbole coûterait N× le temps de retrain pour un gain nul là où il")
        print("est mesurable. La dimension se garde dans le REGISTRE (traçabilité)")
        print("sans être un axe d'entraînement.")
    else:
        print("Q1 et Q2 négatives → la dimension ne porte rien : la retirer.")

    # ── Portée du verdict — la partie qu'on oublie de relire six mois après
    print("\n" + "=" * 78)
    print("PORTÉE DE CE VERDICT — à quelles conditions il reste valable")
    print("=" * 78)
    if corr_trainable:
        vals = [v for _, v in corr_trainable]
        print(f"  Paires ENTRAÎNABLES — corrélation {min(vals):.2f} à {max(vals):.2f}")
        print("    C'est la seule population où « son propre modèle ferait-il")
        print("    mieux ? » est testable, puisqu'il faut pouvoir entraîner les deux.")
        if min(vals) >= CORR_REDUNDANT:
            print(f"    Toutes au-dessus de {CORR_REDUNDANT} : le sous-panier testable est")
            print("    HOMOGÈNE. Qu'un modèle y transfère n'a rien d'étonnant et ne dit")
            print("    RIEN de ce que ferait un actif d'une autre classe.")
    if corr_eval_only:
        vals = [v for _, v in corr_eval_only]
        print(f"\n  Paires ÉVALUÉES SEULEMENT — corrélation {min(vals):.2f} à {max(vals):.2f}")
        print("    Ces actifs n'ont pas assez d'historique pour un modèle propre.")
        print("    On y observe donc qu'un modèle étranger TRANSFÈRE — utile — mais")
        print("    jamais qu'il vaut mieux qu'un modèle local, qui n'existe pas.")
        if min(vals) < CORR_REDUNDANT:
            print(f"    Au moins une est sous {CORR_REDUNDANT} : le transfert s'observe donc")
            print("    déjà vers un actif faiblement corrélé — c'est un indice, pas")
            print("    une preuve, faute de contrefactuel local.")

    print("\n  → REJOUER ce script dès qu'un actif d'une AUTRE CLASSE et doté")
    print("    d'assez d'historique entre dans le bot (actions CAC/Nasdaq, ETF,")
    print("    matières premières) : il rejoindra alors les paires ENTRAÎNABLES,")
    print("    et c'est là seulement que le verdict pourra s'inverser.")
    print("    Aucune édition nécessaire — les symboles sont découverts depuis")
    print(f"    le store (seuil : ≥ {MIN_TRAIN_BARS} barres pour être entraînable).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
