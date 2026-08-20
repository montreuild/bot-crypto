"""S12 — enveloppes de risque emboîtées venue -> symbole -> slot.

Cf. docs/CONCEPTION_ENVELOPPES_DE_RISQUE.md §2-4. Ce que ces tests
verrouillent : le poids par confiance d'edge (pas par performance), les
enveloppes emboîtées avec leurs deux plafonds distincts (notionnel vs risque),
et `max_notional` = enveloppe x levier (spot -> levier 1, donc plafond = capital
alloué au slot, pas un multiple).
"""
import pytest

from app.core.bot_identity import Venue
from app.core.risk.envelope import (
    resolve_envelope,
    slot_weights,
    trade_risk_pct,
    with_reference_envelope,
)

# ── Poids par confiance d'edge ───────────────────────────────────────────────

class TestSlotWeights:
    def test_no_edge_measured_anywhere_splits_equally(self):
        """Bootstrap : aucun slot n'a d'historique -> chacun a sa chance."""
        w = slot_weights({"a": None, "b": None, "c": None}, min_weight=0.0)
        assert w == {"a": pytest.approx(1 / 3), "b": pytest.approx(1 / 3), "c": pytest.approx(1 / 3)}

    def test_unproven_edge_gets_zero_weight_once_one_is_measured(self):
        """Dès qu'une edge existe quelque part, un slot sans edge ne trade pas
        -- ce n'est plus le cas bootstrap "aucune edge nulle part"."""
        w = slot_weights({"a": None, "b": 0.5}, min_weight=0.0)
        assert w["a"] == 0.0
        assert w["b"] == pytest.approx(1.0)

    def test_negative_edge_gets_zero_weight(self):
        w = slot_weights({"a": -0.3, "b": 0.6}, min_weight=0.0)
        assert w["a"] == 0.0
        assert w["b"] == pytest.approx(1.0)

    def test_weights_proportional_to_edge_confidence(self):
        w = slot_weights({"a": 1.0, "b": 3.0}, min_weight=0.0)
        assert w["a"] == pytest.approx(0.25)
        assert w["b"] == pytest.approx(0.75)

    def test_all_measured_but_none_positive_gives_zero_to_everyone(self):
        """Aucune edge prouvee positive -> personne ne trade sur ce symbole,
        plutot qu'un repli par egalite qui masquerait l'absence d'edge."""
        w = slot_weights({"a": -0.1, "b": -0.2}, min_weight=0.0)
        assert w == {"a": 0.0, "b": 0.0}

    def test_floor_is_applied_and_renormalized(self):
        """Plancher 10% sur 2 slots (a tres favorise, b a peine positif) : b ne
        doit jamais descendre sous 10%, et le reliquat (1 - 2x10% = 80%) se
        repartit ENTRE LES DEUX au prorata de leur poids brut -- b garde donc
        un peu plus que le plancher pur (0.10 + 0.01/1.00 x 0.80 = 0.108)."""
        w = slot_weights({"a": 0.99, "b": 0.01}, min_weight=0.10)
        assert w["b"] >= 0.10
        assert w["b"] == pytest.approx(0.108)
        assert w["a"] == pytest.approx(0.892)
        assert sum(w.values()) == pytest.approx(1.0)

    def test_floor_unreachable_for_all_falls_back_to_equal_split(self):
        """3 slots actifs, plancher 40% (3x0.40 > 1) -> impossible a satisfaire
        pour tous -> egalite entre actifs."""
        w = slot_weights({"a": 1.0, "b": 1.0, "c": 1.0}, min_weight=0.40)
        assert w["a"] == pytest.approx(1 / 3)
        assert w["b"] == pytest.approx(1 / 3)
        assert w["c"] == pytest.approx(1 / 3)

    def test_zero_weight_slots_stay_at_zero_after_floor(self):
        """Le plancher ne redonne pas vie a un slot sans edge prouvee."""
        w = slot_weights({"a": 0.0, "b": 1.0, "c": None}, min_weight=0.05)
        assert w["a"] == 0.0
        assert w["c"] == 0.0
        assert w["b"] == pytest.approx(1.0)

    def test_empty_input(self):
        assert slot_weights({}, min_weight=0.05) == {}


# ── Taux de risque par trade (profil) ───────────────────────────────────────

class TestTradeRiskPct:
    def test_default_profile_is_normal(self):
        assert trade_risk_pct({}) == pytest.approx(0.025)

    def test_explicit_profile(self):
        cfg = {"risk": {"profile": "agressif"}}
        assert trade_risk_pct(cfg) == pytest.approx(0.05)

    def test_custom_profiles_override(self):
        cfg = {"risk": {"profile": "custom", "profiles": {"custom": 0.03}}}
        assert trade_risk_pct(cfg) == pytest.approx(0.03)


# ── Enveloppes emboîtées ─────────────────────────────────────────────────────

CRYPTO_VENUE = Venue(name="okx-margin", market_type="margin", margin_mode="isolated",
                     max_leverage=1.0, quote_currency="EUR", min_notional=0.0)
