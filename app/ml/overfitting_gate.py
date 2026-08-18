"""S3-10 : Détection d'overfitting ML — gate de validation post-entraînement.

L'audit V2 notait que la détection d'AUC ≈ 0 (pas d'edge) était silencieusement
tolérée dans `app/ml/trainer.py`. Ce module fournit un **gate de validation
post-entraînement** qui :
    - Mesure l'AUC OOS du modèle entraîné.
    - Émet un warning si AUC < 0.55 (edge faible).
    - Bloque la promotion si AUC < 0.50 (pas d'edge, pire que aléatoire).
    - Logge et notifie pour audit.

Ce n'est pas un contrôle *pendant* l'entraînement (le modèle a déjà
convergé), mais un **gate après entraînement** : si le modèle ne
généralise pas, il n'est pas persisté/promu.
"""
import logging

logger = logging.getLogger(__name__)


# ── Seuils ──────────────────────────────────────────────────────────────────
AUC_RANDOM = 0.50        # performance aléatoire (classifier binaire)
# M-05 : AUC_WEAK est AUSSI le plancher de promotion (policy.auc_floor).
# AUC_GOOD n'est qu'un libellé de qualité — il ne bloque pas la promotion.
AUC_WEAK = 0.55          # edge faible mais détectable = plancher de promotion
AUC_GOOD = 0.60          # libellé « acceptable pour production » (pas un gate)
AUC_STRONG = 0.70        # edge solide


def validate_model_quality(auc_oos: float,
                             strategy_name: str,
                             n_oos_samples: int,
                             n_trials_optimization: int = 1) -> dict:
    """Gate de validation post-entraînement d'un modèle ML.

    Parameters
    ----------
    auc_oos : float
        AUC mesurée sur l'échantillon Out-of-Sample (≥ 0, ≤ 1).
    strategy_name : str
        Nom de la stratégie (pour log/notification).
    n_oos_samples : int
        Nombre d'échantillons OOS (pour juger la significativité).
    n_trials_optimization : int
        Nombre d'essais d'optimisation (pour biais de sélection — cf. Deflated Sharpe).

    Returns
    -------
    dict
        {
            'ok': bool,           # True si le modèle peut être persisté/promu
            'auc': float,
            'level': str,         # 'block' | 'warn' | 'good' | 'strong'
            'reason': str,
            'min_significant_samples': int,
        }
    """
    # Seuil minimal d'échantillons OOS (10 trades minimum — cf. MIN_SIGNIFICANT_TRADES)
    MIN_SAMPLES = 10
    if n_oos_samples < MIN_SAMPLES:
        return {
            'ok': False,
            'auc': auc_oos,
            'level': 'block',
            'reason': (
                f"Pas assez d'échantillons OOS ({n_oos_samples} < {MIN_SAMPLES}) "
                f"— modèle non validable statistiquement"
            ),
            'min_significant_samples': MIN_SAMPLES,
        }

    # AUC hors range
    if auc_oos < 0 or auc_oos > 1:
        return {
            'ok': False,
            'auc': auc_oos,
            'level': 'block',
            'reason': f"AUC OOS invalide ({auc_oos}) — vérifier le calcul",
            'min_significant_samples': MIN_SAMPLES,
        }

    # AUC < random → pire que aléatoire → bloquer
    if auc_oos < AUC_RANDOM:
        logger.warning(
            f"[ML Overfitting Gate] {strategy_name} AUC OOS = {auc_oos:.3f} "
            f"< {AUC_RANDOM} (aléatoire) — MODÈLE BLOQUÉ. Le modèle a appris "
            f"du bruit ou a un bug. n_trials={n_trials_optimization}."
        )
        return {
            'ok': False,
            'auc': auc_oos,
            'level': 'block',
            'reason': (
                f"AUC OOS {auc_oos:.3f} < {AUC_RANDOM} (aléatoire) — "
                f"modèle a appris du bruit, ne pas promouvoir"
            ),
            'min_significant_samples': MIN_SAMPLES,
        }

    # AUC ≈ random → pas d'edge mais pas pire → bloquer (préventif)
    if auc_oos < AUC_WEAK:
        logger.warning(
            f"[ML Overfitting Gate] {strategy_name} AUC OOS = {auc_oos:.3f} "
            f"< {AUC_WEAK} (edge faible) — MODÈLE BLOQUÉ. "
            f"Le modèle n'a pas d'edge significatif. "
            f"n_trials={n_trials_optimization} → biais de sélection probable."
        )
        return {
            'ok': False,
            'auc': auc_oos,
            'level': 'warn',
            'reason': (
                f"AUC OOS {auc_oos:.3f} < {AUC_WEAK} — edge trop faible, "
                f"biais de sélection probable sur {n_trials_optimization} essais"
            ),
            'min_significant_samples': MIN_SAMPLES,
        }

    # AUC ≥ 0.55 < 0.60 → edge faible mais acceptable → warning, ok=True
    if auc_oos < AUC_GOOD:
        logger.info(
            f"[ML Overfitting Gate] {strategy_name} AUC OOS = {auc_oos:.3f} "
            f"({AUC_WEAK} ≤ AUC < {AUC_GOOD}) — edge acceptable, "
            f"surveiller la dégradation."
        )
        return {
            'ok': True,
            'auc': auc_oos,
            'level': 'good',
            'reason': f"AUC OOS {auc_oos:.3f} — edge acceptable (faible)",
            'min_significant_samples': MIN_SAMPLES,
        }

    # AUC ≥ 0.60 < 0.70 → edge bon
    if auc_oos < AUC_STRONG:
        logger.info(
            f"[ML Overfitting Gate] {strategy_name} AUC OOS = {auc_oos:.3f} "
            f"({AUC_GOOD} ≤ AUC < {AUC_STRONG}) — edge bon."
        )
        return {
            'ok': True,
            'auc': auc_oos,
            'level': 'good',
            'reason': f"AUC OOS {auc_oos:.3f} — edge bon",
            'min_significant_samples': MIN_SAMPLES,
        }

    # AUC ≥ 0.70 → edge fort
    logger.info(
        f"[ML Overfitting Gate] {strategy_name} AUC OOS = {auc_oos:.3f} "
        f"≥ {AUC_STRONG} — edge fort (à surveiller pour overfitting)."
    )
    return {
        'ok': True,
        'auc': auc_oos,
        'level': 'strong',
        'reason': f"AUC OOS {auc_oos:.3f} — edge fort (vérifier pas d'overfitting)",
        'min_significant_samples': MIN_SAMPLES,
    }


def compute_auc_oos(y_true: list, y_proba: list) -> float:
    """Calcule l'AUC binaire (Area Under ROC Curve).

    Implementation simple (Manhattan) — pour de gros datasets, utiliser
    sklearn.metrics.roc_auc_score directement.

    Parameters
    ----------
    y_true : list of int (0/1)
        Vraies étiquettes.
    y_proba : list of float
        Probabilités prédites (entre 0 et 1).

    Returns
    -------
    float
        AUC entre 0 et 1.
    """
    n_pos = sum(1 for y in y_true if y == 1)
    n_neg = sum(1 for y in y_true if y == 0)
    if n_pos == 0 or n_neg == 0:
        return 0.5  # dégénéré
    pairs = sorted(zip(y_proba, y_true), key=lambda x: -x[0])
    correct = 0
    for i, (p, y) in enumerate(pairs):
        if y == 1:
            # Compter combien de négatifs ont une proba plus basse
            for j, (p2, y2) in enumerate(pairs):
                if y2 == 0 and (p2 < p or (p2 == p and j > i)):
                    correct += 1
    return correct / (n_pos * n_neg)
