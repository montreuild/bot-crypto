"""Contrat que les mixins de smart_money attendent de leur hôte.

ARCH-05 a découpé la stratégie en trois mixins (`_AnalysisMixin`,
`_PlansMixin`, `smart_money_signals`) qui s'appellent mutuellement à travers
`self`. Chacun voit donc des membres que sa propre classe ne définit pas —
invisibles au typage, et invérifiables : un renommage dans `Strategy` ne
casse rien avant l'exécution.

Classe d'annotations, pas un `Protocol` : hériter d'un `Protocol` convertit
le mixin lui-même en `Protocol` par le MRO (même choix que `LiveHost`,
cf. app/live/protocols.py). Aucun attribut n'existe à l'exécution — seules
les annotations comptent, donc rien n'est masqué dans la MRO réelle.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np


class SmartMoneyHost:
    #: Définis par `Strategy` (app/strategies/smart_money.py).
    name: str
    fixed_params: Dict[str, Any]
    _p: Any
    _none: Any
    _smc_params: Any
    _dir_gate: Any
    _htf_ok: Any
    _vol_ratio_arr: Any
    _ema_arr: Any
    #: Liés depuis `smart_money_signals` par affectation de classe.
    _signal_at: Any
    _build_trade: Any
    #: Définis par `_AnalysisMixin` (app/strategies/smart_money_aux.py).
    min_bars_required: Any
    _analyze_cached: Any
    _cache_valid: Any
    _build_aux: Any

    #: État de passe backtest, posé par `_AnalysisMixin._prime_backtest`.
    _bt_signals: Optional[Dict[int, dict]]
    _bt_events_opposite: Optional[Dict[int, str]]
    _bt_close_ref: Optional[np.ndarray]
    #: Mémo d'analyse (clé, résultat, séries auxiliaires).
    _ana_key: Optional[tuple]
    _ana_res: Optional[dict]
    _ana_aux: Optional[dict]
    #: Mémorisé par `score` : `_build_aux` est atteint par trois chemins qui
    #: n'ont pas tous le symbole sous la main (repli SMT).
    _symbole_courant: str
