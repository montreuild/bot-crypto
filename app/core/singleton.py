"""Accesseur singleton paresseux thread-safe (ARCH-13).

Factorise le pattern double-checked-locking qui était recopié à l'identique
dans ``candle_store.get_store`` et ``feature_store.get_feature_store``.
"""
import threading


def lazy_singleton(factory, doc: str | None = None):
    """Retourne un accesseur ``get(*args, **kwargs)`` qui crée l'instance via
    ``factory`` au premier appel (double-checked locking). Les arguments des
    appels suivants sont ignorés — l'instance existe déjà.

    L'accesseur expose ``get.instance`` (instance courante ou ``None``) et
    ``get.set(obj)`` pour l'injection en test ; ``get.set(None)`` ré-arme la
    création paresseuse.
    """
    lock = threading.Lock()

    def get(*args, **kwargs):
        if get.instance is None:
            with lock:
                if get.instance is None:
                    get.instance = factory(*args, **kwargs)
        return get.instance

    def set_instance(obj) -> None:
        with lock:
            get.instance = obj  # type: ignore[attr-defined]

    get.instance = None  # type: ignore[attr-defined]
    get.set = set_instance  # type: ignore[attr-defined]
    get.__name__ = f"get_{getattr(factory, '__name__', 'instance').lower()}"
    if doc:
        get.__doc__ = doc
    return get
