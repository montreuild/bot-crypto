"""Durée de détention d'une position de backtest (DOWN-03)."""
import logging

from app.core.timeframes import bar_to_days as _bar_to_days

logger = logging.getLogger(__name__)

def _heures_detenues(ctx, bar_entree: int, bar_sortie: int) -> float:
    """Heures réellement écoulées entre deux barres.

    DOWN-03 : la durée venait du COMPTE de barres (``bars × bar_to_days``),
    ce qui suppose un pas régulier. Sur une série trouée, ou sur un marché qui
    ferme, le temps écoulé est supérieur — donc le coût de portage et le
    funding étaient sous-estimés d'autant (mesuré : −7 % sur BTC 1 h, −45 % sur
    XRP 15 m). Repli sur l'ancien calcul si les horodatages manquent.
    """
    try:
        t = ctx.df["time"]
        if 0 <= bar_entree < len(t) and 0 <= bar_sortie < len(t):
            ecart = (t[bar_sortie] - t[bar_entree]).total_seconds() / 3600.0
            if ecart >= 0:
                return float(ecart)
    except Exception:
        pass
    return max(0, bar_sortie - bar_entree) * _bar_to_days(ctx.timeframe) * 24.0
