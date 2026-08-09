"""Régressions sur les quick wins backtest QW-1 et QW-2.

Deux défauts silencieux — le pire genre : la fonctionnalité est exposée dans
l'API et dans l'UI, mais elle ne calcule rien.

- QW-1 : ``alpha_vs_bh`` valait TOUJOURS 0. ``_compute_metrics()`` tourne dans
  ``__init__``, alors que la série de prix (``_close_prices``) n'est posée qu'à
  la fin du run par ``_add_buy_and_hold()``. Sans benchmark,
  ``alpha_vs_buy_hold()`` renvoie 0 par convention.
- QW-2 : le filtre ``start_date``/``end_date`` ne filtrait RIEN. La colonne
  ``time`` du CandleStore est ``datetime[ms]`` sans timezone ; le littéral
  construit était tz-aware, polars levait un ``SchemaError``, et le
  ``except`` renvoyait le DataFrame complet. L'utilisateur croyait backtester
  sa plage et backtestait tout l'historique.
"""
import inspect
from datetime import datetime

import polars as pl
import pytest
from fastapi import HTTPException

from app.api.routes.backtest import _filter_df_by_date_range
from app.engine.backtest import Backtester, BacktestResult

# ── QW-2 : filtre par plage de dates ─────────────────────────────────────────

def _df_naif(n: int = 100):
    """OHLCV avec un ``time`` naïf, comme le Parquet du CandleStore."""
    return pl.DataFrame({
        "time": pl.datetime_range(
            pl.datetime(2024, 1, 1), pl.datetime(2024, 4, 9),
            interval="1d", eager=True, time_unit="ms",
        )[:n],
        "close": [100.0 + i for i in range(n)],
    })


def test_filtre_dates_borne_reellement_le_dataframe():
    df = _df_naif()
    out, used_start, used_end = _filter_df_by_date_range(df, "2024-02-01", "2024-02-29")

    assert len(out) < len(df), "le DataFrame doit être réduit, pas renvoyé entier"
    assert out["time"].min() >= datetime(2024, 2, 1)
    assert out["time"].max() <= datetime(2024, 3, 1)
    assert used_start.startswith("2024-02-01")
    assert used_end.startswith("2024-02-29")


def test_filtre_dates_accepte_un_offset_et_le_ramene_en_utc():
    df = _df_naif()
    out, used_start, _ = _filter_df_by_date_range(df, "2024-02-01T02:00:00+02:00", "")
    # 02:00+02:00 == 00:00 UTC → la bougie du 1er février est incluse.
    assert used_start == "2024-02-01T00:00"
    assert len(out) < len(df)


def test_filtre_dates_sans_borne_renvoie_le_df_inchange():
    df = _df_naif()
    out, used_start, used_end = _filter_df_by_date_range(df, "", "")
    assert out is df
    assert (used_start, used_end) == ("", "")


def test_filtre_dates_invalide_leve_une_400_plutot_que_de_tout_renvoyer():
    """Une date illisible doit être signalée, pas absorbée en silence."""
    with pytest.raises(HTTPException) as exc:
        _filter_df_by_date_range(_df_naif(), "pas-une-date", "")
    assert exc.value.status_code == 400


# ── QW-1 : alpha vs Buy & Hold ───────────────────────────────────────────────

def _resultat_gagnant(n_bars: int = 60):
    trades = [
        {"status": "closed", "pnl": 20.0, "fees": 0.1},
        {"status": "closed", "pnl": 15.0, "fees": 0.1},
        {"status": "closed", "pnl": -5.0, "fees": 0.1},
    ]
    equity = [1000.0 + 3 * i for i in range(60)]
    # `n_bars` = durée réelle du run. Sans elle, aucune annualisation n'est
    # possible et les métriques restent volontairement à 0.
    return BacktestResult(trades, equity, 1000.0, timeframe="1d", n_bars=n_bars)


def test_alpha_vs_bh_est_nul_tant_que_les_prix_sont_inconnus():
    """Contrat : sans série de prix, pas de benchmark donc pas d'alpha."""
    res = _resultat_gagnant()
    assert res.alpha_vs_bh == 0.0


def test_sans_n_bars_aucune_metrique_annualisee_n_est_inventee():
    """La durée est fournie par l'appelant ; à défaut, on ne devine pas.

    Un CAGR calculé sur une durée supposée est un chiffre faux d'apparence
    crédible — c'est exactement ce qui produisait 3 809 %/an.
    """
    res = _resultat_gagnant(n_bars=0)
    assert res._years() is None
    assert res.cagr == 0.0
    assert res.calmar == 0.0


