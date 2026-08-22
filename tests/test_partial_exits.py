"""L1 (§29 §30) — sorties partielles et trailing structurel.

Le moteur ne savait fermer qu'en entier. Les deux spécifications SMC/ICT sont
bâties sur `TP1 25 % → TP2 25 % → runner 50 %` : sans jambes, aucune n'est
implémentable fidèlement, et le runner ne peut être approximé que par un
trailing sur la position entière — ce qui change le profil de risque.

Les tests portent sur trois propriétés :

- **conservation** — la somme des jambes plus le reliquat redonne le PnL du
  trade, et l'équité finale reste la somme des encaissements ;
- **neutralité** — une stratégie qui ne demande rien garde exactement le
  comportement tout-ou-rien ;
- **priorité** — le stop l'emporte toujours sur une cible partielle touchée
  dans la même barre, comme pour le TP plein.
"""
import datetime as dt

import numpy as np
import polars as pl
import pytest

from app.core.execution import plan_partial_targets
from app.core.trailing import StructureTrailingStop, TrailingStopManager
from app.engine.backtest import Backtester
from app.engine.engine import BaseStrategy, Engine

# ── plan_partial_targets : contrat pur ──────────────────────────────────────

def test_les_cibles_en_R_sont_traduites_en_prix():
    sig = {"side": "long", "exits": [{"r": 1.0, "fraction": 0.25},
                                     {"r": 2.0, "fraction": 0.25}]}
    cibles = plan_partial_targets(sig, entry=100.0, stop=90.0)
    assert [c["price"] for c in cibles] == [110.0, 120.0]
    assert [c["fraction"] for c in cibles] == [0.25, 0.25]


def test_le_short_projette_du_bon_cote():
    sig = {"side": "short", "exits": [{"r": 1.5, "fraction": 0.5}]}
    assert plan_partial_targets(sig, entry=100.0, stop=110.0)[0]["price"] == 85.0


def test_une_cible_du_mauvais_cote_est_ignoree():
    """Elle serait touchée dès la première barre : c'est une erreur de
    spécification, pas une sortie."""
    sig = {"side": "long", "exits": [{"price": 95.0, "fraction": 0.5}]}
    assert plan_partial_targets(sig, entry=100.0, stop=90.0) == []


def test_les_fractions_ne_depassent_jamais_la_position():
    sig = {"side": "long", "exits": [{"r": 1.0, "fraction": 0.7},
                                     {"r": 2.0, "fraction": 0.7}]}
    cibles = plan_partial_targets(sig, entry=100.0, stop=90.0)
    assert sum(c["fraction"] for c in cibles) <= 1.0


def test_sans_exits_aucune_cible():
    assert plan_partial_targets({"side": "long"}, 100.0, 90.0) == []


# ── Trailing structurel (§30) ───────────────────────────────────────────────

def test_le_trailing_structurel_se_cale_sous_le_dernier_pivot():
    st = StructureTrailingStop(mult=2.5, buffer_atr=0.2, pivot_k=2)
    st.init(entry=100.0, atr=1.0, side="long")
    # Creux en V : pivot bas confirmé à 104, deux barres plus hautes de chaque
    # côté. Une série monotone n'en contient aucun — d'où le repli sur l'ATR.
    lows = [110, 108, 104, 106, 109, 111, 113]
    stop = st.update(current_price=115.0, current_stop=97.5, atr=1.0,
                     side="long", entry=100.0, bars_held=10,
                     recent_lows=lows)[0]
    assert stop == pytest.approx(104 - 0.2, abs=1e-6), (
        "le stop doit se caler sous le dernier pivot bas confirmé de la fenêtre"
    )


