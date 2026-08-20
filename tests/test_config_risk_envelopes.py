"""S12 — validation des enveloppes de risque au chargement.

Cf. docs/CONCEPTION_ENVELOPPES_DE_RISQUE.md §3 : trois regles dures, refusant
le demarrage. Les regles plus fines (fenetre de stops, n_slots_max...) sont
dans risk_diagnostics.py (test_risk_diagnostics.py), pas ici -- elles ont
besoin des slots actifs reels, qu'app.core ne peut pas voir.
"""
import pytest

from app.core.config import _validate_risk_envelopes


def _cfg(**venues_risk):
    venues = venues_risk.pop("_venues", None) or {
        "default": "okx", "defs": {"okx": {"market_type": "spot"}}, "assign": {},
    }
    return {"venues": venues, "risk": {"envelopes": venues_risk}}


class TestExistence:
    def test_no_venues_defs_is_a_noop(self):
        """Repli legacy S11 : pas de venues.defs -> rien a valider."""
        _validate_risk_envelopes({"venues": {}, "risk": {}})

    def test_default_venue_without_envelope_is_refused(self):
        cfg = _cfg()   # risk.envelopes vide, mais venues.default="okx"
        with pytest.raises(ValueError, match="okx"):
            _validate_risk_envelopes(cfg)

    def test_assigned_venue_without_envelope_is_refused(self):
        cfg = {
            "venues": {"default": "okx",
                      "defs": {"okx": {}, "autre": {}},
                      "assign": {"AIR.PA": "autre"}},
            "risk": {"envelopes": {"okx": {"max_symbol_exposure_pct": 1.0,
                                          "symbol_risk_pct": 0.02, "venue_risk_pct": 0.03}}},
        }
        with pytest.raises(ValueError, match="autre"):
            _validate_risk_envelopes(cfg)

    def test_every_reachable_venue_covered_passes(self):
        cfg = _cfg(okx={"capital": 1000, "max_symbol_exposure_pct": 1.0,
                       "symbol_risk_pct": 0.05, "venue_risk_pct": 0.05})
        _validate_risk_envelopes(cfg)   # ne leve pas

    def test_unreachable_venue_is_not_required_to_have_an_envelope(self):
        """Une venue declaree dans defs mais jamais assignee/default n'a pas
        besoin d'enveloppe -- elle ne peut router aucun bot."""
        cfg = {
            "venues": {"default": "okx", "defs": {"okx": {}, "jamais-utilisee": {}}, "assign": {}},
            "risk": {"envelopes": {"okx": {"max_symbol_exposure_pct": 1.0,
                                          "symbol_risk_pct": 0.05, "venue_risk_pct": 0.05}}},
        }
        _validate_risk_envelopes(cfg)   # ne leve pas


class TestBounds:
    def test_max_symbol_exposure_pct_must_be_in_0_1(self):
        cfg = _cfg(okx={"max_symbol_exposure_pct": 1.5, "symbol_risk_pct": 0.02,
                       "venue_risk_pct": 0.03})
        with pytest.raises(ValueError, match="max_symbol_exposure_pct"):
            _validate_risk_envelopes(cfg)

    def test_max_symbol_exposure_pct_zero_is_refused(self):
        cfg = _cfg(okx={"max_symbol_exposure_pct": 0.0, "symbol_risk_pct": 0.02,
                       "venue_risk_pct": 0.03})
        with pytest.raises(ValueError, match="max_symbol_exposure_pct"):
            _validate_risk_envelopes(cfg)

    def test_symbol_risk_pct_over_10_percent_is_refused(self):
        cfg = _cfg(okx={"max_symbol_exposure_pct": 1.0, "symbol_risk_pct": 0.11,
                       "venue_risk_pct": 0.15})
        with pytest.raises(ValueError, match="symbol_risk_pct"):
            _validate_risk_envelopes(cfg)

    def test_venue_risk_pct_over_20_percent_is_refused(self):
        cfg = _cfg(okx={"max_symbol_exposure_pct": 1.0, "symbol_risk_pct": 0.02,
                       "venue_risk_pct": 0.25})
        with pytest.raises(ValueError, match="venue_risk_pct"):
            _validate_risk_envelopes(cfg)


