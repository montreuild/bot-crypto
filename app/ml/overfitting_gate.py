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


def auc_hanley_ci_low(auc: float, n: int | None = None, z: float = 1.96, *,
                      n_pos: int | None = None,
                      n_neg: int | None = None) -> float:
    """Borne basse 95 % de l'AUC (Hanley–McNeil).

    La variance d'une AUC dépend des effectifs de CHAQUE classe, pas du nombre
    de lignes. Fournir ``n_pos``/``n_neg`` donne la vraie borne ; ne fournir que
    ``n`` fait retomber sur l'hypothèse d'équilibre ``n1 = n0 = n/2``.

    ML-02 — cette hypothèse n'est pas neutre, elle est **permissive** : elle
    surestime ``n1·n0``, donc sous-estime la variance, donc remonte la borne
    basse. Sur des labels construits par seuil d'amplitude — le cas normal ici
    — elle laisse passer des modèles dont la borne réelle est sous 0,50.
    Mesuré à 10 % de positifs, n=200, AUC 0,58 : 0,5010 annoncé contre 0,4429
    réel. Le repli reste offert pour les scorers qui ne publient pas les
    effectifs, mais il est signalé par l'appelant.

    Sans scores bruts on ne peut pas faire DeLong ; cette approximation suffit
    à refuser une AUC statistiquement compatible avec le hasard.
    """
    if n_pos is not None and n_neg is not None:
        n1, n0 = float(max(int(n_pos), 0)), float(max(int(n_neg), 0))
        if n1 < 1.0 or n0 < 1.0:
            return float(auc)
    else:
        if n is None or n <= 1:
            return float(auc)
        n1 = n0 = max(n / 2.0, 1.0)
    if auc <= 0.0 or auc >= 1.0:
        return float(auc)
    q1 = auc / (2.0 - auc)
    q2 = 2.0 * auc * auc / (1.0 + auc)
    var = (
        auc * (1.0 - auc)
        + (n1 - 1.0) * (q1 - auc * auc)
        + (n0 - 1.0) * (q2 - auc * auc)
    ) / (n1 * n0)
    se = var ** 0.5 if var > 0.0 else 0.0
    return max(0.0, float(auc) - z * se)


#: Plancher par CLASSE. Une AUC calculée contre 3 positifs n'est pas une
#: mesure, quel que soit le nombre total de lignes : c'est la classe rare qui
#: fixe la précision (ML-02).
MIN_PER_CLASS = 10