def test_sans_pivot_confirme_le_trailing_structurel_se_replie_sur_l_atr():
    """Sinon le stop resterait à son niveau initial sur toute une impulsion —
    c'est exactement le cas d'une série qui monte sans respirer."""
    st = StructureTrailingStop(mult=2.5, buffer_atr=0.2, pivot_k=2)
    st.init(entry=100.0, atr=1.0, side="long")
    stop = st.update(current_price=115.0, current_stop=97.5, atr=1.0,
                     side="long", entry=100.0, bars_held=10,
                     recent_lows=[100, 102, 104, 106, 108, 110, 112])[0]
    assert stop == pytest.approx(115.0 - 2.5, abs=1e-6)


def test_le_trailing_structurel_ne_recule_jamais():
    st = StructureTrailingStop(mult=2.5, buffer_atr=0.2, pivot_k=2)
    st.init(entry=100.0, atr=1.0, side="long")
    haut = st.update(120.0, 110.0, 1.0, "long", 100.0, 10,
                     recent_lows=[108, 110, 112, 114, 116])[0]
    bas = st.update(118.0, haut, 1.0, "long", 100.0, 11,
                    recent_lows=[100, 101, 102, 103, 104])[0]
    assert bas >= haut


def test_le_mode_structure_est_selectionnable_par_signal():
    tsm = TrailingStopManager(mode="structure", struct_buffer_atr=0.3)
    tsm.initial_stop(100.0, 1.0, "long")
    assert tsm.phase_name == "structure"


# ── Intégration backtest ────────────────────────────────────────────────────

def _ohlcv_haussier(n: int = 400) -> pl.DataFrame:
    """Série qui monte régulièrement : les cibles partielles sont atteintes,
    le stop ne l'est pas. Déterministe — un test de conservation comptable ne
    doit pas dépendre d'un tirage."""
    start = dt.datetime(2021, 1, 1)
    close = np.array([100.0 * (1.004 ** i) for i in range(n)])
    open_ = np.concatenate([[100.0], close[:-1]])
    return pl.DataFrame({
        "time": [start + dt.timedelta(hours=i) for i in range(n)],
        "open": open_, "high": np.maximum(open_, close) * 1.002,
        "low": np.minimum(open_, close) * 0.998, "close": close,
        "volume": np.full(n, 500.0),
    })


def _cfg(nom: str) -> dict:
    return {
        "trading": {"capital": 10000, "risk_per_trade": 0.01, "timeframe": "1h",
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.5, "borrow_rate_daily": 0.0},
        "backtest": {"spread_pct": 0.0005, "atr_stop_mult": 2.0, "trail_wide": 2.5,
                     "trail_normal": 2.0, "trail_lock": 1.5, "trail_tight": 1.0,
                     "grace_bars": 4, "breakeven_r": 1.2, "lock_r": 2.5,
                     "tight_r": 4.0, "lock_ratio": 0.6, "use_swing": False,
                     "max_notional_pct": 0.2},
        "strategies": {"enabled": [nom]},
        "strategy_params": {nom: {}},
    }


class _AvecJambes(BaseStrategy):
    name = "avec_jambes"

    def min_bars_required(self, params: dict = None) -> int:
        return 60

    def score(self, df, params=None, df_htf=None, symbol: str = "") -> dict:
        n = len(df)
        if n != 300:
            return {"name": self.name, "side": "none", "score": 0.0}
        prix = float(df["close"][-1])
        return {"name": self.name, "side": "long", "score": 0.9,
                "stop_hint": prix * 0.98, "disable_trailing": True,
                "exits": [{"r": 0.5, "fraction": 0.25, "reason": "tp1"},
                          {"r": 1.0, "fraction": 0.25, "reason": "tp2"}],
                "exit_after_bars": 60}


class _SansJambes(_AvecJambes):
    """Même signal, sans `exits` — doit reproduire le tout-ou-rien."""

    name = "sans_jambes"

    def score(self, df, params=None, df_htf=None, symbol: str = "") -> dict:
        sig = super().score(df, params, df_htf, symbol)
        sig.pop("exits", None)
        sig["name"] = self.name
        return sig