class TestBudgetCoherence:
    def test_venue_budget_below_symbol_budget_is_refused(self):
        """venue_risk_pct < symbol_risk_pct : le budget symbole serait
        inatteignable meme par un seul symbole occupant toute l'enveloppe."""
        cfg = _cfg(okx={"max_symbol_exposure_pct": 1.0, "symbol_risk_pct": 0.05,
                       "venue_risk_pct": 0.03})
        with pytest.raises(ValueError, match="venue_risk_pct.*symbol_risk_pct"):
            _validate_risk_envelopes(cfg)

    def test_venue_budget_equal_to_symbol_budget_passes(self):
        cfg = _cfg(okx={"max_symbol_exposure_pct": 1.0, "symbol_risk_pct": 0.05,
                       "venue_risk_pct": 0.05})
        _validate_risk_envelopes(cfg)   # ne leve pas


class TestMonoSlotGuarantee:
    """Un symbole reduit a UN seul slot actif doit toujours pouvoir trader.

    Ce slot porte un poids de 1,0 : il risque `profile` % de l'enveloppe du
    symbole, contre un budget de `symbol_risk_pct` % de cette MEME enveloppe.
    Si le profil depasse le budget, RiskLedger refuse chaque ordre -- un bot
    vivant qui ne trade jamais. Le demarrage est refuse plutot que de laisser
    la panne se manifester en silence a la premiere promotion.
    """

    def _cfg_with(self, profile_pct, symbol_risk_pct):
        cfg = _cfg(okx={"capital": 1000, "max_symbol_exposure_pct": 1.0,
                       "symbol_risk_pct": symbol_risk_pct, "venue_risk_pct": 0.10})
        cfg["risk"]["profile"] = "p"
        cfg["risk"]["profiles"] = {"p": profile_pct}
        return cfg

    def test_profile_above_the_symbol_budget_is_refused(self):
        with pytest.raises(ValueError, match="un seul slot actif"):
            _validate_risk_envelopes(self._cfg_with(0.025, 0.02))

    def test_profile_equal_to_the_symbol_budget_passes(self):
        """Cas limite : le budget est exactement consomme par un trade. Le
        slot trade, mais sans marge pour un pyramidage."""
        _validate_risk_envelopes(self._cfg_with(0.025, 0.025))   # ne leve pas

    def test_profile_below_the_symbol_budget_passes(self):
        _validate_risk_envelopes(self._cfg_with(0.025, 0.05))    # ne leve pas

    def test_the_error_names_the_minimum_acceptable_value(self):
        """Le message doit dire quoi corriger, pas seulement que c'est faux."""
        with pytest.raises(ValueError, match="0.025"):
            _validate_risk_envelopes(self._cfg_with(0.025, 0.02))


class TestShippedConfig:
    def test_the_repository_config_passes_validation(self):
        """La config livree (config/risk.yaml) doit rester valide -- garde-fou
        de non-regression sur les valeurs arbitrees en S12 §7.1."""
        import os
        os.environ.setdefault("WEB_API_KEY", "dummy")
        from app.core.config import load_config
        cfg = load_config("config.yaml")
        assert "margin-isolated" in cfg["risk"]["envelopes"]
        assert "euronext-paper" in cfg["risk"]["envelopes"]
        # risk.yaml livré : 50 % max d'exposition par symbole (margin-isolated)
        assert cfg["risk"]["envelopes"]["margin-isolated"]["max_symbol_exposure_pct"] == 0.5
        assert "capital" not in cfg["trading"]
        assert "risk_per_trade" not in cfg["trading"]

    def test_every_shipped_venue_lets_a_mono_slot_symbol_trade(self):
        """Non-regression sur la garantie du §7.1 : aucune venue livree ne doit
        etre reglee de sorte qu'un symbole mono-slot soit mort-ne."""
        import os
        os.environ.setdefault("WEB_API_KEY", "dummy")
        from app.core.config import load_config
        from app.core.risk.envelope import trade_risk_pct
        cfg = load_config("config.yaml")
        risk_pct = trade_risk_pct(cfg)
        for name, env in cfg["risk"]["envelopes"].items():
            assert env["symbol_risk_pct"] >= risk_pct, (
                f"{name} : un slot seul sur son symbole ne pourrait pas trader"
            )
