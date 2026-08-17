"""S12 — RiskLedger : reservation/liberation atomiques sous enveloppe + budget
de risque. Remplace CapitalAllocator.can_allocate/register_open/register_close.

Ce que ces tests verrouillent (cf. docs/CONCEPTION_ENVELOPPES_DE_RISQUE.md
§4.2, §9) : l'ordre des refus et leur code de motif, la symetrie
reserve/release, la liberation de budget quand le trailing remonte le stop,
et surtout -- AUCUN depassement de budget symbole ou venue, jamais, meme sous
50 threads concurrents.
"""
import threading

import pytest

from app.core.risk_envelope import Envelope
from app.core.risk_ledger import Decision, RiskLedger


def _env(**over) -> Envelope:
    base = dict(
        venue="okx-margin", symbol="BTC/USDC", slot_key="a::1h::BTC/USDC",
        currency="EUR",
        venue_envelope=1000.0, venue_risk_budget=30.0,
        symbol_envelope=1000.0, symbol_risk_budget=20.0,
        slot_envelope=100.0, slot_risk_amount=2.5,
        max_leverage=1.0, min_notional=0.0,
        trade_risk_pct=0.025, weight=0.1,
    )
    base.update(over)
    return Envelope(**base)


class TestReserveOrderOfChecks:
    def test_notional_over_slot_cap_is_rejected(self):
        ledger = RiskLedger()
        env = _env(slot_envelope=100.0, max_leverage=1.0)   # max_notional = 100
        d = ledger.reserve(env, risk=2.5, notional=150.0, pos_key="p1")
        assert d.allowed is False
        assert d.reason_code == "enveloppe_slot"

    def test_notional_over_symbol_envelope_is_rejected(self):
        ledger = RiskLedger()
        env = _env(slot_envelope=1000.0, symbol_envelope=200.0, max_leverage=1.0)
        d = ledger.reserve(env, risk=2.5, notional=250.0, pos_key="p1")
        assert d.allowed is False
        assert d.reason_code == "enveloppe_slot"

    def test_risk_over_symbol_budget_is_rejected(self):
        ledger = RiskLedger()
        env = _env(symbol_risk_budget=5.0, slot_envelope=1000.0, max_leverage=1.0)
        d = ledger.reserve(env, risk=6.0, notional=10.0, pos_key="p1")
        assert d.allowed is False
        assert d.reason_code == "budget_symbole"

    def test_risk_over_venue_budget_is_rejected(self):
        ledger = RiskLedger()
        env = _env(symbol_risk_budget=100.0, venue_risk_budget=5.0,
                   slot_envelope=1000.0, max_leverage=1.0)
        d = ledger.reserve(env, risk=6.0, notional=10.0, pos_key="p1")
        assert d.allowed is False
        assert d.reason_code == "budget_venue"

    def test_notional_under_venue_minimum_is_rejected(self):
        ledger = RiskLedger()
        env = _env(min_notional=200.0, slot_envelope=1000.0, symbol_envelope=1000.0,
                   symbol_risk_budget=100.0, venue_risk_budget=100.0, max_leverage=1.0)
        d = ledger.reserve(env, risk=1.0, notional=50.0, pos_key="p1")
        assert d.allowed is False
        assert d.reason_code == "notionnel_min"

    def test_valid_reservation_is_allowed(self):
        ledger = RiskLedger()
        env = _env()
        d = ledger.reserve(env, risk=2.0, notional=90.0, pos_key="p1")
        assert d == Decision(True)

    def test_duplicate_pos_key_is_rejected_without_leaking_budget(self):
        """L-05 : une 2ᵉ réserve sur la même clé n'additionne pas le budget."""
        ledger = RiskLedger()
        env = _env(slot_envelope=1000.0, symbol_envelope=1000.0,
                   symbol_risk_budget=100.0, venue_risk_budget=100.0)
        assert ledger.reserve(env, risk=2.0, notional=90.0, pos_key="p1").allowed
        d = ledger.reserve(env, risk=2.0, notional=90.0, pos_key="p1")
        assert d.allowed is False
        assert d.reason_code == "deja_reserve"
        eng = ledger.engaged()
        assert eng["slot"]["a::1h::BTC/USDC"]["notional"] == pytest.approx(90.0)
        assert eng["slot"]["a::1h::BTC/USDC"]["risk"] == pytest.approx(2.0)

    def test_no_tolerance_overshoot_unlike_the_old_allocator(self):
        """L'ancien CapitalAllocator tolerait +5% (source du bug mesure : 200
        contre un plafond de 95). Ici, la limite est stricte."""
        ledger = RiskLedger()
        env = _env(slot_envelope=100.0, max_leverage=1.0)
        d = ledger.reserve(env, risk=2.5, notional=100.01, pos_key="p1")
        assert d.allowed is False


