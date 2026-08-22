"""Mise en place d'un backtest : complétude, horodatage, modèle figé.

DETTE-04c — extrait de `backtest.py` (810 lignes). Aucune de ces fonctions ne
touche à l'état du `Backtester` : elles préparent ce qu'il consomme.
`run_dual_pass` reste dans `backtest.py`, qui instancie le `Backtester`.
"""
import logging
from typing import Dict, Optional

import polars as pl

logger = logging.getLogger(__name__)

def _mesurer_completude(df, tf: str, symbol: str):
    """Complétude de la série en %, ou None si non mesurable.

    DOWN-02 : l'indicateur existait mais n'était consommé par personne — un
    backtest tournait sur une série à 26 % sans que rien ne le signale. Il
    accompagne désormais les métriques, sans bloquer : c'est un avertissement,
    pas un gate.
    """
    try:
        from app.core import ohlcv_absents as _abs
        from app.core.candle_store import get_store
        from app.core.ohlcv_gaps import (
            calendar_for_symbol,
            completeness_from_gaps,
            detect_ohlcv_gaps,
        )
        try:
            absents = _abs.charger(get_store()._path(symbol, tf), symbol, tf)
        except Exception:
            absents = set()
        gaps = detect_ohlcv_gaps(df, tf, calendar=calendar_for_symbol(symbol),
                                 absents=absents)
        return round(completeness_from_gaps(len(df), gaps) * 100, 2)
    except Exception as e:
        logger.debug("[Backtest] complétude non mesurable %s/%s : %s", symbol, tf, e)
        return None


def _iso_of(df: pl.DataFrame, idx: int) -> Optional[str]:
    """``time`` de la barre ``idx`` en ISO (borne ``as_of`` du registre ML)."""
    if df is None or len(df) == 0 or "time" not in df.columns:
        return None
    from app.ml.model_registry import to_iso
    return to_iso(df["time"][idx])


def _resolve_frozen_ml_model(strat, symbol: Optional[str], tf: Optional[str],
                             window_start: Optional[str], window_end: Optional[str]) -> dict:
    """Charge le modèle figé ``as_of=window_start``. Ne lève jamais :
    introuvable / illisible / chevauchement → ``fallback_to_inline``.
    """
    import app.ml.model_registry as ml_registry

    entry: Dict = {"resolved": False, "fallback_to_inline": True}
    if not tf:
        return entry
    base_dir = getattr(strat, "model_dir", "models") or "models"
    try:
        from app.ml.scoring import resolve_recipe_name
        recipe = resolve_recipe_name(strat)
        art = ml_registry.resolve(tf, recipe, as_of=window_start, base_dir=base_dir)
    except Exception as e:
        logger.warning(f"[Backtest] ml_mode=frozen : resolve() KO pour {strat.name}/{tf} : {e}")
        return entry
    if art is None:
        logger.warning(
            f"[Backtest] ml_mode=frozen : aucun modèle résoluble pour {strat.name}/{tf} "
            f"(symbole={symbol}) — entraînement inline activé (lancez d'abord un cycle "
            f"live, le runner CLI, ou un optimiseur pour publier un modèle)."
        )
        return entry
    overlap = ml_registry.overlaps(art, window_start, window_end)
    if overlap:
        # M-06 : un modèle qui a vu la fenêtre évaluée n'est pas utilisable.
        logger.warning(
            f"[Backtest] ml_mode=frozen : {strat.name}/{tf} — la fenêtre d'entraînement "
            f"de {art.version_id} ({art.train_start}..{art.train_end}) chevauche la "
            f"fenêtre backtestée ({window_start}..{window_end}) : modèle invalidé, "
            f"repli inline."
        )
        return {
            "resolved": False, "fallback_to_inline": True,
            "overlap_warning": True, "invalidated": True,
            "version_id": art.version_id,
            "train_start": art.train_start, "train_end": art.train_end,
        }
    if not strat.load_model(art.path_prefix):
        logger.warning(
            f"[Backtest] ml_mode=frozen : {strat.name}/{tf} — modèle {art.version_id} "
            f"résolu mais illisible — entraînement inline activé."
        )
        return entry
    strat.managed_externally = True
    logger.debug(f"[Backtest] ml_mode=frozen : {strat.name}/{tf} -> {art.version_id} chargé")
    entry.update({
        "resolved": True, "fallback_to_inline": False,
        "version_id": art.version_id, "train_start": art.train_start,
        "train_end": art.train_end, "auc": round(float(art.auc), 4),
        "undated": not art.train_end, "overlap_warning": overlap,
    })
    return entry