def _run(strat):
    eng = Engine()
    eng.register(strat, silent=True)
    return Backtester(eng, _cfg(strat.name), ml_mode="inline").run(
        _ohlcv_haussier(), "BTC/USDC", "1h")


def test_les_jambes_sont_realisees_et_tracees():
    res = _run(_AvecJambes())
    assert res.trades, "aucun trade"
    t = res.trades[0]
    assert len(t["exits"]) == 2, f"attendu 2 jambes, obtenu {t['exits']}"
    assert [e["reason"] for e in t["exits"]] == ["tp1", "tp2"]
    assert all(e["pnl"] > 0 for e in t["exits"]), "jambes sorties en gain attendu"


def test_la_taille_restante_est_le_runner():
    res = _run(_AvecJambes())
    t = res.trades[0]
    sortie = sum(e["size"] for e in t["exits"])
    assert sortie + t["size"] == pytest.approx(t["size_initial"], rel=1e-4)
    # 25 % + 25 % sortis ⇒ il reste la moitié.
    assert t["size"] == pytest.approx(t["size_initial"] * 0.5, rel=0.02)


def test_le_pnl_du_trade_agrege_les_jambes_et_le_reliquat():
    res = _run(_AvecJambes())
    t = res.trades[0]
    jambes = sum(e["pnl"] for e in t["exits"])
    assert t["pnl"] > jambes, "le PnL total doit inclure le reliquat"


def test_l_equite_finale_reste_coherente():
    """La courbe d'équité garde un point par TRADE, pas par jambe : en changer
    la cadence modifierait l'annualisation du Sharpe de tous les backtests."""
    res = _run(_AvecJambes())
    assert len(res.equity_curve) == len(res.trades) + 1
    assert res.net_profit == pytest.approx(
        res.final_equity - res.initial_capital, abs=1e-6)


def test_total_pnl_egale_net_profit_avec_et_sans_jambes():
    """FIN-01 : l'invariant revendiqué par to_dict (total_pnl == net_profit).

    Le cas du PYRAMIDAGE est ajouté plus bas (`_JambesEtPyramide`) : c'est lui
    qui manquait, et c'est par lui que FIN-02 passait — les frais d'entrée
    d'un scale-in n'étaient retranchés nulle part.
    """
    sans = _run(_SansJambes())
    assert sans.total_pnl == pytest.approx(sans.net_profit, abs=1e-4)
    avec = _run(_AvecJambes())
    assert avec.total_pnl == pytest.approx(avec.net_profit, abs=1e-4)


def test_le_stop_passe_au_point_mort_apres_la_premiere_jambe():
    res = _run(_AvecJambes())
    t = res.trades[0]
    assert t["stop"] >= t["entry"], (
        "après TP1 le stop doit couvrir au moins le point mort frais compris"
    )


def test_sans_exits_le_comportement_est_inchange():
    res = _run(_SansJambes())
    assert res.trades
    t = res.trades[0]
    assert t["exits"] == []
    assert t["size"] == pytest.approx(t["size_initial"], rel=1e-9)


# ── TEST-01 — invariants comptables (jambes ET pyramidage) ──────────────────
# Ces deux tests échouaient avant les correctifs FIN-01 / FIN-02 : le champ
# `fees` était écrasé par `_close_at` (frais des jambes et des pyramidages
# perdus), et `entry_fees` n'accumulait pas les frais d'entrée des pyramidages
# (somme des PnL ≠ courbe d'équité). Une égalité, pas une inégalité : c'est
# ce qui manquait à `test_le_pnl_du_trade_agrege_les_jambes_et_le_reliquat`.


class _JambesEtPyramide(_AvecJambes):
    """Jambes partielles ET pyramidage : le seul cas qui exerce les deux
    accumulateurs de frais en même temps."""

    name = "jambes_et_pyramide"

    def score(self, df, params=None, df_htf=None, symbol: str = "") -> dict:
        sig = super().score(df, params, df_htf, symbol)
        sig["name"] = self.name
        return sig

    def check_scale_in(self, df, position: dict, params=None):
        if position.get("scale_ins", 0) >= 2:
            return None
        return {"size_factor": 0.5, "reason": "pyramide"}


