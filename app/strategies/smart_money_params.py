"""Paramètres smart_money (ARCH-05) — extrait de smart_money.py.

Sélection des setups et filtres de signaux : le moteur ``app/core/smc.py``
reste inchangé. L'optimiseur explore ici les *flags de setup* et les
*seuils de filtrage* pour ne retenir que les combinaisons OOS rentables
(cf. ``strategies/smart_money.yaml`` → optimizer_results).
"""
from __future__ import annotations

from typing import Any, Dict, List

# ── Espace d'optimisation (filtres + activation des setups) ──────────────────
# Setups explorables : seuls ceux à True dans FIXED / YAML sont tradés par
# défaut. L'optimiseur peut réactiver breakers/BPR/rejection pour un TF donné.
PARAM_SPACE: Dict[str, List] = {
    # Filtres de qualité (long terme)
    'min_score': [0.0, 0.55, 0.65, 0.70, 0.75],
    'min_rr': [1.0, 1.2, 1.5, 2.0],
    'min_gain_pct': [0.4, 0.8, 1.2],
    'sl_buffer_atr': [0.15, 0.25, 0.5],
    'chop_filter_max': [0.0, 50.0, 61.8],
    'htf_filter': ['off', 'soft', 'strict'],
    'ema_filter_len': [0, 100, 200],
    'require_inducement': [True, False],
    'mitigation_mode': ['off', 'exclude', 'penalize'],
    # Activation setups (ablation / sélection long terme)
    'use_sweep_reversal': [True, False],
    'use_ob_retest': [True, False],
    'use_breakers': [True, False],
    'use_rejection_blocks': [True, False],
    'use_bpr': [True, False],
    'use_calendar_liquidity': [False, 'targets', 'sweeps', True],
    # Bonus / conf (souvent couplés aux seuils élevés)
    'kz_bonus': [True, False],
    'kz_filter': [True, False],
    'amd_bonus': [True, False],
    'vp_confluence': [True, False],
    'candle_bonus': [True, False],
    'use_void_targets': [True, False],
    # Gestion position
    'time_stop_bars': [0, 12, 16, 24],
    'use_trailing': [True, False],
    'trail_mult': [2.0, 2.5, 3.5],
    # L3 (§60) — porte de structure séquentielle.
    'structure_gate': ['off', 'direction', 'no_pullback', 'both'],
    # L4 (§78 §79) — choix de la cible, et plafond de distance au POI (§32 §23).
    'target_mode': ['nearest', 'expected_value'],
    'max_stop_atr': [0, 3.0, 4.0],
    # L6 (§71) — tiers de setup : porte et/ou modulation du risque.
    'tier_gate': [True, False],
    'tier_sizing': [True, False],
    # L2 (§2 §4) — décision au NET. 0 = off (R/R brut, comportement historique).
    'min_net_rr': [0, 1.5, 2.0, 2.5],
    'cost_multiple': [0, 5.0, 8.0],
    # L1 (§29 §30) — sorties partielles et mode de trailing du runner.
    'use_partial_exits': [True, False],
    'tp1_r': [0.75, 1.0, 1.5],
    'tp1_fraction': [0.25, 0.5],
    'tp2_fraction': [0.0, 0.25],
    'trail_mode': ['structure', 'dynamic', 'chandelier'],
    'size_by_confluence': [True, False],
    'size_conf_slope': [2.0, 3.0, 4.0],
    'choch_exit': [True, False],
    # Structure SMC (léger — ne change pas le cœur smc, seulement params)
    'swing_len': [2, 3, 5],
    'eq_tol_atr': [0.15, 0.25, 0.4],
    'disp_body_atr': [1.0, 1.3, 1.6],
    'pd_mode': ['swing', 'ipda'],
    'ipda_lookback': [20, 40, 60],
    'ext_structure_filter': [True, False],
    'tp_measured_move': [True, False],
    'inv_fvg_bonus': [True, False],
    'smt_bonus': [True, False],
    'smt_filter': [True, False],
    'smt_at_origin': [True, False],
    'judas_bonus': [True, False],
    'judas_filter': [True, False],
    'tp_std_dev': [True, False],
    'sb_bonus': [True, False],
    'sb_filter': [True, False],
    'amd_session_anchored': [True, False],
    # ⚠ `amd_range_atr: 2.0` ne déclenche la compression qu'UNE fois sur 8 000
    # barres en BTC 1 h (mesuré) : le module était donc inerte, non par défaut
    # de conception mais parce que son seuil ne correspond à aucun régime de ce
    # marché. Exposé en plage large pour que l'optimiseur puisse le calibrer.
    'amd_range_atr': [2.0, 3.5, 5.0, 7.0],
    'amd_bars': [8, 12, 20],
}

