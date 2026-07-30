"""Lissage ATR/ADX/DI — Wilder (α=1/14) par défaut, ``span=14`` en repli.

Le dépôt calculait ces indicateurs en ``ewm_mean(span=14)``, soit α = 2/15, là
où la définition de Wilder veut α = 1/14 : un ``span=14`` est un Wilder de
période 7,5. La correction a été appliquée APRÈS mesure
(docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §8ter) ; le commutateur survit pour
rejouer la comparaison ou reproduire un backtest antérieur.

Ces tests verrouillent les propriétés dont dépendent la correction ET la
validité de la mesure — chacune, si elle cassait, se traduirait par une
divergence silencieuse plutôt que par une erreur.
"""
import datetime as dt

import numpy as np
import polars as pl
import pytest

import app.core.indicators_precompute as ip


@pytest.fixture(autouse=True)
def _restore_default():
    yield
    ip.set_wilder_atr_adx(True)          # Wilder = défaut depuis §8ter


def _ohlcv(n: int = 600, seed: int = 3) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.012, n))
    return pl.DataFrame({
        "time": [dt.datetime(2024, 1, 1) + dt.timedelta(hours=i) for i in range(n)],
        "open": np.concatenate([[100.0], close[:-1]]),
        "high": close * 1.006, "low": close * 0.994,
        "close": close, "volume": rng.uniform(100, 1000, n),
    })


def test_default_is_wilder():
    """Le défaut est la définition de l'indicateur, pas l'historique du dépôt.

    Un retour silencieux à ``span=14`` ferait diverger ces colonnes du
    catalogue de features V4 (déjà en Wilder) et de l'ATR du moteur SMC —
    exactement l'incohérence que §8ter a supprimée."""
    assert ip.wilder_atr_adx() is True


def test_switch_actually_changes_the_series():
    """Si la bascule était inopérante, toute comparaison conclurait « aucun
    effet » — le pire des résultats, parce qu'il est faux et rassurant."""
    df = _ohlcv()
    ip.set_wilder_atr_adx(False)
    span = ip.precompute_df(df)["_pre_adx14"].to_numpy()
    ip.set_wilder_atr_adx(True)
    wilder = ip.precompute_df(df)["_pre_adx14"].to_numpy()

    assert not np.allclose(span, wilder), "la bascule n'a aucun effet"
    # Moins de lissage ⇒ l'ADX span=14 court plus haut (mesuré : 35.4 vs 28.2
    # sur BTC/USDC 1h). On teste le SENS, pas la valeur.
    assert np.nanmean(span) > np.nanmean(wilder)


def test_cache_is_not_shared_between_conventions():
    """Le cache de pré-calcul est mémoïsé par plage. Sans la convention dans
    la clé, la seconde variante recevrait les colonnes de la première."""
    df = _ohlcv()
    ip.set_wilder_atr_adx(True)
    first = ip.precompute_df(df)["_pre_adx14"].to_numpy()
    ip.set_wilder_atr_adx(False)
    second = ip.precompute_df(df)["_pre_adx14"].to_numpy()
    ip.set_wilder_atr_adx(True)
    third = ip.precompute_df(df)["_pre_adx14"].to_numpy()

    assert not np.allclose(first, second)
    assert np.allclose(first, third), "retour au défaut : série d'origine attendue"


def test_wilder_mode_matches_the_v4_feature_catalogue():
    """En mode Wilder, ``_pre_adx14`` doit rejoindre l'``ADX`` du catalogue V4
    (``app.ml.backend.features``, α = 1/14). C'est ce qui rend les deux
    familles de stratégies comparables — et ce qui prouve que le mode Wilder
    est bien Wilder, pas juste « autre chose »."""
    from app.ml.backend.features import build_features
    df = _ohlcv(1200, seed=5)
    ip.set_wilder_atr_adx(True)
    pre = ip.precompute_df(df)
    feats = build_features(df)
    n = len(feats)
    a = pre["_pre_adx14"].to_numpy()[-n:]
    b = feats["ADX"].to_numpy()
    m = np.isfinite(a) & np.isfinite(b)
    assert m.sum() > 200
    # Même formule, mêmes données : l'écart restant tient au warmup de la
    # fenêtre, pas à la convention.
    assert np.corrcoef(a[m], b[m])[0, 1] > 0.99


def test_span14_is_a_wilder_of_period_seven_and_a_half():
    """La formule qui justifie tout le §8ter : span=N ⇔ α = 2/(N+1)."""
    x = np.asarray(_ohlcv(400)["close"].to_numpy(), dtype=float)
    span14 = pl.Series(x).ewm_mean(span=14, adjust=False).to_numpy()
    alpha_equiv = pl.Series(x).ewm_mean(alpha=2 / 15, adjust=False).to_numpy()
    assert np.allclose(span14, alpha_equiv)
    # 1/alpha = 7.5 : la période réelle de l'indicateur nommé « 14 ».
    assert 1 / (2 / 15) == pytest.approx(7.5)


