"""Moteur de recherche d'hyperparamètres — grid / random / bayesian (TPE).

Découpage ARCH-007 (recherche / persistance / scoring / workers) :
  - ``optimizer_search.py``   : constantes + ``OptimizerSearchEngine`` (grid/random/bayesian)
  - ``opt_scoring.py``        : score composite IS/OOS, ratio de surapprentissage,
                                ``beats_baseline`` (garde-fou d'application)
  - ``opt_persistence.py``    : YAML stratégies, changelog, stratégies actives par TF
  - ``opt_workers.py``        : workers ProcessPoolExecutor (état partagé, cap mémoire)

Ce module ré-exporte les noms historiques de la façade ``optimizer.py``
supprimée (ARCH-007) : les imports existants
(``from app.engine.optimizer_search import apply_best_params, PARAM_SPACES, …``)
restent valides. ``StrategyOptimizer`` est conservé comme alias de
``OptimizerSearchEngine`` pour compatibilité ascendante.
"""
import importlib
import io
import itertools
import logging
import math
import os
import random
import statistics
import threading
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from app.core.is_oos import OOS_FRACTION_DEFAULT as _OOS_FRACTION  # BT-08 : constante partagée
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL

# ── Sous-modules (ré-exports compatibilité — noms historiques inclus) ────────
from app.core.stats_thresholds import MIN_SIGNIFICANT_TRADES
from app.core.timeframes import TF_MINUTES as _TF_MINUTES  # V4-A : source unique
from app.engine.backtest import Backtester
from app.engine.engine import Engine
from app.engine.opt_persistence import (  # noqa: F401
    _append_changelog,
    _changelog_lock,
    _config_write_lock,
    _load_strategy_file,
    _resolve_config_path,
    _strategy_file_path,
    _write_strategy_file,
    apply_best_params,
    get_active_strategies_per_tf,
    record_optimizer_audit,
    save_optimizer_results,
)
from app.engine.opt_scoring import (  # noqa: F401
    _composite_score,
    _overfitting_ratio,
    composite_score,
    overfitting_ratio,
)
from app.engine.opt_workers import (  # noqa: F401
    _W,
    _eval_worker,
    _install_features_cache,
    _worker_init,
)
from app.engine.opt_workers import available_memory_bytes as _available_memory_bytes  # noqa: F401
from app.engine.opt_workers import mem_aware_max_workers as _mem_aware_max_workers  # noqa: F401
from app.engine.registry import (
    get_fixed_params,
    get_param_spaces,
    get_strategy_timeframes,
)

logger = logging.getLogger(__name__)


# Métadonnées auto-découvertes via app.strategies.registry
STRATEGY_TIMEFRAMES: Dict[str, List[str]] = get_strategy_timeframes()
PARAM_SPACES:        Dict[str, Dict[str, List]] = get_param_spaces()
FIXED_PARAMS:        Dict[str, Dict[str, Any]]  = get_fixed_params()
# Alias historique (cli.py optimise via DEFAULT_SPACES)
DEFAULT_SPACES:      Dict[str, Dict[str, List]] = PARAM_SPACES

# Barres recommandées par timeframe
RECOMMENDED_LIMIT: Dict[str, int] = {
    "1m":  2000,   # ~1.4 jours
    "5m":  4000,   # ~14 jours
    "15m": 2000,   # ~21 jours
    "30m": 1500,   # ~31 jours
    "1h":  1500,   # ~62 jours
    "4h":   800,   # ~133 jours
    "1d":  2000,   # ~2000 jours (limité par l'historique OHLCV de l'exchange)
}

GLOBAL_TRADING_PARAMS = {
    "score_threshold", "risk_per_trade", "capital", "timeframe", "timeframes",
    "paper_mode", "max_positions", "taker_fee", "maker_fee",
}

# Fraction de la fenêtre réservée à l'OOS dans le découpage des jobs (cf.
# auto_optimizer : split ≈ 65 % IS / 35 % OOS). Sert à dimensionner le nombre
# minimal de bougies à charger pour qu'une stratégie ne soit PAS ignorée
# (``_OOS_FRACTION``, importé en tête de fichier). ``_TF_MINUTES`` (idem) sert
# à exprimer la fenêtre OOS en temps.

# Fenêtre de TRADING visée dans la tranche OOS, AU-DELÀ du warmup, pour qu'elle
# génère assez de trades (~ lifecycle.eval_min_trades). Sans elle, l'OOS ne
# réservait que le warmup -> 0 bougie tradable après warmup -> 0 trade -> score
# OOS dégénéré : c'est ce qui faisait sortir -999 les opus_omnibus_v* (gros
# warmup ML + filtre horaire). Exprimée en jours, convertie en bougies par TF,
# puis bornée pour ne pas exploser le runtime sur les bas TF ni rester trop
# courte sur les hauts TF.
_OOS_TRADE_DAYS = 200
_OOS_TRADE_BARS_FLOOR = 1500
_OOS_TRADE_BARS_CAP = 7000


def _oos_trade_window_bars(timeframe: str = None) -> int:
    """Bougies de fenêtre de trading visées dans l'OOS pour un TF donné."""
    minutes = _TF_MINUTES.get(timeframe or "1h", 60)
    bars_per_day = 1440.0 / minutes
    return int(min(_OOS_TRADE_BARS_CAP,
                   max(_OOS_TRADE_BARS_FLOOR, round(bars_per_day * _OOS_TRADE_DAYS))))


def required_total_bars(strategy_name: str, timeframe: str = None,
                        params: dict = None) -> int:
    """Bougies TOTALES à charger pour qu'une stratégie soit évaluable en OOS.

    La tranche OOS (~35 %) doit contenir le warmup de la stratégie
    (``min_bars_required``) PLUS une fenêtre de trading suffisante pour générer
    assez de trades. L'ancienne formule ne réservait que le warmup
    (``ceil(min_bars / 0.35)``) : l'OOS = juste le warmup -> 0 trade -> score
    OOS dégénéré (-999) pour les stratégies ML à gros warmup. Fallback
    conservateur si l'import échoue.
    """
    try:
        mod = importlib.import_module(f"app.strategies.{strategy_name}")
        min_bars = int(mod.Strategy().min_bars_required(params))
    except Exception:
        min_bars = 220
    oos_needed = min_bars + _oos_trade_window_bars(timeframe)
    return math.ceil(oos_needed / _OOS_FRACTION)


def auto_fetch_limit(timeframe: str, strategies: List[str],
                     headroom: float = 1.15) -> int:
    """Nombre de bougies à charger pour un TF, dérivé des besoins des stratégies.

    Prend le max entre la base recommandée et le plus gros besoin parmi les
    stratégies (warmup + fenêtre de trading OOS, cf. ``required_total_bars``),
    avec une marge (``headroom``). La fenêtre de trading OOS évite que l'OOS des
    stratégies ML à gros warmup ne contienne aucun trade (score -999).
    """
    base = RECOMMENDED_LIMIT.get(timeframe, 1000)
    needed = max((required_total_bars(s, timeframe) for s in strategies), default=0)
    return int(max(base, math.ceil(needed * headroom)))


# ── Hyperparamètres d'ENTRAÎNEMENT ML explorables (#6, two-phase) ────────────
# Petite grille externe : chaque combinaison segmente le cache d'entraînement
# (clé = hyperparamètres d'entraînement), donc le coût croît ~linéairement avec
# le nombre de combos. On reste volontairement frugal (4 combos par défaut).
# Intersection avec les ``fixed_params`` d'une stratégie : seules les clés
# qu'elle déclare effectivement figées comme hyperparamètres d'entraînement
# sont explorées (les autres seraient ignorées par ``_train`` → combos gâchés).
ML_HP_SPACE: Dict[str, List] = {
    "learning_rate": [0.03, 0.05],
    "n_estimators":  [300, 500],
}


