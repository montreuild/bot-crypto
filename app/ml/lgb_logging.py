"""Routage des messages LightGBM vers le logging Python (ML-02).

LightGBM écrit ses messages depuis sa couche C++, hors du système de logging
Python : ni ``logging`` ni ``warnings.filterwarnings`` ne les atteignent.
``lgb.register_logger()`` est le seul point d'accroche officiel.

Motivation concrète — le chargement des boosters V4 historiques affichait, à
chaque appel :

    [LightGBM] [Warning] Ignoring unrecognized parameter 'bagging_by_query'
    found in model string.

Ce message est **structurellement inoffensif** : LightGBM énumère les
paramètres trouvés dans l'en-tête du fichier modèle et signale ceux que la
version courante ne connaît pas, AVANT de les ignorer. Un paramètre ignoré
n'influence par définition aucune prédiction (vérifié : prédictions
byte-identiques après retrait du paramètre du fichier). C'est un signal de
"modèle produit par une autre version/configuration de LightGBM", pas une
anomalie — exactement ce à quoi il faut s'attendre d'un artefact legacy ou
importé.

Plutôt que de patcher les fichiers un par un (ce qui ne couvre que les
artefacts déjà présents), on dégrade CE motif en DEBUG pour tout modèle,
présent ou futur. Les autres messages LightGBM gardent leur niveau — on ne
veut pas rendre le backend muet, seulement cesser de traiter un fait normal
comme un avertissement.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("lightgbm")

# Motifs dégradés en DEBUG. Volontairement ancrés et étroits : tout message
# non listé conserve son niveau d'origine.
_BENIGN_PATTERNS = (
    # Paramètre présent dans le fichier modèle mais inconnu de cette version
    # de LightGBM — ignoré par LightGBM lui-même, donc sans effet.
    re.compile(r"Ignoring unrecognized parameter", re.IGNORECASE),
    # Même nature, côté paramètres passés à l'entraînement.
    re.compile(r"Unknown parameter\b", re.IGNORECASE),
)

_registered = False


class _LightGBMLogger:
    """Adaptateur minimal conforme à l'interface attendue par
    ``lgb.register_logger`` (méthodes ``info``/``warning``)."""

    @staticmethod
    def _is_benign(msg: str) -> bool:
        return any(p.search(msg) for p in _BENIGN_PATTERNS)

    def info(self, msg: str) -> None:
        logger.debug(msg)

    def warning(self, msg: str) -> None:
        if self._is_benign(str(msg)):
            logger.debug(msg)
        else:
            logger.warning(msg)


def install(force: bool = False) -> bool:
    """Enregistre l'adaptateur auprès de LightGBM. Idempotent.

    Retourne ``True`` si l'enregistrement a eu lieu, ``False`` si déjà fait ou
    si LightGBM est absent (le dépôt doit rester importable sans lightgbm —
    seules les stratégies ML en dépendent).
    """
    global _registered
    if _registered and not force:
        return False
    try:
        import lightgbm as lgb
    except ImportError:
        return False
    try:
        lgb.register_logger(_LightGBMLogger())
    except Exception as e:  # pragma: no cover — API absente/renommée
        logging.getLogger(__name__).debug(f"register_logger indisponible : {e}")
        return False
    _registered = True
    return True
