"""Stratégie `smc_ml_edge` — contrat, portes de décision, causalité du cache.

Le test le plus utile ici est `test_le_cache_ne_lit_jamais_le_futur`. La
stratégie pré-calcule ses features sur la série ENTIÈRE au début du backtest
puis lit la ligne de la barre courante. C'est légitime uniquement parce que les
features sont causales — sinon le backtest serait flatteur et le live décevant,
sans qu'aucune erreur ne se manifeste.

Le second est `test_les_portes_sont_des_quantiles_pas_des_seuils` : la première
version de cette stratégie utilisait des seuils absolus et ne prenait AUCUN
trade, parce que `p_up` ne s'étale que de 0.40 à 0.60. Un test qui vérifie
seulement « la stratégie renvoie un dict valide » n'aurait rien vu.
"""
import pathlib

import numpy as np
import polars as pl
import pytest

pytest.importorskip("lightgbm")

from app.strategies.smc_ml_edge import Strategy

PARQUET = pathlib.Path("data/ohlcv/BTC_USDC/1h.parquet")
NOM = "smc_ml_edge"


@pytest.fixture(scope="module")
def ohlcv():
    if not PARQUET.exists():
        pytest.skip("cache OHLCV absent")
    df = pl.read_parquet(PARQUET)
    if len(df) < 9000:
        pytest.skip(f"historique insuffisant ({len(df)} barres)")
    return df.tail(9000)


@pytest.fixture(scope="module")
def entrainee(ohlcv):
    """Une stratégie déjà entraînée, partagée : l'entraînement coûte ~1 min."""
    s = Strategy()
    s.prepare_for_backtest(ohlcv)
    params = {NOM: {"warmup_bars": 3000, "retrain_every": 10 ** 9,
                    "train_window": 6000, "use_smc_filter": False}}
    s.score(ohlcv.head(7000), params)
    assert s.is_trained, "l'entraînement inline n'a pas eu lieu"
    return s, params


# ─────────────────────────────────────────────────────────────────────────────
#  Contrat
# ─────────────────────────────────────────────────────────────────────────────
def test_declare_la_recette_smc():
    """La stratégie n'a d'intérêt qu'avec le catalogue SMC : si elle pointait
    sur `omnibus_full`, elle mesurerait autre chose que ce qu'elle annonce."""
    from app.ml.recipe import load_recipe, primary_recipe

    recette = primary_recipe(Strategy())
    assert recette == "omnibus_smc"
    assert load_recipe(recette).features_catalog == "v5_smc@1"


def test_signal_valide(entrainee, ohlcv):
    s, params = entrainee
    sig = s.score(ohlcv.head(7500), params)
    assert sig["name"] == NOM
    assert sig["side"] in ("long", "short", "none")
    assert 0.0 <= float(sig["score"]) <= 1.0
    if sig["side"] != "none":
        for cle in ("size_factor", "sl_atr_mult", "tp_atr_mult", "exit_after_bars"):
            assert cle in sig, f"champ {cle} absent — le moteur ne saurait pas gérer la position"


def test_historique_insuffisant_ne_leve_pas():
    s = Strategy()
    court = pl.read_parquet(PARQUET).tail(100) if PARQUET.exists() else None
    if court is None:
        pytest.skip("cache OHLCV absent")
    sig = s.score(court, {NOM: {"warmup_bars": 3000}})
    assert sig["side"] == "none"
    assert "insuffisantes" in sig["reason"]


# ─────────────────────────────────────────────────────────────────────────────
#  Portes de décision
# ─────────────────────────────────────────────────────────────────────────────
def test_les_portes_sont_des_quantiles_pas_des_seuils(entrainee, ohlcv):
    """Un quantile plus permissif doit laisser passer AU MOINS autant de barres.

    C'est ce qui distingue un quantile d'un seuil absolu : sa sélectivité est
    connue d'avance. La première version, à seuils absolus, prenait 0 trade
    parce que `p_up` ne dépasse jamais 0.60 — et rien ne le disait.
    """
    s, base = entrainee
    pris = {}
    for q in (0.05, 0.20, 0.50):
        p = {NOM: {**base[NOM], "amp_top_q": q, "dir_top_q": q}}
        pris[q] = sum(
            1 for i in range(7000, 9000, 25)
            if s.score(ohlcv.head(i), p)["side"] != "none"
        )
    assert pris[0.50] >= pris[0.20] >= pris[0.05], (
        f"sélectivité non monotone : {pris} — les quantiles ne pilotent pas "
        f"réellement les portes"
    )
    assert pris[0.50] > 0, "même au quantile 0.50, aucune barre n'est tradée"


def test_le_filtre_de_structure_ne_fait_que_retirer(entrainee, ohlcv):
    """Le filtre SMC est un refus, jamais une source de signal : il ne peut pas
    faire apparaître un trade que les deux premières portes ont écarté."""
    s, base = entrainee
    echantillon = range(7000, 9000, 25)
    sans = {i for i in echantillon
            if s.score(ohlcv.head(i), {NOM: {**base[NOM], "use_smc_filter": False}})["side"] != "none"}
    avec = {i for i in echantillon
            if s.score(ohlcv.head(i), {NOM: {**base[NOM], "use_smc_filter": True}})["side"] != "none"}
    assert avec <= sans, (
        f"{len(avec - sans)} barre(s) tradée(s) AVEC le filtre et pas sans — "
        f"le filtre crée du signal au lieu d'en retirer"
    )


