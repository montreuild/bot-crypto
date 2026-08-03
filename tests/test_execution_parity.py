"""Parité backtest ↔ live : même trade ⇒ même PnL net.

Les deux chemins (Backtester._close_at et PositionCloseMixin._close_position)
consomment désormais les mêmes formules (app/core/execution.py). Ces tests
verrouillent :
  1. les formules elles-mêmes (frais, emprunt composé, PnL net) ;
  2. la parité de bout en bout : un trade identique (mêmes prix d'entrée/
     sortie, taille, frais, durée) clôturé par le Backtester et par le
     LiveTrader paper produit le même PnL net.
"""
import time
import types
from unittest.mock import MagicMock

import pytest

from app.core.execution import borrow_cost, close_pnl, gross_pnl, net_pnl, risk_position_size, trade_fees

# ── 1. Formules unitaires ───────────────────────────────────────────────────

def test_trade_fees():
    assert trade_fees(100.0, 2.0, 0.001) == pytest.approx(0.2)


def test_borrow_cost_compound():
    # 24 h à 0.072 %/jour en 3 périodes : (1 + 0.00024)^3 - 1
    expected = 1000 * ((1 + 0.00072 / 3) ** 3 - 1)
    assert borrow_cost(1000, 0.00072, 24, 3) == pytest.approx(expected)
    assert borrow_cost(1000, 0.00072, 0, 3) == 0.0
    assert borrow_cost(0, 0.00072, 24, 3) == 0.0


def test_gross_and_net_pnl():
    assert gross_pnl("long", 100, 110, 2) == pytest.approx(20)
    assert gross_pnl("short", 100, 110, 2) == pytest.approx(-20)
    assert net_pnl("long", 100, 110, 2, exit_fees=0.22, borrow=0.05) == \
        pytest.approx(20 - 0.22 - 0.05)


def test_close_pnl_combines_all():
    pnl, fees, borrow = close_pnl("long", 100, 110, 2, notional=200,
                                  fee_rate=0.001, daily_rate=0.00072,
                                  hours_held=24, periods_per_day=3)
    assert fees == pytest.approx(110 * 2 * 0.001)
    assert borrow == pytest.approx(200 * ((1 + 0.00072 / 3) ** 3 - 1))
    assert pnl == pytest.approx(20 - fees - borrow)


def test_risk_position_size_caps_notional():
    size, notional = risk_position_size(10_000, 0.01, entry=100, stop=98,
                                        max_notional_pct=0.30)
    assert size == pytest.approx(3000 / 100)      # cap 30 % du capital
    assert notional == pytest.approx(3000)


# ── 2. Parité de bout en bout backtest ↔ live paper ─────────────────────────

ENTRY, EXIT, SIZE = 100.0, 110.0, 2.0
FEE_RATE, BORROW_RATE, PERIODS = 0.001, 0.00072, 3
HOURS_HELD = 4.0


def _backtest_close_pnl() -> float:
    """Clôture d'un trade connu via Backtester._close_at."""
    from app.engine.backtest import Backtester
    from app.engine.engine import Engine

    cfg = {
        "trading": {"capital": 1000, "risk_per_trade": 0.01, "timeframe": "1h",
                    "taker_fee": FEE_RATE, "maker_fee": FEE_RATE,
                    "borrow_rate_daily": BORROW_RATE,
                    "borrow_periods_per_day": PERIODS},
        "backtest": {"spread_pct": 0.0},
        "strategy_params": {},
    }
    bt = Backtester(Engine(), cfg)
    position = {
        "side": "long", "entry": ENTRY, "size": SIZE,
        "notional": ENTRY * SIZE, "bar": 0, "stop": 95.0,
        "_stop_trail": [], "_trailing": None,
        "mae": 0.0, "mfe": 0.0,
    }
    ctx = types.SimpleNamespace(
        df=None, timeframe="1h", capital=1000.0,
        trades=[], equity_curve=[], timestamps=[],
    )
    # df minimal pour le timestamp de sortie
    from datetime import datetime, timedelta

    import polars as pl
    t0 = datetime(2024, 1, 1)
    ctx.df = pl.DataFrame({"time": [t0 + timedelta(hours=i) for i in range(5)],
                           "close": [ENTRY] * 5})
    pnl = bt._close_at(ctx, position, int(HOURS_HELD), EXIT,
                       "parity_test", maker=False)
    return pnl


