"""
Utilitaires partagés pour la couche live trading.

V4-B (ARCH-02) : la résolution des paramètres vit dans
app/core/param_resolution — les noms ci-dessous sont des RÉ-EXPORTS de
compatibilité pour les importeurs live/api historiques. Tout NOUVEAU code doit
importer depuis app.core.param_resolution directement.
"""
from app.core.sanitize import safe_float as _safe_float   # noqa: F401 — re-export
from app.core.sanitize import sanitize as _sanitize        # noqa: F401 — re-export
from app.core.param_resolution import (                    # noqa: F401 — re-exports
    _is_nan_like, _clean_param_dict, _merge_params,
    _GLOBAL_PARAM_KEYS, DEFAULT_CONFIG_SYMBOL,
    _is_legacy_tf_entry, _select_symbol_entry, resolve_strategy_params,
)
