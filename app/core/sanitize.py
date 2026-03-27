"""
Sanitisation JSON — source unique de vérité.

Regroupe les utilitaires de nettoyage NaN/Inf pour garantir une
sérialisation JSON valide dans toute l'application :
  - ``safe_float``        : valeur scalaire sûre (NaN/Inf → fallback)
  - ``sanitize``          : nettoyage récursif dict/list (NaN/Inf → None)
  - ``clean_for_json``    : sanitize + filtrage des clés privées + sérialisation robuste
  - ``CleanJSONResponse`` : réponse FastAPI utilisant clean_for_json automatiquement
"""
import json
import math
from typing import Any

from fastapi.responses import JSONResponse


# ---------------------------------------------------------------------------
# Scalaire
# ---------------------------------------------------------------------------

def safe_float(v, fallback=None):
    """Convertit nan/inf en *fallback* pour garantir la sérialisation JSON.

    >>> safe_float(float('nan'), 0.0)
    0.0
    >>> safe_float(float('inf'))  # fallback par défaut = None
    >>> safe_float(42.5)
    42.5
    """
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return fallback
        return f
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
# Récursif — usage interne (live trading, sérialisation légère)
# ---------------------------------------------------------------------------

def sanitize(obj):
    """Parcourt récursivement un dict/list et remplace les float nan/inf par None."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, float):
        return safe_float(obj)
    return obj


# ---------------------------------------------------------------------------
# Récursif + strict — usage API (filtrage clés privées, types inconnus → None)
# ---------------------------------------------------------------------------

def clean_for_json(obj: Any) -> Any:
    """Sanitise récursivement pour JSON : NaN→None, ±Inf→None, clés privées ignorées.

    Contrairement à ``sanitize``, cette variante :
      - filtre les clés commençant par ``_`` (attributs internes)
      - vérifie la sérialisabilité des types inconnus (fallback → None)
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {
            k: clean_for_json(v)
            for k, v in obj.items()
            if not str(k).startswith("_")
        }
    if isinstance(obj, list):
        return [clean_for_json(v) for v in obj]
    if isinstance(obj, (int, str, bool, type(None))):
        return obj
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Réponse FastAPI
# ---------------------------------------------------------------------------

class CleanJSONResponse(JSONResponse):
    """JSONResponse qui neutralise les float NaN/Inf sur toutes les réponses."""

    def render(self, content) -> bytes:
        return json.dumps(
            clean_for_json(content),
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")
