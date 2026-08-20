"""Statistiques SMC : les témoins doivent tuer les fausses découvertes.

Le test qui compte est ``test_aucune_decouverte_sur_du_bruit`` : sur une marche
aléatoire il n'y a rien à trouver, donc toute découverte est un défaut de la
méthode. Le reste vérifie les pièces qui le rendent vrai.

Historique utile : la première version utilisait un intervalle de confiance
paramétrique et sortait 125 « découvertes » significatives après correction de
Bonferroni sur du bruit pur. La cause était le CHEVAUCHEMENT des fenêtres de
rendement — à l'horizon 12 avec un motif tous les trois barres, la même hausse
est comptée quatre fois, et l'écart-type naïf suppose l'indépendance. D'où le
test de permutation, qui met le chevauchement dans les deux bras.
"""
import numpy as np
import polars as pl
import pytest

from app.engine.smc_patterns import composites as C
from app.engine.smc_patterns import stats as S
from app.engine.smc_patterns.journal import journal_multi_tf


def _serie(n=2000, seed=3, pas_ms=3_600_000, derive=0.0):
    rng = np.random.default_rng(seed)
    close = 30_000 * np.exp(np.cumsum(rng.normal(derive, 0.006, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    t0 = np.datetime64("2024-01-01T00:00:00", "ms")
    return pl.DataFrame({
        "time": t0 + np.arange(n) * np.timedelta64(pas_ms, "ms"),
        "open": np.concatenate([[close[0]], close[:-1]]),
        "high": high, "low": low, "close": close,
        "volume": np.abs(rng.normal(500, 120, n)),
    })


@pytest.fixture(scope="module")
def contexte():
    frames = {"1h": _serie(2000, 3), "4h": _serie(500, 44, 4 * 3_600_000)}
    return frames, journal_multi_tf(frames, "TEST/USDC")


class TestGardeFouPrincipal:
    def test_aucune_decouverte_sur_du_bruit(self, contexte):
        """Sur une marche aléatoire, toute découverte est un défaut de méthode."""
        frames, journal = contexte
        mesures = S.mouvement_suivant(journal, frames, horizons=(6, 12))
        budget = S.budget_hypotheses(mesures)
        assert mesures.height > 20, "sans mesures, ce test ne prouve rien"
        assert S.survivants(mesures, budget).height == 0

    def test_aucun_compose_ne_survit_sur_du_bruit(self, contexte):
        frames, journal = contexte
        fouille = C.mine(journal, fenetre_s=6 * 3600, longueurs=(2, 3))
        mesures = C.mouvement_apres_composes(
            fouille, journal, frames, 6 * 3600, "1h", horizons=(6, 12))
        assert mesures.height > 50, "sans composés mesurés, ce test ne prouve rien"
        assert C.survivants_composes(mesures, fouille)["survivants"].height == 0


class TestPermutation:
    def test_p_valeur_bornes(self):
        temoin = np.random.default_rng(0).normal(0, 1, 200)
        # Une valeur au centre de la distribution du témoin ne surprend personne.
        assert S.p_valeur_empirique(float(temoin.mean()), temoin) > 0.9
        # Une valeur très à l'écart atteint le plancher (1 + 0) / (1 + N).
        assert S.p_valeur_empirique(50.0, temoin) == pytest.approx(1 / 201)

    def test_jamais_zero(self):
        """Avec N tirages, une p nulle serait de la fausse précision."""
        temoin = np.zeros(50)
        assert S.p_valeur_empirique(10.0, temoin) > 0

    def test_temoin_vide_ou_nan(self):
        assert np.isnan(S.p_valeur_empirique(1.0, np.zeros(0)))
        assert np.isnan(S.p_valeur_empirique(float("nan"), np.ones(10)))

    def test_les_temoins_rendent_une_moyenne_par_tirage(self, contexte):
        """La distribution nulle, c'est N moyennes — pas N×k rendements."""
        frames, journal = contexte
        df = frames["1h"]
        bars = journal.filter(pl.col("tf") == "1h")["bar"].to_numpy()[:60]
        moyennes = S._temoin_decale(bars, df, 12, np.random.default_rng(0))
        assert len(moyennes) == S.N_TIRAGES_TEMOIN


class TestFrequence:
    def test_les_deux_unites_ne_disent_pas_la_meme_chose(self, contexte):
        """1 000 barres valent dix jours en 1 h et quarante en 4 h."""
        frames, journal = contexte
        f = S.frequences(journal, frames)
        assert set(f["tf"].unique()) <= {"1h", "4h"}
        assert (f["par_1000_barres"] > 0).any() and (f["par_mois"] > 0).any()

    def test_seuil_de_significativite(self, contexte):
        frames, journal = contexte
        f = S.frequences(journal, frames)
        from app.core.stats_thresholds import MIN_SIGNIFICANT_TRADES
        for n, sig in f.select(["n", "significatif"]).rows():
            assert sig == (n >= MIN_SIGNIFICANT_TRADES)

    def test_ventilation_rapporte_a_leffectif_du_motif(self, contexte):
        _, journal = contexte
        v = S.ventilation(journal, par="session")
        for (_, _), g in v.group_by(["tf", "motif"]):
            assert float(g["part"].sum()) == pytest.approx(1.0, abs=1e-3)


class TestTransitions:
    def test_lift_compare_a_lindependance(self, contexte):
        _, journal = contexte
        t = S.transitions(journal, "1h", fenetre=12, n_barres=2000)
        assert {"observe", "attendu", "lift"} <= set(t.columns)
        assert t.schema["de"] == pl.Utf8 and t.schema["vers"] == pl.Utf8

    def test_un_motif_frequent_na_pas_un_lift_eleve_par_construction(self, contexte):
        """Le piège que `attendu` existe pour éviter."""
        _, journal = contexte
        t = S.transitions(journal, "1h", fenetre=12, n_barres=2000)
        frequents = t.filter(pl.col("n_de") > 100)
        if frequents.height:
            # Sur du bruit, le lift des paires fréquentes reste proche de 1.
            assert float(frequents["lift"].median()) < 3.0


class TestBudgetHypotheses:
    def test_correction_bonferroni(self):
        faux = pl.DataFrame({"a": list(range(100))})
        b = S.budget_hypotheses(faux)
        assert b["n_hypotheses"] == 100
        assert b["alpha_bonferroni"] == pytest.approx(0.05 / 100)
        assert b["z_requis"] > 3.0

    def test_vide(self):
        b = S.budget_hypotheses(pl.DataFrame())
        assert b["n_hypotheses"] == 0 and b["z_requis"] is None

    def test_les_deux_temoins_doivent_tomber(self):
        """Battre le témoin inconditionnel seul ne suffit pas."""
        mesures = pl.DataFrame({
            "significatif": [True, True],
            "p_decale": [0.0001, 0.9],
            "p_inconditionnel": [0.0001, 0.0001],
            "ecart_decale": [1.0, 1.0],
        })
        budget = {"n_hypotheses": 2, "alpha_nominal": 0.05}
        survivants = S.survivants(mesures, budget)
        assert survivants.height == 1
        # C'est bien la ligne qui bat les DEUX témoins qui reste.
        assert survivants["p_decale"].to_list() == [0.0001]


class TestPlancherEtFDR:
    """Le plancher du test et la correction qui s'y heurte.

    À 200 tirages le plancher des p-values valait 0.00498, au-dessus de tous
    les seuils de Bonferroni de l'étude : le « 0 survivant » était garanti par
    construction. Ces tests verrouillent le diagnostic et le correctif.
    """

    def test_le_plancher_suit_le_nombre_de_tirages(self):
        assert S.plancher_p(200) == pytest.approx(1 / 201)
        assert S.plancher_p(2000) == pytest.approx(1 / 2001)
        # Sans argument, c'est le réglage du module qui s'applique.
        assert S.plancher_p() == pytest.approx(1 / (1 + S.N_TIRAGES_TEMOIN))

    def test_seuil_bh_cas_verifiable_a_la_main(self):
        # m=10, α=0.05 → seuils k·0.005. Rang 2 passe (0.004 ≤ 0.010),
        # rang 3 non (0.02 > 0.015) : le seuil retenu est 0.004.
        assert S.seuil_bh([0.001, 0.004, 0.02, 0.6, 0.9], m=10) == pytest.approx(0.004)

    def test_bh_est_moins_severe_que_bonferroni(self):
        p = [0.0005] * 6 + [0.001] * 20 + [0.5] * 334
        assert S.seuil_bh(p, m=360) >= 0.05 / 360

    def test_un_plancher_trop_haut_ne_rejette_rien(self):
        """Le cœur du diagnostic : à 200 tirages, aucun rejet n'était possible."""
        au_plancher = [1 / 201] * 6 + [2 / 201] * 20 + [0.5] * 334
        assert S.seuil_bh(au_plancher, m=360) is None
        # Le même profil, mesuré à 2000 tirages, redevient rejetable.
        assert S.seuil_bh([1 / 2001] * 6 + [2 / 2001] * 20 + [0.5] * 334,
                          m=360) is not None

    def test_les_hypotheses_non_mesurees_comptent_au_denominateur(self):
        """Filtrer par support puis corriger sur les survivants ne corrige rien."""
        assert S.seuil_bh([1 / 2001] * 3, m=73010) is None
        assert S.seuil_bh([1 / 2001] * 1000, m=73010) is not None

    def test_entrees_vides_ou_non_finies(self):
        assert S.seuil_bh([]) is None
        assert S.seuil_bh([None, float("nan")], m=10) is None

    def test_le_seuil_applique_est_publie(self):
        """Le rapport doit pouvoir montrer le seuil réellement retenu."""
        mesures = pl.DataFrame({
            "significatif": [True, True],
            "p_decale": [0.0001, 0.9],
            "p_inconditionnel": [0.0001, 0.9],
            "ecart_decale": [1.0, 1.0],
        })
        budget = {"n_hypotheses": 2, "alpha_nominal": 0.05}
        S.survivants(mesures, budget)
        assert budget["seuil_bh"] == pytest.approx(0.0001)