def _run_en_mesurant_les_frais(strat):
    """Rejoue un backtest en totalisant les frais RÉELLEMENT prélevés.

    Les deux seules portes de sortie d'un frais : `_close_pnl` (sorties) et
    `Backtester._fees` (entrée et pyramidages). On les instrumente plutôt que
    de reconstruire le total — une reconstruction referait l'erreur qu'on teste.

    DETTE-04c : la clôture vit dans `position_close`. C'est le module qui
    APPELLE `_close_pnl` qu'il faut instrumenter, pas celui qui le ré-exporte.
    """
    import app.engine.position_close as _pl

    preleves = {"total": 0.0}
    _close_pnl_orig = _pl._close_pnl
    _fees_orig = Backtester._fees

    def _spy_close_pnl(**kw):
        pnl, fees, borrow = _close_pnl_orig(**kw)
        preleves["total"] += fees
        return pnl, fees, borrow

    def _spy_fees(self, price, size, maker=False, side="long", is_entry=True):
        f = _fees_orig(self, price, size, maker=maker, side=side,
                       is_entry=is_entry)
        preleves["total"] += f
        return f

    _pl._close_pnl = _spy_close_pnl
    Backtester._fees = _spy_fees
    try:
        res = _run(strat)
    finally:
        _pl._close_pnl = _close_pnl_orig
        Backtester._fees = _fees_orig
    return res, preleves["total"]


@pytest.mark.parametrize("strat", [_AvecJambes, _JambesEtPyramide])
def test_les_frais_journalises_egalent_les_frais_preleves(strat):
    """FIN-01 : `_close_at` ajoute les frais de sortie au cumul, il ne l'écrase
    pas. Sans cela, jambes et pyramidages disparaissent du coût affiché."""
    res, preleves = _run_en_mesurant_les_frais(strat())
    assert res.trades, "aucun trade"
    journalises = sum(t["fees"] for t in res.trades)
    assert journalises == pytest.approx(preleves, abs=1e-6), (
        f"{journalises:.6f} journalisés contre {preleves:.6f} prélevés — "
        f"écart {journalises - preleves:+.6f}"
    )


@pytest.mark.parametrize("strat", [_SansJambes, _AvecJambes, _JambesEtPyramide])
def test_la_somme_des_pnl_egale_la_variation_de_capital(strat):
    """FIN-02 : les frais d'entrée des pyramidages sont débités du capital ;
    ils doivent donc être retranchés du PnL journalisé, sinon la somme des
    trades et la courbe d'équité racontent deux histoires différentes."""
    res = _run(strat())
    assert res.trades, "aucun trade"
    somme_pnl = sum(t["pnl"] for t in res.trades)
    variation = res.equity_curve[-1] - res.equity_curve[0]
    assert somme_pnl == pytest.approx(variation, abs=1e-4), (
        f"somme des PnL {somme_pnl:.6f} contre variation de capital "
        f"{variation:.6f} — écart {somme_pnl - variation:+.6f}"
    )


@pytest.mark.parametrize("strat", [_SansJambes, _AvecJambes, _JambesEtPyramide])
def test_total_pnl_egale_net_profit_y_compris_avec_pyramidage(strat):
    """Le même invariant, exposé cette fois par `BacktestResult`.

    `net_profit` est la variation d'équité, `total_pnl` la somme des PnL de
    trades : ils doivent coïncider. Le commentaire de `backtest_result` a
    longtemps annoncé un écart égal aux frais d'entrée — mesuré, il vaut ~2e-5.
    """
    res = _run(strat())
    assert res.trades
    assert res.total_pnl == pytest.approx(res.net_profit, abs=1e-4), (
        f"total_pnl={res.total_pnl:.6f} net_profit={res.net_profit:.6f} "
        f"écart={res.total_pnl - res.net_profit:+.6f}"
    )
