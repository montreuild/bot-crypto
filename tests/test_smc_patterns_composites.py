"""Motifs composés : découverte et confirmation sur des barres disjointes.

Un composé cherché ET mesuré sur les mêmes barres est toujours beau — c'est
précisément parce qu'il a été choisi pour ça. Le découpage est donc la
propriété à verrouiller, avant même la qualité des mesures.
"""
import numpy as np
import polars as pl

from app.engine.smc_patterns import composites as C


def _journal(evenements, tf="1h", t0="2024-01-01T00:00:00"):
    """Journal minimal : ``evenements`` = [(heure_relative, motif), …]."""
    base = np.datetime64(t0, "ms")
    # Tableau numpy et non liste de datetime64 : polars typerait la colonne en
    # Object et refuserait ensuite `.dt.epoch`.
    temps = base + np.array([int(h) for h, _ in evenements],
                            dtype="timedelta64[h]").astype("timedelta64[ms]")
    return pl.DataFrame({
        "symbol": ["X"] * len(evenements),
        "tf": [tf] * len(evenements),
        "bar": [int(h) for h, _ in evenements],
        "time": temps,
        "famille": ["test"] * len(evenements),
        "motif": [m for _, m in evenements],
        "cote": [""] * len(evenements),
    })


class TestDecoupage:
    def test_les_deux_tranches_sont_disjointes(self):
        ev = [(i, "A" if i % 2 else "B") for i in range(200)]
        r = C.mine(_journal(ev), fenetre_s=6 * 3600, longueurs=(2,))
        assert r["coupure"] is not None
        assert r["n_decouverte"] > 0 and r["n_confirmation"] > 0
        assert r["n_decouverte"] + r["n_confirmation"] == len(ev)

    def test_la_confirmation_est_la_FIN_de_lhistorique(self):
        ev = [(i, "A") for i in range(100)]
        j = _journal(ev)
        r = C.mine(j, fenetre_s=6 * 3600, longueurs=(2,))
        ep = j["time"].dt.epoch(time_unit="s").to_numpy()
        # Tout ce qui précède la coupure est en découverte, rien après.
        assert int((ep < r["coupure"]).sum()) == r["n_decouverte"]

    def test_un_compose_absent_de_la_confirmation_nest_pas_confirme(self):
        """Le cas qui motive tout le découpage."""
        # « C → D » n'existe que dans la première moitié.
        ev = [(i, "C" if i % 2 else "D") for i in range(60)]
        ev += [(60 + i, "E") for i in range(60)]
        r = C.mine(_journal(ev), fenetre_s=6 * 3600, longueurs=(2,))
        cand = r["candidats"]
        cd = cand.filter(pl.col("sequence") == "1h|C → 1h|D")
        if cd.height:
            assert not bool(cd["confirme"][0]), \
                "un composé qui disparaît après la coupure ne doit pas être confirmé"

    def test_historique_trop_court(self):
        r = C.mine(_journal([(i, "A") for i in range(5)]), fenetre_s=3600)
        assert r["candidats"].height == 0 and r["n_enumeres"] == 0


class TestComptageDesHypotheses:
    def test_on_compte_les_enumerees_pas_les_survivantes(self):
        """Corriger sur le nombre de survivants revient à ne pas corriger."""
        # Motifs fréquents + motifs rares : l'espace ÉNUMÉRÉ contient les
        # paires rares, l'espace RETENU ne garde que celles à support suffisant.
        ev = [(i, f"M{i % 6}") for i in range(300)]
        ev += [(3 * i, f"RARE{i}") for i in range(20)]
        ev.sort()
        r = C.mine(_journal(ev), fenetre_s=6 * 3600, longueurs=(2,))
        assert r["candidats"].height > 0, "sans candidat retenu, le test est vide"
        assert r["n_enumeres"] > r["candidats"].height, (
            "n_enumeres doit compter tout l'espace exploré, pas les rescapés")

    def test_le_support_minimal_filtre(self):
        ev = [(i, "A") for i in range(40)] + [(100, "RARE"), (101, "RARE")]
        r = C.mine(_journal(ev), fenetre_s=6 * 3600, longueurs=(2,),
                   support_min=10)
        assert all("RARE" not in s for s in r["candidats"]["sequence"].to_list())


class TestOrdreTemporel:
    def test_les_maillons_sont_ordonnes_par_TEMPS_pas_par_index(self):
        """Un index 4 h et un index 1 h ne sont pas comparables."""
        base = np.datetime64("2024-01-01T00:00:00", "ms")
        # La barre 4h n°1 (t+4h) arrive APRÈS la barre 1h n°2 (t+2h).
        j = pl.DataFrame({
            "symbol": ["X"] * 4, "tf": ["4h", "1h", "1h", "4h"],
            "bar": [0, 1, 2, 1],
            "time": base + np.array([0, 1, 2, 4], dtype="timedelta64[h]"
                                    ).astype("timedelta64[ms]"),
            "famille": ["t"] * 4, "motif": ["P", "Q", "Q", "P"], "cote": [""] * 4,
        })
        ep, maillons = C._evenements_ordonnes(j)
        assert list(ep) == sorted(ep)
        assert maillons[0] == "4h|P" and maillons[-1] == "4h|P"

    def test_le_compose_est_date_a_son_dernier_maillon(self):
        ev = [(0, "A"), (1, "B"), (2, "C")]
        seqs = C._sequences(*C._evenements_ordonnes(_journal(ev)), 3, 6 * 3600)
        assert seqs[("1h|A", "1h|B", "1h|C")] == [2]


class TestGardeFouCombinatoire:
    def test_fenetre_trop_dense_est_ignoree_et_signalee(self, caplog):
        """Mieux vaut refuser que tourner des heures pour produire du bruit."""
        ev = [(0, f"M{i % 5}") for i in range(C.MAX_EVENEMENTS_FENETRE + 40)]
        j = _journal(ev)
        with caplog.at_level("WARNING"):
            C._sequences(*C._evenements_ordonnes(j), 3, 10 * 3600)
        assert any("fenêtre" in r.message or "fenetre" in r.message
                   for r in caplog.records)

    def test_longueur_au_dela_du_max_refusee(self):
        r = C.mine(_journal([(i, "A") for i in range(100)]),
                   fenetre_s=6 * 3600, longueurs=(4,))
        assert r["candidats"].height == 0


class TestSurvivants:
    def test_le_decompte_dhypotheses_vient_de_lenumeration(self):
        mesures = pl.DataFrame({
            "significatif": [True], "p_decale": [0.004],
            "ecart_decale": [1.0], "ic95_pct": [0.1],
        })
        # 10 hypothèses → alpha = 0.005 → 0.004 passe.
        assert C.survivants_composes(mesures, {"n_enumeres": 10})["survivants"].height == 1
        # 1 000 hypothèses → alpha = 5e-5 → 0.004 ne passe plus.
        assert C.survivants_composes(mesures, {"n_enumeres": 1000})["survivants"].height == 0

    def test_sans_enumeration_rien_ne_survit(self):
        mesures = pl.DataFrame({"significatif": [True], "p_decale": [0.0],
                                "ecart_decale": [9.9], "ic95_pct": [0.01]})
        assert C.survivants_composes(mesures, {"n_enumeres": 0})["survivants"].height == 0
