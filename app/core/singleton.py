"""Accesseur singleton paresseux thread-safe (ARCH-13).

Factorise le pattern double-checked-locking qui était recopié à l'identique
dans ``candle_store.get_store`` et ``feature_store.get_feature_store``.
"""
import threading
from typing import Any, Callable, Optional


class _LazySingleton:
    """Appelable exposant ``instance`` et ``set()``.

    TEST-02 : la version fonction portait l'état dans des attributs posés
    APRÈS la définition (``get.instance = None``) — invisibles au typage,
    donc quatre ``type: ignore`` sur des lectures parfaitement légitimes.
    Une classe déclare le même contrat sans les masquer.
    """

    def __init__(self, factory: Callable[..., Any], doc: Optional[str] = None):
        self._factory = factory
        self._lock = threading.Lock()
        self.instance: Optional[Any] = None
        self.__name__ = f"get_{getattr(factory, '__name__', 'instance').lower()}"
        if doc:
            self.__doc__ = doc

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.instance is None:
            with self._lock:
                if self.instance is None:
                    self.instance = self._factory(*args, **kwargs)
        return self.instance

    def set(self, obj: Optional[Any]) -> None:
        """Injecte une instance (tests) ; ``None`` ré-arme la création paresseuse."""
        with self._lock:
            self.instance = obj


def lazy_singleton(factory: Callable[..., Any],
                   doc: Optional[str] = None) -> _LazySingleton:
    """Retourne un accesseur ``get(*args, **kwargs)`` qui crée l'instance via
    ``factory`` au premier appel (double-checked locking). Les arguments des
    appels suivants sont ignorés — l'instance existe déjà.

    L'accesseur expose ``get.instance`` (instance courante ou ``None``) et
    ``get.set(obj)`` pour l'injection en test ; ``get.set(None)`` ré-arme la
    création paresseuse.
    """
    return _LazySingleton(factory, doc)
