"""S12 — diagnostics de faisabilite des enveloppes de risque.

Cf. docs/CONCEPTION_ENVELOPPES_DE_RISQUE.md §6.2. Chaque code doit se
declencher sur un cas construit ET NE PAS se declencher sur un cas sain.
Les valeurs numeriques sont celles verifiees dans la spec (tableau
"Verifie sur les valeurs cibles du §7.1") avant l'implementation.
"""
import pytest

from app.core.risk.diagnostics import Diagnostic, diagnose, n_slots_max


def _cfg(envelopes, venues_defs, *, min_slot_weight=0.0, stop_pct_reference=0.025,
        profile_pct=0.025):
    return {
        "risk": {
            "profile": "custom", "profiles": {"custom": profile_pct},
            "min_slot_weight": min_slot_weight,
            "diagnostics": {"stop_pct_reference": stop_pct_reference},
            "envelopes": envelopes,
        },
        "venues": {"defs": venues_defs},
    }


def _codes(diags, severity=None):
    return {d.code for d in diags if severity is None or d.severity == severity}


CRYPTO_DEF = {"max_leverage": 1.0, "min_notional": 0.0, "fee_min": 0.0, "lot_size": 0.0}
EQUITY_DEF = {"max_leverage": 1.0, "min_notional": 200.0, "fee_min": 2.0, "lot_size": 1.0}


class TestNSlotsMax:
    def test_no_minimum_notional_means_unlimited(self):
        assert n_slots_max(1000, 1.0, 0.0) == float("inf")

    def test_equity_example_from_the_spec(self):
        """2 500 EUR d'enveloppe ticker, levier 1, min_notional 200 -> 12,5
        slots au plus (spec §6.2, exemple actions)."""
        assert n_slots_max(2500, 1.0, 200.0) == pytest.approx(12.5)


