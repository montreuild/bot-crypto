#!/usr/bin/env python3
"""Peuple le cache Parquet avec l'historique d'un univers actions (G2).

Le runner d'entraînement ML (``app/ml/train_runner.py``) ne fetch **jamais**
depuis un provider : il lit le cache local, ce qui rend un entraînement
reproductible par construction. Sur crypto le cache se remplit tout seul —
le scanner tourne en continu. Sur actions, rien ne l'a jamais rempli : il faut
donc un amorçage explicite, et c'est ce que fait ce script.

    python scripts/backfill_equities.py                    # sbf120, 1d + 1h
    python scripts/backfill_equities.py --tf 1d --years 15
    python scripts/backfill_equities.py --symbols AIR.PA,MC.PA --tf 1h
    python scripts/backfill_equities.py --plan             # n'appelle rien

**Profondeur bornée par Yahoo**, et c'est structurant pour le ML : 1 h plafonne
à 730 jours (~2 ans), 15 m/30 m à 60 jours, seul le journalier est illimité.
Les recettes du dépôt demandent ``min_bars`` 1500–2000 **plus** un holdout de
1500 barres ; sur XPAR (8,5 h de séance, ~256 séances/an) cela donne :

    1h  →  ~2 180 barres/an   → 3 500 barres ≈ 1,6 an   (tout juste atteignable)
    1d  →  ~256 barres/an     → 3 500 barres ≈ 13,7 ans (par titre !)

Autrement dit, en journalier un titre seul ne peut quasiment pas satisfaire une
recette. C'est l'argument technique du pooling multi-symboles : ce script
récupère l'historique de TOUS les membres pour que l'entraînement puisse les
mettre en commun ensuite.

Le script est **incrémental et réentrant** : ``CandleStore.fetch`` fusionne sur
``time`` et déduplique, donc le relancer complète le cache au lieu de le
réécrire. Un titre en échec n'interrompt pas les autres — le rapport final
liste ce qui manque.

⚠️ ``--years`` n'est plus une limite sur actions. Le store amorce désormais un
cache vide par ``fetch_ohlcv_max`` (``yfinance``: ``period='max'``), qui laisse
**Yahoo** décider de sa profondeur au lieu de la déduire d'un nombre de bougies.
On récupère donc systématiquement tout le disponible pour la granularité, et
``--years`` ne sert plus qu'à dimensionner le compte visé dans le rapport. C'est
voulu : sur un amorçage, la seule bonne réponse à « quelle profondeur ? » est
« toute celle qui existe ».
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# La console Windows est en cp1252 : les flèches du rapport de plan (« 1d → ~5100
# barres ») faisaient lever UnicodeEncodeError AVANT le premier téléchargement,
# donc `--help` et `--plan` étaient inutilisables sur le poste qui lance
# justement ce script. `errors="replace"` plutôt que renoncer aux caractères :
# un rapport légèrement dégradé vaut mieux qu'un script qui refuse de démarrer.
try:                                                        # pragma: no cover
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

logger = logging.getLogger("backfill")

#: Barres par an et par TF sur une séance XPAR (8,5 h, ~256 séances) — sert à
#: traduire une profondeur en années vers un nombre de bougies.
_BARS_PER_YEAR = {
    "1m": 8 * 60 * 256, "5m": 102 * 256, "15m": 34 * 256, "30m": 17 * 256,
    "1h": 9 * 256, "2h": 5 * 256, "4h": 3 * 256, "1d": 256,
}

#: Profondeur maximale servie par Yahoo, en jours calendaires (0 = illimité).
#: Recopiée de ``app.core.yfinance_provider._INTERVALS`` pour pouvoir avertir
#: AVANT de lancer des centaines de requêtes qui seront tronquées.
_MAX_DAYS = {"1m": 7, "2m": 60, "3m": 7, "5m": 60, "15m": 60, "30m": 60,
             "1h": 730, "2h": 730, "4h": 730, "6h": 730, "8h": 730,
             "12h": 730, "1d": 0}


def _target_bars(tf: str, years: float) -> int:
    return max(1, int(_BARS_PER_YEAR.get(tf, 256) * years))


def _capped_years(tf: str, years: float) -> Tuple[float, bool]:
    """Profondeur réellement servie par Yahoo pour ``tf``, et si elle borne."""
    max_days = _MAX_DAYS.get(tf, 0)
    if not max_days:
        return years, False
    max_years = max_days / 365.0
    return (max_years, True) if years > max_years else (years, False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", default="sbf120",
                    help="nom d'univers dans data/universe/ (défaut : sbf120)")
    ap.add_argument("--symbols", default="",
                    help="liste explicite séparée par des virgules — ignore --universe")
    ap.add_argument("--tf", default="1d,1h",
                    help="timeframes séparés par des virgules (défaut : 1d,1h)")
    ap.add_argument("--years", type=float, default=20.0,
                    help="profondeur visée en années — tronquée par les limites "
                         "Yahoo par granularité (défaut : 20)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--plan", action="store_true",
                    help="affiche ce qui serait récupéré, sans aucun appel réseau")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="pause supplémentaire entre deux titres (le provider "
                         "impose déjà min_request_interval)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Les avertissements du provider (retry, troncature) sont le signal utile ;
    # le reste du bot est bavard au démarrage.
    logging.getLogger("app.core.candle_store").setLevel(logging.WARNING)

    # S11 : la config est découpée (config.yaml = sommaire `include:`). Lire le
    # seul fichier racine ne verrait ni `providers` ni `scanner`, et le backfill
    # tomberait silencieusement sur les défauts yfinance.
    from app.core.config import _load_and_merge
    cfg = _load_and_merge(args.config)

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        from app.core.universe import universe_symbols
        symbols = universe_symbols(args.universe)
    if not symbols:
        logger.error(f"Aucun symbole — univers '{args.universe}' vide ou introuvable.")
        return 1

    tfs = [t.strip() for t in args.tf.split(",") if t.strip()]
    # Un timeframe inconnu — coquille, virgule manquante, shell qui découpe
    # l'argument — lançait tout de même les 121 titres, un avertissement par
    # symbole et « 0 barres » partout. Le refuser AVANT la première requête
    # coûte une ligne et évite de brûler du quota pour rien.
    unknown = [t for t in tfs if t not in _BARS_PER_YEAR and t not in _MAX_DAYS]
    if unknown:
        logger.error(
            f"Timeframe(s) inconnu(s) : {', '.join(unknown)}. "
            f"Valeurs acceptées : {', '.join(sorted(_MAX_DAYS))}. "
            f"(--tf attend une liste séparée par des virgules, ex. --tf 15m,1h,1d)"
        )
        return 1

    from app.core.bot_identity import resolve_venue
    equities = [s for s in symbols if resolve_venue(cfg, symbol=s).asset_class == "equity"]
    if not equities:
        logger.error(
            f"Aucun des {len(symbols)} symboles ne résout sur une venue actions. "
            f"Vérifiez que `scanner.universe` contient '{args.universe}' et que "
            f"data/universe/{args.universe}.yaml déclare `venue:`."
        )
        return 1
    if len(equities) < len(symbols):
        logger.warning(f"{len(symbols) - len(equities)} symbole(s) non-actions ignoré(s).")

    print(f"\nUnivers   : {args.universe} — {len(equities)} instruments")
    print(f"Venue     : {resolve_venue(cfg, symbol=equities[0]).name} "
          f"(provider={resolve_venue(cfg, symbol=equities[0]).data_provider})")
    for tf in tfs:
        years, capped = _capped_years(tf, args.years)
        n = _target_bars(tf, years)
        note = f"  ⚠️ Yahoo plafonne {tf} à {_MAX_DAYS[tf]} j" if capped else ""
        print(f"  {tf:4} → ~{n:6} barres/titre ({years:.1f} an(s)){note}")
    print()

    if args.plan:
        print("--plan : aucun appel réseau effectué.")
        return 0

    from app.core.candle_store import get_store
    from app.core.provider_router import build_market_provider

    # L'exchange ccxt n'est jamais sollicité ici (tous les symboles sont des
    # actions) — un objet minimal suffit et évite d'exiger des clés API.
    class _NoExchange:
        id = "unused"
        rateLimit = 100

        def parse_timeframe(self, tf):  # noqa: D102
            from app.core.timeframes import TF_SECONDS
            return TF_SECONDS.get(tf, 3600)

        def milliseconds(self):  # noqa: D102
            return int(time.time() * 1000)

    provider = build_market_provider(cfg, _NoExchange())
    store = get_store()

    failures: List[Tuple[str, str, str]] = []
    totals: Dict[str, int] = {tf: 0 for tf in tfs}
    for i, symbol in enumerate(equities, 1):
        for tf in tfs:
            years, _ = _capped_years(tf, args.years)
            target = _target_bars(tf, years)
            try:
                df = store.fetch(provider, symbol, tf, target)
                bars = 0 if df is None else len(df)
            except Exception as e:                        # noqa: BLE001
                failures.append((symbol, tf, f"{type(e).__name__}: {e}"))
                print(f"[{i:3}/{len(equities)}] {symbol:10} {tf:4} ERREUR {e}")
                continue
            if bars == 0:
                failures.append((symbol, tf, "aucune donnée"))
            totals[tf] += bars
            st = store.stats(symbol, tf)
            print(f"[{i:3}/{len(equities)}] {symbol:10} {tf:4} {bars:6} barres"
                  f"  {str(st.get('from'))[:10]} → {str(st.get('to'))[:10]}")
        if args.sleep:
            time.sleep(args.sleep)

    print("\n" + "=" * 66)
    for tf in tfs:
        n_ok = len(equities) - sum(1 for f in failures if f[1] == tf)
        moy = totals[tf] // max(n_ok, 1)
        print(f"  {tf:4} : {n_ok}/{len(equities)} titres, {totals[tf]} barres "
              f"({moy} en moyenne par titre)")
    if failures:
        print(f"\n  {len(failures)} échec(s) :")
        for symbol, tf, why in failures[:20]:
            print(f"    {symbol:10} {tf:4} — {why[:80]}")
        if len(failures) > 20:
            print(f"    … et {len(failures) - 20} autre(s)")
        print("\n  Un titre sans donnée est souvent radié ou renommé — "
              "recoupez avec :  python scripts/check_universe.py " + args.universe)
    print("=" * 66)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
