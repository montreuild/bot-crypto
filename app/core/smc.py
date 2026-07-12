"""Moteur d'analyse Smart Money Concepts (SMC).

Détection automatique, en une passe causale O(n), des structures institutionnelles :

  - Swings (pivots fractals) confirmés ``swing_right`` barres après le pivot,
    étiquetés HH / HL / LH / LL par rapport au swing précédent de même nature.
  - Structure de marché : cassures de structure (BOS — Break of Structure,
    continuation) et changements de caractère (CHoCH — Change of Character,
    retournement), sur clôture au-delà du dernier swing confirmé.
  - Zones de liquidité (Liquidity Pools) : doubles/triples sommets et fonds
    (equal highs/lows à ``eq_tol_atr``×ATR près) → Buy-side liquidity au-dessus
    des sommets égaux, Sell-side liquidity sous les fonds égaux. Chaque swing
    isolé reste une poche de liquidité mineure (stops résiduels).
  - Sweeps (prises de liquidité / stop hunts) : mèche qui perce un pool ou un
    swing puis clôture de retour du bon côté (rejet).
  - Order Blocks (zones d'Offre/Demande) : dernière bougie opposée avant une
    impulsion forte (displacement : corps ≥ ``disp_body_atr``×ATR qui engloutit
    l'extrême précédent). Statut suivi : fresh → touché (mitigé) → invalidé.
  - Fair Value Gaps (FVG / imbalances) : gap entre high[i−2] et low[i] (et
    miroir), suivi comblé/mitigé.
  - Liquidity Voids : runs d'au moins ``void_min_bars`` bougies directionnelles
    consécutives traversant ≥ ``void_min_atr``×ATR — zone « fine » que le prix
    a parcourue sans trader, aimant naturel pour un retour (fill).
  - Breaker Blocks : order block invalidé → la zone inverse sa polarité
    (une demande transpercée devient offre, et réciproquement).
  - Premium / Discount : range de travail (dernier swing high ↔ swing low),
    équilibre à 50 %, zone OTE (Optimal Trade Entry, retracement 62–79 %).
  - Tendances : trendline support (2 derniers swing lows), résistance
    (2 derniers swing highs) et canal de régression linéaire.
  - Structure line (zigzag) : polyligne des swings alternés (peaks/troughs)
    pour le tracé « market structure », + projection de cycle sur le canal
    (expected peak/trough à la borne du canal).

Toutes les entités portent des indices de barres (``index``, ``formed_at``,
``swept_at``…) : à la barre ``i``, seules les données ≤ i ont été utilisées, ce
qui rend le résultat directement exploitable par un backtest sans lookahead.

Consommateurs :
  - ``app/strategies/smart_money.py`` (stratégie de trading basée sur ce moteur)
  - ``/api/scanner/smc``           (overlay graphique de la page scanner)

Ce module est une FAÇADE (V4-L / ARCH-14) : l'implémentation vit dans
``smc_primitives`` / ``smc_structure`` / ``smc_geometry`` / ``smc_volume`` /
``smc_sessions``. Tous les noms historiques restent importables d'ici —
aucun site d'appel n'a besoin de changer.
"""
from app.core.smc_primitives import (            # noqa: F401
    DEFAULTS, _MAX_KEEP, _empty_result, _flag_ob_structure,
    _last_opposite_candle, _params, _try_cluster_pool, _wilder_atr,
)
from app.core.smc_structure import analyze        # noqa: F401
from app.core.smc_geometry import (               # noqa: F401
    _cycle_projection, _premium_discount_at, _trendlines, _zigzag,
    liquidity_targets_above, liquidity_targets_below,
    premium_discount_at, trendline_value_at,
    void_targets_above, void_targets_below,
)
from app.core.smc_volume import (                 # noqa: F401
    _regression_channel, regression_channel_at, volume_profile,
)
from app.core.smc_sessions import (               # noqa: F401
    KILLZONES, SESSIONS, _htf_buckets, calendar_liquidity_levels,
    htf_analysis, htf_trend_series, killzone_flags, session_label,
    smt_series,
)
