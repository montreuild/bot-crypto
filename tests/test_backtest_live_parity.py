"""S12 — meme Envelope + meme signal => meme taille des deux cotes.

C'est la propriete qui rend le backtest predictif du live. Elle tenait
auparavant par coincidence de reglage (deux formules distinctes qu'il fallait
garder synchrones a la main) ; elle tient desormais parce que les deux chemins
partagent la MEME base et le MEME ordre de facteurs.

Cf. docs/CONCEPTION_ENVELOPPES_DE_RISQUE.md §5 et §9.
"""
import pytest

from app.core.risk.curve import risk_multiplier
from app.core.risk.envelope import Envelope
from app.core.risk.gate import RiskGate
from app.core.risk.sizer import _floor_to


def _cfg(profile_pct=0.025, capital=1000.0):
    return {
        "trading": {"max_trades_per_minute": 100,
                    "daily_drawdown_limit": 0.05, "max_drawdown_global": 0.20},
        "venues": {"default": "v", "defs": {"v": {"max_leverage": 1}}, "assign": {}},
        "risk": {"profile": "p", "profiles": {"p": profile_pct},
                 "envelopes": {"v": {"capital": capital, "max_symbol_exposure_pct": 1.0,
                                    "symbol_risk_pct": 0.05, "venue_risk_pct": 0.10}}},
        "backtest": {"reference_envelope": 1000},
    }


def _env(slot_envelope=1000.0, risk_pct=0.025, leverage=1.0, min_notional=0.0):
    return Envelope(
        venue="v", symbol="BTC/USDC", slot_key="s::1h::BTC/USDC", currency="USDC",
        venue_envelope=1000.0, venue_risk_budget=100.0,
        symbol_envelope=1000.0, symbol_risk_budget=50.0,
        slot_envelope=slot_envelope, slot_risk_amount=slot_envelope * risk_pct,
        max_leverage=leverage, min_notional=min_notional,
        trade_risk_pct=risk_pct, weight=slot_envelope / 1000.0,
    )


def _backtest_size(env, entry, stop_dist, *, size_factor=1.0, capital=None, peak=None):
    """Reproduit exactement l'arithmetique de Backtester._try_enter, sans
    monter un DataFrame complet : c'est la FORMULE qu'on compare, pas le
    parcours de barres.

    `capital` est l'EQUITE courante (elle compose, et pilote la courbe de
    de-risquage) ; la base de sizing, elle, reste l'enveloppe -- cf.
    Backtester._sizing_base."""
    capital = env.slot_envelope if capital is None else capital
    peak = capital if peak is None else peak
    dd = max(0.0, (peak - capital) / peak) if peak > 0 else 0.0
    sf = max(0.0, min(float(size_factor), 2.0))
    base = env.slot_envelope
    risk_amount = base * env.trade_risk_pct * sf * risk_multiplier(dd)
    max_notional = base * max(env.max_leverage, 1.0)
    size = _floor_to(risk_amount / stop_dist, 6)
    if size * entry > max_notional:
        size = _floor_to(max_notional / entry, 6)
    return size, _floor_to(size * entry, 4)


class TestSameEnvelopeSameSize:
    @pytest.mark.parametrize("entry,stop_dist", [
        (100.0, 5.0), (100.0, 2.5), (37.42, 1.1), (68_420.0, 900.0), (0.05, 0.002),
    ])
    def test_size_and_notional_match(self, entry, stop_dist):
        env = _env()
        live_size, live_notional = RiskGate(_cfg()).compute_size(entry, stop_dist, env)
        bt_size, bt_notional = _backtest_size(env, entry, stop_dist)
        assert live_size == bt_size
        assert live_notional == bt_notional

    @pytest.mark.parametrize("size_factor", [0.0, 0.5, 1.0, 1.5, 2.0, 5.0])
    def test_size_factor_is_applied_identically(self, size_factor):
        """Le facteur de taille s'applique AVANT le plafond des deux cotes.
        Avant S12, le backtest l'appliquait apres : deux tailles differentes
        pour un meme signal des que le plafond mordait."""
        env = _env()
        live, _ = RiskGate(_cfg()).compute_size(100.0, 5.0, env, size_factor=size_factor)
        bt, _ = _backtest_size(env, 100.0, 5.0, size_factor=size_factor)
        assert live == bt

    def test_the_notional_cap_bites_at_the_same_point(self):
        """Stop tres serre : les deux cotes doivent plafonner a l'enveloppe x
        levier, pas l'un a un pourcentage global et l'autre a l'enveloppe."""
        env = _env(leverage=3.0)
        live_size, live_notional = RiskGate(_cfg()).compute_size(100.0, 0.01, env)
        bt_size, bt_notional = _backtest_size(env, 100.0, 0.01, )
        assert live_notional == bt_notional == pytest.approx(3000.0)
        assert live_size == bt_size

    def test_the_drawdown_curve_applies_identically(self):
        """BT-09 partage : a 12 % de drawdown, les deux divisent par deux."""
        env = _env()
        gate = RiskGate(_cfg())
        gate.equity, gate.peak_equity = 880.0, 1000.0
        live, _ = gate.compute_size(100.0, 5.0, env)
        bt, _ = _backtest_size(env, 100.0, 5.0, capital=880.0, peak=1000.0)
        assert live == bt


class TestSameRejectionReason:
    def test_a_notional_below_the_venue_minimum_is_refused_on_both_sides(self):
        """Meme cause, meme code : `notionnel_min` des deux cotes. Sans ce
        vocabulaire partage, un backtest qui trade et un live qui refuse ne
        sont pas comparables."""
        from app.core.rejections import REASONS
        from app.core.risk.ledger import RiskLedger

        env = _env(slot_envelope=100.0, min_notional=200.0)
        gate = RiskGate(_cfg())
        size, notional = gate.compute_size(50.0, 5.0, env)
        decision = RiskLedger().reserve(env, risk=size * 5.0, notional=notional,
                                        pos_key="p")
        assert decision.reason_code == "notionnel_min"
        assert decision.reason_code in REASONS

        # Cote backtest : meme enveloppe, meme prix -> meme notionnel, donc le
        # meme seuil est franchi.
        bt_size, bt_notional = _backtest_size(env, 50.0, 5.0)
        assert bt_notional == notional
        assert bt_notional < env.min_notional
