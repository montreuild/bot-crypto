"""L3 — moteur de structure séquentiel (§60–§64, §73, §82).

Le moteur SMC produit un biais ternaire qui bascule sur la première clôture
au-delà du dernier swing : rien ne distingue un pullback d'un retournement, et
une mèche suivie d'une clôture marginale suffit à retourner le biais.

Trois propriétés sont verrouillées ici :

- **causalité** — la série calculée sur `df[:k]` doit être le préfixe exact de
  celle calculée sur `df` entier. C'est LA propriété qui rend le module
  utilisable en backtest ; sans elle, tout gain mesuré serait une fuite ;
- **inertie** — un premier MSS contraire déclenche un avertissement, jamais une
  confirmation (§62), et une cassure sans displacement ne change rien (§60.3) ;
- **niveaux protégés** — un nouveau HH consacre le dernier HL (§64).
"""
import datetime as dt

import numpy as np
import polars as pl
import pytest

from app.core.smc import analyze
from app.core.smc.state import (
    CODE,
    STATES,
    sequence_label,
    state_label,
    structure_states,
)


def _serie(n: int = 900, seed: int = 11) -> pl.DataFrame:
    """Marche aléatoire avec dérive alternée : produit des tendances, des
    pullbacks et au moins un retournement, ce qu'une série monotone ne ferait
    pas."""
    rng = np.random.RandomState(seed)
    start = dt.datetime(2021, 1, 1)
    derive = np.concatenate([
        np.full(n // 3, 0.0012), np.full(n // 3, -0.0015),
        np.full(n - 2 * (n // 3), 0.0009),
    ])
    close = 100 * np.cumprod(1 + derive + rng.normal(0, 0.009, n))
    open_ = np.concatenate([[100.0], close[:-1]])
    return pl.DataFrame({
        "time": [start + dt.timedelta(hours=i) for i in range(n)],
        "open": open_,
        "high": np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n))),
        "low": np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n))),
        "close": close,
        "volume": rng.uniform(100, 1000, n),
    })


@pytest.fixture(scope="module")
def etat_complet():
    df = _serie()
    return df, structure_states(df, analyze(df))


# ── Causalité ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("k", [200, 300, 400, 500, 600, 700, 800])
def test_la_serie_tronquee_est_un_prefixe_exact(k):
    """Aucune barre ne doit changer d'état quand on découvre le futur. Un
    `.get(confirmed_at)` remplacé par `.get(index)` casserait ce test — et
    c'est exactement l'erreur que le moteur doit rendre impossible."""
    df = _serie()
    complet = structure_states(df, analyze(df))
    tronque = structure_states(df.head(k), analyze(df.head(k)))
    assert np.array_equal(tronque["states"], complet["states"][:k]), (
        f"états divergents sur les {k} premières barres"
    )


@pytest.mark.parametrize("k", [300, 500, 700])
def test_les_niveaux_proteges_sont_causaux(k):
    df = _serie()
    complet = structure_states(df, analyze(df))
    tronque = structure_states(df.head(k), analyze(df.head(k)))
    for cle in ("protected_high", "protected_low"):
        np.testing.assert_allclose(tronque[cle], complet[cle][:k],
                                   equal_nan=True, err_msg=cle)


# ── Forme de la sortie ──────────────────────────────────────────────────────

def test_les_tableaux_sont_alignes_sur_le_dataframe(etat_complet):
    df, st = etat_complet
    for cle in ("states", "sequences", "protected_high", "protected_low"):
        assert len(st[cle]) == len(df), cle


def test_tous_les_codes_sont_des_etats_connus(etat_complet):
    _, st = etat_complet
    assert set(np.unique(st["states"])) <= set(CODE.values())
    assert state_label(st["states"][-1]) in STATES


def test_la_serie_produit_plusieurs_etats(etat_complet):
    """Un moteur qui resterait sur UNKNOWN ou ne produirait qu'un état ne
    mesurerait rien — la série de test contient tendances et retournement."""
    _, st = etat_complet
    vus = {state_label(c) for c in np.unique(st["states"])}
    assert len(vus) >= 4, f"seulement {vus}"


def test_les_evenements_portent_la_convention_interne(etat_complet):
    _, st = etat_complet
    for e in st["mss_events"]:
        assert e["kind"] == "MSS" and "sweep_bar" in e, (
            "un MSS sans balayage de référence n'est pas un MSS (§61)"
        )
    for e in st["bos_events"]:
        assert e["kind"] == "BOS"


# ── Inertie de la structure ─────────────────────────────────────────────────

def _synthetique(motif: list) -> pl.DataFrame:
    """Série OHLC déterministe depuis une liste de clôtures. Les corps sont
    pleins (open = close précédent) et les mèches minimales : le displacement
    est donc entièrement piloté par l'amplitude du motif."""
    close = np.array(motif, dtype=float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    start = dt.datetime(2021, 1, 1)
    return pl.DataFrame({
        "time": [start + dt.timedelta(hours=i) for i in range(len(close))],
        "open": open_, "high": np.maximum(open_, close) * 1.0005,
        "low": np.minimum(open_, close) * 0.9995, "close": close,
        "volume": np.full(len(close), 500.0),
    })


def test_une_cassure_sans_displacement_ne_retourne_pas_le_biais():
    """§60.3 — clôture marginale au-delà du swing, sans corps : le biais ne
    doit pas bouger. C'est la faiblesse exacte du `_trend_arr` du moteur."""
    # Montée franche, puis dérive plate qui effleure les niveaux.
    motif = [100 + i * 0.8 for i in range(60)] + \
            [148 + 0.02 * ((-1) ** i) for i in range(60)]
    df = _synthetique(motif)
    st = structure_states(df, analyze(df), {"break_body_atr": 1.5})
    fin = {state_label(c) for c in st["states"][-30:]}
    assert "BEARISH" not in fin and "BEARISH_CONFIRMED" not in fin, (
        f"le biais a basculé sur du bruit : {fin}"
    )


def test_un_mss_seul_ne_confirme_jamais_un_retournement(etat_complet):
    """§62 — le premier MSS contraire fait passer en WARNING. La confirmation
    exige un nouveau LH/HL puis un BOS."""
    _, st = etat_complet
    for e in st["mss_events"]:
        i = e["index"]
        etat = state_label(st["states"][i])
        assert etat.endswith("WARNING") or etat.endswith("PENDING") \
            or etat.startswith("FAILED"), (
            f"barre {i} : MSS → {etat}, attendu un avertissement"
        )


def test_le_niveau_protege_bas_suit_le_dernier_hl(etat_complet):
    """§64 — tant qu'il tient, la structure haussière est intacte. Un plancher
    protégé qui vaudrait plus que le prix serait déjà cassé."""
    df, st = etat_complet
    bas = df["low"].to_numpy()
    for i, niveau in enumerate(st["protected_low"]):
        if np.isnan(niveau):
            continue
        etat = state_label(st["states"][i])
        if etat in ("BULLISH", "BULLISH_PULLBACK"):
            assert niveau <= max(bas[:i + 1]), (
                f"barre {i} : plancher {niveau} au-dessus de tout le passé"
            )


def test_les_sequences_sont_des_libelles_connus(etat_complet):
    _, st = etat_complet
    for c in np.unique(st["sequences"]):
        assert sequence_label(c) != "NONE" or c == 0


def test_dataframe_vide_ne_plante_pas():
    df = pl.DataFrame({"time": [], "open": [], "high": [], "low": [],
                       "close": [], "volume": []})
    st = structure_states(df, {})
    assert len(st["states"]) == 0