class TestFeasibility:
    def test_btc_ten_slots_leverage_one_touches_the_reference_stop(self):
        """Cas mesure sur la config livree : 10 slots, aucune edge -> chacun a
        100 EUR d'enveloppe. Levier 1 -> le stop de reference (2,5%) touche
        DEJA le plafond notionnel -- tout stop plus serre degradera le
        risque reel sous le risk_pct configure."""
        cfg = _cfg({"okx": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                            "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}},
                   {"okx": CRYPTO_DEF})
        slots = {("okx", "BTC/USDC"): {f"s{i}::1h::BTC/USDC": None for i in range(10)}}
        diags = diagnose(cfg, slots)
        assert "plafond_notionnel_mordant" in _codes(diags)
        assert "trade_impossible" not in _codes(diags, "error")
        assert "trop_de_slots" not in _codes(diags, "error")

    def test_leverage_three_widens_the_window_and_clears_the_warning(self):
        """Meme config, levier 3 : le plafond notionnel ne mord plus au stop
        de reference (spec : [0,83% -- inf), 'ok')."""
        cfg = _cfg({"okx": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                            "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}},
                   {"okx": {**CRYPTO_DEF, "max_leverage": 3.0}})
        slots = {("okx", "BTC/USDC"): {f"s{i}::1h::BTC/USDC": None for i in range(10)}}
        diags = diagnose(cfg, slots)
        assert "plafond_notionnel_mordant" not in _codes(diags)

    def test_equity_five_slots_is_healthy(self):
        """5 slots sur un ticker actions a 2 500 EUR d'enveloppe : fenetre
        [2,5% -- 6,25%], ratio 2.5 -- pas de fenetre etroite ; frais fixes
        0,8% -- sous le seuil de 1%."""
        cfg = _cfg({"eq": {"capital": 10000, "max_symbol_exposure_pct": 0.25,
                          "symbol_risk_pct": 0.02, "venue_risk_pct": 0.05}},
                   {"eq": EQUITY_DEF})
        slots = {("eq", "AIR.PA"): {f"s{i}::1d::AIR.PA": None for i in range(5)}}
        diags = diagnose(cfg, slots)
        assert "fenetre_stops_etroite" not in _codes(diags)
        assert "frais_fixes_dominants" not in _codes(diags)
        assert "trade_impossible" not in _codes(diags, "error")

    def test_equity_ten_slots_triggers_narrow_window_and_fee_drag(self):
        """10 slots -> enveloppe 250 EUR, fenetre [2,5% -- 3,12%] (ratio 1.25
        < 1.5) et frais fixes 1,6% > 1% (spec : 'fenetre etroite + frais
        fixes 1,6%')."""
        cfg = _cfg({"eq": {"capital": 10000, "max_symbol_exposure_pct": 0.25,
                          "symbol_risk_pct": 0.02, "venue_risk_pct": 0.05}},
                   {"eq": EQUITY_DEF})
        slots = {("eq", "AIR.PA"): {f"s{i}::1d::AIR.PA": None for i in range(10)}}
        diags = diagnose(cfg, slots)
        assert "fenetre_stops_etroite" in _codes(diags)
        assert "frais_fixes_dominants" in _codes(diags)

    def test_equity_fifteen_slots_is_flat_out_impossible(self):
        """15 slots -> enveloppe 167 EUR < min_notional 200 : n_slots_max
        (12,5) depasse ET aucun trade ne peut jamais passer (spec : 'vide' /
        'trop_de_slots + trade_impossible')."""
        cfg = _cfg({"eq": {"capital": 10000, "max_symbol_exposure_pct": 0.25,
                          "symbol_risk_pct": 0.02, "venue_risk_pct": 0.05}},
                   {"eq": EQUITY_DEF})
        slots = {("eq", "AIR.PA"): {f"s{i}::1d::AIR.PA": None for i in range(15)}}
        diags = diagnose(cfg, slots)
        errors = _codes(diags, "error")
        assert "trop_de_slots" in errors
        assert "trade_impossible" in errors


class TestVenueLevelChecks:
    def test_two_symbols_at_full_exposure_overflow_the_venue_envelope(self):
        cfg = _cfg({"okx": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                            "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}},
                   {"okx": CRYPTO_DEF})
        slots = {("okx", "BTC/USDC"): {"a::1h::BTC/USDC": None},
                 ("okx", "ETH/USDC"): {"b::1h::ETH/USDC": None}}
        diags = diagnose(cfg, slots)
        assert "enveloppe_venue_depassee" in _codes(diags, "error")

    def test_single_symbol_never_overflows(self):
        cfg = _cfg({"okx": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                            "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}},
                   {"okx": CRYPTO_DEF})
        slots = {("okx", "BTC/USDC"): {"a::1h::BTC/USDC": None}}
        diags = diagnose(cfg, slots)
        assert "enveloppe_venue_depassee" not in _codes(diags)

    def test_no_correlation_discount_with_a_single_symbol(self):
        """Avec un seul symbole attendu, venue_risk_pct (3%) >= 1 x
        symbol_risk_pct (2%) est TOUJOURS vrai : le budget venue ne
        contraint rien -- attendu et documente (§7.1)."""
        cfg = _cfg({"okx": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                            "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}},
                   {"okx": CRYPTO_DEF})
        slots = {("okx", "BTC/USDC"): {"a::1h::BTC/USDC": None}}
        diags = diagnose(cfg, slots)
        assert "decote_correlation_absente" in _codes(diags, "warning")

    def test_min_slot_weight_insufficient_on_equity_venue(self):
        """plancher 5% x enveloppe symbole (2500) = 125 EUR < min_notional
        (200) : les plus petits slots ne pourront jamais trader."""
        cfg = _cfg({"eq": {"capital": 10000, "max_symbol_exposure_pct": 0.25,
                          "symbol_risk_pct": 0.02, "venue_risk_pct": 0.05}},
                   {"eq": EQUITY_DEF}, min_slot_weight=0.05)
        diags = diagnose(cfg, {("eq", "AIR.PA"): {"a::1d::AIR.PA": None}})
        assert "min_slot_weight_insuffisant" in _codes(diags, "warning")

    def test_min_slot_weight_sufficient_on_crypto_venue(self):
        """Crypto n'a pas de min_notional -> le check ne mord jamais."""
        cfg = _cfg({"okx": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                            "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}},
                   {"okx": CRYPTO_DEF}, min_slot_weight=0.05)
        diags = diagnose(cfg, {("okx", "BTC/USDC"): {"a::1h::BTC/USDC": None}})
        assert "min_slot_weight_insuffisant" not in _codes(diags)


class TestRiskBudgetFeasibility:
    """Le slot risque a lui seul plus que le budget partage au-dessus de lui :
    RiskLedger refusera CHAQUE trade. Detecte en ecrivant
    test_sizing_coherence -- exactement la classe de panne silencieuse que S12
    existe pour supprimer."""

    def test_single_slot_with_a_risk_profile_above_the_symbol_budget(self):
        """1 slot -> poids 1,0 -> risque 25 EUR contre 20 EUR de budget
        symbole. C'est la config LIVREE (profil normal 2,5 % contre
        symbol_risk_pct 2 %) des qu'un symbole tombe a un seul slot actif."""
        cfg = _cfg({"okx": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                            "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}},
                   {"okx": CRYPTO_DEF}, profile_pct=0.025)
        diags = diagnose(cfg, {("okx", "BTC/USDC"): {"a::1h::BTC/USDC": None}})
        assert "budget_symbole_insuffisant" in _codes(diags, "error")

    def test_enough_slots_dilute_the_weight_and_clear_it(self):
        """10 slots -> poids 0,1 -> risque 2,5 EUR : largement sous le budget.
        Le meme reglage est sain ou fatal selon le nombre de slots actifs --
        d'ou l'interet de diagnostiquer sur les slots REELS."""
        cfg = _cfg({"okx": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                            "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}},
                   {"okx": CRYPTO_DEF}, profile_pct=0.025)
        slots = {("okx", "BTC/USDC"): {f"s{i}::1h::BTC/USDC": None for i in range(10)}}
        diags = diagnose(cfg, slots)
        assert "budget_symbole_insuffisant" not in _codes(diags)

    def test_a_profile_at_or_below_the_symbol_budget_is_safe_at_any_weight(self):
        cfg = _cfg({"okx": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                            "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}},
                   {"okx": CRYPTO_DEF}, profile_pct=0.02)
        diags = diagnose(cfg, {("okx", "BTC/USDC"): {"a::1h::BTC/USDC": None}})
        assert "budget_symbole_insuffisant" not in _codes(diags)

    def test_venue_budget_below_the_trade_risk_is_flagged(self):
        cfg = _cfg({"okx": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                            "symbol_risk_pct": 0.10, "venue_risk_pct": 0.01}},
                   {"okx": CRYPTO_DEF}, profile_pct=0.05)
        diags = diagnose(cfg, {("okx", "BTC/USDC"): {"a::1h::BTC/USDC": None}})
        assert "budget_venue_insuffisant" in _codes(diags, "error")


class TestSlotWithoutEdge:
    def test_slot_with_zero_weight_is_flagged(self):
        cfg = _cfg({"okx": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                            "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}},
                   {"okx": CRYPTO_DEF})
        slots = {("okx", "BTC/USDC"): {"proven::1h::BTC/USDC": 0.5,
                                       "unproven::1h::BTC/USDC": None}}
        diags = diagnose(cfg, slots)
        flagged = [d for d in diags if d.code == "slot_sans_edge"]
        assert len(flagged) == 1
        assert flagged[0].scope == "slot:unproven::1h::BTC/USDC"


class TestNoFalsePositivesOnAHealthyConfig:
    def test_two_well_sized_symbols_raise_nothing(self):
        """Deux symboles (pas un seul) : avec un seul symbole,
        venue_risk_pct >= symbol_risk_pct est TOUJOURS vrai (impose par la
        validation) et declenche systematiquement decote_correlation_absente
        -- cf. test_no_correlation_discount_with_a_single_symbol. Un cas
        vraiment sain a donc besoin d'au moins deux symboles, dimensionnes
        pour ne mordre sur aucun code -- profil de risque compris (2,5 % sur
        un slot a poids 1,0 depasserait le budget symbole de 1 %)."""
        cfg = _cfg({"okx": {"capital": 100_000, "max_symbol_exposure_pct": 0.4,
                            "symbol_risk_pct": 0.01, "venue_risk_pct": 0.015}},
                   {"okx": {"max_leverage": 5.0, "min_notional": 10.0,
                           "fee_min": 0.0, "lot_size": 0.0}},
                   min_slot_weight=0.05, stop_pct_reference=0.01,
                   profile_pct=0.01)
        diags = diagnose(cfg, {("okx", "BTC/USDC"): {"a::1h::BTC/USDC": None},
                               ("okx", "ETH/USDC"): {"b::1h::ETH/USDC": None}})
        assert diags == []


class TestDiagnosticShape:
    def test_diagnostic_is_frozen_and_carries_values(self):
        d = Diagnostic("error", "trade_impossible", "slot:x", "message", {"a": 1})
        assert d.severity == "error"
        assert d.values == {"a": 1}
