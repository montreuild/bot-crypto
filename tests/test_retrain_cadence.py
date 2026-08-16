"""Cadence de réentraînement — y compris quand l'entraînement échoue.

Le test de besoin était ``(tf non entraîné) OU (appels - dernier_succès >=
retrain_every)``. Un entraînement qui ÉCHOUE ne met pas à jour
``dernier_succès``, et tant qu'aucun modèle n'existe la première clause reste
vraie : la stratégie relançait un entraînement complet à CHAQUE appel de
``score()``. Mesuré sur 8 000 barres : 368 échecs ré-essayés pour 7 succès à
``retrain_every=800``, et 1 168 pour 3 succès à 1 600 — le paramètre ne
gouvernait pas la cadence.
"""
import pytest

from app.ml.retrain import RETRY_BACKOFF_DIV, due_for_training, retry_backoff


class TestModeleEntraine:
    def test_cadence_nominale(self):
        """Cas normal : rien ne change par rapport à avant."""
        assert not due_for_training(is_trained=True, calls=1500, last_success=800,
                                    last_attempt=800, cadence=800)
        assert due_for_training(is_trained=True, calls=1600, last_success=800,
                                last_attempt=800, cadence=800)

    def test_un_succes_remet_le_compteur_a_zero(self):
        assert not due_for_training(is_trained=True, calls=900, last_success=890,
                                    last_attempt=100, cadence=800)


class TestModeleAbsent:
    def test_premiere_tentative_immediate(self):
        """Il faut bien un premier modèle : pas de délai avant le premier essai."""
        assert due_for_training(is_trained=False, calls=1, last_success=0,
                                last_attempt=0, cadence=800)

    def test_un_echec_impose_un_delai(self):
        """C'est tout le correctif : plus de nouvel essai à la barre suivante."""
        assert not due_for_training(is_trained=False, calls=301, last_success=0,
                                    last_attempt=300, cadence=800)
        assert not due_for_training(is_trained=False, calls=379, last_success=0,
                                    last_attempt=300, cadence=800)
        assert due_for_training(is_trained=False, calls=380, last_success=0,
                                last_attempt=300, cadence=800)

    def test_le_delai_reste_court_devant_la_cadence(self):
        """80 barres au lieu d'une : dix fois moins d'essais, sans retarder
        le premier succès de plus de 80 barres."""
        assert retry_backoff(800) == 80
        assert retry_backoff(2000) == 200
        assert retry_backoff(5) == 1                 # jamais zéro
        assert retry_backoff(0) == 1

    def test_nombre_dessais_borne_sur_une_serie(self):
        """Simulation : combien d'essais sur 8 000 appels si tout échoue ?"""
        essais, last = 0, 0
        for cnt in range(1, 8001):
            if due_for_training(is_trained=False, calls=cnt, last_success=0,
                                last_attempt=last, cadence=800):
                essais += 1
                last = cnt
        assert essais == pytest.approx(8000 / retry_backoff(800), rel=0.05)
        assert essais < 8000 / RETRY_BACKOFF_DIV * 1.05


def test_cadence_nulle_ou_negative_ne_divise_pas_par_zero():
    assert due_for_training(is_trained=True, calls=10, last_success=0,
                            last_attempt=0, cadence=0)
    assert due_for_training(is_trained=True, calls=10, last_success=0,
                            last_attempt=0, cadence=-5)
