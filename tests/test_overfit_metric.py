"""Le ratio de surapprentissage et la pénalité qu'il déclenche.

Deux défauts mesurés sur la campagne de recalibration, corrigés ici :

1. ``overfitting_ratio`` rendait **0.0** — la meilleure valeur de l'échelle —
   quand le score IS était négatif, c'est-à-dire pour une configuration qui ne
   marche nulle part. `multi_tf_sr` ETH 4 h (+371,7 de PnL OOS) et
   `fear_momentum` BTC 1 h (−168,4) recevaient la même note.
2. ``_penalized_score`` multipliait le score par ``2.5 / overfit`` (< 1) sans
   regarder son signe. Sur un score NÉGATIF, ça le rapproche de zéro : une
   récompense déguisée en pénalité.

Les chiffres des tests viennent de `scripts/_recal_reprise.json`, pas d'une
invention.
"""
import math

import pytest

from app.engine.opt_scoring import overfitting_ratio


class _Moteur:
    """Porte `_penalized_score` sans construire tout l'optimiseur (qui exige
    des DataFrames, une config et une stratégie résolue)."""

    from app.engine.optimizer_search import OptimizerSearchEngine as _O
    _penalized_score = _O._penalized_score


def penalise(oos: float, ovf: float) -> float:
    return _Moteur()._penalized_score({"oos_score": oos, "overfit": ovf})


# ── overfitting_ratio ───────────────────────────────────────────────────────

def test_un_ratio_normal_est_calcule():
    assert overfitting_ratio(0.8, 0.4) == 2.0


def test_le_ratio_sature_a_dix():
    assert overfitting_ratio(50.0, 0.001) == 10.0


@pytest.mark.parametrize("is_score,oos_score", [
    (-0.34, 0.5),     # échoue en apprentissage
    (0.0, 0.5),       # idem, cas limite
    (0.8, -0.10),     # échoue hors échantillon
    (0.8, 0.0),       # idem, cas limite
])
def test_les_cas_degeneres_rendent_nan(is_score, oos_score):
    """Un ratio n'a de sens que si les deux scores sont positifs. Rendre un
    nombre les ferait entrer dans un classement où ils n'ont rien à faire."""
    assert math.isnan(overfitting_ratio(is_score, oos_score))


def test_une_config_qui_echoue_partout_n_est_plus_la_mieux_notee():
    """LE défaut corrigé : `fear_momentum` BTC 1 h (PnL OOS −168,4) recevait
    0.0, la meilleure valeur, à égalité avec `multi_tf_sr` ETH 4 h (+371,7)."""
    perdante = overfitting_ratio(-0.20, -0.34)     # échoue IS ET OOS
    saine = overfitting_ratio(0.90, 0.82)          # multi_tf_sr ETH 4h
    assert math.isnan(perdante)
    assert saine == pytest.approx(1.1, abs=0.05)


def test_un_run_degenere_reste_nan():
    assert math.isnan(overfitting_ratio(-999.0, 0.5))
    assert math.isnan(overfitting_ratio(0.5, -999.0))


# ── _penalized_score ────────────────────────────────────────────────────────

def test_un_score_positif_surappris_est_bien_penalise():
    assert penalise(0.80, 10.0) == pytest.approx(0.80 * 0.25)


def test_un_score_positif_sain_n_est_pas_touche():
    assert penalise(0.80, 1.5) == 0.80


def test_un_score_negatif_n_est_JAMAIS_ameliore():
    """`fear_momentum` BTC 4 h : brut −0,433, « pénalisé » −0,108 — quatre fois
    meilleur au classement pour cause de surapprentissage."""
    assert penalise(-0.433, 10.0) == -0.433


def test_le_classement_des_perdantes_suit_le_score_brut():
    """Avant : la config surapprise (−0,433) passait DEVANT la saine (−0,099)."""
    surapprise = penalise(-0.433, 10.0)      # fear_momentum BTC 4h
    saine = penalise(-0.099, float("nan"))   # supertrend_macd ETH 1h
    assert surapprise < saine, (
        "une configuration quatre fois pire en brut ne doit pas mieux se classer"
    )


def test_un_overfit_indefini_laisse_le_score_intact():
    assert penalise(0.60, float("nan")) == 0.60
