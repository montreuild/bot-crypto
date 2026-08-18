"""SMC ML Edge — la stratégie qui consomme `omnibus_smc`.

Elle est délibérément SIMPLE. La valeur ajoutée du travail précédent est dans
le signal, pas dans un empilement de règles : le catalogue `v5_smc@1` a fait
passer la tête direction de ~0.53 à ~0.60 d'AUC (cf. `docs/ML_ABLATION_SMC.md`).
Une stratégie compliquée par-dessus rendrait impossible d'attribuer un résultat
de backtest au signal plutôt qu'aux règles.

La logique tient en trois portes, dans cet ordre :

1. **Amplitude** — `p_event` dans le haut de sa distribution (`amp_top_q`).
   C'est là que le modèle est le plus sûr (AUC ~0.72) et c'est la condition
   économique : un mouvement médian ne couvre pas les frais, un mouvement du
   top 20 % oui. Cette porte ne dit rien du sens ; elle dit qu'il vaut la peine
   de regarder.
2. **Direction** — l'écart de `p_up` à 0.5 dans le haut de sa distribution
   (`dir_top_q`). En dessous, le modèle est indécis et le trade serait un pari.
3. **Structure** (optionnelle, `use_smc_filter`) — refuse d'acheter dans le
   haut du dealing range et de vendre dans le bas, et exige l'accord de la
   tendance structurelle. Les colonnes viennent du frame de features DÉJÀ
   calculé : le filtre ne coûte rien.

Les deux premières portes sont des QUANTILES, pas des seuils de probabilité,
et c'est un enseignement de la mesure : la tête `amp` cible le top 20 %, donc
ses sorties tournent autour de 0.35 ; `p_up` ne s'étale que de 0.40 à 0.60.
Des seuils absolus d'apparence raisonnable (`p_event > 0.55`, écart > 0.12)
laissaient passer 8.7 % puis 0 % des barres — la première version de cette
stratégie ne prenait aucun trade. Un quantile garde son sens quel que soit le
symbole, le timeframe et le réentraînement.

La porte 3 est un paramètre et non une conviction : son apport se mesure par
ablation, comme le reste. C'est aussi la seule façon honnête de savoir si le
modèle a déjà intégré la structure — auquel cas la refiltrer ne servirait
qu'à perdre des trades.

Pourquoi le cache de features est légitime
------------------------------------------
`prepare_for_backtest` calcule les features UNE fois sur la série entière, puis
`score()` lit la ligne de la barre courante. Ce serait une fuite si les
features n'étaient pas causales — la ligne `i` d'un calcul global pourrait
différer de ce qu'un calcul en temps réel aurait produit.

C'est précisément ce que `tests/test_features_smc.py::test_aucune_fuite_temporelle`
vérifie, et pourquoi ce test a été écrit avant la stratégie : 20 des 21
colonnes SMC sont identiques au bit près entre un calcul sur préfixe et un
calcul sur série complète. Le cache est donc exact, pas seulement rapide.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Optional, Tuple

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategyML

logger = logging.getLogger(__name__)

#: Recette par défaut. Surchargeable par le bloc `models:` du YAML sans
#: toucher au code (cf. `app.ml.recipe.strategy_models`).
_RECETTE = "omnibus_smc"


class Strategy(BaseStrategyML):
    """Signal ML sur catalogue SMC — amplitude puis direction puis structure."""

    name = "smc_ml_edge"
    model_dir = "models"
    models: Dict[str, str] = {"signal": _RECETTE}

    timeframes = ["15m", "30m", "1h", "2h", "4h"]

    # Pas de `gate_spec` ici : les conventions de labels (horizons,
    # `amp_top_pct`, métrique) sont déclarées par `recipes/omnibus_smc.yaml` et
    # résolues par `resolve_gate_spec`. Les redéclarer donnerait deux sources
    # pour la même convention, qui finiraient par diverger — c'est l'origine du
    # décalage 0.732 vs 0.702 que `tests/test_recipe_contract.py` verrouille.

    param_space: Dict[str, Any] = {
        "amp_top_q": [0.05, 0.10, 0.20, 0.35],
        "dir_top_q": [0.10, 0.20, 0.35, 0.50],
        "use_smc_filter": [True, False],
        # Recentré sur la zone mesurée utile : 1.0 et 1.5 ont été mesurés
        # nuisibles (stops prématurés), la borne haute reste ouverte parce que
        # rien ne dit que 2.5 soit un optimum plutôt qu'un plancher.
        "sl_atr_mult": [2.0, 2.5, 3.0],
        "tp_atr_mult": [1.5, 2.5, 3.5],
        "max_hold_bars": [6, 12, 24],
    }

    #: Ces valeurs définissent le MODÈLE, pas la décision : les échantillonner
    #: forcerait un réentraînement complet à chaque trial d'optimisation.
    fixed_params: Dict[str, Any] = {
        "warmup_bars": 3000,
        "retrain_every": 800,
    }

    _DEFAULTS: Dict[str, Any] = {
        # ── Portes de décision, exprimées en QUANTILES ──
        # Un seuil absolu n'a pas de sens sur ces têtes, et c'est mesuré : sur
        # BTC/USDC 1h, `p_event` s'étale de 0.10 à 0.67 (moyenne 0.35, parce
        # que la cible est le top 20 % — le taux de base est 0.20, pas 0.50) et
        # `p_up` de 0.398 à 0.599 seulement. Un `dir_margin` de 0.12 ne laissait
        # AUCUNE barre passer, et un `amp_min` de 0.55 était au 91ᵉ centile
        # sans que rien ne le dise.
        #
        # Ces bornes changent avec le symbole, le timeframe et chaque
        # réentraînement. Un quantile, lui, garde son sens : « prendre les 10 %
        # de barres où ce modèle est le plus confiant » se transpose partout.
        "amp_top_q": 0.10,
        "dir_top_q": 0.20,
        "use_smc_filter": True,
        # Bornes du dealing range au-delà desquelles on refuse d'ouvrir dans le
        # sens du prix : acheter à 85 % du range, c'est acheter la résistance.
        "pd_long_max": 0.75,
        "pd_short_min": 0.25,
        # ── Gestion de position ──
        # MESURÉ. 1.5 ATR coupait les positions avant leur cible, sur les DEUX
        # symboles — c'était le vrai coupable derrière l'écart BTC/ETH, pas un
        # besoin de réglage par symbole. Passage à 2.5 :
        #
        #   symbole   PnL 1.5 → 2.5     profit factor    taux de réussite
        #   BTC       +5.98 % → +9.18 %   1.81 → 6.45      59.1 % → 78.9 %
        #   ETH       −3.07 % → −0.05 %   0.78 → 0.99      43.3 % → 61.1 %
        #
        # ⚠ 18 à 30 trades par configuration, et BTC/ETH corrèlent à 0.835 :
        # ce n'est pas une réplication indépendante. Le sens de l'effet est
        # cohérent sur tous les axes, son amplitude ne doit pas être prise au
        # pied de la lettre. Cf. `docs/STRATEGY_SMC_ML_EDGE.md` §3 bis.
        "sl_atr_mult": 2.5,
        "tp_atr_mult": 2.5,
        "max_hold_bars": 12,
        "min_size_factor": 0.4,
        # ── Modèle ──
        "warmup_bars": 3000,
        "retrain_every": 800,
        "train_window": 20000,
    }

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._trained: Dict[str, Any] = {}          # tf_key -> TrainedRecipe
        #: tf_key -> tableau (n, 2) des sorties du modèle sur sa fenêtre
        #: d'entraînement, qui sert d'échelle aux quantiles de décision.
        self._echelle: Dict[str, Any] = {}
        #: tf_key -> {"amp": ndarray, "dir": ndarray} alignes sur `_bt_frame`.
        #: Precalcules a chaque reentrainement (cf. `_precalcule_predictions`).
        self._preds: Dict[str, Any] = {}
        self._last_retrain: Dict[str, int] = {}
        self._call_cnt: int = 0
        self._managed = False
        # Cache de features du backtest : (longueur de la série, frame, noms)
        self._bt_frame: Optional[pl.DataFrame] = None
        self._bt_len: int = 0

    # ── Cycle de vie ────────────────────────────────────────────────────────
    @property
    def managed_externally(self) -> bool:
        return self._managed

    @managed_externally.setter
    def managed_externally(self, v: bool) -> None:
        self._managed = bool(v)

    @property
    def is_trained(self) -> bool:
        return bool(self._trained)

    def reset_model(self) -> None:
        with self._lock:
            self._trained.clear()
            self._echelle.clear()
            self._preds.clear()
            self._last_retrain.clear()
            self._call_cnt = 0
            self._bt_frame = None
            self._bt_len = 0

    def min_bars_required(self, params: dict | None = None) -> int:
        p = (params or {}).get(self.name, {})
        return int(p.get("warmup_bars", self._DEFAULTS["warmup_bars"]))

    def _recipe(self, params: Optional[dict] = None) -> str:
        from app.ml.recipe import primary_recipe
        return primary_recipe(self, params) or _RECETTE

    # ── Features ────────────────────────────────────────────────────────────
    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Pré-calcule les features sur toute la série (cf. docstring module).

        Sans ce cache, chaque barre du backtest reconstruirait 464 colonnes sur
        tout l'historique visible : le coût serait quadratique et le backtest
        inutilisable. Le cache est exact parce que les features sont causales.
        """
        self._bt_frame = None
        self._bt_len = 0
        frame = self._build_features(df)
        if frame is None:
            logger.warning(f"[{self.name}] prepare_for_backtest : features non construites")
            return
        self._bt_frame, self._bt_len = frame, len(df)
        # Le frame a change : les tableaux precalcules ne lui correspondent plus.
        self._preds.clear()
        logger.info(
            f"[{self.name}] cache de features : {len(frame)} lignes × "
            f"{len(frame.columns)} colonnes"
        )

    def _build_features(self, df: pl.DataFrame) -> Optional[pl.DataFrame]:
        from app.ml import features_catalog as fc
        from app.ml.recipe import load_recipe

        try:
            r = load_recipe(self._recipe())
            fs = fc.build(r.features_catalog, df, r.features_params)
        except Exception as e:
            logger.warning(f"[{self.name}] construction des features KO : {e}")
            return None
        if fs is None:
            return None
        self._feature_names = list(fs.names)
        return fs.frame

    def _frame_for(self, df: pl.DataFrame
                   ) -> Tuple[Optional[pl.DataFrame], int]:
        """``(frame, idx)`` — frame de features et INDICE de la barre courante.

        On rend un indice plutôt qu'un frame tronqué, et ce n'est pas un détail
        de style : tronquer puis sélectionner 464 colonnes matérialisait tout
        l'historique visible À CHAQUE BARRE, ce qui rend le backtest quadratique
        (mesuré : plusieurs minutes pour quelques milliers de barres). Avec
        l'indice, l'appelant découpe la ligne AVANT de choisir les colonnes.

        Le cache porte sur la série ENTIÈRE du backtest ; ``df`` en est un
        préfixe, donc ``idx = len(df) - 1``. Aucune ligne au-delà n'est lue —
        c'est la seule chose qui empêche le cache d'être une fuite.
        """
        if self._bt_frame is not None and len(df) <= len(self._bt_frame):
            return self._bt_frame, len(df) - 1
        frame = self._build_features(df)
        return (frame, len(frame) - 1) if frame is not None else (None, -1)

    # ── Entraînement ────────────────────────────────────────────────────────
    #: État par TF que ``cached_train`` capture et restaure sur un hit.
    _TRAIN_STATE_ATTRS = ("_trained", "_echelle")
    #: Aucun paramètre de DÉCISION n'entre dans l'entraînement ici : la recette
    #: porte tout, et ``train_window`` est déjà décrit par l'empreinte de la
    #: fenêtre. La clé de cache se réduit donc à (recette, tf, fenêtre).
    _TRAIN_PARAM_KEYS = ()

    def _train(self, df: pl.DataFrame, tf_key: str, p: dict) -> bool:
        """Entraînement avec cache process-wide (cf. app/core/train_cache.py).

        La fenêtre est alignée sur la grille ``retrain_every`` : sans cela,
        deux essais d'optimisation aux seuils différents retrainent à des
        barres décalées, produisent des fenêtres distinctes et le cache ne peut
        jamais servir. Staleness bornée à ``retrain_every`` barres — celle du
        déclenchement lui-même.
        """
        from app.core.train_cache import aligned_train_window, cached_train

        fenetre = int(p.get("train_window", self._DEFAULTS["train_window"]))
        if fenetre and len(df) > fenetre:
            train_df, _ = aligned_train_window(
                df, int(p.get("retrain_every", self._DEFAULTS["retrain_every"])),
                fenetre)
        else:
            train_df = df
        ok = cached_train(self, train_df, tf_key, dict(p), self._train_impl,
                          self._TRAIN_STATE_ATTRS, self._TRAIN_PARAM_KEYS)
        # Semé même sur un HIT : le cache rend les modèles, pas les prédictions.
        if ok:
            out = self._trained.get(tf_key)
            if out is not None:
                self._precalcule_predictions(out, tf_key)
        return ok

    def _train_impl(self, train_df: pl.DataFrame, tf_key: str, p: dict) -> bool:
        """Corps de l'entraînement — le cache est géré par ``_train``."""
        from app.ml import recipe_trainer as rt

        recette = self._recipe()
        try:
            out = rt.train(recette, train_df, tf_key)
        except Exception as e:
            logger.warning(f"[{self.name}] entraînement {tf_key} KO : {e}")
            return False
        if out is None or "amp" not in out.boosters or "dir" not in out.boosters:
            return False
        with self._lock:
            self._trained[tf_key] = out
            self._echelle[tf_key] = self._mesure_echelle(out, train_df)
        meta = out.train_meta or {}
        logger.info(
            f"[{self.name}] {tf_key} entraîné sur {len(train_df)} barres | "
            f"AUC amp={meta.get('auc_amp')} dir={meta.get('auc_dir')} | "
            f"features {meta.get('n_features')}→{meta.get('n_kept_features')}"
        )
        return True

    def _precalcule_predictions(self, out, tf_key: str) -> None:
        """Prédit une fois sur TOUT le frame en cache, au lieu de barre par barre.

        Gain mesuré : ~190 s → ~15 s pour un backtest de 12 000 barres. LightGBM
        paie un coût fixe par appel bien plus lourd que le calcul lui-même, et
        `polars.slice().select()` sur 464 colonnes n'est pas gratuit non plus ;
        les payer 12 000 fois pour une ligne à chaque fois était le vrai coût du
        backtest, pas l'entraînement.

        **Ce n'est pas une fuite**, et la raison mérite d'être écrite. Le modèle
        utilisé est celui du dernier réentraînement, donc entraîné sur des
        barres ANTÉRIEURES ; les features sont causales (vérifié par
        `tests/test_features_smc.py`) ; et la barre `i` ne lit que `arr[i]`. On
        déplace le moment du CALCUL, jamais l'information disponible. Le tableau
        est réécrit à chaque réentraînement, donc une barre n'est jamais servie
        par un modèle plus récent qu'elle.
        """
        frame = self._bt_frame
        if frame is None:
            self._preds.pop(tf_key, None)
            return
        noms = [c for c in out.feature_names if c in frame.columns]
        if len(noms) != len(out.feature_names):
            self._preds.pop(tf_key, None)
            return
        X = frame.select(noms).to_numpy().astype(np.float32)
        for j, col in enumerate(noms):
            invalide = ~np.isfinite(X[:, j])
            if invalide.any():
                X[invalide, j] = float(out.medians.get(col, 0.0))
        try:
            sorties = {}
            for tete in ("amp", "dir"):
                p = np.asarray(out.boosters[tete].predict(X), dtype=float)
                cal = (out.calibrators or {}).get(tete)
                if cal is not None:
                    try:
                        p = np.asarray(cal.predict(p), dtype=float)
                    except Exception:                       # pragma: no cover
                        pass
                sorties[tete] = np.clip(p, 0.0, 1.0)
            self._preds[tf_key] = sorties
        except Exception as e:                              # pragma: no cover
            logger.warning(f"[{self.name}] précalcul des prédictions KO : {e}")
            self._preds.pop(tf_key, None)

    def _mesure_echelle(self, out, train_df: pl.DataFrame) -> Optional[np.ndarray]:
        """Distribution des sorties du modèle sur SA fenêtre d'entraînement.

        Sert à convertir un quantile en seuil. Deux propriétés comptent :

        - **c'est causal** : la fenêtre d'entraînement est entièrement dans le
          passé de la barre décidée, et on n'en tire qu'une ÉCHELLE, jamais un
          avis directionnel ;
        - **c'est réévalué à chaque réentraînement**, donc l'échelle suit le
          modèle. Un seuil figé dans le YAML dériverait silencieusement dès que
          la distribution des prédictions change.

        Retourne un tableau ``(n, 2)`` — colonnes ``p_event`` et ``|p_up−0.5|``.
        """
        frame, _ = self._frame_for(train_df)
        if frame is None or not len(frame):
            return None
        noms = [c for c in out.feature_names if c in frame.columns]
        if len(noms) != len(out.feature_names):
            return None
        X = frame.select(noms).to_numpy().astype(np.float32)
        for j, col in enumerate(noms):
            invalide = ~np.isfinite(X[:, j])
            if invalide.any():
                X[invalide, j] = float(out.medians.get(col, 0.0))
        try:
            amp = np.asarray(out.boosters["amp"].predict(X), dtype=float)
            direction = np.asarray(out.boosters["dir"].predict(X), dtype=float)
        except Exception as e:                              # pragma: no cover
            logger.warning(f"[{self.name}] échelle non mesurée : {e}")
            return None
        return np.column_stack([amp, np.abs(direction - 0.5)])

    def _seuils(self, tf_key: str, p: dict) -> Optional[Tuple[float, float]]:
        """``(seuil_amp, seuil_ecart_dir)`` pour les quantiles demandés."""
        ech = self._echelle.get(tf_key)
        if ech is None:
            ech = self._echelle.get("default")
        if ech is None or not len(ech):
            return None
        q_amp = 100.0 * (1.0 - float(p["amp_top_q"]))
        q_dir = 100.0 * (1.0 - float(p["dir_top_q"]))
        return (float(np.percentile(ech[:, 0], q_amp)),
                float(np.percentile(ech[:, 1], q_dir)))

    def fit(self, df: pl.DataFrame, params: dict | None = None) -> None:
        p = (params or {}).get(self.name, {})
        self._train(df, "default", {**self._DEFAULTS, **p})

    # ── Inférence ───────────────────────────────────────────────────────────
    def _predict_heads(self, frame: pl.DataFrame, idx: int,
                       tf_key: str) -> Tuple[Optional[float], Optional[float]]:
        """``(p_event, p_up)`` pour la ligne ``idx`` de ``frame``.

        Seul point par lequel la décision consulte le modèle — le garder unique
        est ce qui permet de changer de recette sans toucher aux règles.
        """
        out = self._trained.get(tf_key) or self._trained.get("default")
        if out is None or idx < 0 or idx >= len(frame):
            return None, None

        # Chemin rapide : tableau precalcule au dernier reentrainement. Meme
        # valeur qu'un predict a la barre, pour un cout nul.
        pre = self._preds.get(tf_key) or self._preds.get("default")
        if pre is not None and idx < len(pre["amp"]):
            return float(pre["amp"][idx]), float(pre["dir"][idx])

        noms = [c for c in out.feature_names if c in frame.columns]
        if len(noms) != len(out.feature_names):
            logger.warning(
                f"[{self.name}] {len(out.feature_names) - len(noms)} feature(s) du "
                f"modèle absente(s) du frame — prédiction abandonnée plutôt que "
                f"faite sur des colonnes décalées"
            )
            return None, None

        # Ligne d'abord, colonnes ensuite : l'ordre inverse matérialiserait
        # 464 colonnes sur tout l'historique à chaque barre.
        X = frame.slice(idx, 1).select(noms).to_numpy().astype(np.float32)
        for j, col in enumerate(noms):                 # imputation par médiane
            if not np.isfinite(X[0, j]):
                X[0, j] = float(out.medians.get(col, 0.0))

        sorties = []
        for tete in ("amp", "dir"):
            booster = out.boosters.get(tete)
            if booster is None:
                return None, None
            valeur = float(booster.predict(X)[0])
            cal = (out.calibrators or {}).get(tete)
            if cal is not None:
                try:
                    valeur = float(cal.predict([valeur])[0])
                except Exception:                       # pragma: no cover
                    pass
            sorties.append(min(max(valeur, 0.0), 1.0))
        return sorties[0], sorties[1]

    # ── Décision ────────────────────────────────────────────────────────────
    def _none(self, raison: str, **extra) -> Dict[str, Any]:
        return {"name": self.name, "side": "none", "score": 0.0,
                "reason": raison, **extra}

    def score(self, df: pl.DataFrame, params: dict | None = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = {**self._DEFAULTS, **((params or {}).get(self.name, {}))}
        warmup = int(p["warmup_bars"])
        if df is None or len(df) < warmup:
            return self._none(f"Données insuffisantes ({0 if df is None else len(df)} < {warmup})")

        from app.ml.backend.features import detect_timeframe
        try:
            tf = detect_timeframe(df) or "default"
        except Exception:
            tf = "default"
        tf_key = symbol or tf

        frame, idx = self._frame_for(df)
        if frame is None or idx < 0:
            return self._none("Features indisponibles")

        # ── Réentraînement périodique ───────────────────────────────────────
        self._call_cnt += 1
        besoin = (tf_key not in self._trained) or (
            self._call_cnt - self._last_retrain.get(tf_key, 0)
            >= int(p["retrain_every"]))
        if besoin and not self._managed:
            if self._train(df, tf_key, p):
                self._last_retrain[tf_key] = self._call_cnt
        if tf_key not in self._trained and "default" not in self._trained:
            return self._none("Modèle non entraîné")

        p_event, p_up = self._predict_heads(frame, idx, tf_key)
        if p_event is None or p_up is None:
            return self._none("Prédiction indisponible")

        indicateurs = {"p_event": round(p_event, 4), "p_up": round(p_up, 4)}
        conditions = [f"Modèle : p_event={p_event:.3f} p_up={p_up:.3f}"]

        seuils = self._seuils(tf_key, p)
        if seuils is None:
            return self._none("Échelle des prédictions non mesurée",
                              indicators=indicateurs)
        amp_min, marge = seuils
        indicateurs["seuil_amp"] = round(amp_min, 4)
        indicateurs["seuil_dir"] = round(marge, 4)

        # ── Porte 1 : amplitude ─────────────────────────────────────────────
        if p_event < amp_min:
            return self._none(
                f"Amplitude insuffisante ({p_event:.3f} < {amp_min:.3f}, "
                f"top {100 * float(p['amp_top_q']):.0f} %)",
                indicators=indicateurs, p_event=p_event, p_up=p_up)

        # ── Porte 2 : direction ─────────────────────────────────────────────
        ecart = abs(p_up - 0.5)
        if ecart < marge:
            return self._none(
                f"Direction indécise (écart {ecart:.3f} < {marge:.3f})",
                indicators=indicateurs, p_event=p_event, p_up=p_up)
        side = "long" if p_up > 0.5 else "short"
        conditions.append(f"Direction {side.upper()} (écart {ecart:.3f} ≥ {marge:.3f})")

        # ── Porte 3 : structure de marché (colonnes déjà calculées) ────────
        derniere = frame.slice(idx, 1)
        if bool(p["use_smc_filter"]):
            refus = self._filtre_structure(derniere, side, p)
            if refus:
                return self._none(refus, indicators=indicateurs,
                                  p_event=p_event, p_up=p_up)
            conditions.append("Structure SMC : accord")

        # ── Score et taille ─────────────────────────────────────────────────
        conf_dir = min(ecart / max(marge * 2.0, 1e-9), 1.0)
        conf_amp = min((p_event - amp_min) / max(amp_min, 1e-9), 1.0)
        score = 0.5 + 0.5 * (0.6 * conf_amp + 0.4 * conf_dir)
        taille = max(float(p["min_size_factor"]),
                     round(0.5 * (conf_amp + conf_dir), 4))

        for col in ("smc_trend", "smc_pd_position", "regime_code"):
            if col in derniere.columns:
                indicateurs[col] = round(float(derniere[col][0]), 4)

        return {
            "name": self.name,
            "side": side,
            "score": round(min(score, 0.97), 4),
            "size_factor": taille,
            "sl_atr_mult": float(p["sl_atr_mult"]),
            "tp_atr_mult": float(p["tp_atr_mult"]),
            "exit_after_bars": int(p["max_hold_bars"]),
            "tf_detected": tf,
            "p_event": p_event,
            "p_up": p_up,
            "indicators": indicateurs,
            "conditions": conditions,
            "reason": (f"{side.upper()} p_event={p_event:.3f} p_up={p_up:.3f} "
                       f"score={score:.3f}"),
        }

    def _filtre_structure(self, ligne: pl.DataFrame, side: str,
                          p: dict) -> Optional[str]:
        """Raison du refus, ou ``None`` si la structure ne s'y oppose pas.

        Deux refus seulement, choisis parce qu'ils sont les moins redondants
        avec ce que le modèle voit déjà :

        - **position dans le dealing range** — acheter dans le haut du range
          revient à acheter la résistance. Le modèle a la colonne, mais rien ne
          l'oblige à en faire une règle dure ;
        - **désaccord de tendance structurelle** — ouvrir contre la structure
          confirmée (BOS/CHoCH) est le trade que le cadre SMC déconseille le
          plus explicitement.
        """
        def val(col: str) -> Optional[float]:
            if col not in ligne.columns:
                return None
            v = ligne[col][0]
            return float(v) if v is not None else None

        pos = val("smc_pd_position")
        if pos is not None:
            if side == "long" and pos > float(p["pd_long_max"]):
                return (f"Structure : achat en zone premium "
                        f"(position {pos:.2f} > {p['pd_long_max']})")
            if side == "short" and pos < float(p["pd_short_min"]):
                return (f"Structure : vente en zone discount "
                        f"(position {pos:.2f} < {p['pd_short_min']})")

        trend = val("smc_trend")
        if trend is not None:
            if side == "long" and trend < 0:
                return "Structure : tendance baissière confirmée, achat refusé"
            if side == "short" and trend > 0:
                return "Structure : tendance haussière confirmée, vente refusée"
        return None

    def predict(self, df: pl.DataFrame, params: dict | None = None) -> Dict[str, Any]:
        return self.score(df, params)

    # ── Persistance ─────────────────────────────────────────────────────────
    def _cle_sauvegarde(self, tf_key: str) -> Optional[str]:
        with self._lock:
            if tf_key in self._trained:
                return tf_key
            if "default" in self._trained:
                return "default"
            cles = list(self._trained)
        return cles[0] if len(cles) == 1 else None

    def save_model(self, path: str, extra_meta: Optional[dict] = None) -> None:
        """Délègue à ``TrainedRecipe.save`` : c'est le seul écrivain qui pose
        TOUJOURS la liste des features et les médianes dans le meta.json, sans
        quoi l'artefact serait illisible par le scorer du gate."""
        tf_key = os.path.splitext(os.path.basename(path))[0].rsplit("_", 1)[-1]
        cle = self._cle_sauvegarde(tf_key)
        if cle is None:
            logger.warning(
                f"[{self.name}] save_model({path}) : rien à persister "
                f"(TF demandé={tf_key!r}, clés={sorted(self._trained)})")
            return
        prefix = path[:-4] if path.endswith(".lgb") else path
        self._trained[cle].save(prefix)

    def load_model(self, path: str) -> bool:
        from app.ml.backend.persistence import load_amp_dir_bundle
        from app.ml.recipe_trainer import TrainedRecipe

        prefix = path[:-4] if path.endswith(".lgb") else path
        bundle = load_amp_dir_bundle(prefix)
        if not bundle or bundle.get("amp_model") is None:
            return False
        tf_key = os.path.splitext(os.path.basename(path))[0].rsplit("_", 1)[-1]
        with self._lock:
            self._trained[tf_key] = TrainedRecipe(
                recipe=self._recipe(), tf=tf_key,
                boosters={"amp": bundle["amp_model"], "dir": bundle["dir_model"]},
                feature_names=list(bundle.get("features") or []),
                medians=dict(bundle.get("medians") or {}),
                train_meta=dict(bundle.get("train_meta") or {}),
                calibrators={k: v for k, v in
                             (("amp", bundle.get("amp_cal")),
                              ("dir", bundle.get("dir_cal"))) if v is not None},
            )
        return True

    # ── Gate ────────────────────────────────────────────────────────────────
    @classmethod
    def score_holdout(cls, path_prefix: str, holdout_df, *,
                      gate_cfg: Any = None, params: dict | None = None) -> Dict[str, Any]:
        """Score un artefact sur un holdout, avec les features de CETTE recette.

        La surcharge est obligatoire, pas cosmétique. L'implémentation par
        défaut (`score_amp_dir_bundle`) reconstruit des features **V4**, soit
        437 colonnes ; le modèle en attend 464. Sans surcharge, le gate
        scorerait chaque candidat sur une matrice qui n'est pas la sienne et
        arbitrerait la promotion sur un chiffre faux.
        """
        from app.ml import features_catalog as fc
        from app.ml import labelling
        from app.ml.backend.persistence import load_amp_dir_bundle
        from app.ml.recipe import load_recipe

        bundle = load_amp_dir_bundle(path_prefix)
        if not bundle or bundle.get("amp_model") is None:
            return {}
        try:
            r = load_recipe(cls.models.get("signal") or _RECETTE)
            fs = fc.build(r.features_catalog, holdout_df, r.features_params)
            if fs is None:
                return {}
            lab = labelling.build(r.label_scheme, fs.frame, r.train_params())
        except Exception as e:
            logger.warning(f"[{cls.name}] score_holdout KO : {e}")
            return {}
        if lab is None or lab.n < 100:
            return {}

        noms = list(bundle.get("features") or fs.names)
        manquantes = [c for c in noms if c not in fs.frame.columns]
        if manquantes:
            logger.warning(
                f"[{cls.name}] score_holdout : {len(manquantes)} feature(s) de "
                f"l'artefact absente(s) du catalogue courant — non scoré")
            return {}

        X = fs.frame.select(noms).to_numpy().astype(np.float32)[:lab.n]
        medians = dict(bundle.get("medians") or {})
        for j, col in enumerate(noms):
            colonne = X[:, j]
            invalide = ~np.isfinite(colonne)
            if invalide.any():
                colonne[invalide] = float(medians.get(col, 0.0))

        from app.ml.scoring import rank_auc
        out: Dict[str, Any] = {"n": int(lab.n)}
        for tete, cle in (("amp", "amp_model"), ("dir", "dir_model")):
            modele, y = bundle.get(cle), lab.y.get(tete)
            if modele is None or y is None or len(np.unique(y)) < 2:
                continue
            auc = rank_auc(np.asarray(y), np.asarray(modele.predict(X)))
            if auc is not None:
                out[f"auc_{tete}"] = round(float(auc), 4)
        return out
