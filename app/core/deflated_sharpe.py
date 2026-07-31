"""S3-02 : Deflated Sharpe Ratio (López de Prado 2014).

Corrige le biais de multiple testing dans l'optimisation de stratégies :
quand on teste N paramétrages, le meilleur Sharpe observé est biaisé vers le
haut (sélection sur le bruit). Le Deflated Sharpe estime le Sharpe attendu
sous l'hypothèse nulle (pas d'edge), puis l'utilise pour ajuster le Sharpe
observé du meilleur paramétrage.

Formule :
    SR_deflated = SR_observed × sqrt(1 - max(0, 1 - 1/λ²) × (1 - γ²))
    où :
    - SR_observed = Sharpe annualisé observé
    - λ² = (SR_observed - SR_null)² / variance_sharpe
    - γ = constante d'Euler-Mascheroni (≈ 0.5772)
    - SR_null = √(2 × ln(N)) × σ_sharpe (espérance du max de N v.a. N(0, σ²))

Référence :
    López de Prado, L. (2014). "The Deflated Sharpe Ratio: Correcting for
    Selection Bias, Backtest Overfitting, and Non-Normality."
    Journal of Portfolio Management 40(5): 94-107.

Le Deflated Sharpe est utilisé comme **gate de naissance** d'un bot :
    - SR_deflated ≥ 0.5  → bot peut être promu
    - SR_deflated < 0.5  → warning (biais de sélection probable)
    - SR_deflated < 0    → refus (pas d'edge réel)
"""
import math
from typing import Optional

# Constante d'Euler-Mascheroni (approximation)
_EULER_MASCHERONI = 0.5772156649015329


def expected_max_sharpe_under_null(n_trials: int,
                                    sharpe_std: float = 1.0) -> float:
    """Espérance du maximum de N Sharpes sous H0 (pas d'edge).

    Sous H0, les Sharpes sont ~ N(0, sharpe_std²). L'espérance du max de
    N v.a. gaussiennes i.i.d. est approximativement :
        E[max_N] ≈ sharpe_std × (sqrt(2 × ln(N)) + γ / sqrt(2 × ln(N)))

    Pour N=1, on retourne 0 (pas de biais de sélection).
    """
    if n_trials <= 1:
        return 0.0
    log_n = math.log(n_trials)
    if log_n <= 0:
        return 0.0
    base = math.sqrt(2.0 * log_n)
    correction = _EULER_MASCHERONI / (2.0 * base) if base > 0 else 0.0
    return sharpe_std * (base + correction)


def deflated_sharpe(sharpe_observed: float,
                     n_trials: int,
                     sharpe_std: float = 1.0,
                     sample_size: Optional[int] = None) -> float:
    """Calcule le Deflated Sharpe Ratio.

    Parameters
    ----------
    sharpe_observed : float
        Sharpe annualisé observé (meilleur parmi n_trials essais).
    n_trials : int
        Nombre de paramétrages testés (1 = pas de multiple testing).
    sharpe_std : float
        Écart-type du Sharpe (souvent 1.0 pour Sharpe annualisé).
    sample_size : Optional[int]
        Taille d'échantillon (nb de trades ou de périodes) — utilisée
        pour estimer la variance empirique si sharpe_std non fourni.
        Si None, utilise sharpe_std tel quel.

    Returns
    -------
    float
        Deflated Sharpe Ratio. < sharpe_observed si n_trials > 1.
        = sharpe_observed si n_trials == 1 (pas de correction).
    """
    if n_trials <= 1:
        return sharpe_observed

    # Espérance du max sous H0
    sr_null = expected_max_sharpe_under_null(n_trials, sharpe_std)

    # Si le Sharpe observé est sous l'espérance du max sous H0, le signal
    # est très probablement du bruit → Deflated Sharpe négatif.
    if sharpe_observed <= sr_null:
        # Pénalité croissante avec l'écart
        deficit = sr_null - sharpe_observed
        return -deficit

    # Variance du Sharpe estimée (approximation : 1/N pour N périodes)
    if sample_size and sample_size > 1:
        var_sharpe = sharpe_std ** 2 / sample_size
    else:
        var_sharpe = sharpe_std ** 2

    # λ² = (SR_observed - SR_null)² / var_sharpe
    lambda_sq = (sharpe_observed - sr_null) ** 2 / max(var_sharpe, 1e-10)

    # Facteur de déflation
    # SR_deflated = SR_observed × sqrt(1 - max(0, 1 - 1/λ²) × (1 - γ²))
    gamma_factor = 1.0 - _EULER_MASCHERONI ** 2  # ≈ 0.667
    deflation_factor = max(0.0, 1.0 - 1.0 / max(lambda_sq, 1e-10))
    multiplier = math.sqrt(max(0.0, 1.0 - deflation_factor * gamma_factor))

    return sharpe_observed * multiplier


def is_deflated_sharpe_significant(sharpe_observed: float,
                                   n_trials: int,
                                   min_deflated_sharpe: float = 0.5,
                                   sharpe_std: float = 1.0) -> tuple:
    """Gate de naissance d'un bot basé sur le Deflated Sharpe.

    Parameters
    ----------
    sharpe_observed : float
        Sharpe annualisé observé.
    n_trials : int
        Nombre d'essais d'optimisation.
    min_deflated_sharpe : float
        Seuil de promotion (défaut 0.5 — conservative).
    sharpe_std : float
        Écart-type du Sharpe (défaut 1.0).

    Returns
    -------
    (ok: bool, deflated: float, reason: str)
        ok=True si le bot peut être promu.
    """
    ds = deflated_sharpe(sharpe_observed, n_trials, sharpe_std)
    if ds < 0:
        return False, ds, "Deflated Sharpe négatif — edge probablement nul (biais de sélection)"
    if ds < min_deflated_sharpe:
        return False, ds, (
            f"Deflated Sharpe {ds:.2f} < seuil {min_deflated_sharpe:.2f} "
            f"(biais de sélection probable sur {n_trials} essais)"
        )
    return True, ds, f"Deflated Sharpe {ds:.2f} ≥ seuil — edge valide"
