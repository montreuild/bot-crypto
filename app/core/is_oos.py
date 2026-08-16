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


# Fraction réservée au HOLDOUT — la tranche que la recherche ne voit jamais.
#
# Pourquoi elle existe. `df_oos` sert quatre fois dans le pipeline actuel : à
# classer les N essais, à mesurer le gagnant, à mesurer la référence, et (via
# `df_full`) à vérifier la consistance walk-forward. Après N sélections, un
# score sur cette tranche n'est plus une estimation hors-échantillon : c'est un
# maximum d'ordre N, biaisé vers le haut par construction. Le Deflated Sharpe
# corrige exactement ce biais — mais sur le seul Sharpe, alors que
# `beats_baseline` exige aussi un PnL et un win-rate.
#
# Le holdout est donc découpé À LA FIN de l'historique, et la recherche se
# déroule intégralement AVANT lui : rien ne change dans la mécanique IS/OOS,
# elle s'applique simplement à la partie restante.
HOLDOUT_FRACTION_DEFAULT = 0.20


def split_with_holdout(df, warmup: int = WARMUP_BARS_DEFAULT,
                       oos_fraction: float = OOS_FRACTION_DEFAULT,
                       holdout_fraction: float = HOLDOUT_FRACTION_DEFAULT,
                       min_holdout_bars: int = 0) -> Tuple:
    """``(df_is, df_oos, split_idx, df_recherche, df_holdout)``.

    ``df_recherche`` est tout ce qui précède le holdout — c'est lui qui remplace
    ``df_full`` pour le walk-forward, sans quoi le gate de robustesse verrait la
    tranche qu'on veut garder aveugle.

    ``df_holdout`` vaut ``None`` — et le découpage retombe exactement sur
    :func:`split_is_oos` — quand l'historique ne permet pas de réserver une
    tranche EXPLOITABLE (``min_holdout_bars``, typiquement le ``min_bars``
    de la stratégie). Refuser franchement vaut mieux que servir un holdout de
    30 bougies sur lequel aucune décision ne tiendrait : l'appelant journalise
    la raison et reste sur l'ancien contrat.
    """
    n = len(df) if df is not None else 0
    if n == 0:
        return None, None, 0, None, None

    n_holdout = int(n * max(0.0, holdout_fraction))
    besoin = max(int(min_holdout_bars), 0)
    reste = n - n_holdout
    # Le holdout doit être exploitable, ET ce qui reste doit encore permettre
    # une recherche : sinon on a déplacé le problème au lieu de le résoudre.
    if (n_holdout <= 0 or n_holdout < besoin
            or reste < max(warmup + 100 + besoin, int(besoin / max(oos_fraction, 1e-9)))):
        df_is, df_oos, split = split_is_oos(df, warmup, oos_fraction)
        return df_is, df_oos, split, df, None

    df_recherche = df[:reste]
    df_holdout = df[reste:]
    df_is, df_oos, split = split_is_oos(df_recherche, warmup, oos_fraction)
    return df_is, df_oos, split, df_recherche, df_holdout


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