def test_les_deux_sens_sont_atteignables(entrainee, ohlcv):
    """Une stratégie qui ne prendrait que des longs afficherait le rendement du
    marché, pas un edge directionnel. Le test le rendrait visible."""
    s, base = entrainee
    p = {NOM: {**base[NOM], "amp_top_q": 0.35, "dir_top_q": 0.50}}
    sens = {s.score(ohlcv.head(i), p)["side"] for i in range(7000, 9000, 10)}
    assert {"long", "short"} <= sens, f"sens observés : {sens}"


# ─────────────────────────────────────────────────────────────────────────────
#  Causalité
# ─────────────────────────────────────────────────────────────────────────────
def test_le_cache_ne_lit_jamais_le_futur(ohlcv):
    """LE test de la stratégie.

    `prepare_for_backtest` calcule les features sur toute la série ; `score()`
    doit produire exactement la même décision que si la stratégie n'avait vu
    que le préfixe. Sinon, le backtest exploiterait des barres futures et son
    résultat ne voudrait rien dire.
    """
    params = {NOM: {"warmup_bars": 3000, "retrain_every": 10 ** 9,
                    "train_window": 6000, "use_smc_filter": True}}

    avec_cache = Strategy()
    avec_cache.prepare_for_backtest(ohlcv)          # voit les 9000 barres
    avec_cache.score(ohlcv.head(7000), params)

    sans_cache = Strategy()                          # ne verra que les préfixes
    sans_cache.score(ohlcv.head(7000), params)

    # Les modèles diffèrent (deux entraînements LightGBM indépendants) ; ce qui
    # doit coïncider, ce sont les FEATURES vues à chaque barre. On les compare
    # directement, sur les colonnes SMC.
    from app.ml.features_smc import SMC_FEATURE_NAMES

    for n in (7200, 7900, 8600):
        frame_cache, idx_cache = avec_cache._frame_for(ohlcv.head(n))
        frame_direct, idx_direct = sans_cache._frame_for(ohlcv.head(n))
        assert idx_cache == n - 1 and idx_direct == n - 1
        a = frame_cache.slice(idx_cache, 1).select(SMC_FEATURE_NAMES).to_numpy()
        b = frame_direct.slice(idx_direct, 1).select(SMC_FEATURE_NAMES).to_numpy()
        differentes = [
            SMC_FEATURE_NAMES[j] for j in range(len(SMC_FEATURE_NAMES))
            if not np.isclose(a[0, j], b[0, j], rtol=1e-9, atol=1e-9)
        ]
        # `smc_liq_*` tolérées : ré-agrégation des poches de liquidité, écart
        # documenté et conservateur (cf. docs/ML_ABLATION_SMC.md §5).
        interdites = [c for c in differentes if not c.startswith("smc_liq_")]
        assert not interdites, (
            f"barre {n} : le cache donne d'autres valeurs que le calcul en "
            f"temps réel pour {interdites} — fuite temporelle"
        )


def test_le_cache_ne_depasse_jamais_la_barre_courante(ohlcv):
    """L'indice rendu doit pointer la dernière barre visible, jamais au-delà."""
    s = Strategy()
    s.prepare_for_backtest(ohlcv)
    for n in (4000, 6500, len(ohlcv)):
        frame, idx = s._frame_for(ohlcv.head(n))
        assert idx == n - 1
        assert idx < len(frame)


# ─────────────────────────────────────────────────────────────────────────────
#  Gate
# ─────────────────────────────────────────────────────────────────────────────
def test_score_holdout_est_surcharge():
    """L'implémentation par défaut reconstruit des features V4 (437 colonnes)
    alors que le modèle en attend 464 : sans surcharge, le gate arbitrerait la
    promotion sur un chiffre faux."""
    from app.engine.engine import BaseStrategyML

    assert Strategy.score_holdout.__func__ is not BaseStrategyML.score_holdout.__func__


def test_score_holdout_rend_des_auc(ohlcv, tmp_path, entrainee):
    s, _ = entrainee
    prefix = str(tmp_path / "art_1h")
    s.save_model(prefix + ".lgb")
    if not pathlib.Path(prefix + ".amp.lgb").exists():
        pytest.skip("artefact non écrit")
    out = Strategy.score_holdout(prefix, ohlcv.tail(3000))
    assert out.get("n", 0) > 0
    assert 0.0 <= out.get("auc_amp", 0.0) <= 1.0
    assert 0.0 <= out.get("auc_dir", 0.0) <= 1.0


def test_aller_retour_disque(ohlcv, tmp_path, entrainee):
    """Un modèle sauvé puis rechargé doit rendre la même décision."""
    s, params = entrainee
    prefix = str(tmp_path / "rt_1h")
    s.save_model(prefix + ".lgb")
    if not pathlib.Path(prefix + ".amp.lgb").exists():
        pytest.skip("artefact non écrit")

    recharge = Strategy()
    recharge.prepare_for_backtest(ohlcv)
    assert recharge.load_model(prefix + ".lgb")
    # L'échelle des quantiles n'est pas persistée : sans elle, la stratégie
    # refuse de décider plutôt que d'inventer un seuil.
    sig = recharge.score(ohlcv.head(7500), {NOM: {**params[NOM], "retrain_every": 10 ** 9}})
    assert sig["side"] == "none"
    assert "Échelle" in sig["reason"]
