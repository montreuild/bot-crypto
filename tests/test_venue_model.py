"""S11 — modèle de venue non ambigu (spot / margin sur OKX, actions au comptant).

Ce que ces tests verrouillent, et pourquoi :

1. **Une seule source de vérité.** Avant S11, `venues.default` vide faisait
   retomber la résolution sur `default_venue_from_cfg`, qui fabriquait une
   venue à partir des globales `exchange.margin` / `trading.margin_mode` /
   `trading.max_leverage`. Sur la config livrée, cette venue s'appelait
   `margin-isolated` — le nom exact d'une entrée de `venues.defs`, mais avec un
   levier différent (1 au lieu de 3). Deux objets homonymes et divergents.
2. **Le marché commande.** Une venue `spot` ne peut porter ni levier, ni
   `margin_mode`, ni taux d'emprunt : ces clés sont forcées et journalisées.
3. **Le comptant n'emprunte pas.** Ni le spot crypto, ni les actions Euronext.
"""
import pytest

from app.core.bot_identity import (
    Venue,
    _enforce_market_coherence,
    default_venue_from_cfg,
    resolve_venue,
)
from app.core.config import _validate_venues


# ── 1. Repli sur les globales : identifiable, jamais homonyme ────────────────

class TestDerivedVenue:
    def test_derived_venue_name_is_prefixed(self):
        """Le repli ne peut plus être confondu avec une venue déclarée."""
        cfg = {"trading": {"margin_mode": "isolated", "max_leverage": 1},
               "exchange": {"name": "okx", "margin": True}}
        v = default_venue_from_cfg(cfg)
        assert v.name == "auto:margin-isolated"
        assert v.market_type == "margin"

    def test_derived_spot_venue_name_is_prefixed(self):
        v = default_venue_from_cfg({"trading": {}, "exchange": {"name": "okx"}})
        assert v.name == "auto:spot"
        assert v.market_type == "spot"

    def test_declared_venue_wins_over_globals(self):
        """`venues.default` prime : les globales ne fabriquent plus rien."""
        cfg = {
            "trading": {"margin_mode": "isolated", "max_leverage": 1},
            "exchange": {"name": "okx", "margin": True},
            "venues": {"default": "spot",
                       "defs": {"spot": {"market_type": "spot"}}},
        }
        v = resolve_venue(cfg, "trend_rider", "1h", "BTC/USDC")
        assert v.name == "spot"
        assert v.market_type == "spot"
        assert v.max_leverage == 1


# ── 2. Cohérence marché ↔ clés margin ───────────────────────────────────────

class TestMarketCoherence:
    def test_spot_venue_cannot_carry_leverage(self, caplog):
        v = Venue(name="v", market_type="spot", max_leverage=3.0,
                  margin_mode="isolated", borrow_rate_daily=0.001)
        out = _enforce_market_coherence(v)
        assert out.max_leverage == 1.0
        assert out.margin_mode is None
        assert out.borrow_rate_daily is None
        assert "market_type=spot" in caplog.text

    def test_margin_venue_is_left_untouched(self):
        v = Venue(name="v", market_type="margin", margin_mode="isolated",
                  max_leverage=3.0, borrow_rate_daily=0.001)
        assert _enforce_market_coherence(v) == v

    def test_resolution_applies_the_guard(self):
        """Une def spot contradictoire est corrigée à la résolution, pas seulement
        dans le helper — c'est le chemin réellement emprunté par le bot."""
        cfg = {"venues": {"default": "bancal", "defs": {
            "bancal": {"market_type": "spot", "max_leverage": 5,
                       "margin_mode": "cross"},
        }}}
        v = resolve_venue(cfg, "s", "1h", "BTC/USDC")
        assert v.max_leverage == 1.0
        assert v.margin_mode is None


# ── 3. Emprunt : le marché décide ───────────────────────────────────────────

class TestBorrowByVenue:
    def test_spot_never_borrows(self):
        v = Venue(name="spot", market_type="spot")
        assert v.borrows is False
        assert v.effective_borrow_rate(0.00072) == 0.0

    def test_equity_spot_never_borrows(self):
        """Une action au comptant ne paie pas d'intérêt d'emprunt (le défaut
        d'avant S11 : ~30 %/an facturés sur un achat SBF 120)."""
        v = Venue(name="euronext-paper", market_type="spot",
                  asset_class="equity", quote_currency="EUR")
        assert v.borrows is False
        assert v.effective_borrow_rate(0.00072) == 0.0

    def test_margin_borrows_at_global_rate_by_default(self):
        v = Venue(name="m", market_type="margin")
        assert v.borrows is True
        assert v.effective_borrow_rate(0.00072) == pytest.approx(0.00072)

    def test_venue_rate_overrides_the_global_one(self):
        v = Venue(name="m", market_type="margin", borrow_rate_daily=0.002)
        assert v.effective_borrow_rate(0.00072) == pytest.approx(0.002)

    def test_perp_borrows(self):
        assert Venue(name="p", market_type="perp").borrows is True


# ── 4. Validation au chargement ─────────────────────────────────────────────

class TestValidateVenues:
    def _cfg(self, **venues):
        return {"trading": {"paper_mode": True}, "exchange": {}, "venues": venues}

    def test_defs_without_default_is_refused(self):
        cfg = self._cfg(defs={"spot": {"market_type": "spot"}}, assign={})
        with pytest.raises(ValueError, match="venues.default"):
            _validate_venues(cfg)

    def test_unknown_default_is_refused(self):
        cfg = self._cfg(default="fantome", defs={"spot": {}}, assign={})
        with pytest.raises(ValueError, match="fantome"):
            _validate_venues(cfg)

    def test_unknown_assign_target_is_refused(self):
        cfg = self._cfg(default="spot", defs={"spot": {}},
                        assign={"AIR.PA": "euronext-paper"})
        with pytest.raises(ValueError, match="euronext-paper"):
            _validate_venues(cfg)

    def test_valid_config_passes(self):
        cfg = self._cfg(default="spot", defs={"spot": {"market_type": "spot"}},
                        assign={})
        _validate_venues(cfg)   # ne lève pas

    def test_no_venues_block_is_still_valid(self):
        """Rétro-compat : une config sans bloc `venues:` reste chargeable."""
        _validate_venues({"trading": {}, "exchange": {}})

    def test_globals_contradicting_the_default_venue_warn(self, caplog):
        cfg = {"trading": {"paper_mode": True, "margin_mode": "isolated"},
               "exchange": {"margin": True},
               "venues": {"default": "spot",
                          "defs": {"spot": {"market_type": "spot"}}}}
        _validate_venues(cfg)
        assert "contredisent la venue par défaut" in caplog.text

    def test_paper_plus_margin_warns_with_the_right_remedy(self, caplog):
        """Le remède annoncé doit être le bon : basculer la VENUE. Mettre
        `exchange.margin: false` ne suffit pas — le taux est porté par la venue."""
        cfg = {"trading": {"paper_mode": True, "margin_mode": "isolated",
                           "borrow_rate_daily": 0.00072},
               "exchange": {"margin": True},
               "venues": {"default": "m",
                          "defs": {"m": {"market_type": "margin",
                                         "max_leverage": 3}}}}
        _validate_venues(cfg)
        assert "paper_mode" in caplog.text
        assert "venues.default" in caplog.text