EQUITY_VENUE = Venue(name="euronext-paper", market_type="spot", max_leverage=1.0,
                     quote_currency="EUR", min_notional=200.0, asset_class="equity")


def _cfg(**envelopes):
    return {"risk": {"profile": "normal", "profiles": {"normal": 0.025},
                     "min_slot_weight": 0.0, "envelopes": envelopes}}


class TestResolveEnvelope:
    def test_single_slot_gets_the_whole_symbol_envelope(self):
        cfg = _cfg(**{"okx-margin": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                                     "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}})
        env = resolve_envelope(cfg, CRYPTO_VENUE, "BTC/USDC", "a::1h::BTC/USDC",
                               peers=["a::1h::BTC/USDC"], edges={"a::1h::BTC/USDC": None})
        assert env.venue_envelope == pytest.approx(1000)
        assert env.venue_risk_budget == pytest.approx(30)
        assert env.symbol_envelope == pytest.approx(1000)
        assert env.symbol_risk_budget == pytest.approx(20)
        assert env.slot_envelope == pytest.approx(1000)
        assert env.slot_risk_amount == pytest.approx(25)   # 1000 x 0.025
        assert env.weight == pytest.approx(1.0)

    def test_ten_equal_slots_split_the_symbol_envelope(self):
        """Le cas mesure sur la config livree : 10 slots BTC, aucune edge
        mesuree -> chacun recoit 1/10 -- pas 1/11 ni un budget global fictif."""
        cfg = _cfg(**{"okx-margin": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                                     "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}})
        peers = [f"s{i}::1h::BTC/USDC" for i in range(10)]
        edges = {p: None for p in peers}
        env = resolve_envelope(cfg, CRYPTO_VENUE, "BTC/USDC", peers[0],
                               peers=peers, edges=edges)
        assert env.slot_envelope == pytest.approx(100.0)
        assert env.slot_risk_amount == pytest.approx(2.5)

    def test_max_notional_on_spot_venue_equals_slot_envelope(self):
        """Spot -> levier 1 -> le plafond notionnel n'est PAS un multiple du
        capital alloue, contrairement au defaut historique (max_notional_pct
        global de 20% qui ignorait le budget reel du slot)."""
        cfg = _cfg(**{"okx-margin": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                                     "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}})
        env = resolve_envelope(cfg, CRYPTO_VENUE, "BTC/USDC", "a::1h::BTC/USDC",
                               peers=["a::1h::BTC/USDC"], edges={"a::1h::BTC/USDC": None})
        assert env.max_notional == pytest.approx(env.slot_envelope)

    def test_equity_venue_carries_its_min_notional(self):
        cfg = _cfg(**{"euronext-paper": {"capital": 10000, "max_symbol_exposure_pct": 0.25,
                                         "symbol_risk_pct": 0.02, "venue_risk_pct": 0.05}})
        env = resolve_envelope(cfg, EQUITY_VENUE, "AIR.PA", "b::1d::AIR.PA",
                               peers=["b::1d::AIR.PA"], edges={"b::1d::AIR.PA": None})
        assert env.symbol_envelope == pytest.approx(2500)
        assert env.symbol_risk_budget == pytest.approx(50)
        assert env.min_notional == pytest.approx(200.0)

    def test_unknown_venue_yields_zero_envelope_rather_than_crashing(self):
        """Config incomplete (venue non declaree dans risk.envelopes) : repli
        prudent a zero -- tout trade sera refuse plutot qu'un crash du live."""
        cfg = _cfg()   # aucune entree
        env = resolve_envelope(cfg, CRYPTO_VENUE, "BTC/USDC", "a::1h::BTC/USDC",
                               peers=["a::1h::BTC/USDC"], edges={"a::1h::BTC/USDC": None})
        assert env.slot_envelope == 0.0
        assert env.max_notional == 0.0


class TestReferenceEnvelope:
    def test_reference_ignores_the_weight(self):
        """La passe d'etude est fixe, independante de l'allocation courante --
        c'est ce qui la rend comparable d'un run a l'autre malgre un nombre de
        slots ou des poids d'edge qui changent."""
        cfg = _cfg(**{"okx-margin": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                                     "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}})
        peers = [f"s{i}::1h::BTC/USDC" for i in range(10)]
        env = resolve_envelope(cfg, CRYPTO_VENUE, "BTC/USDC", peers[0],
                               peers=peers, edges={p: None for p in peers})
        ref = with_reference_envelope(env, 1000.0)
        assert ref.slot_envelope == pytest.approx(1000.0)
        assert ref.slot_risk_amount == pytest.approx(25.0)
        # Le reste de l'enveloppe (budgets symbole/venue) est inchange : seule
        # l'echelle DU SLOT bouge, pas la structure des plafonds parents.
        assert ref.symbol_envelope == env.symbol_envelope
        assert ref.venue_risk_budget == env.venue_risk_budget
        assert ref.weight == env.weight