def _live_close_pnl(margin: bool = True) -> float:
    """Clôture du même trade via PositionCloseMixin._close_position (paper).

    ``margin`` pilote la venue résolue pour le trade (S11) : c'est elle, et non
    plus ``trading.borrow_rate_daily`` seul, qui décide si l'emprunt est
    facturé. ``True`` = venue margin (cas de référence pour la parité, le
    backtest chargeant l'emprunt) ; ``False`` = venue spot ⇒ emprunt nul.
    """
    from app.live.position_close_mixin import PositionCloseMixin
    from app.live.position_open_mixin import PositionOpenMixin

    class Harness(PositionOpenMixin, PositionCloseMixin):
        def __init__(self):
            import threading
            self.cfg = {"trading": {
                "paper_mode": True, "paper_slippage": 0.0,
                "taker_fee": FEE_RATE, "borrow_rate_daily": BORROW_RATE,
                "borrow_periods_per_day": PERIODS,
                "reentry_cooldown_bars": 0,
                **({"margin_mode": "isolated"} if margin else {}),
            }, "exchange": {"name": "okx", "margin": margin}}
            self.exchange = MagicMock()
            self.exchange.create_order.return_value = {"price": EXIT, "id": "x"}
            self.risk = MagicMock()
            self.notif = MagicMock()
            self.allocator = MagicMock()
            # S12 : la clôture rend l'enveloppe et le budget au ledger.
            from app.core.rejections import RejectionCounter
            from app.core.risk_ledger import RiskLedger
            self.ledger = RiskLedger()
            self.rejections = RejectionCounter()
            self.SessionLocal = MagicMock()
            self._positions_lock = threading.Lock()
            self._capital_lock = threading.Lock()
            self._paper_base = 1000.0
            self.capital_display = 1000.0
            self._margin_interest = 0.0
            self._loss_notified = set()
            self._cooldown = {}
            self.signal_log = []
            self._strat_thresholds = {}
            self.threshold = 0.55
            self.interval = 60
            self.tf = "1h"
            self.open_positions = {}

    h = Harness()
    pos = {
        "id": "p1", "symbol": "BTC/USDC", "side": "long", "strategy": "t",
        "timeframe": "1h", "score": 0.8, "entry": ENTRY, "stop": 95.0,
        "size": SIZE, "notional": ENTRY * SIZE, "leverage": 1,
        "open_time": time.time() - HOURS_HELD * 3600,
        "fees": 0.0, "pnl": 0.0, "reason": "", "order_id": "o1",
    }
    h.open_positions["p1"] = pos
    base_before = h._paper_base
    h._close_position("p1", EXIT)
    return h._paper_base - base_before


def test_backtest_and_live_close_same_net_pnl():
    """Même trade (entrée/sortie/taille/frais/durée) ⇒ même PnL net."""
    pnl_bt = _backtest_close_pnl()
    pnl_live = _live_close_pnl()
    pnl_ref, _, _ = close_pnl("long", ENTRY, EXIT, SIZE, ENTRY * SIZE,
                              FEE_RATE, BORROW_RATE, HOURS_HELD, PERIODS)
    # Le chemin live mesure la durée par horloge → tolérance d'une seconde
    # d'intérêts ; tout le reste doit être identique.
    assert pnl_bt == pytest.approx(pnl_ref, abs=1e-9)
    assert pnl_live == pytest.approx(pnl_ref, abs=1e-4)
    assert pnl_bt == pytest.approx(pnl_live, abs=1e-4)


# ── 3. Parité de SIZING : même signal + même stop ⇒ même taille ─────────────
#
# Le troisième maillon, longtemps absent : les formules monétaires étaient
# verrouillées et le stop live aussi, mais rien ne vérifiait que le backtest et
# le live dérivent la MÊME distance au stop du même signal. Ils la calculent
# dans deux fichiers différents (`Backtester._try_enter` et
# `PositionOpenMixin._initial_stop_distance`), avec la même chaîne de priorité
# recopiée : `sl_atr_mult` → `stop_hint` → trailing initial.

_SIZING_ATR, _SIZING_PRICE = 2.0, 100.0
_TRAIL_CFG = {"trail_wide": 2.5, "grace_bars": 4, "breakeven_r": 1.2,
              "trail_tight": 1.0, "lock_r": 2.5, "tight_r": 4.0,
              "lock_ratio": 0.60, "use_swing": False}


def _live_stop_distance(signal: dict) -> float:
    from app.live.position_open_mixin import PositionOpenMixin

    class H(PositionOpenMixin):
        _trailing_cfg = {
            "mult": _TRAIL_CFG["trail_wide"], "grace_bars": _TRAIL_CFG["grace_bars"],
            "breakeven_r": _TRAIL_CFG["breakeven_r"],
            "trail_tight_mult": _TRAIL_CFG["trail_tight"],
            "lock_r": _TRAIL_CFG["lock_r"], "tight_r": _TRAIL_CFG["tight_r"],
            "lock_ratio": _TRAIL_CFG["lock_ratio"], "use_swing": _TRAIL_CFG["use_swing"],
        }

    return H()._initial_stop_distance(signal["side"], _SIZING_PRICE,
                                      _SIZING_ATR, signal)


