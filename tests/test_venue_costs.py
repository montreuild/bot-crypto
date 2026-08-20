"""G2 — quantification des tailles et coûts par venue (app/core/execution.py).

Le point sensible : ces formules sont partagées backtest ↔ live, et la
généralisation actions ne doit RIEN changer en crypto. Chaque test de
comportement actions a donc son pendant « venue crypto → identique à avant ».
"""
import pytest

from app.core.bot_identity import Venue
from app.core.execution import (
    close_pnl,
    quantize_price,
    quantize_size,
    trade_fees,
    venue_trade_cost,
)

CRYPTO = Venue(name="spot")                       # tous défauts = comportement historique
# S11 : c'est le `market_type` qui décide de l'emprunt — une venue spot ne paie
# aucun intérêt, une venue margin oui. Les deux fixtures sont donc nécessaires.
CRYPTO_MARGIN = Venue(name="margin-isolated", market_type="margin",
                      margin_mode="isolated", max_leverage=3)
EQUITY = Venue(
    name="euronext-paper", asset_class="equity", quote_currency="EUR",
    fractional=False, allow_short=False, tick_size=0.001,
    fee_pct=0.001, fee_fixed=0.0, fee_min=2.0,
    transaction_tax_pct=0.004, tax_on_buy_only=True,
)


# ── Quantification des tailles ─────────────────────────────────────────────

class TestQuantizeSize:
    def test_no_venue_is_identity(self):
        assert quantize_size(3.746291, None) == 3.746291

    def test_crypto_venue_is_identity(self):
        assert quantize_size(3.746291, CRYPTO) == 3.746291

    def test_non_fractional_venue_floors_to_integer(self):
        assert quantize_size(3.9, EQUITY) == 3.0
        assert quantize_size(1.0, EQUITY) == 1.0

    def test_rounds_down_never_up(self):
        """Arrondir au-dessus engagerait plus de risque que le sizing n'autorise."""
        assert quantize_size(9.999, EQUITY) == 9.0

    def test_below_one_unit_becomes_zero(self):
        """Signal clair pour l'appelant : capital insuffisant pour ce titre."""
        assert quantize_size(0.8, EQUITY) == 0.0

    def test_explicit_lot_size_takes_precedence(self):
        lots = Venue(name="lots", lot_size=25.0)
        assert quantize_size(140.0, lots) == 125.0

    def test_negative_or_zero_passthrough(self):
        assert quantize_size(0.0, EQUITY) == 0.0
        assert quantize_size(-1.0, EQUITY) == -1.0


class TestQuantizePrice:
    def test_no_tick_is_identity(self):
        assert quantize_price(123.456789, CRYPTO) == 123.456789
        assert quantize_price(123.456789, None) == 123.456789

    def test_snaps_to_tick_grid(self):
        assert quantize_price(123.45678, EQUITY) == pytest.approx(123.457)
        assert quantize_price(99.9994, EQUITY) == pytest.approx(99.999)


# ── Coûts ──────────────────────────────────────────────────────────────────

