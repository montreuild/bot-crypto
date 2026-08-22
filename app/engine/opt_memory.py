"""Portillon mémoire des jobs d'optimisation (DETTE-04).

Admission control anti-OOM : extrait d'`auto_optimizer.py`, inchangé.
"""

import logging
import threading
from typing import Optional

from app.engine.opt_workers import available_memory_bytes

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
#  Portillon mémoire (admission control anti-OOM inter-jobs)
# ════════════════════════════════════════════════════════════════════════════
# Le sémaphore ci-dessus borne le nombre de jobs concurrents par le CPU (cpu-1),
# mais PAS par la mémoire. Or avec n_jobs=1 (défaut de l'UI), chaque job évalue
# ses trials IN-PROCESS : cpu-1 backtests ML walk-forward — qui réentraînent
# LightGBM en boucle sur de larges matrices de features — tournent alors
# simultanément dans le MÊME process. Sur de gros jeux de données le pic mémoire
# cumulé épuise la RAM → std::bad_alloc LightGBM / OOM → mort silencieuse du
# process (aucune traceback). Le cap ``mem_aware_max_workers`` existant ne couvre
# QUE le ProcessPool d'un job (chemin n_jobs>1), jamais cette concurrence
# inter-jobs.
#
# Ce portillon borne la mémoire CUMULÉE des jobs actifs : un job n'entre en
# exécution que si son empreinte estimée tient dans le budget restant (70 % de la
# RAM dispo, snapshotée par lot). Règle anti-blocage : un job seul (aucun autre
# n'occupe de mémoire) est toujours admis, même si son estimation dépasse le
# budget — au pire les jobs lourds se sérialisent un par un.
_MEM_BUDGET_FRACTION = 0.70
_mem_cond            = threading.Condition()
_mem_committed       = 0                 # octets réservés par les jobs en cours
_mem_budget: Optional[int] = None        # snapshot du budget (None = non calculé)


def _snapshot_mem_budget() -> None:
    """(Re)mesure la RAM dispo et fixe le budget d'admission pour le lot courant.
    Appelé au démarrage d'un lot (start_async / optimize_sequential)."""
    global _mem_budget
    avail = available_memory_bytes()
    with _mem_cond:
        _mem_budget = int(avail * _MEM_BUDGET_FRACTION) if avail else None


def _mem_budget_bytes() -> Optional[int]:
    """Budget d'admission courant (snapshot paresseux au 1er appel si besoin).
    Retourne None si la RAM dispo est inconnue (→ portillon désactivé)."""
    if _mem_budget is None:
        _snapshot_mem_budget()
    return _mem_budget


def _estimate_job_bytes(df_is, df_oos, is_ml: bool) -> int:
    """Estimation prudente du pic mémoire d'un job d'optimisation.

    Échelonnée sur la TAILLE réelle des données (la variable qui provoque l'OOM) :
    nb de barres × nb de colonnes de features × 8 o × nb de copies vivant
    simultanément (features float64 + copie scalée + Dataset LightGBM biné +
    temporaires numpy), plus un plancher fixe (interpréteur, modèles, caches)."""
    rows = (len(df_is) if df_is is not None else 0) + (len(df_oos) if df_oos is not None else 0)
    if is_ml:
        feat_cols, copies, floor = 480, 6, 350 * 1024 * 1024
    else:
        feat_cols, copies, floor = 64, 3, 150 * 1024 * 1024
    return rows * feat_cols * 8 * copies + floor


def _acquire_mem_slot(need: int, cancel_event: Optional[threading.Event] = None) -> bool:
    """Réserve ``need`` octets dès qu'ils tiennent dans le budget restant.

    Retourne True si la réservation a été faite (→ à libérer ensuite via
    ``_release_mem_slot``), False si le portillon est désactivé (RAM inconnue) ou
    si annulé pendant l'attente."""
    global _mem_committed
    budget = _mem_budget_bytes()
    if not budget:
        return False
    with _mem_cond:
        while True:
            # committed == 0 : ce job est seul → toujours admis (anti-blocage).
            if _mem_committed == 0 or _mem_committed + need <= budget:
                _mem_committed += need
                return True
            if cancel_event is not None and cancel_event.is_set():
                return False
            _mem_cond.wait(timeout=1.0)


def _release_mem_slot(need: int) -> None:
    global _mem_committed
    with _mem_cond:
        _mem_committed = max(0, _mem_committed - need)
        _mem_cond.notify_all()


