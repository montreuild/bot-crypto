"""Découpage des statistiques par setup et par module (§65).

Une stratégie SMC agrège plusieurs familles de setups dont rien ne garantit
qu'elles partagent le même edge. Sans découpage, un module rentable et un
module perdant se compensent dans un chiffre global qui ne dit rien — et le
seul moyen de le savoir est une ablation manuelle, refaite à chaque fois.

Les tests portent sur deux propriétés :

- **conservation** — la somme des groupes doit redonner le total, sinon des
  trades disparaissent du découpage sans que rien ne le signale ;
- **généricité** — le moteur ne connaît aucun nom de setup. Toute stratégie qui
  pose `setup` / `module` sur son signal obtient le découpage, et celles qui
  n'en posent pas gardent exactement le comportement d'avant.
"""
import datetime as dt

import numpy as np
import polars as pl
import pytest

from app.engine.backtest import Backtester, BacktestResult
from app.engine.engine import BaseStrategy, Engine


def _ohlcv(n: int = 600, seed: int = 3) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    start = dt.datetime(2021, 1, 1)
    rets = rng.normal(0.0004, 0.012, n)
    close = 100 * np.cumprod(1 + rets)
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


class _DeuxModules(BaseStrategy):
    """Alterne deux setups appartenant à deux modules différents."""

    name = "deux_modules"

    def min_bars_required(self, params: dict = None) -> int:
        return 60

    def score(self, df, params=None, df_htf=None, symbol: str = "") -> dict:
        n = len(df)
        if n < 60 or n % 40 != 0:
            return {"name": self.name, "side": "none", "score": 0.0}
        pair = (n // 40) % 2 == 0
        return {
            "name": self.name, "side": "long" if pair else "short", "score": 0.8,
            "setup": "ALPHA" if pair else "BETA",
            "module": "MODULE_A" if pair else "MODULE_B",
            "exit_after_bars": 6,
        }


class _SansModule(BaseStrategy):
    """Ne pose ni `setup` ni `module` — le cas de toutes les stratégies
    existantes, dont le comportement ne doit pas changer."""

    name = "sans_module"

    def min_bars_required(self, params: dict = None) -> int:
        return 60

    def score(self, df, params=None, df_htf=None, symbol: str = "") -> dict:
        n = len(df)
        if n < 60 or n % 40 != 0:
            return {"name": self.name, "side": "none", "score": 0.0}
        return {"name": self.name, "side": "long", "score": 0.8,
                "exit_after_bars": 6}


def _run(strat) -> BacktestResult:
    eng = Engine()
    eng.register(strat, silent=True)
    return Backtester(eng, _cfg(strat.name), ml_mode="inline").run(
        _ohlcv(), "BTC/USDC", "1h")


@pytest.fixture(scope="module")
def resultat():
    res = _run(_DeuxModules())
    if not res.trades:
        pytest.skip("aucun trade produit par la stratégie de test")
    return res


def test_les_deux_axes_sont_produits(resultat):
    assert set(resultat.by_module) == {"MODULE_A", "MODULE_B"}
    assert set(resultat.by_setup) == {"ALPHA", "BETA"}


def test_la_somme_des_modules_redonne_le_total(resultat):
    """Un trade qui tomberait hors de tout groupe disparaîtrait du découpage
    sans qu'aucune erreur ne le dise."""
    n = sum(v["total_trades"] for v in resultat.by_module.values())
    pnl = sum(v["total_pnl"] for v in resultat.by_module.values())
    assert n == len(resultat.trades)
    # Tolérance à 1e-3 et non machine : `_group_metrics` ARRONDIT le PnL de
    # chaque groupe à 4 décimales avant publication, donc leur somme ne peut
    # pas égaler le total au bit près. Exiger mieux testerait l'arrondi, pas la
    # conservation des trades.
    assert pnl == pytest.approx(resultat.total_pnl, abs=1e-3)


def test_la_somme_des_setups_redonne_le_total(resultat):
    n = sum(v["total_trades"] for v in resultat.by_setup.values())
    assert n == len(resultat.trades)


def test_les_groupes_portent_les_memes_metriques_que_by_strategy(resultat):
    """Le calcul est partagé (`_group_metrics`) : trois copies auraient fini
    par diverger, et un profit factor calculé différemment selon l'axe ne
    serait comparable à rien."""
    attendus = {"total_trades", "total_pnl", "win_rate", "profit_factor",
                "expectancy", "max_drawdown", "sharpe", "avg_win", "avg_loss",
                "equity_curve", "final_equity"}
    for axe in (resultat.by_module, resultat.by_setup, resultat.by_strategy):
        for v in axe.values():
            assert attendus <= set(v), f"clés manquantes : {attendus - set(v)}"


def test_une_strategie_sans_module_ne_change_pas_de_comportement():
    """Le découpage doit être invisible pour les stratégies qui n'en posent
    pas — sinon on aurait modifié toutes les autres du dépôt."""
    res = _run(_SansModule())
    assert res.by_module == {}
    assert res.by_setup == {}
    assert res.by_strategy, "by_strategy doit rester peuplé"


def test_le_trade_transporte_le_module(resultat):
    for t in resultat.trades:
        assert t.get("module") in ("MODULE_A", "MODULE_B")
        assert t.get("setup") in ("ALPHA", "BETA")


def test_expose_dans_le_dict_de_sortie(resultat):
    d = resultat.to_dict()
    assert "by_module" in d and "by_setup" in d
    assert set(d["by_module"]) == set(resultat.by_module)


# ─────────────────────────────────────────────────────────────────────────────
#  Cartographie des setups de smart_money
# ─────────────────────────────────────────────────────────────────────────────
def test_chaque_setup_de_smart_money_a_un_module():
    """Un setup absent de la table tomberait dans « AUTRE » et fausserait la
    lecture par module sans rien casser."""
    from app.strategies.smart_money_signals import (
        _SETUP_CHECKERS,
        SETUP_MODULES,
        setup_module,
    )

    manquants = [s for s in _SETUP_CHECKERS if s not in SETUP_MODULES]
    assert not manquants, f"setups sans module déclaré : {manquants}"
    assert setup_module("SWEEP_REVERSAL") == "SMC_CORE"
    assert setup_module("BREAKER_RETEST") == "ICT_ADVANCED"
    assert setup_module("CALENDAR_SWEEP") == "ICT_SESSION"
    assert setup_module(None) is None
    assert setup_module("INEXISTANT") == "AUTRE"


def test_silver_bullet_reclasse_sans_changer_les_decisions():
    """§31 — le Silver Bullet doit être mesurable à part, sans que l'activer
    modifie les trades pris.

    Il existait déjà comme bonus TRANSVERSE (`sb_bonus` / `sb_filter`) : ses
    trades étaient comptés avec le SMC Core et son apport propre invisible. Le
    reclassement les en sort — mais il ne doit rien décider. Un changement du
    nombre de trades signalerait que la comptabilité s'est mise à influencer la
    stratégie.
    """
    from app.strategies.smart_money_signals import (
        MODULE_SILVER_BULLET,
        _score_setup,
    )

    cands = [{"side": "long", "score": 0.9, "setup": "OB_RETEST",
              "module": "SMC_CORE"}]
    p = {"sb_bonus": True, "min_score": 0.0}

    hors = _score_setup([dict(cands[0])], None, 0, p, in_sb=False)
    dedans = _score_setup([dict(cands[0])], None, 0, p, in_sb=True)
    assert hors["module"] == "SMC_CORE"
    assert dedans["module"] == MODULE_SILVER_BULLET
    # Le reclassement ne touche NI le score NI le sens.
    assert hors["score"] == dedans["score"]
    assert hors["side"] == dedans["side"]


def test_silver_bullet_desactive_ne_reclasse_rien():
    """Les deux drapeaux valent False par défaut : aucune analyse existante ne
    doit changer de découpage."""
    from app.strategies.smart_money_signals import _score_setup

    cand = {"side": "long", "score": 0.9, "setup": "OB_RETEST",
            "module": "SMC_CORE"}
    out = _score_setup([dict(cand)], None, 0, {"min_score": 0.0}, in_sb=True)
    assert out["module"] == "SMC_CORE"
