"""Moteur multi-stratégies avec scoring — chaque stratégie retourne un dict signal."""
import logging
from typing import Any, Dict, List, Optional  # noqa: F401

import polars as pl

logger = logging.getLogger(__name__)


class BaseStrategy:
    """Interface que toutes les stratégies doivent respecter."""
    name: str = "base"

    # Métadonnées d'optimisation (surchargées par chaque stratégie)
    timeframes:   List[str] = []
    param_space:  Dict[str, List] = {}
    fixed_params: Dict[str, Any]  = {}

    # S2-04 (généralisation multi-actifs) : classes d'actifs compatibles.
    # Défaut = les deux (comportement inchangé pour toute stratégie qui ne
    # dépend pas de données crypto-only). Les stratégies consommant des
    # features dérivés (funding/OI/long-short/taker — crypto perpetuals
    # uniquement) surchargent avec frozenset({"crypto"}).
    asset_classes: frozenset = frozenset({"crypto", "equity"})

    def min_bars_required(self, params: dict = None) -> int:
        """Nombre minimum de bougies requis pour calculer les indicateurs."""
        return 50

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        """Retourne {"score": float [0-1], "side": "long"|"short"|"none", "name": str}."""
        raise NotImplementedError

    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        """Hook optionnel : la stratégie peut demander la sortie anticipée d'une
        position déjà ouverte (ex. changement de régime, inversion du signal
        directionnel). Appelé par l'engine de backtest et le live à chaque cycle
        après la barre courante, **avant** la vérification SL/TP.

        Retourne :
          - ``None`` si la position doit rester ouverte (comportement par défaut)
          - une chaîne (raison de sortie, ex. ``"regime_exit_TD"``) sinon : la
            position sera clôturée immédiatement au prix de clôture courant et
            ``exit_reason`` sera renseigné avec cette chaîne.
        """
        return None

    def check_scale_in(self, df: pl.DataFrame, position: dict,
                       params: dict = None) -> Optional[Dict[str, Any]]:
        """Hook optionnel : pyramidage (ajout d'une unité sur position gagnante).

        Appelé par le backtest et le live à chaque cycle quand une position de
        cette stratégie est ouverte, **après** la mise à jour du trailing stop.
        La stratégie peut stocker son état de pyramidage (nb d'unités, prochain
        niveau d'ajout…) directement dans ``position`` — le dict est persisté
        par l'appelant entre les cycles.

        Retourne :
          - ``None`` si aucun ajout (comportement par défaut)
          - un dict ``{"size_factor": float, "reason": str}`` sinon : l'appelant
            ouvre une unité supplémentaire dans le sens de la position, sizée
            comme une entrée normale (risque % équité / distance au stop)
            multipliée par ``size_factor``, dans la limite des contraintes de
            risque globales (notional max, budget du slot).
        """
        return None


class BaseStrategyML(BaseStrategy):
    """
    Classe de base pour les stratégies ML.
    Distingue les stratégies ML (entraînement périodique, persistance modèle)
    des stratégies classiques. managed_externally=True désactive le réentraînement inline.
    """
    retrain_interval_h: int = 6
    model_dir: str = "models"

    # ── Contrat de gate (ML-02) ────────────────────────────────────────────
    # Déclaratif : conventions de labels/métrique de CETTE recette, quand elle
    # utilise le scorer par défaut (bundle amplitude+direction V4) mais avec
    # d'autres réglages que ceux hérités de V11. Clés reconnues :
    #   label_horizons : horizons de labellisation (ex. [1] = single-horizon)
    #   amp_top_pct    : quantile définissant un "événement" d'amplitude
    #   metric         : métrique sur laquelle le gate arbitre ("auc_amp"…)
    # Vide = défauts de ``app.ml.policy.GateConfig`` (comportement historique).
    # Une recette dont le FORMAT diffère surcharge plutôt ``score_holdout``.
    gate_spec: Dict[str, Any] = {}

    @classmethod
    def score_holdout(cls, path_prefix: str, holdout_df, *,
                      gate_cfg: Any = None, params: dict = None) -> Dict[str, Any]:
        """Charge l'artefact à ``path_prefix`` et retourne ses métriques sur
        ``holdout_df`` — utilisé par le gate de promotion pour comparer un
        candidat au sortant sur un holdout commun (``app.ml.policy``).

        Implémentation par défaut : format bundle 3-fichiers + features V4
        (``app.ml.scoring.score_amp_dir_bundle``), qui couvre la grande
        majorité des recettes du dépôt — ne surcharger que si le format de
        persistance, les features ou les labels diffèrent réellement.

        **classmethod délibérément** : le scoring porte sur un artefact SUR
        DISQUE (souvent le sortant, pas ce qui est chargé en mémoire), et ne
        doit jamais muter l'état ML de l'instance courante. Les réglages
        variables passent par ``gate_cfg`` (knobs de gate) et ``params``
        (params résolus de la stratégie).

        Retourne un dict de métriques (``{"n": …, "auc_amp": …}``) ; ``{}``
        si l'artefact est illisible ou le holdout inexploitable.
        """
        from app.ml.scoring import score_amp_dir_bundle
        return score_amp_dir_bundle(
            path_prefix, holdout_df,
            label_horizons=getattr(gate_cfg, "label_horizons", None),
            amp_top_pct=getattr(gate_cfg, "amp_top_pct", 0.30),
        )

    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        """Entraîne le modèle sur df avec les paramètres fournis."""
        raise NotImplementedError

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        """Retourne un signal en utilisant le modèle déjà entraîné."""
        raise NotImplementedError

    def save_model(self, path: str, extra_meta: Optional[dict] = None) -> None:
        """Persiste le modèle entraîné sur disque (implémentation optionnelle).

        ``extra_meta`` (ML-02) : bloc de provenance optionnel
        (``{"provenance": {...}, "gate": {...}}``) à fusionner dans le
        meta.json — utilisé par ``app.ml.model_registry``/``app.ml.policy``
        pour dater/gater les modèles. Les implémentations qui l'ignorent
        restent valides (comportement historique inchangé)."""

    def load_model(self, path: str) -> bool:
        """Charge un modèle depuis le disque. Retourne True si réussi."""
        return False

    def reset_model(self) -> None:
        """Réinitialise l'état du modèle (utilisé par le walk-forward backtest)."""

    @property
    def is_trained(self) -> bool:
        return False


