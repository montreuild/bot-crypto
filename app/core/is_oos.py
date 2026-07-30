"""Convention UNIQUE de split IS/OOS pour l'optimisation (BT-08).

Avant ce module, quatre variantes coexistaient sans constante partagée :
``auto_optimizer`` dupliquait ``WARMUP = 210`` + ``0.65`` en dur à TROIS
endroits (start_async / optimize_sequential / optimize_all), pendant
qu'``optimizer.py`` définissait sa propre ``_OOS_FRACTION = 0.35`` — les deux
pouvaient diverger silencieusement.

Cas volontairement DISTINCTS (ne pas « unifier » aveuglément) :
- ``WalkForwardAnalyzer`` : méthodologie par folds glissants (pas un split
  2 tranches) — il importe seulement le warmup par défaut ;
- ``oos_tracker`` : fenêtre glissante de forward-test (re-backtest récent
  comparé au live), pas un split IS/OOS d'optimisation.
"""
from typing import Tuple

# Barres de chauffe minimales avant que les stratégies produisent des signaux
# exploitables (indicateurs longs : EMA200 + marge).
WARMUP_BARS_DEFAULT = 210

# Fraction de l'historique réservée à l'out-of-sample (l'IS reçoit 1 − x).
OOS_FRACTION_DEFAULT = 0.35


def split_is_oos(df, warmup: int = WARMUP_BARS_DEFAULT,
                 oos_fraction: float = OOS_FRACTION_DEFAULT) -> Tuple:
    """Coupe ``df`` en ``(df_is, df_oos, split_idx)`` selon la convention canon.

    ``split_idx = max(warmup + 100, int(n × (1 − oos_fraction)))`` — l'IS ne
    descend jamais sous ``warmup + 100`` barres (sinon aucune stratégie ne
    produit de trade côté IS). Reproduit à l'identique le calcul historique
    d'``auto_optimizer`` (byte-identique : mêmes indices de coupure).

    ``df`` peut être None (retourne ``(None, None, 0)``).
    """
    n = len(df) if df is not None else 0
    if n == 0:
        return None, None, 0
    split = max(warmup + 100, int(n * (1.0 - oos_fraction)))
    return df[:split], df[split:], split