def validate_model_quality(auc_oos: float,
                             strategy_name: str,
                             n_oos_samples: int,
                             n_trials_optimization: int = 1,
                             *,
                             n_pos: int | None = None,
                             n_neg: int | None = None) -> dict:
    """Gate de validation post-entraînement d'un modèle ML.

    Parameters
    ----------
    auc_oos : float
        AUC mesurée sur l'échantillon Out-of-Sample (≥ 0, ≤ 1).
    strategy_name : str
        Nom de la stratégie (pour log/notification).
    n_oos_samples : int
        Nombre d'échantillons OOS RÉELLEMENT scorés (pour juger la
        significativité) — pas la taille de holdout demandée en configuration.
    n_trials_optimization : int
        Nombre d'essais d'optimisation (pour biais de sélection — cf. Deflated Sharpe).
    n_pos, n_neg : int, optionnels
        Effectifs par classe. Fournis, ils donnent la vraie borne de confiance
        et activent le plancher ``MIN_PER_CLASS``. Absents, on retombe sur
        l'hypothèse d'équilibre — plus permissive, et signalée dans le résultat
        par ``classes_estimees=True`` (ML-02).

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
    classes_estimees = n_pos is None or n_neg is None
    ci_low = auc_hanley_ci_low(float(auc_oos), int(n_oos_samples),
                               n_pos=n_pos, n_neg=n_neg)
    _base = {'auc': auc_oos, 'auc_ci_low': ci_low,
             'n_pos': n_pos, 'n_neg': n_neg,
             'classes_estimees': classes_estimees,
             'min_significant_samples': MIN_SAMPLES}
    if classes_estimees:
        logger.debug(
            f"[ML Overfitting Gate] {strategy_name} : effectifs par classe "
            f"absents — borne de confiance calculée sous hypothèse d'équilibre "
            f"(plus permissive, cf. ML-02)"
        )
    # ML-02 : c'est la classe RARE qui fixe la précision. 200 lignes dont 3
    # positifs ne mesurent rien, et le total franchirait pourtant MIN_SAMPLES.
    if (n_pos is not None and n_neg is not None
            and min(int(n_pos), int(n_neg)) < MIN_PER_CLASS):
        logger.warning(
            f"[ML Overfitting Gate] {strategy_name} : {n_pos} positifs / "
            f"{n_neg} négatifs — sous {MIN_PER_CLASS} par classe, "
            f"l'AUC {auc_oos:.3f} n'est pas mesurable. MODÈLE BLOQUÉ."
        )
        return {
            **_base,
            'ok': False,
            'level': 'block',
            'reason': (
                f"Classe trop rare ({n_pos} positifs / {n_neg} négatifs, "
                f"minimum {MIN_PER_CLASS} par classe) — AUC {auc_oos:.3f} "
                f"non mesurable"
            ),
        }
    if n_oos_samples < MIN_SAMPLES:
        return {
            **_base,
            'ok': False,
            'level': 'block',
            'reason': (
                f"Pas assez d'échantillons OOS ({n_oos_samples} < {MIN_SAMPLES}) "
                f"— modèle non validable statistiquement"
            ),
        }

    # AUC hors range
    if auc_oos < 0 or auc_oos > 1:
        return {
            **_base,
            'ok': False,
            'level': 'block',
            'reason': f"AUC OOS invalide ({auc_oos}) — vérifier le calcul",
        }

    # AUC < random → pire que aléatoire → bloquer
    if auc_oos < AUC_RANDOM:
        logger.warning(
            f"[ML Overfitting Gate] {strategy_name} AUC OOS = {auc_oos:.3f} "
            f"< {AUC_RANDOM} (aléatoire) — MODÈLE BLOQUÉ. Le modèle a appris "
            f"du bruit ou a un bug. n_trials={n_trials_optimization}."
        )
        return {
            **_base,
            'ok': False,
            'level': 'block',
            'reason': (
                f"AUC OOS {auc_oos:.3f} < {AUC_RANDOM} (aléatoire) — "
                f"modèle a appris du bruit, ne pas promouvoir"
            ),
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
            **_base,
            'ok': False,
            'level': 'warn',
            'reason': (
                f"AUC OOS {auc_oos:.3f} < {AUC_WEAK} — edge trop faible, "
                f"biais de sélection probable sur {n_trials_optimization} essais"
            ),
        }

    # ML-01 : une AUC ponctuelle ≥ 0,55 dont l'IC 95 % recouvre 0,50 n'est
    # pas distinguable du hasard — bloquer (borne basse < aléatoire).
    if ci_low < AUC_RANDOM:
        logger.warning(
            f"[ML Overfitting Gate] {strategy_name} AUC OOS = {auc_oos:.3f} "
            f"mais IC95 bas = {ci_low:.3f} < {AUC_RANDOM} "
            f"(n={n_oos_samples}) — MODÈLE BLOQUÉ."
        )
        return {
            **_base,
            'ok': False,
            'level': 'block',
            'reason': (
                f"AUC OOS {auc_oos:.3f} (IC95 bas {ci_low:.3f}) "
                f"indistinguable du hasard — n={n_oos_samples}"
            ),
        }

    # AUC ≥ 0.55 < 0.60 → edge faible mais acceptable → warning, ok=True
    if auc_oos < AUC_GOOD:
        logger.info(
            f"[ML Overfitting Gate] {strategy_name} AUC OOS = {auc_oos:.3f} "
            f"({AUC_WEAK} ≤ AUC < {AUC_GOOD}) — edge acceptable, "
            f"surveiller la dégradation."
        )
        return {
            **_base,
            'ok': True,
            'level': 'good',
            'reason': f"AUC OOS {auc_oos:.3f} — edge acceptable (faible)",
        }

    # AUC ≥ 0.60 < 0.70 → edge bon
    if auc_oos < AUC_STRONG:
        logger.info(
            f"[ML Overfitting Gate] {strategy_name} AUC OOS = {auc_oos:.3f} "
            f"({AUC_GOOD} ≤ AUC < {AUC_STRONG}) — edge bon."
        )
        return {
            **_base,
            'ok': True,
            'level': 'good',
            'reason': f"AUC OOS {auc_oos:.3f} — edge bon",
        }

    # AUC ≥ 0.70 → edge fort
    logger.info(
        f"[ML Overfitting Gate] {strategy_name} AUC OOS = {auc_oos:.3f} "
        f"≥ {AUC_STRONG} — edge fort (à surveiller pour overfitting)."
    )
    return {
        **_base,
        'ok': True,
        'level': 'strong',
        'reason': f"AUC OOS {auc_oos:.3f} — edge fort (vérifier pas d'overfitting)",
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