def test_worker_relay_key_exists_in_cfg_contract():
    """Les workers de l'optimiseur sont SPAWNÉS : ils n'héritent d'aucun
    global. Le drapeau doit voyager par ``cfg["indicators"]["wilder_atr_adx"]``
    — sans ce relais, un bras « Wilder » tournerait entièrement en span=14."""
    import inspect

    from app.engine import opt_workers
    src = inspect.getsource(opt_workers)
    assert 'wilder_atr_adx' in src
    assert src.count("set_wilder_atr_adx") >= 2, (
        "le relais doit exister dans _worker_init ET _eval_worker"
    )


def test_worker_relay_does_not_force_a_default():
    """Le relais ne doit surcharger QUE si la clé est présente dans cfg.

    Un ``.get("wilder_atr_adx", False)`` ferait tourner tous les workers en
    ancienne convention pendant que le parent serait en Wilder : optimisation
    parallèle et backtest in-process divergeraient sans rien signaler."""
    import inspect

    from app.engine import opt_workers
    src = inspect.getsource(opt_workers)
    assert 'wilder_atr_adx", False' not in src, (
        "le relais force un défaut au lieu de laisser celui du module"
    )
    assert src.count("if _w is not None") >= 2


def test_all_atr_paths_are_the_series_smc_depends_on():
    """``atr``, ``atr_series`` et ``_pre_atr14`` coïncident avec ``atr_wilder``.

    Une seule implémentation du lissage Wilder, donc aucune dérive possible
    entre le contrat du moteur SMC et le reste du dépôt."""
    import app.core.indicators_core as ic
    df = _ohlcv(800, seed=7)
    ip.set_wilder_atr_adx(True)
    ref = ic.atr_wilder(df, 14).to_numpy()
    assert np.allclose(ic.atr(df, 14).to_numpy(), ref)
    assert np.allclose(ic.atr_series(df, 14).to_numpy(), ref)
    assert np.allclose(ip.precompute_df(df)["_pre_atr14"].to_numpy(), ref)


def test_core_adx_is_wilder_at_all_four_stages():
    """ATR, +DI, −DI et la ligne ADX : les quatre étages en α = 1/n.

    N'en corriger que trois donnerait un ADX intermédiaire, ni Wilder ni
    l'ancien — le pire des trois mondes, et invisible sans ce test."""
    import app.core.indicators_core as ic
    df = _ohlcv(900, seed=11)
    adx_l, pdi, ndi = ic.adx(df, 14)
    a = 1.0 / 14

    tr = ic._true_range(df)
    atr_ref = tr.ewm_mean(alpha=a, adjust=False)
    assert np.allclose(atr_ref.to_numpy(), ic.atr_wilder(df, 14).to_numpy())

    up = df["high"].diff(1).clip(lower_bound=0)
    dn = (-df["low"].diff(1)).clip(lower_bound=0)
    pdm = up * (up > dn).cast(pl.Float64)
    ndm = dn * (dn > up).cast(pl.Float64)
    safe = atr_ref.clip(lower_bound=1e-10)
    # Ordre identique à l'implémentation : ``fill_null`` seulement à la sortie,
    # sinon ``dx`` diffère sur les premières barres et le test échoue pour une
    # raison qui n'a rien à voir avec le lissage.
    pdi_ref = 100 * pdm.ewm_mean(alpha=a, adjust=False) / safe
    ndi_ref = 100 * ndm.ewm_mean(alpha=a, adjust=False) / safe
    sum_di = (pdi_ref + ndi_ref).clip(lower_bound=1e-10)
    dx = 100 * (pdi_ref - ndi_ref).abs() / sum_di
    adx_ref = dx.ewm_mean(alpha=a, adjust=False).fill_null(0)

    assert np.allclose(pdi.to_numpy(), pdi_ref.fill_null(0).to_numpy())
    assert np.allclose(ndi.to_numpy(), ndi_ref.fill_null(0).to_numpy())
    assert np.allclose(adx_l.to_numpy(), adx_ref.to_numpy())

    # Contre-épreuve : la même construction en span=n doit DIVERGER, sinon le
    # test ne discrimine rien.
    span_adx = (100 * (pdm.ewm_mean(span=14, adjust=False)
                       - ndm.ewm_mean(span=14, adjust=False)).abs()
                / (pdm.ewm_mean(span=14, adjust=False)
                   + ndm.ewm_mean(span=14, adjust=False)).clip(lower_bound=1e-10)
                ).ewm_mean(span=14, adjust=False).fill_null(0)
    assert not np.allclose(adx_l.to_numpy(), span_adx.to_numpy())


def test_smc_reads_none_of_the_corrected_columns():
    """Le moteur SMC est hors périmètre de la correction — par construction.

    Il ne lit aucune colonne ``_pre_*`` et passe par ``atr_wilder``, déjà
    correct avant §8ter. Ce test garde cette indépendance : si un module SMC
    se mettait à lire ``_pre_atr14``, il hériterait d'une convention qu'il n'a
    pas choisie, et la garantie « SMC inchangé » tomberait en silence."""
    import pathlib
    mods = sorted(pathlib.Path("app/core").glob("smc*.py")) + [
        pathlib.Path("app/core/ict.py")]
    assert mods, "modules SMC introuvables"
    for path in mods:
        src = path.read_text(encoding="utf-8")
        assert "_pre_atr14" not in src, f"{path.name} lit _pre_atr14"
        assert "_pre_adx14" not in src, f"{path.name} lit _pre_adx14"