def _backtest_stop_distance(signal: dict) -> float:
    """Rejoue la dérivation du stop de ``Backtester._try_enter`` via le vrai
    ``_make_trailing`` du Backtester (pas une recopie de la formule)."""
    from app.engine.backtest import Backtester
    from app.engine.engine import Engine

    bt = Backtester(Engine(), {"trading": {"capital": 1000, "risk_per_trade": 0.01,
                                           "timeframe": "1h"},
                               "backtest": dict(_TRAIL_CFG), "strategy_params": {}})
    if signal.get("sl_atr_mult") is not None:
        m = float(signal["sl_atr_mult"])
        stop = (_SIZING_PRICE - m * _SIZING_ATR) if signal["side"] == "long" \
            else (_SIZING_PRICE + m * _SIZING_ATR)
    elif signal.get("stop_hint") is not None:
        stop = float(signal["stop_hint"])
    else:
        stop = bt._make_trailing(signal.get("trail_override")).initial_stop(
            _SIZING_PRICE, _SIZING_ATR, signal["side"])
    return abs(_SIZING_PRICE - stop)


@pytest.mark.parametrize("signal", [
    {"side": "long"},                                   # repli trailing
    {"side": "short"},
    {"side": "long", "sl_atr_mult": 1.5},               # stop de la stratégie
    {"side": "short", "sl_atr_mult": 3.0},
    {"side": "long", "stop_hint": 96.0},                # stop absolu
    {"side": "long", "trail_override": {"trail_wide": 4.0}},
])
def test_same_signal_gives_the_same_stop_distance_both_sides(signal):
    live = _live_stop_distance(signal)
    backtest = _backtest_stop_distance(signal)
    assert live == pytest.approx(backtest), (
        f"stop_dist live={live} vs backtest={backtest} pour {signal} — "
        f"le sizing des deux chemins divergerait d'autant")


def test_same_stop_distance_gives_the_same_size():
    """A distance au stop egale, les deux chemins dimensionnent pareil :
    ``risk_amount / stop_dist``. S12 : le risk_amount vient de l'enveloppe du
    slot des DEUX cotes -- c'est ce partage de base qui fait la parite."""
    from app.core.risk_curve import risk_multiplier
    from app.core.risk_envelope import Envelope
    from app.core.risk_gate import RiskGate

    slot_envelope, risk_pct = 1000.0, 0.01
    stop_dist = _live_stop_distance({"side": "long"})

    rm = RiskGate({
        "trading": {"max_trades_per_minute": 3,
                    "daily_drawdown_limit": 0.05, "max_drawdown_global": 0.20},
        "venues": {"default": "v", "defs": {"v": {"max_leverage": 1}}, "assign": {}},
        "risk": {"profile": "t", "profiles": {"t": risk_pct},
                 "envelopes": {"v": {"capital": slot_envelope,
                                    "max_symbol_exposure_pct": 1.0,
                                    "symbol_risk_pct": 0.02, "venue_risk_pct": 0.05}}},
    })
    env = Envelope(
        venue="v", symbol="BTC/USDC", slot_key="s::1h::BTC/USDC", currency="USDC",
        venue_envelope=slot_envelope, venue_risk_budget=50.0,
        symbol_envelope=slot_envelope, symbol_risk_budget=20.0,
        slot_envelope=slot_envelope, slot_risk_amount=slot_envelope * risk_pct,
        max_leverage=1.0, min_notional=0.0, trade_risk_pct=risk_pct, weight=1.0,
    )
    size_live, _ = rm.compute_size(entry=_SIZING_PRICE, stop_dist=stop_dist, env=env)
    # Formule du Backtester (_try_enter), sans drawdown ni partial fill.
    size_backtest = slot_envelope * risk_pct * risk_multiplier(0.0) / stop_dist

    assert size_live == pytest.approx(size_backtest, rel=1e-5)


def test_spot_venue_charges_no_borrow_on_the_live_path():
    """S11 — sur une venue spot, le coût d'emprunt est nul.

    Avant, ``trading.borrow_rate_daily`` était facturé quelle que soit la
    venue : un achat comptant (spot crypto comme action SBF 120) payait des
    intérêts d'emprunt qui n'existent pas. L'écart attendu est exactement le
    coût d'emprunt du même trade en margin.
    """
    pnl_margin = _live_close_pnl(margin=True)
    pnl_spot   = _live_close_pnl(margin=False)
    expected_borrow = borrow_cost(ENTRY * SIZE, BORROW_RATE, HOURS_HELD, PERIODS)

    assert expected_borrow > 0
    assert pnl_spot > pnl_margin
    assert pnl_spot - pnl_margin == pytest.approx(expected_borrow, abs=1e-4)
