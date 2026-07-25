"""Cache process-wide des entraînements ML inline (backtest / optimiseur).

Problème : les stratégies ML « retrained » (opus_omnibus_v7/v11/v12,
opus_stat_retrained_v4, opus_omnibus_v10_retrained…) réentraînent leurs
modèles LightGBM en walk-forward toutes les ``retrain_every`` barres pendant
un backtest. Sur une optimisation (40 trials × 50 000 bougies), les mêmes
entraînements (même fenêtre de données, mêmes hyperparamètres d'entraînement)
sont relancés à l'identique dans chaque trial qui ne fait varier que des
paramètres de *décision* (seuils, SL/TP…) — des heures de calcul redondant.

Solution : un cache LRU au niveau du process, partagé entre les instances de
stratégie. Les workers de l'optimiseur étant persistants
(ProcessPoolExecutor + _worker_init), le cache survit d'un trial à l'autre.

Clé = (classe, tf, empreinte de la fenêtre d'entraînement, params
d'entraînement). Valeur = snapshot de l'état par TF (modèles, feature_cols,
medians, AUC, meta). Les objets modèles sont partagés par référence (lecture
seule en prédiction) ; les dicts/listes sont copiés à la restauration.

Dimensionnement : TRAIN_CACHE_MAX entrées (env var, défaut 160 ≈ une passe
complète de retrains sur 50k bougies à retrain_every=800, plusieurs jeux de
params). TRAIN_CACHE_MAX=0 désactive le cache.
"""
import logging
import os
import threading
from collections import OrderedDict
from typing import Callable, Dict, Iterable

logger = logging.getLogger(__name__)

_MAX = int(os.environ.get("TRAIN_CACHE_MAX", "160"))
_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_lock = threading.Lock()
_stats = {"hits": 0, "misses": 0, "empty_snapshots": 0}


def _df_fingerprint(df) -> tuple:
    """Empreinte légère et déterministe de la fenêtre d'entraînement."""
    try:
        n = len(df)
        t0 = str(df["time"][0]) if "time" in df.columns else ""
        t1 = str(df["time"][-1]) if "time" in df.columns else ""
        c1 = float(df["close"][-1]) if "close" in df.columns else 0.0
        return (n, t0, t1, c1)
    except Exception:
        return (id(df),)