class Engine:
    def __init__(self):
        self.strategies: List[BaseStrategy] = []

    def register(self, strategy: BaseStrategy, silent: bool = False):
        # Garde contre les doublons (même nom)
        if any(s.name == strategy.name for s in self.strategies):
            logger.warning(f"[Engine] Doublon ignoré : {strategy.name}")
            return
        if not silent:
            logger.info(f"[Engine] Stratégie enregistrée : {strategy.name}")
        self.strategies.append(strategy)

    def best_signal(self, df: pl.DataFrame, params: dict = None,
                    df_htf=None, symbol: str = "",
                    threshold: float = 0.0,
                    stats: Optional[Dict[str, Dict[str, int]]] = None) -> Dict[str, Any]:
        """
        Retourne le signal avec le meilleur score parmi toutes les stratégies.

        Applique le seuil global ``threshold`` ET les seuils par stratégie
        définis dans ``params[strat.name]["score_threshold"]``.
        Si aucun signal ne passe, retourne {"score": 0, "side": "none", "name": ""}.

        Si ``stats`` est fourni (dict), il est incrémenté en place pour le
        diagnostic du backtest. Pour chaque stratégie :
          - evaluated       : appels score() réussis
          - none            : side == "none" ou score <= 0
          - proposed        : un signal directionnel a été émis (score > 0)
          - below_threshold : signal émis mais sous le seuil (rejeté)
          - above_threshold : signal émis et >= seuil (candidat retenu)
          - errors          : exceptions levées par la stratégie
        """
        if df is None or len(df) < 2:
            return {"score": 0, "side": "none", "name": ""}

        best = {"score": 0.0, "side": "none", "name": ""}
        for strat in self.strategies:
            sd = None
            if stats is not None:
                sd = stats.setdefault(strat.name, {
                    "evaluated": 0, "none": 0, "proposed": 0,
                    "below_threshold": 0, "above_threshold": 0, "errors": 0,
                })
            try:
                result = strat.score(df, params, df_htf=df_htf, symbol=symbol)
                if not isinstance(result, dict):
                    continue
                if sd is not None:
                    sd["evaluated"] += 1
                score = result.get("score", 0)
                side  = result.get("side", "none")
                if side == "none" or score <= 0:
                    if sd is not None:
                        sd["none"] += 1
                    continue
                if sd is not None:
                    sd["proposed"] += 1
                # Seuil par stratégie (prioritaire) ou seuil global
                strat_threshold = float(
                    (params or {}).get(strat.name, {}).get("score_threshold", threshold)
                )
                if score < strat_threshold:
                    if sd is not None:
                        sd["below_threshold"] += 1
                    continue
                if sd is not None:
                    sd["above_threshold"] += 1
                if score <= best["score"]:
                    continue
                best = result
            except Exception as e:
                if sd is not None:
                    sd["errors"] += 1
                logger.error(f"[Engine] Erreur dans stratégie {strat.name} : {e}")
        return best