class TestVenueTradeCost:
    def test_without_venue_is_exactly_trade_fees(self):
        for price, size, rate in ((100.0, 2.5, 0.001), (43_211.7, 0.031, 0.0008)):
            assert venue_trade_cost(price, size, rate) == trade_fees(price, size, rate)

    def test_crypto_venue_is_exactly_trade_fees(self):
        """Une venue sans modèle de coûts déclaré ne coûte pas un centime de plus."""
        for price, size, rate in ((100.0, 2.5, 0.001), (43_211.7, 0.031, 0.0008)):
            assert venue_trade_cost(price, size, rate, venue=CRYPTO) == \
                   trade_fees(price, size, rate)

    def test_venue_fee_pct_overrides_default_rate(self):
        v = Venue(name="v", fee_pct=0.002)
        assert venue_trade_cost(1000.0, 1.0, 0.001, venue=v) == pytest.approx(2.0)

    def test_fixed_fee_added_per_order(self):
        v = Venue(name="v", fee_pct=0.0, fee_fixed=1.5)
        assert venue_trade_cost(1000.0, 1.0, 0.001, venue=v) == pytest.approx(1.5)

    def test_fee_floor_applies_on_small_orders(self):
        v = Venue(name="v", fee_pct=0.001, fee_min=5.0)
        assert venue_trade_cost(100.0, 1.0, 0.001, venue=v) == pytest.approx(5.0)   # 0.10 → 5.00
        assert venue_trade_cost(100_000.0, 1.0, 0.001, venue=v) == pytest.approx(100.0)

    def test_transaction_tax_hits_buys_only(self):
        """TTF française : due à l'acquisition, pas à la revente."""
        buy = venue_trade_cost(10_000.0, 1.0, 0.001, side="long",
                               venue=EQUITY, is_entry=True)
        sell = venue_trade_cost(10_000.0, 1.0, 0.001, side="long",
                                venue=EQUITY, is_entry=False)
        # Achat : commission max(10, 2) + TTF 0,4 % de 10 000 = 10 + 40.
        assert buy == pytest.approx(50.0)
        assert sell == pytest.approx(10.0)

    def test_short_pays_tax_on_the_buy_back(self):
        """Le rachat d'un short EST l'acquisition : c'est lui qui est taxé."""
        entry = venue_trade_cost(10_000.0, 1.0, 0.001, side="short",
                                 venue=EQUITY, is_entry=True)
        exit_ = venue_trade_cost(10_000.0, 1.0, 0.001, side="short",
                                 venue=EQUITY, is_entry=False)
        assert entry == pytest.approx(10.0)
        assert exit_ == pytest.approx(50.0)

    def test_tax_on_both_sides_when_configured(self):
        v = Venue(name="v", fee_pct=0.0, transaction_tax_pct=0.001,
                  tax_on_buy_only=False)
        for is_entry in (True, False):
            assert venue_trade_cost(1000.0, 1.0, 0.0, side="long", venue=v,
                                    is_entry=is_entry) == pytest.approx(1.0)


class TestClosePnlVenue:
    def test_margin_short_matches_historical_full_notional(self):
        """Un short emprunte l'actif entier : même montant qu'avant S11/F-04."""
        args = dict(side="short", entry=100.0, exit_price=90.0, size=2.0,
                    notional=200.0, fee_rate=0.001, daily_rate=0.0002,
                    hours_held=5.0)
        assert close_pnl(**args) == close_pnl(**args, venue=CRYPTO_MARGIN)

    def test_margin_long_at_3x_borrows_two_thirds(self):
        """F-04 : long ×3 n'emprunte que 2/3 du notionnel."""
        from app.core.execution import borrow_cost, venue_borrow_rate
        args = dict(side="long", entry=100.0, exit_price=110.0, size=2.0,
                    notional=200.0, fee_rate=0.001, daily_rate=0.0002,
                    hours_held=5.0)
        _, _, none_borrow = close_pnl(**args)
        _, _, lev3 = close_pnl(**args, venue=CRYPTO_MARGIN)
        # FIN-10 : venue=None → levier 1 → un long n'emprunte rien.
        assert none_borrow == 0.0
        full = borrow_cost(200.0, venue_borrow_rate(0.0002, CRYPTO_MARGIN), 5.0)
        assert lev3 == pytest.approx(full * (2.0 / 3.0))

    def test_spot_venue_charges_no_borrow(self):
        """S11 — une venue spot n'emprunte pas : intérêts nuls, frais inchangés.

        Le taux global reste renseigné (``daily_rate``) : c'est bien la venue,
        et non la config, qui annule le coût. Sans ce garde-fou, un achat au
        comptant (spot crypto ou action SBF 120) payait ~30 %/an d'intérêts
        fictifs, des deux côtés backtest et live.
        """
        args = dict(side="long", entry=100.0, exit_price=110.0, size=2.0,
                    notional=200.0, fee_rate=0.001, daily_rate=0.0002,
                    hours_held=5.0)
        pnl_spot, fees_spot, borrow_spot = close_pnl(**args, venue=CRYPTO)
        pnl_margin, fees_margin, borrow_margin = close_pnl(**args, venue=CRYPTO_MARGIN)

        assert borrow_spot == 0.0
        assert borrow_margin > 0.0
        assert fees_spot == fees_margin
        assert pnl_spot == pytest.approx(pnl_margin + borrow_margin)

    def test_equity_venue_charges_the_floor_on_exit(self):
        pnl, fees, borrow = close_pnl(
            side="long", entry=100.0, exit_price=110.0, size=2.0, notional=200.0,
            fee_rate=0.001, daily_rate=0.0, hours_held=5.0, venue=EQUITY,
        )
        # Sortie d'un long : pas de TTF, mais le plancher de courtage (2.0)
        # l'emporte sur 0,1 % de 220 = 0,22.
        assert fees == pytest.approx(2.0)
        assert pnl == pytest.approx(20.0 - 2.0)
        assert borrow == 0.0
