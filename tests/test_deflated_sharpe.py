"""S4-04 — Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

Vérifie les propriétés statistiques attendues plutôt que des valeurs de
référence externes (aucune implémentation tierce disponible sans scipy) :
bornes [0,1], monotonie en n_trials/sharpe, cas dégénérés, et équivalence
au Probabilistic Sharpe Ratio quand n_trials=1 (pas de correction de
sélection, sr0=0 par construction).
"""
import math
from statistics import NormalDist

from app.engine.opt_scoring import _expected_max_sharpe, deflated_sharpe_ratio

_NORMAL = NormalDist(mu=0.0, sigma=1.0)


def test_degenerate_sample_returns_zero():
    assert deflated_sharpe_ratio(sharpe=2.0, n_observations=1) == 0.0
    assert deflated_sharpe_ratio(sharpe=2.0, n_observations=0) == 0.0
    assert deflated_sharpe_ratio(sharpe=None, n_observations=50) == 0.0


def test_bounded_between_zero_and_one():
    for sr in (-5.0, -1.0, 0.0, 0.5, 1.0, 3.0, 10.0):
        for n_trials in (1, 5, 50, 500):
            dsr = deflated_sharpe_ratio(sharpe=sr, n_observations=100, n_trials=n_trials)
            assert 0.0 <= dsr <= 1.0


def test_single_trial_equals_probabilistic_sharpe_ratio():
    """n_trials=1 → aucune correction de sélection (sr0=0 par construction
    de _expected_max_sharpe) : DSR = Φ(SR·√(T-1)), le Probabilistic Sharpe
    Ratio standard (test que le Sharpe observé est > 0)."""
    sr, t = 1.5, 60
    dsr = deflated_sharpe_ratio(sharpe=sr, n_observations=t, n_trials=1)
    expected = round(_NORMAL.cdf(sr * math.sqrt(t - 1)), 6)
    assert dsr == expected


def test_zero_sharpe_with_single_trial_is_fifty_percent():
    """Sharpe observé exactement nul, sans correction de sélection : la
    probabilité qu'il soit "réellement positif" doit être 50% (frontière)."""
    dsr = deflated_sharpe_ratio(sharpe=0.0, n_observations=100, n_trials=1)
    assert dsr == 0.5


def test_more_trials_deflates_the_ratio():
    """À Sharpe et échantillon fixes, plus l'optimiseur a testé de
    combinaisons, plus la barre de significativité monte (biais de
    sélection) → le DSR doit DIMINUER (ou rester stable) avec n_trials."""
    sr, t = 2.0, 100
    dsr_1   = deflated_sharpe_ratio(sharpe=sr, n_observations=t, n_trials=1)
    dsr_10  = deflated_sharpe_ratio(sharpe=sr, n_observations=t, n_trials=10)
    dsr_100 = deflated_sharpe_ratio(sharpe=sr, n_observations=t, n_trials=100)
    assert dsr_1 >= dsr_10 >= dsr_100


def test_higher_observed_sharpe_increases_dsr():
    """À nombre d'essais et échantillon fixes, un Sharpe observé plus élevé
    ne peut jamais réduire le DSR."""
    prev = -1.0
    for sr in (-1.0, 0.0, 0.5, 1.0, 2.0, 4.0):
        dsr = deflated_sharpe_ratio(sharpe=sr, n_observations=100, n_trials=20)
        assert dsr >= prev
        prev = dsr


def test_more_observations_increases_confidence_above_deflation_threshold():
    """Plus l'échantillon de rendements est grand, plus la confiance
    augmente — À CONDITION que le Sharpe observé dépasse déjà le seuil de
    déflation (sr0, ~1.19 ici pour 5 essais) : sous ce seuil, plus de
    données ne fait que confirmer plus fermement que le Sharpe n'est PAS
    distinguable du hasard (comportement statistique correct, pas un bug)."""
    sr = 3.0  # nettement au-dessus de _expected_max_sharpe(5, 1.0) ≈ 1.19
    dsr_small = deflated_sharpe_ratio(sharpe=sr, n_observations=10, n_trials=5)
    dsr_large = deflated_sharpe_ratio(sharpe=sr, n_observations=500, n_trials=5)
    assert dsr_large >= dsr_small


# ── _expected_max_sharpe ────────────────────────────────────────────────────

def test_expected_max_sharpe_zero_for_single_trial():
    assert _expected_max_sharpe(1, sharpe_std=1.0) == 0.0
    assert _expected_max_sharpe(0, sharpe_std=1.0) == 0.0


def test_expected_max_sharpe_zero_for_zero_std():
    assert _expected_max_sharpe(50, sharpe_std=0.0) == 0.0


def test_expected_max_sharpe_increases_with_trials():
    e10 = _expected_max_sharpe(10, sharpe_std=1.0)
    e100 = _expected_max_sharpe(100, sharpe_std=1.0)
    e1000 = _expected_max_sharpe(1000, sharpe_std=1.0)
    assert 0.0 < e10 < e100 < e1000


def test_expected_max_sharpe_scales_with_std():
    base = _expected_max_sharpe(50, sharpe_std=1.0)
    doubled = _expected_max_sharpe(50, sharpe_std=2.0)
    assert doubled == base * 2
