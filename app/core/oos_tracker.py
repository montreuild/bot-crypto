"""Contrats Monte-Carlo du forward-test glissant (Phase 0 — observationnel).

Ce module ne prend **aucune** décision de trading. Il contient la partie
**analytique pure** du forward-test glissant, plus sa persistance :

1. **Contrat Monte-Carlo glissant** (``_mc_contract``) — fourchette d'issues
   plausibles du rendement moyen par trade, recalculée à chaque exécution
   (donc *glissante*, jamais figée).
2. **Cône d'edge** (``_edge_contract``) — intervalle de confiance de
   l'expectancy sur tout l'échantillon backtest (promotion par edge).
3. **Verdict** (``_verdict``) — le rendement réel moyen par trade tombe-t-il
   dans la fourchette Monte-Carlo ? C'est la donnée « le live confirme-t-il
   la simulation ? » qui rend fiables les décisions d'allocation.

L'**exécution** du forward-test (re-backtest des params figés via ``Engine``/
``Backtester``/``MonteCarlo``, boucle sur les slots actifs) vit dans
``app/engine/forward_test.py`` (V4-E / ARCH-09) : app/core ne dépend
d'aucun module app/engine.

Toutes les grandeurs comparées sont **budget-indépendantes** (rendement % par
trade), cohérent avec le score budget-indépendant (``opt_scoring.py``).

Persistance : ``data/oos_tracker.json`` — un dict ``slot_key → enregistrement``,
le dernier écrasant le précédent (même esprit que ``backtest_history.py``).
"""
import json
import logging
import os
import threading

import numpy as np

logger = logging.getLogger(__name__)

_TRACKER_PATH = os.path.join("data", "oos_tracker.json")
_lock = threading.Lock()


# ── Persistance ────────────────────────────────────────────────────────────
def _path() -> str:
    os.makedirs(os.path.dirname(_TRACKER_PATH), exist_ok=True)
    return _TRACKER_PATH


def load_oos_tracker() -> dict:
    """Retourne le dict ``slot_key → enregistrement`` (vide si absent/illisible)."""
    try:
        with open(_TRACKER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        logger.warning(f"[oos_tracker] lecture KO : {e}")
        return {}


def _save_record(slot_key: str, record: dict) -> None:
    try:
        with _lock:
            data = load_oos_tracker()
            data[slot_key] = record
            with open(_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[oos_tracker] écriture KO ({slot_key}) : {e}")


# ── Helpers ────────────────────────────────────────────────────────────────
def _closed_trades(trades: list) -> list:
    return [t for t in (trades or []) if str(t.get("status", "")).startswith("closed")]


def _per_trade_returns_pct(trades: list) -> list:
    """Rendements par trade en % (budget-indépendant). Utilise ``pnl_pct`` quand
    présent (trades simulés et live le portent), sinon retombe sur 0."""
    out = []
    for t in trades:
        v = t.get("pnl_pct")
        if v is None:
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _mc_contract(sim_returns_pct: list, n_live: int,
                 runs: int = 2000, conf: float = 0.90) -> dict:
    """Fourchette Monte-Carlo glissante sur le **rendement moyen par trade**.

    On rééchantillonne (bootstrap) ``n_live`` rendements parmi les rendements
    simulés et on prend la distribution de leur moyenne. La fourchette
    [p_low, p_high] est l'intervalle de confiance ``conf`` de ce que la
    simulation prédit pour ``n_live`` trades — exactement le nombre de trades
    réels observés, d'où une comparaison apples-to-apples.
    """
    if not sim_returns_pct or n_live <= 0:
        return {"available": False}
    arr = np.asarray(sim_returns_pct, dtype=float)
    rng = np.random.default_rng(42)
    means = rng.choice(arr, size=(runs, n_live), replace=True).mean(axis=1)
    lo = (1.0 - conf) / 2.0 * 100.0
    hi = (1.0 + conf) / 2.0 * 100.0
    return {
        "available":      True,
        "runs":           runs,
        "confidence":     conf,
        "n_live":         n_live,
        "sim_mean_pct":   round(float(arr.mean()), 4),
        "band_low_pct":   round(float(np.percentile(means, lo)), 4),
        "band_high_pct":  round(float(np.percentile(means, hi)), 4),
    }


def _edge_contract(sim_returns_pct: list, runs: int = 2000,
                   conf: float = 0.90) -> dict:
    """Cône d'**edge** : intervalle de confiance de l'expectancy sur le backtest.

    Contrairement à ``_mc_contract`` (qui bootstrappe ``n_live`` tirages pour
    comparer au live), on bootstrappe ici la moyenne sur **``n_sim``** trades —
    tout l'échantillon backtest. La borne basse ``ci_low_pct`` répond à « l'edge
    est-elle significativement positive ? » *sans aucun trade live* (cf.
    docs/CONCEPTION_PROMOTION_PAR_EDGE.md §2.1). La largeur ~ ``std/√n_sim``
    encode la taille d'échantillon ; ``worst_trade_pct`` sert de garde-fou de
    queue (indispensable pour les 100 % winrate).
    """
    if not sim_returns_pct:
        return {"available": False}
    arr = np.asarray(sim_returns_pct, dtype=float)
    n = int(arr.size)
    rng = np.random.default_rng(42)
    means = rng.choice(arr, size=(runs, n), replace=True).mean(axis=1)
    lo = (1.0 - conf) / 2.0 * 100.0
    hi = (1.0 + conf) / 2.0 * 100.0
    return {
        "available":       True,
        "n":               n,
        "confidence":      conf,
        "expectancy_pct":  round(float(arr.mean()), 4),
        "ci_low_pct":      round(float(np.percentile(means, lo)), 4),
        "ci_high_pct":     round(float(np.percentile(means, hi)), 4),
        "worst_trade_pct": round(float(arr.min()), 4),
    }


def _verdict(contract: dict, live_mean_pct):
    """Compare le rendement réel moyen par trade à la fourchette MC.

    Retourne (in_band: bool|None, verdict: str). ``in_band`` vaut None tant
    qu'on n'a pas de données live exploitables.
    """
    if not contract.get("available") or live_mean_pct is None:
        return None, "pas_assez_de_trades_reels"
    lo = contract["band_low_pct"]
    hi = contract["band_high_pct"]
    if live_mean_pct < lo:
        return False, "sous_la_simulation"   # le live sous-performe la sim
    if live_mean_pct > hi:
        return False, "au_dessus_de_la_simulation"  # rare : à investiguer aussi
    return True, "conforme"