def test_alpha_vs_bh_est_recalcule_une_fois_les_prix_connus():
    """Régression : `_add_buy_and_hold` doit relancer les métriques étendues.

    `_add_buy_and_hold` n'utilise pas `self` — on l'appelle en méthode non liée
    pour éviter de monter un Engine complet dans un test unitaire.
    """
    res = _resultat_gagnant()
    df = pl.DataFrame({
        "time": pl.datetime_range(
            pl.datetime(2024, 1, 1), pl.datetime(2024, 3, 10),
            interval="1d", eager=True, time_unit="ms",
        )[:70],
        "close": [100.0 + 0.5 * i for i in range(70)],
    })

    Backtester._add_buy_and_hold(None, res, df, warmup=10)

    assert res._close_prices, "la série de close doit être stockée sur le résultat"
    assert res.alpha_vs_bh != 0.0, "l'alpha doit être recalculé avec le benchmark"
    assert res.to_dict()["alpha_vs_bh"] == pytest.approx(res.alpha_vs_bh, abs=1e-4)


# ── QW-6 : cohérence de la clé de slot du risk gate ──────────────────────────

def test_le_gate_utilise_la_meme_cle_de_slot_a_l_entree_et_a_la_cloture():
    """Régression : `_try_enter` et `_close_at` visaient deux slots différents.

    Le signal porte le nom de la stratégie sous `name` ; c'est de là que vient
    le `"strategy"` de la position. `_try_enter` lisait `signal["strategy"]`,
    absent, donc toujours "" — d'où un slot fantôme « ::tf::symbole » sur
    lequel les entrées étaient contrôlées, pendant que `_close_at` enregistrait
    les pertes sur « breakout::tf::symbole ». Le slot contrôlé n'accumulait
    jamais rien : pertes consécutives, DD journalier par slot et trades/jour ne
    pouvaient pas se déclencher.

    Le diagnostic d'un run réel montrait les deux clés côte à côte, l'une avec
    0 trade (celle qui gardait la porte) et l'autre avec l'historique complet.
    """
    import app.engine.backtest as bt_mod
    src = inspect.getsource(bt_mod.Backtester._try_enter)
    assert 'signal.get("name")' in src, (
        "_try_enter doit lire le nom sous `name`, comme la construction du trade"
    )

    # Les deux chemins doivent produire une clé identique pour un même bot.
    from app.core.bot_identity import build_slot_key
    signal = {"name": "breakout", "side": "long"}
    position = {"strategy": signal.get("name", "")}
    entree = build_slot_key(signal.get("name") or signal.get("strategy") or "", "1d", "BTC/USDC")
    cloture = build_slot_key(position.get("strategy", ""), "1d", "BTC/USDC")
    assert entree == cloture
    assert not entree.startswith("::"), "un slot sans nom de stratégie est un slot fantôme"


def test_sharpe_annualise_a_la_cadence_des_trades_et_non_des_bougies():
    """Le Sharpe historique souffrait du même défaut d'échelle que le CAGR.

    `BacktestResult._compute_metrics` annualisait des rendements PAR TRADE avec
    sqrt(bougies par an) : sur un run de 8 trades en 5,5 ans, cela supposait
    365 observations annuelles là où le bot en produit 1,5, et affichait un
    Sharpe de 9,5 — du même ordre d'invraisemblance que le Calmar de 3 961.
    """
    trades = [{"status": "closed", "pnl": p, "fees": 0.1}
              for p in (10, -5, 35, -10, 30, -10, 30, 14.6)]
    equity = [1000.0, 1010, 1005, 1040, 1030, 1060, 1050, 1080, 1094.6]

    # 2 000 bougies journalières ≈ 5,48 ans → ~1,5 trade/an.
    res = BacktestResult(trades, equity, 1000.0, timeframe="1d", n_bars=2000)
    # Même série, mais annualisée comme avant (365 obs/an) : le rapport des
    # deux Sharpe vaut sqrt(365 / 1,46) ≈ 15.
    gonfle = BacktestResult(trades, equity, 1000.0, timeframe="1d", n_bars=8)

    assert abs(res.sharpe) < abs(gonfle.sharpe)
    assert abs(gonfle.sharpe / res.sharpe) == pytest.approx(15.0, abs=1.0)
    assert abs(res.sharpe) < 3.0, f"Sharpe encore implausible : {res.sharpe}"
