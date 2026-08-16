"""Journal SMC/ICT : aucun motif n'est daté plus tôt que ce qui était sachable.

C'est LA propriété qui rend toute la suite interprétable. Un motif daté à sa
barre d'origine plutôt qu'à sa barre de confirmation ne casse rien de visible :
il produit simplement des rendements « après le motif » qui contiennent déjà le
mouvement, donc des statistiques flatteuses et fausses.

Le test compare, à 24 points de coupe, le journal d'un préfixe de N barres au
journal complet tronqué à N. Deux issues possibles :

  - un événement présent dans le PLEIN mais pas dans le PRÉFIXE ⇒ le journal
    complet l'a daté à une barre où l'historique ne permettait pas de le
    connaître. **Interdit sans exception.**
  - un événement présent dans le PRÉFIXE mais pas dans le PLEIN ⇒ l'entité est
    mutable (un pool de liquidité est renforcé à chaque swing qui le rejoint,
    et ``formed_at`` désigne le dernier renforcement). Le journal complet
    sous-compte. C'est le sens conservateur, toléré pour les seules familles
    déclarées dans ``FAMILLES_MUTABLES``.
"""
import numpy as np
import polars as pl
import pytest

from app.engine.smc_patterns import (
    CONNAISSABILITE,
    FAMILLES_MUTABLES,
    barre_connaissable,
    journal_multi_tf,
    journal_un_tf,
)
from app.engine.smc_patterns.journal import _index_htf_causal

CLES = ["bar", "motif", "famille", "cote"]


def _serie(n: int = 1200, seed: int = 5, pas_ms: int = 3_600_000) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    vol = 0.003 + 0.006 * (1 + np.sin(np.arange(n) / 53.0)) / 2
    close = 30_000 * np.exp(np.cumsum(rng.normal(0, 1, n) * vol))
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    t0 = np.datetime64("2024-01-01T00:00:00", "ms")
    return pl.DataFrame({
        "time": t0 + np.arange(n) * np.timedelta64(pas_ms, "ms"),
        "open": np.concatenate([[close[0]], close[:-1]]),
        "high": high, "low": low, "close": close,
        "volume": np.abs(rng.normal(500, 120, n)),
    })


def _lignes(j: pl.DataFrame, famille: str = None) -> set:
    if famille is not None:
        j = j.filter(pl.col("famille") == famille)
    return set(map(tuple, j.select(CLES).rows()))


class TestCausalite:
    @pytest.fixture(scope="class")
    @classmethod
    def contexte(cls):
        df = _serie(1200)
        return df, journal_un_tf(df, "1h", "TEST/USDC")

    def test_le_journal_nest_pas_vide(self, contexte):
        """Un test de causalité sur un journal vide ne prouve rien."""
        _, plein = contexte
        assert plein.height > 200
        assert plein["famille"].n_unique() >= 7

    def test_aucun_motif_date_trop_tot(self, contexte):
        """La propriété qui compte : le plein ne connaît rien de plus que le préfixe."""
        df, plein = contexte
        fautifs = {}
        for n in range(300, len(df), 37):
            trop_tot = _lignes(plein.filter(pl.col("bar") < n)) - _lignes(
                journal_un_tf(df[:n], "1h", "TEST/USDC"))
            for _, _, famille, _ in trop_tot:
                fautifs[famille] = fautifs.get(famille, 0) + 1
        assert not fautifs, (
            f"motifs datés avant leur barre de connaissabilité : {fautifs}")

    def test_les_familles_stables_sont_identiques_au_prefixe(self, contexte):
        df, plein = contexte
        for n in (400, 700, 1000):
            prefixe = journal_un_tf(df[:n], "1h", "TEST/USDC")
            for famille in CONNAISSABILITE:
                if famille in FAMILLES_MUTABLES:
                    continue
                assert _lignes(plein.filter(pl.col("bar") < n), famille) == \
                    _lignes(prefixe, famille), f"{famille} diverge à n={n}"

    def test_les_familles_mutables_sous_comptent_jamais_linverse(self, contexte):
        """Le sens de l'écart toléré est vérifié, pas seulement son existence."""
        df, plein = contexte
        for n in (400, 700, 1000):
            prefixe = journal_un_tf(df[:n], "1h", "TEST/USDC")
            for famille in FAMILLES_MUTABLES:
                assert _lignes(plein.filter(pl.col("bar") < n), famille) <= \
                    _lignes(prefixe, famille), \
                    f"{famille} : le journal complet a daté un motif trop tôt"