class TestReserveReleaseSymmetry:
    def test_release_frees_the_engaged_amounts(self):
        ledger = RiskLedger()
        env = _env()
        ledger.reserve(env, risk=2.0, notional=90.0, pos_key="p1")
        ledger.release("p1")
        engaged = ledger.engaged()
        assert engaged["venue"] == {} or engaged["venue"].get("okx-margin", {}).get("notional", 0) == 0
        assert engaged["symbol"].get("okx-margin::BTC/USDC", {}).get("risk", 0) == 0

    def test_release_of_unknown_key_is_a_noop(self):
        ledger = RiskLedger()
        ledger.release("does-not-exist")   # ne leve pas

    def test_after_release_the_budget_is_available_again(self):
        ledger = RiskLedger()
        env = _env(symbol_envelope=100.0, slot_envelope=100.0, max_leverage=1.0)
        assert ledger.reserve(env, risk=2.0, notional=100.0, pos_key="p1").allowed
        assert not ledger.reserve(env, risk=2.0, notional=100.0, pos_key="p2").allowed
        ledger.release("p1")
        assert ledger.reserve(env, risk=2.0, notional=100.0, pos_key="p2").allowed

    def test_two_slots_of_the_same_symbol_share_the_budget(self):
        """Le point du modele : deux bots sur le meme symbole epuisent le MEME
        budget de risque, meme s'ils ont des slot_key differents."""
        ledger = RiskLedger()
        env_a = _env(slot_key="a::1h::BTC/USDC", symbol_risk_budget=5.0,
                    slot_envelope=1000.0, max_leverage=1.0)
        env_b = _env(slot_key="b::1h::BTC/USDC", symbol_risk_budget=5.0,
                    slot_envelope=1000.0, max_leverage=1.0)
        assert ledger.reserve(env_a, risk=4.0, notional=10.0, pos_key="pa").allowed
        d = ledger.reserve(env_b, risk=4.0, notional=10.0, pos_key="pb")
        assert d.allowed is False
        assert d.reason_code == "budget_symbole"


class TestUpdateRisk:
    def test_trailing_reduces_engaged_risk_and_frees_budget(self):
        """Le trailing remonte le stop -> risque reel plus faible -> libere du
        budget pour un AUTRE slot du meme symbole, sans toucher l'enveloppe."""
        ledger = RiskLedger()
        env = _env(symbol_risk_budget=5.0, slot_envelope=1000.0, max_leverage=1.0)
        ledger.reserve(env, risk=4.0, notional=10.0, pos_key="p1")
        other = _env(slot_key="b::1h::BTC/USDC", symbol_risk_budget=5.0,
                    slot_envelope=1000.0, max_leverage=1.0)
        assert not ledger.reserve(other, risk=2.0, notional=10.0, pos_key="p2").allowed

        ledger.update_risk("p1", risk=1.0)   # stop remonte, risque reel baisse
        assert ledger.reserve(other, risk=2.0, notional=10.0, pos_key="p2").allowed

    def test_update_risk_on_unknown_position_is_a_noop(self):
        ledger = RiskLedger()
        ledger.update_risk("does-not-exist", risk=5.0)   # ne leve pas

    def test_update_risk_never_goes_negative(self):
        ledger = RiskLedger()
        env = _env()
        ledger.reserve(env, risk=2.0, notional=10.0, pos_key="p1")
        ledger.update_risk("p1", risk=0.0)
        assert ledger.engaged()["slot"]["a::1h::BTC/USDC"]["risk"] == 0.0


class TestEngagedSnapshot:
    def test_engaged_aggregates_across_levels(self):
        ledger = RiskLedger()
        env = _env()
        ledger.reserve(env, risk=2.0, notional=90.0, pos_key="p1")
        eng = ledger.engaged()
        assert eng["venue"]["okx-margin"]["notional"] == pytest.approx(90.0)
        assert eng["symbol"]["okx-margin::BTC/USDC"]["risk"] == pytest.approx(2.0)
        assert eng["slot"]["a::1h::BTC/USDC"]["notional"] == pytest.approx(90.0)

    def test_snapshot_matches_engaged(self):
        ledger = RiskLedger()
        assert ledger.snapshot() == ledger.engaged()


# ── Concurrence ──────────────────────────────────────────────────────────────

class TestConcurrency:
    def test_no_overshoot_under_fifty_concurrent_threads(self):
        """50 threads tentent simultanement de reserver 3 EUR de risque sur un
        budget symbole de 20 -- au plus 6 doivent reussir (6x3=18 <= 20 < 21),
        jamais plus, quelle que soit l'ordonnance des threads."""
        ledger = RiskLedger()
        env = _env(symbol_risk_budget=20.0, slot_envelope=10_000.0, max_leverage=1.0)
        results = []
        results_lock = threading.Lock()

        def _attempt(i: int):
            d = ledger.reserve(env, risk=3.0, notional=1.0, pos_key=f"p{i}")
            with results_lock:
                results.append(d.allowed)

        threads = [threading.Thread(target=_attempt, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        n_allowed = sum(results)
        assert n_allowed <= 6
        engaged_risk = ledger.engaged()["symbol"]["okx-margin::BTC/USDC"]["risk"]
        assert engaged_risk <= 20.0 + 1e-9
        assert engaged_risk == pytest.approx(n_allowed * 3.0)
