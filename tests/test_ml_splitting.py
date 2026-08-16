"""Embargo entraînement/validation et seuil d'amplitude appris sur le train.

Les labels regardent devant : ``y[i]`` dépend de ``close[i + h]``. Sans
intervalle, les dernières lignes d'entraînement portent une information tirée
de la validation — et c'est l'AUC de validation qui arbitre la promotion des
modèles.
"""
import numpy as np
import pytest

from app.ml.backend.features import multi_horizon_labels, single_horizon_labels
from app.ml.splitting import (
    MIN_TRAIN,
    MIN_VALID,
    chrono_split,
    label_embargo,
    purged_time_series_splits,
)


class TestEmbargo:
    def test_horizon_maximal(self):
        """Une seule tête qui regarde loin suffit à contaminer autant de lignes."""
        assert label_embargo([1, 3, 6, 12]) == 12
        assert label_embargo([1]) == 1
        assert label_embargo(None) == 1
        assert label_embargo([]) == 1
        assert label_embargo(["3", 7]) == 7

    def test_le_split_reproduit_la_formule_historique(self):
        """À embargo nul, le découpage doit être celui d'avant, indice pour indice."""
        for n in (1000, 5000, 12345):
            plan = chrono_split(n, [1])
            attendu = min(max(int(n * 0.8), MIN_TRAIN), n - MIN_VALID)
            assert plan.split == attendu
            assert plan.train == attendu - 1        # embargo de 1 pour l'horizon 1

    def test_lembargo_ne_mord_que_sur_lentrainement(self):
        """La validation garde ses lignes : les AUC restent comparables."""
        n = 5000
        court = chrono_split(n, [1])
        long_ = chrono_split(n, [1, 3, 6, 12])
        assert long_.split == court.split
        assert n - long_.split == n - court.split
        assert long_.train == long_.split - 12

    def test_refus_quand_lembargo_ne_laisse_pas_assez(self):
        assert chrono_split(0, [1]) is None
        assert chrono_split(160, [1, 3, 6, 200]) is None
        assert chrono_split(-5, [1]) is None


class TestSeuilAmplitude:
    def _serie(self, n=2000, seed=4):
        rng = np.random.default_rng(seed)
        # Volatilité qui MONTE : un seuil calibré sur tout le jeu diffère
        # nettement d'un seuil calibré sur le seul début.
        vol = np.linspace(0.002, 0.02, n)
        return 100 * np.exp(np.cumsum(rng.normal(0, 1, n) * vol))

    def test_thr_upto_change_reellement_le_seuil(self):
        close = self._serie()
        _, _, _, thr_tout, _ = multi_horizon_labels(close, [1, 3, 6, 12], 0.30)
        _, _, _, thr_train, _ = multi_horizon_labels(close, [1, 3, 6, 12], 0.30,
                                                     thr_upto=1500)
        assert thr_train < thr_tout, \
            "sur une volatilité croissante, le seuil du début doit être plus bas"

    def test_le_defaut_reste_le_comportement_historique(self):
        close = self._serie()
        a = multi_horizon_labels(close, [1, 3, 6, 12], 0.30)
        b = multi_horizon_labels(close, [1, 3, 6, 12], 0.30, thr_upto=None)
        assert a[3] == b[3]
        assert np.array_equal(a[0], b[0])

    def test_single_horizon_aussi(self):
        close = self._serie()
        _, _, _, thr_tout = single_horizon_labels(close, 0.30)
        _, _, _, thr_train = single_horizon_labels(close, 0.30, thr_upto=1500)
        assert thr_train < thr_tout

    def test_le_seuil_borne_ne_regarde_pas_au_dela(self):
        """Modifier la fin de la série ne doit pas bouger un seuil borné au début."""
        close = self._serie()
        modifie = close.copy()
        modifie[1600:] *= np.linspace(1.0, 3.0, len(close) - 1600)
        _, _, _, a = single_horizon_labels(close, 0.30, thr_upto=1500)
        _, _, _, b = single_horizon_labels(modifie, 0.30, thr_upto=1500)
        assert a == pytest.approx(b)


class TestFoldsPurges:
    def test_embargo_retire_la_fin_de_chaque_entrainement(self):
        folds = purged_time_series_splits(1000, 4, embargo=10)
        assert folds
        for train_idx, test_idx in folds:
            assert train_idx[-1] + 1 + 10 == test_idx[0], \
                "il doit rester exactement `embargo` lignes entre train et test"

    def test_sans_embargo_geometrie_historique(self):
        folds = purged_time_series_splits(1000, 4, embargo=0)
        for train_idx, test_idx in folds:
            assert train_idx[-1] + 1 == test_idx[0]
        assert len(folds) == 4

    def test_folds_impraticables_ecartes_pas_tronques(self):
        """Un entraînement vidé par l'embargo est écarté, pas rendu vide."""
        folds = purged_time_series_splits(100, 4, embargo=25)
        assert all(len(tr) > 0 for tr, _ in folds)
        assert purged_time_series_splits(10, 1) == []
        assert purged_time_series_splits(3, 5) == []
