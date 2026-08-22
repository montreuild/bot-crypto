"""Mémos de non-disponibilité du CandleStore (DETTE-04).

Trois mécanismes évitent de reposer une question dont la réponse est connue :
créneaux confirmés absents (persistant, `ohlcv_absents`), « historique épuisé »
et cooldown de recousage — ces deux derniers en mémoire vive, 6 h.
"""

import logging
import time
from typing import Any, Dict, Optional

from app.core.candle_helpers import _NO_HISTORY_RETRY_S

logger = logging.getLogger(__name__)


class CandleMemosHost:
    """Contrat attendu de `CandleStore` — annotations, pas de Protocol
    (même choix que `LiveHost`, cf. app/live/protocols.py)."""

    _no_history: Dict[tuple, tuple]
    _unfillable_gaps: Dict[tuple, tuple]
    _no_history_lock: Any
    _path: Any


class CandleMemosMixin(CandleMemosHost):
    def _history_exhausted(self, symbol: str, tf: str,
                           first_ms: Optional[int]) -> bool:
        with self._no_history_lock:
            entry = self._no_history.get((symbol, tf))
        if entry is None:
            return False
        marked_first, retry_at = entry
        if time.time() >= retry_at:
            return False        # le provider a peut-être publié de l'historique
        # La borne basse a bougé : de l'historique est arrivé par une autre
        # voie (backfill hors ligne, autre timeframe). Le mémo ne vaut plus.
        return marked_first == first_ms

    def _mark_exhausted(self, symbol: str, tf: str,
                        first_ms: Optional[int]) -> None:
        with self._no_history_lock:
            self._no_history[(symbol, tf)] = (first_ms,
                                              time.time() + _NO_HISTORY_RETRY_S)

    def _forget_exhausted(self, symbol: str, tf: str) -> None:
        with self._no_history_lock:
            self._no_history.pop((symbol, tf), None)

    def _gaps_on_cooldown(self, symbol: str, tf: str, missing: int) -> bool:
        with self._no_history_lock:
            entry = self._unfillable_gaps.get((symbol, tf))
        if entry is None:
            return False
        marked_missing, retry_at = entry
        if time.time() >= retry_at:
            return False
        return marked_missing == missing

    def _mark_gaps_unfillable(self, symbol: str, tf: str, missing: int) -> None:
        with self._no_history_lock:
            self._unfillable_gaps[(symbol, tf)] = (
                missing, time.time() + _NO_HISTORY_RETRY_S)

    def _forget_gaps_unfillable(self, symbol: str, tf: str) -> None:
        with self._no_history_lock:
            self._unfillable_gaps.pop((symbol, tf), None)

    def oublier_memos(self, symbol: str, tf: str) -> None:
        """Efface les trois mémos qui font sauter une redemande.

        Trois mécanismes distincts évitent de reposer une question dont on
        connaît la réponse : les créneaux confirmés absents (persistant), le
        mémo « historique épuisé » et le cooldown de recousage (6 h, mémoire
        vive). Chacun est justifié en régime nominal — mais un geste explicite
        de l'opérateur (`refetch`, `backfill-equities`) veut dire « refais la
        mesure », pas « redonne-moi ta conclusion ». Le `refetch` n'effaçait
        que le premier : un symbole déclaré épuisé par le live dans les 6 h
        précédentes était sauté, en log DEBUG donc invisible.
        """
        from app.core import ohlcv_absents as _abs
        _abs.oublier(self._path(symbol, tf), symbol, tf)
        self._forget_exhausted(symbol, tf)
        self._forget_gaps_unfillable(symbol, tf)

