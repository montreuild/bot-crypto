"""OBS-02 — logs JSON structurés et identifiants de corrélation.

Le problème que ça résout. Tous les logs du dépôt sont des f-strings :
``logger.info(f"[RecipeTrainer] {recipe}/{tf} : {split} train…")``. Lisible par
un humain, opaque pour une machine — pour répondre à « montre-moi tout ce qui
concerne l'entraînement 1h de omnibus_v4_multi », il faut une expression
régulière par format de message, et elle casse au premier reformulage.

**Ce module ne réécrit pas les 900 appels de log du dépôt.** Ce serait un
diff énorme, risqué, et à la valeur douteuse : le texte du message reste utile.
Il ajoute ce qui manquait autour — un enregistrement structuré (horodatage,
niveau, module, fichier, ligne, exception) et surtout un **identifiant de
corrélation** qui relie entre elles toutes les lignes issues d'une même
requête HTTP ou d'un même job de fond. C'est ce lien qui manquait le plus :
avec plusieurs threads (trader, retrain ML, optimiseur) écrivant dans le même
fichier, l'entrelacement rendait impossible de suivre une opération.

**contextvars et non thread-local.** Un ``ContextVar`` suit la tâche asyncio,
ce dont a besoin l'API FastAPI ; un thread-local ne l'aurait pas fait. Revers
connu et traité explicitement : un ``ContextVar`` ne traverse PAS un
``threading.Thread`` — le thread démarre avec un contexte vierge. Les jobs de
fond doivent donc transporter l'identifiant à la main, d'où
``run_with_correlation``. Ne pas le faire donnerait des jobs orphelins, ce qui
est précisément le défaut qu'on corrige.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as _dt
import json
import logging
import uuid
from typing import Any, Callable, Dict, Iterator, Optional

#: Identifiant de l'opération en cours. Vide hors de toute opération tracée —
#: un log de démarrage n'appartient à aucune requête, et lui inventer un
#: identifiant serait un faux lien.
_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="")

#: Champs déjà portés par ``LogRecord`` : tout le reste de ``record.__dict__``
#: est un extra fourni par l'appelant (``logger.info(msg, extra={...})``) et
#: mérite d'apparaître dans la sortie JSON.
_STD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "asctime", "message", "taskName", "correlation_id",
}


def get_correlation_id() -> str:
    return _correlation_id.get()


def set_correlation_id(value: str) -> contextvars.Token:
    return _correlation_id.set(value or "")


def new_correlation_id() -> str:
    """Identifiant court — 12 hex.

    Un UUID complet est illisible dans un `grep`, et 48 bits suffisent
    largement : la collision n'a de conséquence que si deux opérations
    concurrentes la subissent, et elle rendrait alors deux traces mêlées, pas
    une donnée fausse.
    """
    return uuid.uuid4().hex[:12]


@contextlib.contextmanager
def correlation_scope(value: Optional[str] = None) -> Iterator[str]:
    """Pose un identifiant le temps d'un bloc, puis restaure le précédent."""
    cid = value or new_correlation_id()
    token = set_correlation_id(cid)
    try:
        yield cid
    finally:
        _correlation_id.reset(token)


def run_with_correlation(cid: str, fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """Exécute ``fn`` sous l'identifiant ``cid`` — passerelle vers un thread.

    ``threading.Thread(target=run_with_correlation, args=(cid, f, …))`` suffit
    à ce qu'un job de fond reste rattaché à la requête qui l'a déclenché. Sans
    ça, la moitié intéressante d'une opération (l'entraînement, pas l'appel
    HTTP qui l'a lancé) tomberait hors de la trace.
    """
    with correlation_scope(cid):
        return fn(*args, **kwargs)


class CorrelationFilter(logging.Filter):
    """Attache l'identifiant courant à chaque enregistrement.

    Un filtre plutôt qu'un formatter : l'attribut doit exister pour TOUS les
    formatters attachés (console et fichier), et un formatter ne peut pas
    renseigner ce qu'un autre lira.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id()
        return True


class JsonFormatter(logging.Formatter):
    """Un objet JSON par ligne (JSON Lines) — directement ingérable.

    L'horodatage est en UTC et suffixé ``Z``. Le bot tourne sur des marchés
    de plusieurs fuseaux et une machine en Europe/Paris : une heure locale sans
    décalage rend deux lignes incomparables deux fois par an.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": _dt.datetime.fromtimestamp(
                record.created, _dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        cid = getattr(record, "correlation_id", "")
        if cid:
            payload["correlation_id"] = cid
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Emplacement : utile en debug, bruyant partout ailleurs.
        if record.levelno >= logging.WARNING:
            payload["module"] = record.module
            payload["line"] = record.lineno

        for key, value in record.__dict__.items():
            if key in _STD_ATTRS or key.startswith("_"):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)

        # ``default=str`` : un log ne doit jamais échouer à cause de son
        # propre formatage — un objet non sérialisable devient son repr.
        return json.dumps(payload, ensure_ascii=False, default=str)
