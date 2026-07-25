"""Commutateur de lissage ATR/ADX/DI — span=14 (historique) vs Wilder (α=1/14).

Le dépôt calculait ces indicateurs en ``ewm_mean(span=14)``, soit α = 2/15, là
où la définition de Wilder veut α = 1/14 : un ``span=14`` est un Wilder de
période 7,5. Le commutateur permet de mesurer les deux conventions avant de
trancher (docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §8ter).

Ces tests verrouillent les trois propriétés dont dépend la validité de cette
mesure — chacune, si elle cassait, ferait silencieusement conclure « aucun
effet ».
"""
import datetime as dt

import numpy as np
import polars as pl
import pytest

import app.core.indicators_precompute as ip


@pytest.fixture(autouse=True)
def _restore_default():
    yield
    ip.set_wilder_atr_adx(False)


def _ohlcv(n: int = 600, seed: int = 3) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.012, n))
    return pl.DataFrame({
        "time": [dt.datetime(2024, 1, 1) + dt.timedelta(hours=i) for i in range(n)],
        "open": np.concatenate([[100.0], close[:-1]]),
        "high": close * 1.006, "low": close * 0.994,
        "close": close, "volume": rng.uniform(100, 1000, n),
    })


def test_default_is_the_historical_convention():
    """Le défaut ne bouge pas : la mesure ne doit rien changer au bot."""
    assert ip.wilder_atr_adx() is False


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
    ip.set_wilder_atr_adx(False)
    first = ip.precompute_df(df)["_pre_adx14"].to_numpy()
    ip.set_wilder_atr_adx(True)
    second = ip.precompute_df(df)["_pre_adx14"].to_numpy()
    ip.set_wilder_atr_adx(False)
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