FIXED_PARAMS: Dict[str, Any] = {
    'swing_len': 3,
    'eq_tol_atr': 0.25,
    'disp_body_atr': 1.3,
    'ob_lookback': 5,
    'fvg_min_atr': 0.2,
    'min_gain_pct': 0.4,
    'min_rr': 1.2,
    'sl_buffer_atr': 0.25,
    'tp_rr_fallback': 2.0,
    'tp_front_run_atr': 0.1,
    'vol_confluence': 1.2,
    'allow_counter_trend': False,
    # ── Setups retenus (validation long terme BTC 2018→2026) ─────────────
    # SWEEP_REVERSAL + OB_RETEST : cœur rentable.
    # BREAKER / REJECTION / BPR : dilutifs ou négatifs → off (optimisable).
    'use_sweep_reversal': True,
    'use_ob_retest': True,
    'use_breakers': False,
    'use_rejection_blocks': False,
    'use_bpr': False,
    'use_void_targets': True,
    'tl_tol_atr': 0.3,
    'htf_filter': 'soft',
    'htf_mult': 4,
    'rb_wick_atr': 0.5,
    'vp_confluence': False,
    'vp_targets': False,
    'vp_lookback': 240,
    'vp_bins': 40,
    'kz_bonus': False,
    'kz_filter': False,
    'amd_bonus': False,
    'amd_bars': 12,
    'amd_range_atr': 2.0,
    # min_score : seuil de confluence (0 = laisse score_threshold engine).
    # Les optimizer_results TF surchargent (ex. 4h → 0.70).
    'min_score': 0.0,
    'ema_filter_len': 200,
    'choch_exit': False,
    'max_window': 3000,
    'ob_max_age': 250,
    'pool_max_age': 500,
    'choch_guard_bars': 5,
    'time_stop_bars': 0,
    'use_trailing': False,
    'trail_mult': 2.5,
    'ts_profit_r': 1.0,
    # L3 — la porte filtre (off), le journal enregistre toujours l'état : sans
    # le second on ne pourrait pas mesurer si le premier vaut quelque chose.
    'structure_gate': 'off',
    'structure_journal': True,
    # L4 — `nearest` reproduit le ciblage historique ; 0 = plafond de stop off.
    'target_mode': 'nearest',
    'max_stop_atr': 0,
    # L6 — tiers journalisés toujours, mais sans effet sur la décision : c'est
    # la mesure `by_tier` qui doit dire s'ils valent quelque chose.
    'tier_gate': False,
    'tier_sizing': False,
    'tier_a_score': 0.85,
    # L2 — off : les optimizer_results du YAML ont été mesurés sur le R/R BRUT.
    # `net_rr` reste journalisé, pour mesurer l'écart avant de le refermer.
    'min_net_rr': 0,
    'cost_multiple': 0,
    'taker_fee': 0.001,
    'spread_pct': 0.0005,
    'break_body_atr': 0.5,
    'mss_sweep_lookback': 8,
    'warning_max_bars': 40,
    # L1 — off : les optimizer_results du YAML ont été mesurés en tout-ou-rien.
    'use_partial_exits': False,
    'tp1_r': 1.0,
    'tp1_fraction': 0.25,
    'tp2_fraction': 0.25,
    'trail_mode': 'structure',
    'size_by_confluence': False,
    'size_conf_slope': 3.0,
    'size_conf_center': 0.83,
    'ext_structure_filter': False,
    'ext_swing_len': 8,
    'tp_measured_move': False,
    'inv_fvg_bonus': False,
    'chop_filter_max': 0.0,
    'chop_len': 14,
    'candle_bonus': False,
    'smt_correlate_path': '',
    'smt_lookback': 20,
    'smt_bonus': False,
    'smt_filter': False,
    'smt_conf': 0.05,
    'smt_at_origin': False,
    'require_inducement': False,
    'inducement_lookback': 12,
    'judas_bonus': False,
    'judas_filter': False,
    'judas_open_hour': 8,
    'judas_window': 3,
    'judas_lookback': 12,
    'tp_std_dev': False,
    'sb_bonus': False,
    'sb_filter': False,
    'pd_mode': 'swing',
    'ipda_lookback': 40,
    'mitigation_mode': 'off',
    'amd_session_anchored': False,
    'use_calendar_liquidity': False,
}