def ml_hp_space_for(strategy_name: str) -> Dict[str, List]:
    """Sous-espace d'hyperparamètres d'entraînement réellement réglables pour
    une stratégie (intersection de ``ML_HP_SPACE`` avec ses ``fixed_params``).
    Vide si la stratégie n'expose aucun de ces hyperparamètres → phase unique."""
    fixed = FIXED_PARAMS.get(strategy_name, {})
    return {k: v for k, v in ML_HP_SPACE.items() if k in fixed}


@dataclass
class _PoolHandle:
    """Pool de process ouvert et déjà initialisé (workers avec features
    pré-calculées), partageable entre plusieurs vagues d'évaluation d'un même
    appel (ex: dépistage puis recherche réduite) — évite de repayer le spawn
    + ré-import complet de l'appli (lightgbm, sklearn, polars…) à chaque
    phase, coût quasi fixe par pool et dominant pour les stratégies ML
    multi-modèles (ex: opus_omnibus_v12)."""
    executor: Any
    cfg_yaml: str
    df_is_ipc: bytes
    df_oos_ipc: bytes
    safe_jobs: int


# ── OptimizerSearchEngine — classe principale ──
class OptimizerSearchEngine:
    def __init__(self, strategy_name: str, cfg: dict,
                 df_is: pl.DataFrame, df_oos: pl.DataFrame,
                 param_space: Dict = None,
                 progress_callback: Optional[Callable] = None,
                 symbol: str = DEFAULT_CONFIG_SYMBOL,
                 df_full: pl.DataFrame = None,
                 split: int = None,
                 timeframe: str = None,
                 cancel_event: Optional[threading.Event] = None,
                 ml_mode: Optional[str] = None):
        self.strategy_name     = strategy_name
        self.cfg               = deepcopy(cfg)
        self.df_is             = df_is
        self.df_oos            = df_oos
        self.param_space       = param_space or PARAM_SPACES.get(strategy_name, {})
        self.progress_callback = progress_callback
        self.symbol            = symbol
        self.timeframe         = timeframe
        self._cancel_event     = cancel_event
        self.results: List[Dict] = []
        self.df_full = df_full if df_full is not None else pl.concat([df_is, df_oos])
        self.split   = split   if split   is not None else len(df_is)
        # Hyperparamètres d'entraînement ML figés pour la passe courante (#6,
        # two-phase) — fusionnés dans chaque jeu de params échantillonné via
        # ``_with_hp``. None = phase unique (comportement historique inchangé).
        self._fixed_ml_hp: Optional[Dict] = None
        # ML-02 : ml_mode du Backtester utilisé par CHAQUE trial. "inline"
        # (défaut, comportement historique inchangé) réentraîne à chaque essai
        # — c'est délibéré pour évaluer le comportement réel de la ML sur des
        # seuils de décision variés. "frozen" gèle un modèle déjà publié au
        # registre et n'optimise QUE les seuils contre lui (plus rapide, cible
        # fixe) — cf. docs/CONCEPTION_CYCLE_DE_VIE_ML.md §4.2. Lu depuis
        # cfg["optimizer"]["ml_mode"] si non fourni explicitement — les workers
        # (opt_workers._eval_worker) le redérivent de la même clé après
        # désérialisation du YAML, aucun paramètre supplémentaire à faire
        # traverser la frontière de process.
        self.ml_mode = ml_mode if ml_mode is not None else (self.cfg.get("optimizer") or {}).get("ml_mode", "inline")

        # S11 : annonce le contexte facturé AVANT le premier essai. Le
        # Backtester le journalise aussi, mais seulement au premier trial et de
        # façon throttlée : ici l'opérateur voit sur quoi il lance son
        # optimisation au moment où il la lance.
        try:
            from app.core.execution import format_cost_model
            model = self._cost_model()
            if model:
                logger.info("[Optimizer] %s\n%s", strategy_name,
                            format_cost_model(model, symbol or "", timeframe or ""))
        except Exception as e:      # pragma: no cover — jamais bloquant
            logger.debug(f"[Optimizer] annonce du modèle de coûts KO : {e}")

    def _with_hp(self, params: dict) -> dict:
        """Fusionne les hyperparamètres d'entraînement ML figés (``_fixed_ml_hp``)
        dans un jeu de params échantillonné. Injecté au niveau du sampler pour
        que les HP atteignent à la fois ``_eval`` (in-process) et les workers
        (``_eval_worker`` reçoit ``params`` tel quel), et soient persistés dans
        ``best_params``. No-op quand ``_fixed_ml_hp`` est None."""
        if not self._fixed_ml_hp:
            return params
        return {**params, **self._fixed_ml_hp}

    def _load_strategy(self):
        mod = importlib.import_module(f"app.strategies.{self.strategy_name}")
        eng = Engine()
        eng.register(mod.Strategy(), silent=True)  # silence: appelé à chaque trial
        return eng

    def _eval(self, params: dict) -> dict:
        eng = self._load_strategy()
        cfg = deepcopy(self.cfg)
        cfg.setdefault("strategy_params", {})[self.strategy_name] = params
        # Empêche resolve_strategy_params() d'écraser les params échantillonnés :
        # l'entrée optimizer_results sauvegardée dans le YAML stratégie a une
        # priorité supérieure et avalerait silencieusement les params du trial.
        if self.strategy_name in cfg.get("optimizer_results", {}):
            del cfg["optimizer_results"][self.strategy_name]
        bt  = Backtester(eng, cfg, cancel_event=self._cancel_event, ml_mode=self.ml_mode)

        res_is  = bt.run(self.df_is,  self.symbol, timeframe=self.timeframe)
        res_oos = bt.run(self.df_oos, self.symbol, timeframe=self.timeframe)

        # Plancher de sélection = plancher de décision (MIN_SIGNIFICANT_TRADES).
        # Le dépôt portait deux seuils : il refusait de PROMOUVOIR sous 10 tout
        # en SÉLECTIONNANT avec un plancher de 2, et c'est par cet écart que
        # passaient les optima hyper-sélectifs (docs/SUITE_ABLATION_V3.md §1).
        _min_tr = self._min_trades()
        is_score  = _composite_score(res_is,  min_trades=_min_tr)
        oos_score = _composite_score(res_oos, min_trades=_min_tr)
        overfit   = _overfitting_ratio(is_score, oos_score)

        return {
            "params":      params,
            "is_score":    is_score,
            "oos_score":   oos_score,
            "overfit":     overfit,
            "is_pnl":      res_is.total_pnl,
            "oos_pnl":     res_oos.total_pnl,
            "is_sharpe":   res_is.sharpe,
            "oos_sharpe":  res_oos.sharpe,
            "is_trades":   res_is.total_trades,
            "oos_trades":  res_oos.total_trades,
            "is_wr":       res_is.win_rate,
            "oos_wr":      res_oos.win_rate,
            "oos_dd":      res_oos.max_drawdown,
            "oos_alpha":   getattr(res_oos, "alpha", None),
        }

    def _penalized_score(self, r: dict) -> float:
        """Score final pénalisé si surapprentissage détecté."""
        oos = r["oos_score"]
        ovf = r.get("overfit", 1.0)
        if np.isnan(ovf):
            return oos
        if ovf > 2.5:
            return oos * (2.5 / ovf)
        return oos

    def random_search(self, n_trials: int = 40, n_jobs: int = 1,
                      early_stop_patience: int = 0,
                      param_search_optim: bool = True) -> dict:
        if not self.param_space:
            return {"error": f"Aucun espace de params pour {self.strategy_name}"}

        param_keys = list(self.param_space.keys())
        do_reduce = param_search_optim and self._should_reduce_space(n_trials)
        reduction = None
        try:
            with self._open_pool(n_jobs) as pool:
                if do_reduce:
                    # Phase A = dépistage EN BUDGET (1er tiers des essais, sur
                    # l'espace ET la fenêtre COMPLETS — pas une fenêtre à
                    # part) : le gel décidé ici s'applique aux essais restants
                    # du MÊME appel, sur le MÊME pool déjà chaud. Remplace
                    # l'ancien design (dépistage sur fenêtre réduite, en plus
                    # du budget de n_trials, sur un 2e pool) qui coûtait plus
                    # cher que la recherche qu'il préparait sur les gros
                    # espaces (mesuré : 749s vs 287s sur opus_omnibus_v9) et
                    # ne profitait jamais aux stratégies ML multi-modèles
                    # comme v12 (spawn d'un 2e pool = coût quasi fixe,
                    # indépendant du nombre d'essais qu'il contient).
                    k = min(n_trials, max(8, n_trials // 3))
                    # base : ne compter que les essais de CET appel — self.results
                    # peut contenir des résultats antérieurs si l'instance est
                    # réutilisée (les slices/décomptes relatifs à la fin de la
                    # liste mélangeraient des essais d'une autre recherche).
                    base = len(self.results)
                    did = self._run_parallel(k, n_trials, trial_offset=0, pool=pool,
                                             early_stop_patience=early_stop_patience)
                    screen = self.results[base:]
                    if screen:
                        reduction = self._freeze_from_results(screen, param_keys)
                    # Budget décompté en TENTATIVES (did), pas en succès : un
                    # échec de worker en phase A ne doit pas gonfler la phase B.
                    remaining = n_trials - did
                    if remaining > 0:
                        initial_best = max((self._penalized_score(r) for r in screen),
                                           default=-999.0)
                        self._run_parallel(remaining, n_trials, trial_offset=did,
                                           pool=pool, early_stop_patience=early_stop_patience,
                                           initial_best=initial_best)
                else:
                    self._run_parallel(n_trials, n_trials, trial_offset=0, pool=pool,
                                       early_stop_patience=early_stop_patience)
            result = self._best_result()
        finally:
            self._restore_param_space()
        if reduction:
            result["param_search_optim"] = reduction
        return result

    # ── Param Search Optim : dépistage EN BUDGET + gel des paramètres à
    # faible impact ────────────────────────────────────────────────────────
    # Option (activée par défaut) appliquée PENDANT random_search/
    # bayesian_search/grid_search — pas AVANT : leurs premiers essais SONT le
    # dépistage. Ce n'est pas un 4e mode de recherche. Principe : les essais
    # de dépistage sont les premiers essais de la recherche elle-même, sur la
    # fenêtre complète, comptés dans le budget demandé — jamais un essai ni
    # un pool de process en plus.
    def _should_reduce_space(self, n_trials: int) -> bool:
        """Ne réduit que quand ça peut vraiment aider : espace à au moins 6
        paramètres ET couverture (n_trials / cardinalité) très faible — même
        seuil d'esprit que scripts/audit_param_space.py. Sur un petit espace
        déjà bien couvert, le gel ne ferait que réduire l'exploration pour
        rien."""
        if len(self.param_space) < 6:
            return False
        card = math.prod(len(v) for v in self.param_space.values())
        return card > max(n_trials, 1) * 200

    def _min_trades(self) -> int:
        """Plancher de non-dégénérescence de la métrique de sélection.

        ``optimizer.min_trades`` dans la config, sinon
        ``MIN_SIGNIFICANT_TRADES`` — le même seuil que ``beats_baseline`` et le
        lifecycle, désormais unique dans le dépôt.

        La clé de config reste la sortie de secours des études qui ont besoin de
        l'ancien plancher (2) : classer des paramétrages à deux trades est
        légitime pour explorer, jamais pour décider.
        """
        return int((self.cfg.get("optimizer") or {}).get(
            "min_trades", MIN_SIGNIFICANT_TRADES))

    def _impact_scores(self, results: List[dict],
                       param_keys: List[str]) -> Tuple[Dict[str, float], Dict[str, Any]]:
        """Impact marginal de chaque paramètre à partir d'essais déjà joués :
        écart entre la moyenne du score final des essais groupés par valeur,
        la plus haute et la plus basse. NaN si le paramètre n'a pas été
        observé à au moins 2 valeurs distinctes (donnée insuffisante pour
        juger — DIFFÉRENT d'un impact mesuré et nul, cf. ``_freeze_params``).
        0.0 si mesuré et effectivement plat (essais tous dégénérés, par ex.)."""
        impacts: Dict[str, float] = {}
        best_value_by_param: Dict[str, Any] = {}
        for k in param_keys:
            by_value: Dict[Any, List[float]] = {}
            for r in results:
                v = r["params"].get(k)
                by_value.setdefault(v, []).append(r["final_score"])
            means = {v: statistics.mean(s) for v, s in by_value.items() if s}
            finite = {v: m for v, m in means.items() if math.isfinite(m)}
            impacts[k] = (max(finite.values()) - min(finite.values())
                         if len(finite) >= 2 else float("nan"))
            if finite:
                best_value_by_param[k] = max(finite.items(), key=lambda kv: kv[1])[0]
            elif means:
                best_value_by_param[k] = next(iter(means))
            else:
                opts = self.param_space[k]
                best_value_by_param[k] = opts[len(opts) // 2]
        return impacts, best_value_by_param

    def _freeze_params(self, impacts: Dict[str, float], best_value_by_param: Dict[str, Any],
                       param_keys: List[str], *,
                       max_cardinality: Optional[float] = None,
                       min_impact_share: float = 0.10) -> Tuple[dict, list]:
        """Décide quels paramètres geler à partir d'impacts déjà calculés.
        Toujours au moins 1 paramètre conservé (jamais un espace totalement
        gelé). Deux modes :
          - ``max_cardinality`` (grid) : gèle par impact CROISSANT jusqu'à
            repasser sous ce seuil — réduction OBLIGATOIRE (une grille de
            plusieurs milliards de combinaisons est infaisable), donc gèle
            même sur signal faible ou absent (NaN inclus, en dernier
            recours), sinon la cible ne serait jamais atteignable.
          - sinon (random/bayesian, réduction facultative) : gèle seulement
            les paramètres MESURÉS à faible impact (part sous
            ``min_impact_share``). Un impact NaN (paramètre observé à moins
            de 2 valeurs distinctes dans le dépistage — arrive vite avec un
            dépistage en budget court face à un espace à beaucoup de
            paramètres, ex. 8 essais pour 21 paramètres) n'est PAS une
            preuve de faible impact : ce paramètre n'est jamais gelé sur
            cette base. Idem si aucun paramètre n'a de signal exploitable
            (impacts tous nuls ou NaN, ex: essais tous dégénérés à -999,
            observé sur opus_omnibus_v12 avec une fenêtre de test trop
            courte) : ne gèle RIEN plutôt que de figer des paramètres sur du
            bruit ou une absence de données.
        """
        # NaN (donnée insuffisante) toujours après les impacts mesurés, pour
        # qu'ils ne soient gelés qu'en tout dernier recours (mode grid) ou
        # jamais (mode random/bayesian, cf. boucle ci-dessous).
        ranked = sorted(param_keys, key=lambda k: (not math.isfinite(impacts.get(k, 0.0)),
                                                    impacts.get(k, 0.0)
                                                    if math.isfinite(impacts.get(k, 0.0)) else 0.0))
        if max_cardinality is not None:
            frozen_keys: List[str] = []
            card = math.prod(len(self.param_space[k]) for k in param_keys)
            for k in ranked:
                if card <= max_cardinality or len(frozen_keys) >= len(param_keys) - 1:
                    break
                card = max(1, card // max(1, len(self.param_space[k])))
                frozen_keys.append(k)
        else:
            total = sum(v for v in impacts.values() if math.isfinite(v) and v > 0)
            frozen_keys = []
            if total > 0:
                for k in ranked:
                    if len(frozen_keys) >= len(param_keys) - 1:
                        break
                    v = impacts.get(k, 0.0)
                    if not math.isfinite(v):
                        continue  # pas assez de données -> jamais gelé sur cette base
                    if v / total > min_impact_share:
                        break
                    frozen_keys.append(k)
        frozen = {k: best_value_by_param[k] for k in frozen_keys}
        kept_keys = [k for k in param_keys if k not in frozen]
        return frozen, kept_keys

    # Sous ce ratio (essais de dépistage / nombre de paramètres), l'estimateur
    # marginal n'a plus de signal fiable à offrir en mode facultatif : avec
    # aussi peu d'essais que de paramètres (voire moins), CHAQUE paramètre
    # varie simultanément à presque chaque essai — l'« impact » mesuré d'un
    # paramètre est alors dominé par le bruit de tous les autres qui changent
    # en même temps, pas par son propre effet. Mesuré sur opus_omnibus_v9 (21
    # paramètres, 8 essais de dépistage) : geler 20/21 paramètres sur cette
    # base, à chaque run, gel ou pas gel du signal NaN (cf. commit précédent).
    # Le mode grid (max_cardinality) n'est PAS concerné : sa réduction est
    # obligatoire (une grille de plusieurs milliards de combinaisons est
    # infaisable), il n'a pas le choix de reculer.
    _MIN_SCREEN_PER_PARAM = 2

    def _freeze_from_results(self, screen_results: List[dict], param_keys: List[str], *,
                             max_cardinality: Optional[float] = None) -> Optional[dict]:
        """Calcule l'impact de chaque paramètre à partir d'essais DÉJÀ joués
        (en budget, même fenêtre que la recherche) et gèle les moins
        impactants — mute ``self.param_space`` EN PLACE (sauvegardé pour
        ``_restore_param_space``). Retourne ``None`` si rien n'a été gelé."""
        if not screen_results:
            return None
        if (max_cardinality is None
                and len(screen_results) < self._MIN_SCREEN_PER_PARAM * len(param_keys)):
            return None
        impacts, best_value_by_param = self._impact_scores(screen_results, param_keys)
        frozen, kept_keys = self._freeze_params(impacts, best_value_by_param, param_keys,
                                                max_cardinality=max_cardinality)
        if not frozen:
            return None
        card_before = math.prod(len(self.param_space[k]) for k in param_keys)
        if getattr(self, "_param_space_backup", None) is None:
            self._param_space_backup = dict(self.param_space)
        for k, v in frozen.items():
            self.param_space[k] = [v]
        card_after = math.prod(len(self.param_space[k]) for k in param_keys)
        logger.info(
            f"[ParamSearchOptim] {self.strategy_name} : {len(frozen)}/{len(param_keys)} "
            f"paramètres gelés (dépistage {len(screen_results)} essais, dans le budget) — "
            f"cardinalité {card_before:,} -> {card_after:,}"
        )
        return {
            "frozen_params": frozen, "kept_params": kept_keys,
            "n_screen": len(screen_results),
            "cardinality_before": card_before, "cardinality_after": card_after,
        }

    def _restore_param_space(self) -> None:
        backup = getattr(self, "_param_space_backup", None)
        if backup is not None:
            self.param_space = backup
            self._param_space_backup = None

    # ── Pool de process partagé (dépistage + recherche, sans re-spawn) ──────
    @contextmanager
    def _open_pool(self, n_jobs: int):
        """Pool PERSISTANT pour toute la durée du bloc ``with`` — plusieurs
        appels à ``_run_parallel`` (ex: dépistage puis recherche réduite)
        peuvent le partager sans jamais repayer le spawn + ré-import complet
        de l'appli. ``None`` en mode séquentiel (n_jobs<=1, ou mémoire
        insuffisante pour >1 worker) : ``_run_parallel`` bascule alors en
        boucle in-process."""
        safe_jobs = self._safe_worker_count(n_jobs)
        if safe_jobs <= 1:
            yield None
            return
        import concurrent.futures
        import multiprocessing as _mp
        cfg_yaml, df_is_ipc, df_oos_ipc, init_args = self._serialize_pool_inputs()
        ctx = _mp.get_context("spawn")
        executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=safe_jobs, mp_context=ctx,
            initializer=_worker_init, initargs=init_args)
        try:
            yield _PoolHandle(executor, cfg_yaml, df_is_ipc, df_oos_ipc, safe_jobs)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

    def _submit_wave(self, pool: "_PoolHandle", params_list: List[dict],
                     timeout: int = 300) -> Tuple[List[dict], bool, List[dict]]:
        """Soumet une vague de ``params_list`` au pool DÉJÀ OUVERT (jamais
        créé ici) et attend leurs résultats. Retourne (résultats OK, pool
        cassé ?, params non traités si cassé). Primitive bas niveau partagée
        par ``_run_parallel`` (random/grid) et ``_optuna_parallel`` (bayésien
        parallèle)."""
        import concurrent.futures
        try:
            from concurrent.futures.process import BrokenProcessPool
        except ImportError:
            BrokenProcessPool = Exception  # type: ignore

        worker_args = [self._worker_args(p, pool.cfg_yaml, pool.df_is_ipc, pool.df_oos_ipc)
                      for p in params_list]
        try:
            futures_map = {pool.executor.submit(_eval_worker, a): i
                           for i, a in enumerate(worker_args)}
        except BrokenProcessPool as _bp:
            logger.error("[Optimizer] pool déjà cassé, bascule séquentielle : %s", _bp)
            return [], True, list(params_list)

        results: List[Optional[dict]] = [None] * len(params_list)
        broken = False
        remaining: List[dict] = []
        for fut in concurrent.futures.as_completed(futures_map):
            i = futures_map[fut]
            try:
                r = fut.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                logger.warning("[Optimizer] worker timeout (>%ds), ignoré", timeout)
                continue
            except BrokenProcessPool as _bp:
                logger.error("[Optimizer] BrokenProcessPool (worker tué, ex: OOM) : %s", _bp)
                broken = True
                for f, idx in futures_map.items():
                    if not f.done():
                        remaining.append(params_list[idx])
                        f.cancel()
                break
            except Exception as _e:
                logger.warning(f"[Optimizer] worker KO : {_e}")
                continue
            if "error" in r:
                logger.warning("[Optimizer] worker erreur : %s", r["error"])
                continue
            results[i] = r
        ok_results = [r for r in results if r is not None]
        return ok_results, broken, remaining

    def bayesian_search(self, n_trials: int = 40, n_jobs: int = 1,
                        early_stop_patience: int = 0,
                        param_search_optim: bool = True) -> dict:
        """Recherche bayésienne. Utilise Optuna (TPE, recherche informée par un
        modèle de substitution) si la librairie est installée ; sinon retombe sur
        l'heuristique historique (exploration aléatoire + raffinement local)."""
        if not self.param_space:
            return {"error": f"Aucun espace de params pour {self.strategy_name}"}
        try:
            import optuna  # noqa: F401
        except Exception:
            logger.info("[Bayesian] Optuna absent — repli sur random+perturbation. "
                        "Installez optuna pour une vraie recherche TPE.")
            try:
                return self._bayesian_search_legacy(n_trials, n_jobs, early_stop_patience,
                                                    param_search_optim=param_search_optim)
            finally:
                self._restore_param_space()
        return self._bayesian_search_optuna(n_trials, n_jobs, early_stop_patience,
                                            param_search_optim=param_search_optim)

    # ── Optuna (TPE) : vraie recherche informée ───────────────────────────────
    def _params_from_trial(self, trial) -> dict:
        """Construit un jeu de params depuis un trial Optuna. Chaque paramètre est
        encodé par l'INDICE de son option (catégoriel hashable) — robuste quel que
        soit le type des valeurs (float/int/bool/list)."""
        p = {}
        for k, opts in self.param_space.items():
            idx = trial.suggest_categorical(k, list(range(len(opts))))
            p[k] = opts[idx]
        return self._with_hp(p)

    def _bayesian_search_optuna(self, n_trials: int, n_jobs: int,
                                early_stop_patience: int,
                                param_search_optim: bool = True) -> dict:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        n_startup = max(8, n_trials // 3)
        sampler = optuna.samplers.TPESampler(n_startup_trials=n_startup, seed=0)
        study = optuna.create_study(direction="maximize", sampler=sampler)

        param_keys = list(self.param_space.keys())
        freeze_at = (n_startup if (param_search_optim and self._should_reduce_space(n_trials))
                    else None)

        safe_jobs = self._safe_worker_count(n_jobs)
        if safe_jobs <= 1:
            reduction = self._optuna_sequential(study, n_trials, early_stop_patience,
                                                freeze_at=freeze_at, param_keys=param_keys)
        else:
            reduction = self._optuna_parallel(study, n_trials, safe_jobs, early_stop_patience,
                                              freeze_at=freeze_at, param_keys=param_keys)
        result = self._best_result()
        if reduction:
            result["param_search_optim"] = reduction
        return result

    def _optuna_param_importances(self, study,
                                  param_keys: List[str]) -> Optional[Dict[str, float]]:
        """Importances de paramètres estimées par Optuna, basées ANOVA.

        Essaie les évaluateurs dans l'ordre de préférence :
          1. fANOVA — le plus robuste face aux paramètres corrélés, mais requiert
             scikit-learn (RandomForestRegressor interne). Depuis la suppression
             de sklearn (phase 6), il n'est donc plus disponible en production ;
             conservé en tête pour les environnements de dev qui l'ont encore.
          2. PedANOVA — variante sklearn-free (pure Optuna), disponible même sans
             sklearn. C'est le chemin effectif du repo post-phase 6.

        Retourne ``None`` si aucun évaluateur ne rend un signal fini de somme
        strictement positive — l'appelant retombe alors sur l'estimateur
        marginal ``_impact_scores`` (défense en profondeur)."""
        import warnings

        import optuna
        from optuna.importance import get_param_importances

        evaluator_factories = []
        try:
            from optuna.importance import FanovaImportanceEvaluator
            evaluator_factories.append(("fANOVA", lambda: FanovaImportanceEvaluator(seed=0)))
        except Exception:
            pass
        try:
            from optuna.importance import PedAnovaImportanceEvaluator
            evaluator_factories.append(("PedANOVA", lambda: PedAnovaImportanceEvaluator()))
        except Exception:
            pass

        for name, make_evaluator in evaluator_factories:
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore", category=optuna.exceptions.ExperimentalWarning)
                    raw = get_param_importances(study, evaluator=make_evaluator())
                impacts = {k: float(raw.get(k, 0.0)) for k in param_keys}
                if (all(math.isfinite(v) for v in impacts.values())
                        and sum(impacts.values()) > 0):
                    return impacts
            except Exception as _e:
                logger.info("[Bayesian/TPE] importance %s indisponible (%s) — "
                            "essai de l'évaluateur suivant.", name, _e)
        return None

    def _optuna_apply_freeze(self, study, screen_results: List[dict],
                             param_keys: List[str]) -> Optional[dict]:
        """Gèle le SAMPLER Optuna (``PartialFixedSampler``) sur les paramètres
        à faible impact, calculé à partir des essais de démarrage du TPE déjà
        joués (en budget). Ne mute JAMAIS ``self.param_space`` : le sampler
        fige la valeur lui-même, contrairement à random/grid/legacy — pas de
        backup/restore nécessaire pour ce chemin. Utilise l'estimateur
        d'importance fANOVA d'Optuna quand disponible (plus robuste que
        l'écart marginal simple face à des paramètres corrélés), avec repli
        sur l'estimateur marginal partagé (``_impact_scores``) si fANOVA
        échoue ou ne rend aucun signal exploitable."""
        if len(screen_results) < self._MIN_SCREEN_PER_PARAM * len(param_keys):
            return None
        import optuna
        own_impacts, best_value_by_param = self._impact_scores(screen_results, param_keys)
        optuna_impacts = self._optuna_param_importances(study, param_keys)
        impacts = optuna_impacts if optuna_impacts is not None else own_impacts

        frozen, kept_keys = self._freeze_params(impacts, best_value_by_param, param_keys)
        if not frozen:
            return None
        fixed_indices: Dict[str, int] = {}
        for k, v in frozen.items():
            try:
                fixed_indices[k] = self.param_space[k].index(v)
            except ValueError:
                continue  # valeur introuvable (ne devrait pas arriver) -> ne fige pas ce param
        if not fixed_indices:
            return None

        import warnings as _warnings
        with _warnings.catch_warnings():
            _warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)
            study.sampler = optuna.samplers.PartialFixedSampler(fixed_indices, study.sampler)

        card_before = math.prod(len(self.param_space[k]) for k in param_keys)
        card_after  = math.prod(1 if k in fixed_indices else len(self.param_space[k])
                                for k in param_keys)
        logger.info(
            f"[ParamSearchOptim] {self.strategy_name} (bayésien/TPE) : "
            f"{len(fixed_indices)}/{len(param_keys)} paramètres gelés "
            f"(dépistage {len(screen_results)} essais, dans le budget, sampler figé) — "
            f"cardinalité {card_before:,} -> {card_after:,}"
        )
        return {
            "frozen_params": {k: frozen[k] for k in fixed_indices},
            "kept_params": kept_keys,
            "n_screen": len(screen_results),
            "cardinality_before": card_before, "cardinality_after": card_after,
        }

    def _optuna_sequential(self, study, n_trials: int, early_stop_patience: int,
                           freeze_at: Optional[int] = None,
                           param_keys: Optional[List[str]] = None) -> Optional[dict]:
        """Ask/tell séquentiel in-process — garde le cache d'entraînement chaud
        (même process) d'un trial à l'autre."""
        best_score = -999.0
        no_improve = 0
        reduction = None
        own_results: List[dict] = []
        for i in range(n_trials):
            trial  = study.ask()
            params = self._params_from_trial(trial)
            r      = self._eval(params)
            score  = self._penalized_score(r)
            r["final_score"] = score
            self.results.append(r)
            own_results.append(r)
            study.tell(trial, score if math.isfinite(score) else -999.0)

            if score > best_score:
                best_score = score
                no_improve = 0
            else:
                no_improve += 1

            if self.progress_callback:
                self.progress_callback(i + 1, n_trials, best_score, {
                    "oos_pnl":     r["oos_pnl"],
                    "oos_sharpe":  r["oos_sharpe"],
                    "final_score": score,
                    "overfit":     r.get("overfit", 1.0),
                })

            if freeze_at is not None and reduction is None and len(own_results) >= freeze_at:
                reduction = self._optuna_apply_freeze(study, own_results, param_keys)

            if early_stop_patience > 0 and no_improve >= early_stop_patience:
                logger.info(f"[Bayesian/TPE] Early stop à trial {i+1}/{n_trials}")
                break
        return reduction

    def _optuna_parallel(self, study, n_trials: int, safe_jobs: int,
                         early_stop_patience: int,
                         freeze_at: Optional[int] = None,
                         param_keys: Optional[List[str]] = None) -> Optional[dict]:
        """Ask/tell par lots sur un ProcessPool **persistant** (un seul pool pour
        toute la recherche → workers et cache de features/entraînement réutilisés
        entre les lots). Repli séquentiel si le pool casse (OOM worker)."""
        import concurrent.futures
        try:
            from concurrent.futures.process import BrokenProcessPool
        except ImportError:
            BrokenProcessPool = Exception  # type: ignore
        import multiprocessing as _mp

        cfg_yaml, df_is_ipc, df_oos_ipc, init_args = self._serialize_pool_inputs()
        ctx = _mp.get_context("spawn")
        done = 0
        best_score = -999.0
        no_improve = 0
        _worker_timeout = 300
        reduction = None
        own_results: List[dict] = []

        try:
            with concurrent.futures.ProcessPoolExecutor(
                    max_workers=safe_jobs, mp_context=ctx,
                    initializer=_worker_init, initargs=init_args) as exe:
                while done < n_trials:
                    if self._cancel_event is not None and self._cancel_event.is_set():
                        raise InterruptedError("annulé")
                    k = min(safe_jobs, n_trials - done)
                    trials = [study.ask() for _ in range(k)]
                    params = [self._params_from_trial(t) for t in trials]
                    args   = [self._worker_args(p, cfg_yaml, df_is_ipc, df_oos_ipc)
                              for p in params]
                    futs   = {exe.submit(_eval_worker, a): i for i, a in enumerate(args)}
                    for fut in concurrent.futures.as_completed(futs):
                        i = futs[fut]
                        try:
                            r = fut.result(timeout=_worker_timeout)
                        except Exception as _e:
                            logger.warning(f"[Bayesian/TPE] worker KO : {_e}")
                            study.tell(trials[i], -999.0)
                            continue
                        if "error" in r:
                            logger.warning("[Bayesian/TPE] worker erreur : %s", r["error"])
                            study.tell(trials[i], -999.0)
                            continue
                        score = self._penalized_score(r)
                        r["final_score"] = score
                        self.results.append(r)
                        own_results.append(r)
                        study.tell(trials[i], score if math.isfinite(score) else -999.0)
                        if score > best_score:
                            best_score = score
                            no_improve = 0
                        else:
                            no_improve += 1
                        if self.progress_callback:
                            self.progress_callback(len(self.results), n_trials, best_score, {
                                "oos_pnl":     r["oos_pnl"],
                                "oos_sharpe":  r["oos_sharpe"],
                                "final_score": score,
                                "overfit":     r.get("overfit", 1.0),
                            })
                    done += k
                    if freeze_at is not None and reduction is None and len(own_results) >= freeze_at:
                        reduction = self._optuna_apply_freeze(study, own_results, param_keys)
                    if early_stop_patience > 0 and no_improve >= early_stop_patience:
                        logger.info(f"[Bayesian/TPE] Early stop à {done}/{n_trials}")
                        break
        except BrokenProcessPool as _bp:
            logger.error("[Bayesian/TPE] pool brisé (OOM worker ?) — repli séquentiel "
                         "pour les trials restants : %s", _bp)
            remaining_freeze_at = None
            if freeze_at is not None and reduction is None:
                remaining_freeze_at = freeze_at - len(own_results)
                if remaining_freeze_at <= 0:
                    remaining_freeze_at = None
            seq_reduction = self._optuna_sequential(
                study, n_trials - len(self.results), early_stop_patience,
                freeze_at=remaining_freeze_at, param_keys=param_keys)
            reduction = reduction or seq_reduction
        return reduction

    def _bayesian_search_legacy(self, n_trials: int = 40, n_jobs: int = 1,
                                early_stop_patience: int = 0,
                                param_search_optim: bool = True) -> dict:
        """Heuristique historique : exploration aléatoire (1/3, EN BUDGET) puis
        raffinement local (perturbation ±1 cran autour du meilleur). Repli quand
        Optuna est absent (ex. environnement de production sans la dépendance).
        La phase d'exploration sert aussi de dépistage Param Search Optim :
        aucun essai ni pool de process supplémentaire."""
        n_explore = max(8, n_trials // 3)
        n_exploit = n_trials - n_explore
        param_keys = list(self.param_space.keys())
        do_reduce = param_search_optim and self._should_reduce_space(n_trials)

        base = len(self.results)  # essais de CET appel seulement (réutilisation d'instance)
        with self._open_pool(n_jobs) as pool:
            self._run_parallel(n_explore, n_trials, trial_offset=0,
                               sampler=lambda: self._with_hp(
                                   {k: random.choice(v) for k, v in self.param_space.items()}),
                               pool=pool)

        reduction = None
        if do_reduce and len(self.results) > base:
            reduction = self._freeze_from_results(self.results[base:], param_keys)

        # Phase exploitation : gaussian autour du meilleur (séquentielle — le
        # pool ci-dessus est déjà refermé, pas de 2e pool ouvert ici).
        if self.results and n_exploit > 0:
            best = max(self.results, key=self._penalized_score)
            no_improve = 0
            best_score = self._penalized_score(best)

            for i in range(n_exploit):
                trial_idx = n_explore + i
                params = self._with_hp(self._perturb(best["params"]))
                r = self._eval(params)
                score = self._penalized_score(r)
                r["final_score"] = score
                self.results.append(r)

                if score > best_score:
                    best_score = score
                    best = r
                    no_improve = 0
                else:
                    no_improve += 1

                if self.progress_callback:
                    self.progress_callback(trial_idx + 1, n_trials, best_score, {
                        "oos_pnl":     r["oos_pnl"],
                        "oos_sharpe":  r["oos_sharpe"],
                        "final_score": score,
                        "overfit":     r.get("overfit", 1.0),
                    })
                if early_stop_patience > 0 and no_improve >= early_stop_patience:
                    logger.info(f"[Bayesian] Early stop exploit trial {i+1}/{n_exploit}")
                    break

        result = self._best_result()
        if reduction:
            result["param_search_optim"] = reduction
        return result

    # ── Helpers ProcessPool (partagés random/bayesian) ───────────────────────
    def _serialize_pool_inputs(self):
        """Sérialise (une fois) cfg + DataFrames IS/OOS pour les workers spawn.
        Retourne ``(cfg_yaml, df_is_ipc, df_oos_ipc, init_args)``."""
        import yaml as _yaml
        _buf_is = io.BytesIO()
        self.df_is.write_ipc(_buf_is)
        df_is_ipc  = _buf_is.getvalue()
        _buf_oos = io.BytesIO()
        self.df_oos.write_ipc(_buf_oos)
        df_oos_ipc = _buf_oos.getvalue()
        cfg_yaml = _yaml.dump(self.cfg)
        init_args = (self.strategy_name, cfg_yaml,
                     df_is_ipc, df_oos_ipc, self.symbol, self.timeframe)
        return cfg_yaml, df_is_ipc, df_oos_ipc, init_args

    def _worker_args(self, params: dict, cfg_yaml: str,
                     df_is_ipc: bytes, df_oos_ipc: bytes) -> tuple:
        return (self.strategy_name, cfg_yaml, df_is_ipc, df_oos_ipc,
                self.symbol, params, self.timeframe)

    def _safe_worker_count(self, n_jobs: int) -> int:
        """Plafonne le nombre de workers : cpu-1 puis cap mémoire anti-OOM."""
        if n_jobs <= 1:
            return 1
        _cpu = os.cpu_count() or 1
        safe = max(1, min(n_jobs, max(1, _cpu - 1)))
        # Estimation prudente ~5× le payload IPC + 256 Mo (features + LightGBM).
        try:
            _buf_is = io.BytesIO()
            self.df_is.write_ipc(_buf_is)
            _buf_oos = io.BytesIO()
            self.df_oos.write_ipc(_buf_oos)
            per_worker = int((_buf_is.tell() + _buf_oos.tell()) * 5) + 256 * 1024 * 1024
            safe = _mem_aware_max_workers(safe, per_worker)
        except Exception:
            pass
        return safe

    def _run_parallel(self, n: int, n_total: int, trial_offset: int = 0,
                      sampler=None, pool: Optional["_PoolHandle"] = None,
                      early_stop_patience: int = 0,
                      initial_best: float = -999.0) -> int:
        """Évalue ``n`` essais et les ajoute à ``self.results`` : séquentiel si
        ``pool`` est ``None``, sinon par vagues de ``pool.safe_jobs`` sur le
        pool DÉJÀ OUVERT (jamais créé ici — cf. ``_open_pool``). Plusieurs
        appels successifs peuvent partager le même ``pool`` (dépistage puis
        recherche réduite) sans jamais rouvrir de 2e pool. ``initial_best``
        amorce le meilleur score reporté au ``progress_callback`` quand cet
        appel poursuit une recherche déjà commencée (évite un faux retour à
        -999 affiché en UI entre deux phases). Retourne le nombre de trials
        TENTÉS (échecs inclus, ≤ n ; < n seulement sur early stop) — c'est ce
        décompte, pas celui des seuls succès, que l'appelant doit soustraire
        de son budget."""
        if sampler is None:
            def sampler(): return self._with_hp(
                {k: random.choice(v) for k, v in self.param_space.items()})

        best_so_far = initial_best
        no_improve = 0
        done = 0

        def _record(r: dict) -> None:
            nonlocal best_so_far, no_improve, done
            score = self._penalized_score(r)
            r["final_score"] = score
            self.results.append(r)
            done += 1
            if score > best_so_far:
                best_so_far = score
                no_improve = 0
            else:
                no_improve += 1
            if self.progress_callback:
                self.progress_callback(trial_offset + done, n_total, best_so_far, {
                    "oos_pnl":     r["oos_pnl"],
                    "oos_sharpe":  r["oos_sharpe"],
                    "final_score": score,
                    "overfit":     r.get("overfit", 1.0),
                })

        if pool is None:
            attempted = 0
            for _ in range(n):
                attempted += 1
                _record(self._eval(sampler()))
                if early_stop_patience > 0 and no_improve >= early_stop_patience:
                    logger.info(f"[Optimizer] Early stop à trial {trial_offset + done}/{n_total}")
                    break
            return attempted

        # Parallèle : par vagues de pool.safe_jobs sur le pool partagé (jamais
        # créé/fermé ici — cf. _open_pool). La boucle compte les TENTATIVES
        # (``attempted``), pas les seuls succès (``done``) : un trial en échec
        # (worker KO/timeout/erreur stratégie) consomme quand même son
        # créneau du budget ``n``. Compter les succès faisait échantillonner
        # au-delà du budget en cas d'échecs — et pour grid_search, dont le
        # sampler épuise une énumération finie, levait StopIteration en plein
        # milieu de la grille.
        pool_broken = False
        attempted = 0
        while attempted < n:
            if self._cancel_event is not None and self._cancel_event.is_set():
                raise InterruptedError("annulé")
            if pool_broken:
                # Pool définitivement mort (BrokenProcessPool observé) : bascule
                # séquentielle pour le reste, un trial à la fois.
                params = sampler()
                attempted += 1
                try:
                    r = self._eval(params)
                except Exception as _se:
                    logger.warning(f"[Optimizer] trial séquentiel KO : {_se}")
                    continue
                _record(r)
            else:
                k = min(pool.safe_jobs, n - attempted)
                param_list = [sampler() for _ in range(k)]
                attempted += k
                wave_results, broken, remaining = self._submit_wave(pool, param_list)
                for r in wave_results:
                    _record(r)
                if broken:
                    pool_broken = True
                    logger.error(
                        "[Optimizer] BrokenProcessPool (worker tué, ex: OOM) — "
                        "bascule en séquentiel pour les trials restants")
                    # Re-tentatives des params déjà comptés dans ``attempted``
                    # (soumis puis annulés par la casse du pool) — pas des
                    # tirages en plus.
                    for p in remaining:
                        try:
                            r = self._eval(p)
                        except Exception as _se:
                            logger.warning(f"[Optimizer] trial séquentiel KO : {_se}")
                            continue
                        _record(r)
            if early_stop_patience > 0 and no_improve >= early_stop_patience:
                logger.info(f"[Optimizer] Early stop à {trial_offset + done}/{n_total}")
                break
        return attempted

    def _perturb(self, params: dict) -> dict:
        """Perturbation légère d'un jeu de params pour l'exploitation."""
        new_params = deepcopy(params)
        keys = list(self.param_space.keys())
        if not keys:
            return new_params
        n_perturb = max(1, len(keys) // 3)
        for k in random.sample(keys, min(n_perturb, len(keys))):
            options = self.param_space[k]
            if len(options) > 1:
                curr_idx = options.index(params[k]) if params[k] in options else 0
                offsets  = [-1, 0, 1]
                new_idx  = curr_idx + random.choice(offsets)
                new_idx  = max(0, min(len(options) - 1, new_idx))
                new_params[k] = options[new_idx]
        return new_params

    # Grid search est exhaustif — au-delà de ce nombre de combinaisons, une
    # grille complète devient impraticable ; c'est là que la réduction
    # d'espace (gel des paramètres à faible impact) rend le plus service.
    _GRID_REDUCE_THRESHOLD = 5000

    def grid_search(self, n_jobs: int = 1, param_search_optim: bool = True) -> dict:
        if not self.param_space:
            return {"error": "Pas d'espace de params"}

        param_keys = list(self.param_space.keys())
        full_card = math.prod(len(v) for v in self.param_space.values())
        do_reduce = (param_search_optim and len(param_keys) >= 6
                    and full_card > self._GRID_REDUCE_THRESHOLD)
        reduction = None
        try:
            with self._open_pool(n_jobs) as pool:
                if do_reduce:
                    # Dépistage sur la fenêtre COMPLÈTE (même IS/OOS que
                    # l'énumération qui suit) et le MÊME pool que
                    # l'énumération — une fenêtre réduite forcerait un 2e pool
                    # (les workers pré-chargent IS/OOS une seule fois à
                    # l'ouverture, cf. opt_workers._worker_init) et casserait
                    # le cache de features entre dépistage et énumération.
                    n_screen = min(max(12, 2 * len(param_keys)), 60)
                    base = len(self.results)  # essais de CET appel seulement
                    self._run_parallel(n_screen, n_screen, trial_offset=0, pool=pool)
                    reduction = self._freeze_from_results(
                        self.results[base:], param_keys,
                        max_cardinality=self._GRID_REDUCE_THRESHOLD)

                keys = list(self.param_space.keys())
                vals = list(self.param_space.values())
                combos = list(itertools.product(*vals))
                n_combos = len(combos)
                logger.info(f"[Optimizer] Grid search : {n_combos} combinaisons")
                param_iter = iter(self._with_hp(dict(zip(keys, c))) for c in combos)
                initial_best = max((self._penalized_score(r) for r in self.results),
                                   default=-999.0)
                # Énumération parallélisée (n_jobs) sur le pool partagé — la
                # boucle exhaustive était auparavant toujours séquentielle,
                # même quand n_jobs>1 était demandé.
                self._run_parallel(n_combos, n_combos, trial_offset=0,
                                   sampler=lambda: next(param_iter), pool=pool,
                                   initial_best=initial_best)
            result = self._best_result()
        finally:
            self._restore_param_space()
        if reduction:
            result["param_search_optim"] = reduction
        return result

    def _best_result(self) -> dict:
        if not self.results:
            return {
                "error": ("Aucun trial complété (workers tous KO — ex: OOM LightGBM). "
                          "Réduisez --jobs / n_jobs, ou diminuez le nombre de bougies."),
                "failed": True,
                "completed_trials": 0,
            }
        best = max(self.results, key=self._penalized_score)
        # Top 5 par score final
        sorted_results = sorted(self.results, key=self._penalized_score, reverse=True)
        top5 = [
            {
                "is_score":    round(r["is_score"], 4),
                "oos_score":   round(r["oos_score"], 4),
                "final_score": round(self._penalized_score(r), 4),
                "oos_pnl":     round(r["oos_pnl"], 2),
                "oos_wr":      round(r.get("oos_wr", 0.0), 1),
                "oos_dd":      round(r.get("oos_dd", 0.0), 2),
                "overfit":     round(r.get("overfit", 1.0), 2),
            }
            for r in sorted_results[:5]
        ]
        return {
            "strategy":       self.strategy_name,
            "timeframe":      self.timeframe,
            "symbol":         self.symbol,
            "best_params":    best["params"],
            "best_is_score":  best["is_score"],
            "best_oos_score": self._penalized_score(best),
            "best_is_pnl":    best["is_pnl"],
            "best_oos_pnl":   best["oos_pnl"],
            "best_is_sharpe": best["is_sharpe"],
            "best_oos_sharpe":best["oos_sharpe"],
            "best_is_trades": best["is_trades"],
            "best_oos_trades":best["oos_trades"],
            "best_oos_wr":    round(best.get("oos_wr", 0.0), 1),
            "best_is_wr":     round(best.get("is_wr", 0.0), 1),
            "best_oos_dd":    round(best.get("oos_dd", 0.0), 2),
            "best_oos_alpha": round(best["oos_alpha"], 4) if best.get("oos_alpha") is not None else None,
            "overfit":        best.get("overfit", 1.0),
            "n_trials":       len(self.results),
            "top5":           top5,
            # S11 : contexte d'exécution facturé pendant toute l'optimisation
            # (venue, spot/margin, levier, détail des frais, emprunt). Sans lui,
            # un `oos_score` n'est pas comparable d'un run à l'autre : deux
            # scores très différents peuvent ne différer que par la venue.
            "cost_model":     self._cost_model(),
        }

    def _cost_model(self) -> dict:
        """Modèle de coûts de CE couple (symbole, timeframe).

        Recalculé depuis la config et la venue résolue plutôt que capturé sur un
        trial : tous les trials partagent le même contexte (seuls les params de
        stratégie varient), et un trial peut avoir échoué.
        """
        from app.core.bot_identity import resolve_venue
        from app.core.execution import cost_model
        try:
            venue = resolve_venue(self.cfg, tf=self.timeframe, symbol=self.symbol)
            return cost_model(self.cfg, venue)
        except Exception as e:      # pragma: no cover — jamais bloquant
            logger.debug(f"[Optimizer] modèle de coûts indisponible : {e}")
            return {}

    # ── Dispatch & two-phase ML (#6) ──────────────────────────────────────────
    def _dispatch(self, method: str, n_trials: int, n_jobs: int,
                  early_stop_patience: int = 0,
                  param_search_optim: bool = True) -> dict:
        """Lance une recherche selon la méthode demandée (phase unique).

        ``param_search_optim`` (dépistage en budget puis gel des paramètres à
        faible impact, cf. ``StrategyOptimizer._freeze_from_results`` /
        ``_optuna_apply_freeze``) n'est PAS un 4e ``method`` — c'est une
        option orthogonale appliquée par ``random_search``/
        ``bayesian_search``/``grid_search`` eux-mêmes, quel que soit celui
        choisi ici."""
        if method == "grid":
            return self.grid_search(n_jobs=n_jobs, param_search_optim=param_search_optim)
        if method == "bayesian":
            return self.bayesian_search(n_trials, n_jobs=n_jobs,
                                        early_stop_patience=early_stop_patience,
                                        param_search_optim=param_search_optim)
        return self.random_search(n_trials, n_jobs=n_jobs,
                                  early_stop_patience=early_stop_patience,
                                  param_search_optim=param_search_optim)

    def optimize_two_phase(self, method: str, n_trials: int, n_jobs: int,
                           ml_hp_space: Dict[str, List],
                           early_stop_patience: int = 0,
                           param_search_optim: bool = True) -> dict:
        """Optimisation ML en deux phases (#6).

        Phase externe : petite **grille** sur les hyperparamètres d'entraînement
        (``ml_hp_space``). Phase interne : recherche ``method`` sur les seuils de
        décision (``param_space``), avec les HP figés pour la combinaison
        courante. Les HP figés sont injectés dans chaque jeu de params
        (``_with_hp``) → le cache d'entraînement reste chaud *au sein* d'une
        combinaison (clé de cache constante), et chaque combinaison repaie ses
        propres réentraînements (coût ~linéaire avec le nombre de combos).

        Sans HP réglables (``ml_hp_space`` vide), retombe sur la phase unique.
        Retourne le meilleur résultat (best_oos_score) parmi les combinaisons ;
        ``best_params`` y inclut les HP retenus (donc persistés et réutilisés au
        ré-entraînement du modèle final).
        """
        keys = list(ml_hp_space.keys())
        if not keys:
            return self._dispatch(method, n_trials, n_jobs, early_stop_patience,
                                  param_search_optim=param_search_optim)

        combos = list(itertools.product(*[ml_hp_space[k] for k in keys]))
        logger.info("[Optimizer] Two-phase ML %s : %d combo(s) HP × recherche %s",
                    self.strategy_name, len(combos), method)

        candidates: List[dict] = []
        for combo in combos:
            hp = dict(zip(keys, combo))
            self._fixed_ml_hp = hp
            self.results = []  # isole les trials de cette combinaison
            try:
                res = self._dispatch(method, n_trials, n_jobs, early_stop_patience,
                                     param_search_optim=param_search_optim)
            except InterruptedError:
                self._fixed_ml_hp = None
                raise
            if res.get("best_params"):
                res = dict(res)
                res["ml_hp"] = hp
                candidates.append(res)
            logger.info("[Optimizer]   HP=%s → OOS=%.4f", hp,
                        res.get("best_oos_score", float("nan")))
        self._fixed_ml_hp = None

        if not candidates:
            return {"error": "two-phase : aucun trial exploitable", "failed": True}
        best = max(candidates, key=lambda r: r.get("best_oos_score", -999))
        best["n_hp_combos"] = len(combos)
        return best


# ── Compatibilité ascendante ─────────────────────────────────────────────────
# La façade ``app.engine.optimizer`` a été supprimée (ARCH-007). Les consommateurs
# historiques qui référencent ``StrategyOptimizer`` continuent de fonctionner via
# cet alias — nouvelle classe canonique : ``OptimizerSearchEngine``.
StrategyOptimizer = OptimizerSearchEngine
