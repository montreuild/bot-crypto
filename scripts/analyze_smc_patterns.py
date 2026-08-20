#!/usr/bin/env python3
"""Analyse statistique des motifs SMC/ICT — un symbole, plusieurs timeframes.

Produit le journal daté de tous les motifs détectés par le moteur du dépôt,
puis mesure ce qui les entoure : fréquence, enchaînements intra- et inter-TF,
motifs composés, et mouvement du prix APRÈS confirmation — chaque chiffre face
à ses témoins.

Étude de MESURE : aucune stratégie n'est modifiée, aucun détecteur n'est
réécrit. Un seul symbole par exécution, plusieurs timeframes.

Usage :
    python scripts/analyze_smc_patterns.py --symbol BTC/USDC --tfs 15m,1h,4h
    python scripts/analyze_smc_patterns.py --symbol BTC/USDC --tfs 1h,4h \\
        --horizons 6,12,24 --fenetre-composes 12 --sortie research/smc

Sans données en cache le script ÉCHOUE en disant quoi lancer : il ne fabrique
jamais de série de substitution. Un rapport sur des prix synthétiques serait
pire que pas de rapport.
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger("analyze_smc_patterns")


def _tfs(valeur: str):
    return [t.strip() for t in valeur.split(",") if t.strip()]


def _entiers(valeur: str):
    return tuple(int(x) for x in valeur.split(",") if x.strip())


def charger(symbol: str, tfs, depuis=None, jusqu_a=None):
    """Frames OHLCV depuis le CandleStore. Lève ``SystemExit`` si le cache est vide."""
    import polars as pl

    from app.core.candle_store import CandleStore

    store = CandleStore()
    frames, manquants = {}, []
    for tf in tfs:
        df = store.load_cached(symbol, tf)
        if df is None or len(df) < 250:
            manquants.append(f"{tf} ({0 if df is None else len(df)} bougies)")
            continue
        if depuis:
            df = df.filter(pl.col("time") >= pl.lit(depuis).str.to_datetime())
        if jusqu_a:
            df = df.filter(pl.col("time") <= pl.lit(jusqu_a).str.to_datetime())
        frames[tf] = df
    if not frames:
        raise SystemExit(
            f"\nAucune donnée en cache pour {symbol} sur {', '.join(tfs)}.\n"
            f"Manquant : {', '.join(manquants) if manquants else 'tout'}\n\n"
            f"Alimenter le cache d'abord — le backtest CLI télécharge et met en\n"
            f"cache par le même CandleStore :\n"
            f"    python cli.py --backtest {symbol} --timeframes {','.join(tfs)} "
            f"--limit 50000\n"
            f"ou en lançant l'API, qui remplit data/ohlcv/ au fil des scans.\n"
            f"Ce script ne fabrique PAS de série de substitution : une mesure\n"
            f"sur des prix synthétiques serait pire que pas de mesure.\n")
    if manquants:
        logger.warning("TF ignoré(s) faute de données : %s", ", ".join(manquants))
    return frames


def executer(args) -> dict:
    from app.core.timeframes import TF_SECONDS
    from app.engine.smc_patterns import composites as C
    from app.engine.smc_patterns import stats as S
    from app.engine.smc_patterns.journal import journal_multi_tf

    frames = charger(args.symbol, args.tfs, args.depuis, args.jusqu_a)
    for tf, df in frames.items():
        logger.info("%s/%s : %d bougies (%s → %s)", args.symbol, tf, len(df),
                    df["time"][0], df["time"][-1])

    journal = journal_multi_tf(frames, args.symbol)
    if journal.height == 0:
        raise SystemExit("Journal vide : historique trop court pour le moteur SMC.")
    logger.info("Journal : %d événements, %d motifs distincts",
                journal.height, journal["motif"].n_unique())

    tf_bas = min(frames, key=lambda t: TF_SECONDS.get(t, 0))
    fenetre_s = args.fenetre_composes * TF_SECONDS.get(tf_bas, 3600)

    resultats = {
        "frequences": S.frequences(journal, frames),
        "ventilation_session": S.ventilation(journal, "session"),
        "ventilation_etat": S.ventilation(journal, "etat"),
        "ventilation_htf": S.ventilation(journal, "htf_etat"),
    }
    for tf, df in frames.items():
        resultats[f"transitions_{tf}"] = S.transitions(
            journal, tf, fenetre=args.fenetre_transitions, n_barres=len(df))

    mesures = S.mouvement_suivant(journal, frames, horizons=args.horizons)
    budget = S.budget_hypotheses(mesures)
    resultats["mouvement"] = mesures
    resultats["survivants"] = S.survivants(mesures, budget)

    fouille = C.mine(journal, fenetre_s=fenetre_s, longueurs=(2, 3))
    mes_comp = C.mouvement_apres_composes(
        fouille, journal, frames, fenetre_s, tf_bas, horizons=args.horizons)
    surv_comp = C.survivants_composes(mes_comp, fouille)
    resultats["composes"] = fouille["candidats"]
    resultats["composes_mesures"] = mes_comp
    resultats["composes_survivants"] = surv_comp["survivants"]

    resume = {
        "symbole": args.symbol,
        "timeframes": {tf: len(df) for tf, df in frames.items()},
        "evenements": journal.height,
        "motifs_distincts": int(journal["motif"].n_unique()),
        "hypotheses_motifs": budget["n_hypotheses"],
        "alpha_bonferroni_motifs": budget["alpha_bonferroni"],
        "motifs_survivants": resultats["survivants"].height,
        "composes_enumeres": fouille["n_enumeres"],
        "composes_retenus": fouille["candidats"].height,
        "composes_confirmes": (int(fouille["candidats"]["confirme"].sum())
                               if fouille["candidats"].height else 0),
        "composes_survivants": surv_comp["survivants"].height,
        "coupure_decouverte_confirmation": fouille["coupure"],
    }
    return {"journal": journal, "resultats": resultats, "resume": resume}


def ecrire(sortie: Path, symbol: str, paquet: dict) -> None:
    sortie.mkdir(parents=True, exist_ok=True)
    base = symbol.replace("/", "_")
    paquet["journal"].write_parquet(sortie / f"{base}_journal.parquet")
    for nom, df in paquet["resultats"].items():
        if getattr(df, "height", 0):
            df.write_parquet(sortie / f"{base}_{nom}.parquet")
    (sortie / f"{base}_resume.json").write_text(
        json.dumps(paquet["resume"], indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    logger.info("Écrit dans %s", sortie)


def afficher(paquet: dict) -> None:
    import polars as pl

    r, resume = paquet["resultats"], paquet["resume"]
    print("\n" + "=" * 78)
    print(f"  {resume['symbole']} — {resume['evenements']} événements, "
          f"{resume['motifs_distincts']} motifs distincts")
    print("=" * 78)

    # Le décompte d'hypothèses va EN TÊTE : c'est lui qui dit combien de
    # chances le hasard a eues de produire ce qui suit.
    print(f"\nHYPOTHÈSES TESTÉES  motifs : {resume['hypotheses_motifs']} "
          f"(α corrigé {resume['alpha_bonferroni_motifs']:.2g})"
          if resume["alpha_bonferroni_motifs"] else "\nHYPOTHÈSES TESTÉES : 0")
    print(f"                    composés énumérés : {resume['composes_enumeres']}, "
          f"retenus {resume['composes_retenus']}, "
          f"confirmés {resume['composes_confirmes']}")

    with pl.Config(tbl_rows=15, fmt_str_lengths=48, tbl_width_chars=120):
        print("\n── Fréquence ──")
        print(r["frequences"].head(15))
        print("\n── Mouvement suivant (extrait, trié par écart au témoin décalé) ──")
        if r["mouvement"].height:
            print(r["mouvement"].sort("ecart_decale", descending=True,
                                      nulls_last=True).head(10))
        print("\n── Composés confirmés sur la tranche de confirmation ──")
        if r["composes"].height:
            print(r["composes"].filter(pl.col("confirme")).head(10))

    print("\n" + "─" * 78)
    print(f"SURVIVANTS après correction — motifs : {resume['motifs_survivants']}, "
          f"composés : {resume['composes_survivants']}")
    if resume["motifs_survivants"] == 0 and resume["composes_survivants"] == 0:
        print("Aucun motif ni composé ne se distingue de ses témoins sur cet")
        print("échantillon. C'est un résultat, pas un échec de la mesure.")
    print("─" * 78 + "\n")


def _stdout_utf8() -> None:
    """Force UTF-8 en sortie — sinon le rapport est illisible sous Windows.

    Le rapport imprime `α`, `→` et les tableaux polars (`┌`, `─`, `┆`) : aucun
    de ces caractères n'existe en cp1252, l'encodage par défaut d'un stdout
    redirigé sous Windows. Sans ce correctif le script meurt sur
    `UnicodeEncodeError` APRÈS avoir fait tout le calcul — le pire moment.
    """
    for flux in (sys.stdout, sys.stderr):
        reconfigure = getattr(flux, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def main() -> int:
    _stdout_utf8()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", required=True, help="ex. BTC/USDC (UN seul symbole)")
    p.add_argument("--tfs", type=_tfs, default=["15m", "1h", "4h"],
                   help="timeframes séparés par des virgules (défaut 15m,1h,4h)")
    p.add_argument("--depuis", default=None, help="ISO, ex. 2023-01-01T00:00:00")
    p.add_argument("--jusqu-a", dest="jusqu_a", default=None, help="ISO")
    p.add_argument("--horizons", type=_entiers, default=(1, 3, 6, 12, 24),
                   help="horizons de mesure en barres (défaut 1,3,6,12,24)")
    p.add_argument("--fenetre-transitions", type=int, default=12,
                   help="fenêtre des transitions, en barres (défaut 12)")
    p.add_argument("--fenetre-composes", type=int, default=12,
                   help="fenêtre des composés, en barres du TF le plus bas "
                        "(défaut 12). ⚠ le coût de l'énumération à 3 maillons "
                        "croît en carré : sur 4 TF, commencer à 4 "
                        "(cf. docs/MESURE_PATTERNS_SMC.md §5 bis)")
    p.add_argument("--sortie", type=Path, default=Path("research/smc_patterns"),
                   help="répertoire de sortie (défaut research/smc_patterns)")
    p.add_argument("--sans-ecriture", action="store_true",
                   help="afficher seulement, ne rien écrire")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for bruyant in ("app.core.smc_structure", "app.core.config", "app.core.feature_store"):
        logging.getLogger(bruyant).setLevel(logging.WARNING)

    paquet = executer(args)
    afficher(paquet)
    if not args.sans_ecriture:
        ecrire(args.sortie, args.symbol, paquet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
