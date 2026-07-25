"""``MLBackendMixin`` — cycle de vie ML d'une stratégie, en un seul endroit.

Chaque stratégie ML portait sa propre copie de la même mécanique :
entraînement, persistance, inférence, état par timeframe, cache de backtest.
Mesuré avant factorisation (docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §1.3) :
**2 396 lignes de plomberie sur 10 742**, soit 22 % du code des stratégies ML —
et jusqu'à 38 % pour celles qui ne composaient pas ``MLBackend``.

Ce mixin est cette mécanique, extraite de ``opus_omnibus_v11`` qui l'avait déjà
en composition. Une stratégie qui l'utilise ne garde que son **routing** :
setups, seuils, sizing, sorties.

**Ce que la bascule ne change pas.** Les ``_train_impl`` autonomes qu'il
remplace produisaient déjà exactement les mêmes modèles que ``MLBackend`` —
écart maximum 0.000000 sur ``amp`` comme sur ``dir``, corrélation 1.0000, pour
les trois stratégies concernées et chacune sur les paramètres de SA recette
(``scripts/check_train_impl_equivalence.py``). Sans cette mesure préalable, la
factorisation aurait été un pari sur du code d'entraînement dupliqué.

Deux détails portent des corrections de bugs et ne doivent pas être « simplifiés » :

* ``_train`` fusionne ``_DEFAULTS`` sous les params reçus. ``score()`` entraîne
  par ce chemin, pas par ``fit()`` : sans la fusion, les paramètres
  d'entraînement non passés explicitement n'atteignaient jamais le backend, qui
  appliquait les valeurs de son constructeur — une stratégie déclarant
  ``calibrate: False`` s'entraînait quand même en calibré.
* ``_TRAIN_STATE_ATTRS`` vient de ``TrainState`` et nomme les attributs SANS
  underscore, alors que les propriétés ci-dessous les exposent AVEC. C'est
  ``app.core.train_cache`` qui réconcilie les deux ; le décalage vidait
  silencieusement le snapshot du cache.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import polars as pl

from app.ml.backend import MLBackend

logger = logging.getLogger(__name__)


class MLBackendMixin:
    """Cycle de vie ML délégué à ``MLBackend``.

    La stratégie hôte doit définir ``name``, ``model_dir`` et ``_DEFAULTS``, et
    peut régler l'entraînement via les trois attributs de classe ci-dessous —
    valeurs de repli seulement : la recette et les ``_DEFAULTS`` ont le dernier
    mot au moment de l'entraînement.
    """

    ml_calibrate: bool = True
    ml_prune_features: bool = True
    ml_multi_horizon: bool = True

    #: ``False`` pour un preset dont la recette ne produit aucun artefact
    #: (prédicteur proxy) : ni entraînement, ni garde « pas encore entraîné ».
    uses_artifact: bool = True

    def __init__(self):
        self.ml = MLBackend(
            name=self.name,
            model_dir=self.model_dir,
            calibrate=self.ml_calibrate,
            prune_features=self.ml_prune_features,
            multi_horizon=self.ml_multi_horizon,
        )

    # ── État ML — exposé sur la Strategy pour ``train_cache`` ────────────────
    _TRAIN_STATE_ATTRS = MLBackend._TRAIN_STATE_ATTRS
    _TRAIN_PARAM_KEYS = MLBackend._TRAIN_PARAM_KEYS

    @property
    def _amp_models(self): return self.ml.state.amp_models
    @property
    def _dir_models(self): return self.ml.state.dir_models
    @property
    def _amp_cal(self): return self.ml.state.amp_cal
    @property
    def _dir_cal(self): return self.ml.state.dir_cal
    @property
    def _feature_cols(self): return self.ml.state.feature_cols
    @property
    def _kept_features(self): return self.ml.state.kept_features
    @property
    def _medians(self): return self.ml.state.medians
    @property
    def _trained_tfs(self): return self.ml.state.trained_tfs
    @property
    def _best_auc_per_tf(self): return self.ml.state.best_auc_per_tf
    @property
    def _train_meta(self): return self.ml.state.train_meta
    @property
    def _last_retrain(self): return self.ml.state.last_retrain
    @property
    def _call_cnt(self): return self.ml.state.call_cnt

    @property
    def _best_auc(self): return self.ml.state.best_auc

    @_best_auc.setter
    def _best_auc(self, v): self.ml.state.best_auc = float(v)

    @property
    def _managed_externally(self): return self.ml.state.managed_externally

    @_managed_externally.setter
    def _managed_externally(self, v): self.ml.state.managed_externally = bool(v)

    # ── Cache de features du backtest ───────────────────────────────────────
    @property
    def _bt_features(self): return self.ml._bt_features
    @property
    def _bt_features_len(self): return self.ml._bt_features_len

    @property
    def _bt_train_offset(self): return self.ml._bt_train_offset

    @_bt_train_offset.setter
    def _bt_train_offset(self, v): self.ml._bt_train_offset = v

    @property
    def _lock(self): return self.ml._lock

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        self.ml.prepare_for_backtest(df, getattr(self, "_bt_symbol", None),
                                     getattr(self, "_bt_tf", None))

    # ── Cycle de vie ────────────────────────────────────────────────────────
    @property
    def is_trained(self) -> bool:
        return self.ml.is_trained

    @property
    def managed_externally(self) -> bool:
        return self.ml.managed_externally

    @managed_externally.setter
    def managed_externally(self, v: bool) -> None:
        self.ml.managed_externally = v

    def reset_model(self) -> None:
        self.ml.reset_model()

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        warmup = int(p.get("warmup_bars", self._DEFAULTS["warmup_bars"]))
        return max(230, warmup + 30)

    # ── Persistance ─────────────────────────────────────────────────────────
    def save_model(self, path: str, extra_meta: Optional[dict] = None) -> None:
        self.ml.save_model(path, extra_meta=extra_meta)

    def load_model(self, path: str) -> bool:
        return self.ml.load_model(path)

    # ── Entraînement ────────────────────────────────────────────────────────
    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        self.ml.fit(df, params, defaults=self._DEFAULTS, strategy=self)

    def _train(self, df: pl.DataFrame, tf_key: str, params: dict) -> bool:
        from app.core.train_cache import cached_train
        # Fusion des ``_DEFAULTS`` : cf. docstring du module — ``score()``
        # entraîne par ici et ne passe pas tous les paramètres d'entraînement.
        params = {**self._DEFAULTS, **(params or {})}
        return cached_train(self, df, tf_key, params, self._train_impl,
                            self._TRAIN_STATE_ATTRS, self._TRAIN_PARAM_KEYS)

    def _train_impl(self, df: pl.DataFrame, tf_key: str, params: dict) -> bool:
        # Le cache est géré par ``_train`` : ne pas le réappliquer ici.
        return self.ml._train_impl_wrapper(df, tf_key, params)

    # ── Inférence ───────────────────────────────────────────────────────────
    def _predict(self, features_df: pl.DataFrame, tf: str,
                 target: str) -> Optional[float]:
        return self.ml.predict_single(features_df, tf, target)

    def _predict_series(self, features_df: pl.DataFrame, tf: str, target: str):
        return self.ml.predict_series(features_df, tf, target)

    def predict_amplitude(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return self.ml.predict_amplitude(features_df, tf)

    def predict_direction(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return self.ml.predict_direction(features_df, tf)

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        return {"amp": None, "dir": None}