def aligned_train_window(df, retrain_every: int, n_train: int):
    """Fenêtre d'entraînement (tail) dont la fin est alignée sur la grille
    ``retrain_every``. Retourne ``(train_df, start_offset)``.

    Le déclenchement d'un retrain dépend du compteur d'appels ``score()``,
    lui-même fonction des trades du trial (on ne cherche un signal que hors
    position). Deux trials aux seuils de décision différents retrainent donc
    à des barres légèrement décalées → fenêtres distinctes → aucun hit du
    cache process-wide. En alignant la fin de fenêtre sur le multiple de
    ``retrain_every`` inférieur, les fenêtres deviennent identiques entre
    trials (quantifiées sur la même grille), avec une staleness bornée à
    ``retrain_every`` barres — équivalente à celle du déclenchement.
    """
    step = max(int(retrain_every), 1)
    end = (len(df) // step) * step
    if end <= 0:
        end = len(df)
    start = max(0, end - int(n_train))
    return df.slice(start, end - start), start



def _state_dict(strategy, attr: str):
    """Dict d'état par TF, que la stratégie le nomme ``attr`` ou ``_attr``.

    ``TrainState.STATE_ATTRS`` liste des noms SANS underscore
    (``amp_models``) parce qu'ils décrivent l'état du backend ; les stratégies
    qui composent ``MLBackend`` les exposent AVEC (``_amp_models``). Chercher
    les deux évite de faire dépendre la correction du cache d'une convention
    de nommage — c'est ce décalage qui vidait le snapshot de V11/V12.
    """
    d = getattr(strategy, attr, None)
    if isinstance(d, dict):
        return d
    return getattr(strategy, f"_{attr.lstrip('_')}", None)


def cached_train(strategy, df, tf_key: str, params: dict,
                 train_impl: Callable[..., bool],
                 state_attrs: Iterable[str],
                 param_keys: Iterable[str]) -> bool:
    """Exécute ``train_impl(df, tf_key, params)`` avec cache process-wide.

    - ``state_attrs`` : noms des dicts d'état par TF de la stratégie
      (ex. ``_amp_models``, ``_medians``…) à snapshotter/restaurer.
    - ``param_keys``  : sous-ensemble des params qui influencent
      l'entraînement (les autres — seuils de décision — sont ignorés
      pour maximiser les hits entre trials de l'optimiseur).
    """
    if _MAX <= 0:
        return train_impl(df, tf_key, params)

    def _hashable(v):
        if isinstance(v, list):
            return tuple(v)
        if isinstance(v, dict):
            return tuple(sorted(v.items()))
        return v

    key = (
        type(strategy).__module__,
        tf_key,
        _df_fingerprint(df),
        tuple((k, _hashable(params.get(k))) for k in sorted(param_keys)),
    )

    with _lock:
        snap = _cache.get(key)
        if snap is not None:
            _cache.move_to_end(key)
            _stats["hits"] += 1

    if snap is not None:
        for attr, value in snap.items():
            d = _state_dict(strategy, attr)
            if isinstance(d, dict):
                d[tf_key] = dict(value) if isinstance(value, dict) \
                    else list(value) if isinstance(value, list) else value
        trained = getattr(strategy, "_trained_tfs", None)
        if isinstance(trained, set):
            trained.add(tf_key)
        auc_map = _state_dict(strategy, "best_auc_per_tf")
        if isinstance(auc_map, dict) and tf_key in auc_map:
            strategy._best_auc = max(
                getattr(strategy, "_best_auc", 0.0) or 0.0, auc_map[tf_key])
        return True

    ok = train_impl(df, tf_key, params)
    if not ok:
        with _lock:
            _stats["misses"] += 1
        return ok

    snap = {}
    for attr in state_attrs:
        d = _state_dict(strategy, attr)
        if isinstance(d, dict) and tf_key in d:
            v = d[tf_key]
            snap[attr] = dict(v) if isinstance(v, dict) \
                else list(v) if isinstance(v, list) else v

    # Un snapshot VIDE après un entraînement réussi ne peut pas être
    # légitime : le restaurer marquerait la stratégie « entraînée » sans lui
    # rendre le moindre modèle. C'est exactement ce qui se produisait pour
    # opus_omnibus_v11/v12, dont les ``_TRAIN_STATE_ATTRS`` sont ceux de
    # ``TrainState`` (``amp_models``) alors que la stratégie expose
    # ``_amp_models`` : chaque hit produisait un « Modèle indisponible », donc
    # zéro signal à partir du 2e essai de l'optimiseur dans un même process.
    # Ne rien mettre en cache est toujours correct ; mettre en cache un état
    # vide ne l'est jamais.
    if not snap:
        logger.warning(
            f"[TrainCache] snapshot vide pour {type(strategy).__name__}/{tf_key} "
            f"(attributs cherchés : {tuple(state_attrs)}) — mise en cache refusée. "
            f"Vérifiez _TRAIN_STATE_ATTRS : les noms doivent correspondre aux "
            f"attributs réellement portés par la stratégie."
        )
        with _lock:
            _stats["misses"] += 1
            _stats["empty_snapshots"] += 1
        return ok

    with _lock:
        _stats["misses"] += 1
        _cache[key] = snap
        _cache.move_to_end(key)
        while len(_cache) > _MAX:
            _cache.popitem(last=False)
    return ok


def stats() -> Dict[str, int]:
    with _lock:
        return {**_stats, "entries": len(_cache), "max": _MAX}


def clear() -> None:
    with _lock:
        _cache.clear()
        _stats["hits"] = _stats["misses"] = _stats["empty_snapshots"] = 0
