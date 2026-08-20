"""S12 §5.2 — une edge n'est transposable que sur l'echelle qui l'a mesuree.

Une expectancy s'exprime en % : elle semble donc invariante a l'echelle. Elle
ne l'est pas, parce que `min_notional`, le lot indivisible et les frais fixes
ne se mettent PAS a l'echelle. Promouvoir un bot sur une edge mesuree a
1 000 EUR alors qu'il tradera a 90 EUR, c'est refaire le defaut d'origine --
d'ou la garde de derive.
"""
import pytest

from app.core.risk.envelope import Envelope, base_drift, envelope_base
from app.live.slot_lifecycle import LifecycleState, SlotLifecycleManager


def _env(slot_envelope=100.0):
    return Envelope(
        venue="v", symbol="BTC/USDC", slot_key="s::1h::BTC/USDC", currency="EUR",
        venue_envelope=1000.0, venue_risk_budget=50.0,
        symbol_envelope=1000.0, symbol_risk_budget=50.0,
        slot_envelope=slot_envelope, slot_risk_amount=slot_envelope * 0.025,
        max_leverage=1.0, min_notional=0.0, trade_risk_pct=0.025,
        weight=slot_envelope / 1000.0,
    )


def _mgr(tolerance=0.20):
    return SlotLifecycleManager({
        "risk": {"base_drift_tolerance": tolerance},
        "lifecycle": {"edge_min_trades": 5, "fidelity_min_fills": 3,
                      "max_worst_trade_pct": 50.0},
    })


def _promotable(**over):
    """Slot par ailleurs irreprochable : edge significative, fidelite live OK."""
    d = {"slot_key": "s::1h::BTC/USDC", "edge_ci_low": 0.8, "edge_n": 40,
         "worst_trade_pct": -5.0, "budget_pct": 0.5, "live_trades": 10,
         "live_in_band": True, "live_avg_return_pct": 1.2}
    d.update(over)
    return d


class TestEnvelopeBase:
    def test_base_records_the_scale_not_just_the_number(self):
        b = envelope_base(_env(90.0), as_of="2026-08-03")
        assert b["slot_envelope"] == 90.0
        assert b["trade_risk_pct"] == 0.025
        assert b["currency"] == "EUR"
        assert b["venue"] == "v"
        assert b["as_of"] == "2026-08-03"


class TestBaseDrift:
    def test_no_drift_when_the_envelope_is_unchanged(self):
        assert base_drift({"slot_envelope": 100.0}, _env(100.0)) == 0.0

    def test_drift_is_relative_to_the_recorded_base(self):
        assert base_drift({"slot_envelope": 100.0}, _env(130.0)) == pytest.approx(0.30)
        assert base_drift({"slot_envelope": 100.0}, _env(70.0)) == pytest.approx(0.30)

    def test_a_missing_base_is_unknown_not_zero(self):
        """Bot anterieur a S12 : l'information manque, ce n'est PAS une
        derive nulle -- confondre les deux promouvrait en silence sur une base
        inconnue."""
        assert base_drift(None, _env()) is None
        assert base_drift({}, _env()) is None

    def test_a_zero_recorded_base_is_unusable(self):
        assert base_drift({"slot_envelope": 0.0}, _env()) is None


class TestPromotionGuard:
    def test_a_stable_base_promotes(self):
        assert _mgr()._propose(_promotable(base_drift=0.05)) == LifecycleState.ACTIF

    def test_drift_beyond_tolerance_blocks_promotion(self):
        """Au-dela de 20 %, le bot retombe CANDIDAT : son edge doit etre
        recalculee sur la nouvelle echelle avant de valoir promotion."""
        assert _mgr()._propose(_promotable(base_drift=0.45)) == LifecycleState.CANDIDAT

    def test_exactly_at_the_tolerance_still_promotes(self):
        assert _mgr()._propose(_promotable(base_drift=0.20)) == LifecycleState.ACTIF

    def test_an_unknown_base_does_not_block(self):
        """La garde bloque une derive CONSTATEE, pas une base absente : sinon
        aucun bot anterieur a S12 ne pourrait plus jamais etre promu."""
        assert _mgr()._propose(_promotable(base_drift=None)) == LifecycleState.ACTIF

    def test_the_tolerance_is_configurable(self):
        assert _mgr(0.50)._propose(_promotable(base_drift=0.45)) == LifecycleState.ACTIF
        assert _mgr(0.10)._propose(_promotable(base_drift=0.15)) == LifecycleState.CANDIDAT

    def test_a_forced_slot_still_bypasses_everything(self):
        """Le forçage manuel reste un droit de veto de l'utilisateur."""
        d = _promotable(base_drift=0.90, force_active=True)
        assert _mgr()._propose(d) == LifecycleState.ACTIF
