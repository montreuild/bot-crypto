"""P2-8 : Façade pour scanner_service — découpage planifié.

``scanner_service.py`` fait 778 lignes avec 7 fonctions. Ce module façade
ré-exporte les fonctions pour permettre aux appelants d'importer depuis un
module plus léger, documentant le découpage futur en sous-modules :

- ``scanner_chart.py`` : ``build_chart_payload``
- ``scanner_smc.py`` : ``build_smc_payload``, ``build_smc_replay_payload``
- ``scanner_signals.py`` : ``build_signals_payload``, ``_setup_series_v11``
- ``scanner_utils.py`` : ``_atr_at``, ``_tp_sl``
"""
from app.api.services.scanner_service import (
    _atr_at,
    _setup_series_v11,
    _tp_sl,
    build_chart_payload,
    build_signals_payload,
    build_smc_payload,
    build_smc_replay_payload,
)

__all__ = [
    'build_chart_payload',
    'build_smc_payload',
    'build_smc_replay_payload',
    'build_signals_payload',
    '_setup_series_v11',
    '_atr_at',
    '_tp_sl',
]