class TestDatation:
    def test_regles_de_connaissabilite(self):
        assert barre_connaissable("swings", {"index": 10, "confirmed_at": 13}) == 13
        assert barre_connaissable("structure_events", {"index": 42}) == 42
        assert barre_connaissable("order_blocks", {"index": 7, "created_at": 11}) == 11
        # Le FVG se voit à la troisième bougie ; `index` est celle du milieu.
        assert barre_connaissable("fvgs", {"index": 20}) == 21
        # La course n'est close qu'une fois qu'on constate qu'elle ne va pas plus loin.
        assert barre_connaissable("liquidity_voids", {"end_index": 30}) == 31

    def test_entite_sans_indice_est_ecartee(self):
        """Mieux vaut un motif manquant qu'un motif daté au jugé."""
        assert barre_connaissable("swings", {"index": 10}) is None
        assert barre_connaissable("famille_inconnue", {"index": 10}) is None

    def test_toutes_les_familles_journalisees_ont_une_regle(self):
        from app.engine.smc_patterns.journal import _SOURCE_COMPLETE
        assert set(_SOURCE_COMPLETE) == set(CONNAISSABILITE)


class TestAlignementInterTF:
    """Le piège propre au multi-TF : voir une bougie HTF qui n'est pas close."""

    def test_seuls_les_buckets_clotures_sont_visibles(self):
        ltf = _serie(400, pas_ms=3_600_000)                   # 1 h
        htf = _serie(100, pas_ms=4 * 3_600_000)               # 4 h
        idx = _index_htf_causal(ltf, htf, "4h")

        ep_ltf = ltf["time"].dt.epoch(time_unit="s").to_numpy()
        ep_htf = htf["time"].dt.epoch(time_unit="s").to_numpy()
        for i in range(len(ltf)):
            j = int(idx[i])
            if j < 0:
                continue
            fin_htf = ep_htf[j] + 4 * 3600
            fin_ltf = ep_ltf[i] + 3600
            assert fin_htf <= fin_ltf, (
                f"barre LTF {i} voit le bucket HTF {j} qui se termine après elle")
            if j + 1 < len(ep_htf):
                assert ep_htf[j + 1] + 4 * 3600 > fin_ltf, \
                    f"barre LTF {i} : un bucket clôturé plus récent était disponible"

    def test_la_premiere_barre_ltf_ne_voit_aucun_htf(self):
        ltf = _serie(50, pas_ms=3_600_000)
        htf = _serie(20, pas_ms=4 * 3_600_000)
        assert int(_index_htf_causal(ltf, htf, "4h")[0]) == -1

    def test_un_join_au_plus_proche_serait_en_avance(self):
        """Garde-fou : le test précédent doit ÉCHOUER sur une jointure naïve."""
        ltf = _serie(200, pas_ms=3_600_000)
        htf = _serie(50, pas_ms=4 * 3_600_000)
        ep_ltf = ltf["time"].dt.epoch(time_unit="s").to_numpy()
        ep_htf = htf["time"].dt.epoch(time_unit="s").to_numpy()
        # Jointure « bucket contenant la barre » — celle qu'on écrirait d'instinct.
        naif = np.searchsorted(ep_htf, ep_ltf, side="right") - 1
        causal = _index_htf_causal(ltf, htf, "4h")
        assert (naif > causal).any(), \
            "sans écart, ce test ne prouve pas que la version causale sert à quelque chose"

    def test_journal_multi_tf_porte_le_contexte_du_tf_superieur(self):
        frames = {"1h": _serie(600, pas_ms=3_600_000),
                  "4h": _serie(200, seed=6, pas_ms=4 * 3_600_000)}
        j = journal_multi_tf(frames, "TEST/USDC")
        assert set(j["tf"].unique()) == {"1h", "4h"}
        # Le TF le plus haut n'a pas de contexte : c'est la seule réponse honnête.
        assert j.filter(pl.col("tf") == "4h")["tf_htf"].unique().to_list() == [""]
        assert j.filter(pl.col("tf") == "1h")["tf_htf"].unique().to_list() == ["4h"]


class TestRobustesse:
    def test_serie_trop_courte(self):
        assert journal_un_tf(_serie(15), "1h", "X").height == 0
        assert journal_multi_tf({"1h": _serie(5)}, "X").height == 0

    def test_tf_unique_sans_contexte(self):
        j = journal_multi_tf({"1h": _serie(400)}, "X")
        assert j.height > 0
        assert j["tf_htf"].unique().to_list() == [""]
