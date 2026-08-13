"""L0 — journal de trade et ventilation des coûts.

Deux questions n'avaient aucune réponse chiffrée dans le dépôt :

- **où meurent les positions ?** — `by_exit_reason` la donne, et sans elle on
  ne peut pas savoir si le problème est le stop, la cible ou la durée ;
- **où va l'argent ?** — `fees` écrasait les frais d'entrée par ceux de sortie,
  donc `total_fees` en sous-déclarait un côté. Le PnL et l'équité étaient justes,
  le report ne l'était pas.

Les tests verrouillent la conservation comptable : brut − coûts = net, et la
somme des groupes redonne le total.
"""
import datetime as dt

import numpy as np
import polars as pl
import pytest

from app.engine.backtest import Backtester, BacktestResult
from app.engine.engine import BaseStrategy, Engine


def _ohlcv(n: int = 600, seed: int = 5) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    start = dt.datetime(2021, 1, 1)
    close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.012, n))
    open_ = np.concatenate([[100.0], close[:-1]])
    return pl.DataFrame({
        "time": [start + dt.timedelta(hours=i) for i in range(n)],
        "open": open_,
        "high": np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n))),
        "low": np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n))),
        "close": close,
        "volume": rng.uniform(100, 1000, n),
    })


def _cfg(nom: str) -> dict:
    return {
        "trading": {"capital": 10000, "risk_per_trade": 0.01, "timeframe": "1h",
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.5, "borrow_rate_daily": 0.0002},
        "backtest": {"spread_pct": 0.0005, "atr_stop_mult": 2.0, "trail_wide": 2.5,
                     "trail_normal": 2.0, "trail_lock": 1.5, "trail_tight": 1.0,
                     "grace_bars": 4, "breakeven_r": 1.2, "lock_r": 2.5,
                     "tight_r": 4.0, "lock_ratio": 0.6, "use_swing": False,
                     "max_notional_pct": 0.2},
        "strategies": {"enabled": [nom]},
        "strategy_params": {nom: {}},
    }


class _Sorties(BaseStrategy):
    """Alterne sortie sur TP fixe et sortie sur expiration, pour peupler au
    moins deux `exit_reason` distincts."""

    name = "sorties_variees"

    def min_bars_required(self, params: dict = None) -> int:
        return 60

    def score(self, df, params=None, df_htf=None, symbol: str = "") -> dict:
        n = len(df)
        if n < 60 or n % 40 != 0:
            return {"name": self.name, "side": "none", "score": 0.0}
        prix = float(df["close"][-1])
        sur_tp = (n // 40) % 2 == 0
        sig = {"name": self.name, "side": "long", "score": 0.8,
               "setup": "TEST", "module": "MODULE_TEST",
               "htf_bias": "haussier", "session": "london",
               "gross_rr": 2.0}
        if sur_tp:
            sig["tp_hint"] = prix * 1.004
            sig["stop_hint"] = prix * 0.97
        else:
            sig["exit_after_bars"] = 6
        return sig


@pytest.fixture(scope="module")
def resultat() -> BacktestResult:
    eng = Engine()
    eng.register(_Sorties(), silent=True)
    res = Backtester(eng, _cfg("sorties_variees"), ml_mode="inline").run(
        _ohlcv(), "BTC/USDC", "1h")
    if not res.trades:
        pytest.skip("aucun trade produit par la stratégie de test")
    return res


def test_by_exit_reason_est_produit_et_conserve_les_trades(resultat):
    assert resultat.by_exit_reason, "aucune raison de sortie recensée"
    n = sum(v["total_trades"] for v in resultat.by_exit_reason.values())
    assert n == len(resultat.trades)


def test_chaque_trade_porte_une_raison_de_sortie_connue(resultat):
    for t in resultat.trades:
        assert t.get("exit_reason"), f"trade {t['id']} sans exit_reason"
        assert t["exit_reason"] != "inconnu"


def test_les_frais_agregent_l_entree_et_la_sortie(resultat):
    """`close_pnl` ne rend que les frais de sortie : les écrire tels quels
    écrasait ceux d'entrée, déjà portés par la position."""
    for t in resultat.trades:
        assert t["fees"] >= t["entry_fees"] > 0, (
            f"trade {t['id']} : fees={t['fees']} < entry_fees={t['entry_fees']} — "
            "les frais d'entrée ont été écrasés"
        )


def test_brut_moins_couts_redonne_le_net(resultat):
    """Conservation comptable par trade. `pnl` ne retranche pas les frais
    d'entrée (prélevés sur le capital à l'ouverture) : c'est exactement l'écart
    attendu, et le test l'exprime plutôt que de le masquer."""
    for t in resultat.trades:
        attendu = (t["gross_pnl"] - t["fees"] - t["borrow_cost"]
                   + t["entry_fees"])
        assert t["pnl"] == pytest.approx(attendu, abs=1e-4), (
            f"trade {t['id']} : brut {t['gross_pnl']} − coûts ≠ net {t['pnl']}"
        )


def test_net_profit_est_la_vraie_variation_d_equite(resultat):
    assert resultat.net_profit == pytest.approx(
        resultat.final_equity - resultat.initial_capital, abs=1e-6)


def test_l_ecart_total_pnl_net_profit_vaut_les_frais_d_entree(resultat):
    """Le seul écart légitime entre les deux agrégats. S'il change, c'est qu'un
    coût est compté deux fois ou pas du tout."""
    assert resultat.total_pnl - resultat.net_profit == pytest.approx(
        resultat.total_entry_fees, abs=1e-3)


def test_le_slippage_est_isole_et_non_nul(resultat):
    """Le spread est un coût réel, embarqué dans le prix d'exécution. Il était
    reporté à 0 faute de champ dédié."""
    assert resultat.total_slippage_cost > 0
    assert all(t["slippage_cost"] >= 0 for t in resultat.trades)


def test_les_champs_de_contexte_du_signal_sont_journalises(resultat):
    """§99 — une stratégie qui les pose les retrouve dans le trade ; celles qui
    ne les posent pas laissent des None sans rien casser."""
    t = resultat.trades[0]
    assert t["htf_bias"] == "haussier"
    assert t["session"] == "london"
    assert t["gross_rr"] == 2.0
    for absent in ("structure_state", "sequence_type", "net_rr"):
        assert absent in t
