"""S11 — le contexte d'exécution facturé est affiché, backtest comme optimiseur.

Un PnL seul n'est pas interprétable : le même paramétrage donne des chiffres
très différents selon que l'instrument est résolu sur une venue spot ou margin
(l'emprunt n'est facturé que sur margin), et selon le modèle de frais (une
action paie une commission fixe, un plancher de courtage et une TTF que la
crypto ignore). Ces tests verrouillent trois choses :

1. `cost_model` rapporte les valeurs **réellement appliquées**, pas les valeurs
   configurées — c'est toute la différence sur une venue spot, où le taux
   d'emprunt de la config ne s'applique pas ;
2. le rendu texte nomme explicitement l'absence d'emprunt (une ligne manquante
   se lit comme un oubli, pas comme une information) ;
3. le modèle voyage jusqu'au résultat de backtest et à la fiche de job.
"""
import logging

import pytest

from app.core.bot_identity import Venue
from app.core.execution import cost_model, format_cost_model

CFG = {
    "trading": {"taker_fee": 0.001, "maker_fee": 0.0008,
                "borrow_rate_daily": 0.00072, "borrow_periods_per_day": 24},
    "backtest": {"spread_pct": 0.0005, "partial_fill_pct": 0.95,
                 "slippage_model": "static", "slippage_k": 1.0,
                 "max_notional_pct": 0.20},
}

MARGIN = Venue(name="margin-isolated", market_type="margin",
               margin_mode="isolated", max_leverage=3.0, exchange="okx")
SPOT = Venue(name="spot", market_type="spot", max_leverage=1.0,
             allow_short=False, exchange="okx")
ACTION = Venue(
    name="euronext-paper", market_type="spot", exchange="euronext",
    asset_class="equity", quote_currency="EUR", fractional=False,
    allow_short=False, tick_size=0.001, min_notional=200.0, calendar="XPAR",
    data_provider="yfinance", can_execute=False,
    fee_pct=0.001, fee_fixed=0.0, fee_min=2.0,
    transaction_tax_pct=0.004, tax_on_buy_only=True,
)


# ── 1. Valeurs appliquées, pas valeurs configurées ──────────────────────────

class TestCostModel:
    def test_margin_reports_the_configured_borrow(self):
        m = cost_model(CFG, MARGIN)
        assert m["market_type"] == "margin"
        assert m["margin_mode"] == "isolated"
        assert m["max_leverage"] == 3.0
        assert m["borrows"] is True
        assert m["borrow_rate_daily"] == pytest.approx(0.00072)
        # Un taux journalier ne parle pas ; l'équivalent annuel composé, si.
        assert m["borrow_rate_annual"] == pytest.approx(0.30, abs=0.02)

    def test_spot_reports_zero_borrow_despite_the_config(self):
        """Le point crucial : la config déclare un taux, la venue ne l'applique
        pas. Rapporter la valeur configurée serait un affichage mensonger."""
        m = cost_model(CFG, SPOT)
        assert m["borrows"] is False
        assert m["borrow_rate_daily"] == 0.0
        assert m["borrow_rate_annual"] == 0.0

    def test_equity_reports_its_own_fee_model(self):
        m = cost_model(CFG, ACTION)
        # `fee_pct` de la venue prime sur trading.taker_fee.
        assert m["fee_rate_taker"] == pytest.approx(0.001)
        assert m["fee_pct_override"] == pytest.approx(0.001)
        assert m["fee_min"] == 2.0
        assert m["transaction_tax_pct"] == pytest.approx(0.004)
        assert m["tax_on_buy_only"] is True
        assert m["fractional"] is False
        assert m["min_notional"] == 200.0
        assert m["borrows"] is False

    def test_no_venue_keeps_the_global_rates(self):
        """Sans venue (comportement historique), les globales s'appliquent."""
        m = cost_model(CFG, None)
        assert m["fee_rate_taker"] == pytest.approx(0.001)
        assert m["fee_rate_maker"] == pytest.approx(0.0008)
        assert m["borrows"] is True
        assert m["borrow_rate_daily"] == pytest.approx(0.00072)

    def test_venue_borrow_rate_overrides_the_global(self):
        v = Venue(name="m", market_type="margin", borrow_rate_daily=0.002)
        assert cost_model(CFG, v)["borrow_rate_daily"] == pytest.approx(0.002)


# ── 2. Rendu texte ──────────────────────────────────────────────────────────

class TestFormatting:
    def test_margin_line_states_market_and_leverage(self):
        txt = format_cost_model(cost_model(CFG, MARGIN), "BTC/USDC", "1h")
        assert "BTC/USDC 1h" in txt
        assert "okx:margin/isolated" in txt
        assert "levier ×3" in txt
        assert "taker 0.100 %" in txt
        assert "emprunt" in txt and "/jour" in txt and "/an" in txt

    def test_spot_says_out_loud_that_there_is_no_borrow(self):
        txt = format_cost_model(cost_model(CFG, SPOT), "BTC/USDC", "1h")
        assert "pas d'emprunt" in txt
        assert "marché spot" in txt

    def test_equity_line_details_the_broker_costs(self):
        txt = format_cost_model(cost_model(CFG, ACTION), "AIR.PA", "1d")
        assert "euronext:spot" in txt
        assert "equity/EUR" in txt
        assert "[XPAR]" in txt
        assert "(data-only)" in txt
        assert "short interdit" in txt
        assert "plancher 2.00" in txt
        assert "taxe transaction" in txt and "à l'achat" in txt
        assert "quantité entière" in txt
        assert "notionnel min 200.00" in txt
        assert "pas d'emprunt" in txt

    def test_crypto_line_omits_the_equity_only_costs(self):
        """Afficher « plancher 0.00 » ou « taxe 0.000 % » en crypto ajouterait
        du bruit sans information."""
        txt = format_cost_model(cost_model(CFG, MARGIN), "BTC/USDC", "1h")
        assert "plancher" not in txt
        assert "taxe transaction" not in txt
        assert "quantité entière" not in txt


# ── 3. Le modèle voyage jusqu'aux consommateurs ─────────────────────────────

def _mini_ohlcv(n: int = 400):
    from datetime import datetime, timedelta

    import numpy as np
    import polars as pl
    rng = np.random.default_rng(7)
    close = 30_000 + np.cumsum(rng.standard_normal(n) * 50)
    t0 = datetime(2025, 1, 1)
    return pl.DataFrame({
        "time": [t0 + timedelta(hours=i) for i in range(n)],
        "open": close, "high": close * 1.002, "low": close * 0.998,
        "close": close, "volume": rng.uniform(500, 2000, n),
    })


class TestPropagation:
    def _run(self, venues: dict):
        import importlib

        from app.engine.backtest import Backtester
        from app.engine.engine import Engine

        cfg = {**CFG, "trading": {**CFG["trading"], "capital": 1000,
                                  "risk_per_trade": 0.01, "timeframe": "1h"},
               "strategy_params": {}, "venues": venues}
        eng = Engine()
        eng.register(importlib.import_module("app.strategies.trend_rider").Strategy(),
                     silent=True)
        return Backtester(eng, cfg).run(_mini_ohlcv(), "BTC/USDC", timeframe="1h")

    def test_backtest_result_carries_the_cost_model(self):
        d = self._run({"default": "margin-isolated", "defs": {
            "margin-isolated": {"market_type": "margin", "margin_mode": "isolated",
                                "max_leverage": 3}}}).to_dict()
        assert d["cost_model"]["market_type"] == "margin"
        assert d["cost_model"]["max_leverage"] == 3.0
        assert d["cost_model"]["borrows"] is True

    def test_the_cost_model_follows_the_venue_not_the_config(self):
        """Même config de frais, venue différente ⇒ contexte rapporté différent.
        C'est ce qui rend deux runs comparables de bonne foi."""
        d = self._run({"default": "spot",
                       "defs": {"spot": {"market_type": "spot"}}}).to_dict()
        assert d["cost_model"]["borrows"] is False
        assert d["cost_model"]["borrow_rate_daily"] == 0.0

    def test_backtest_announces_the_context_in_the_logs(self, caplog):
        from app.core.log_throttle import reset_throttle
        reset_throttle()
        with caplog.at_level(logging.INFO):
            self._run({"default": "spot", "defs": {"spot": {"market_type": "spot"}}})
        assert "[Coûts]" in caplog.text
        assert "pas d'emprunt" in caplog.text


# ── 4. La route API expose le contexte ──────────────────────────────────────
#
# Piège vérifié en conditions réelles : `POST /api/backtest` construit son
# PROPRE payload et ne renvoie PAS `BacktestResult.to_dict()`. Le champ
# existait donc côté moteur, passait tous les tests unitaires, et n'atteignait
# jamais l'UI — la carte restait invisible. Ce test ferme ce trou.

class TestApiRoute:
    def test_the_route_payload_exposes_the_cost_model(self):
        from app.api import state
        from app.api.routes.backtest import _cost_model_for

        cfg = {**CFG,
               "trading": {**CFG["trading"], "capital": 1000, "risk_per_trade": 0.01},
               "venues": {"default": "margin-isolated",
                          "defs": {"margin-isolated": {"market_type": "margin",
                                                       "margin_mode": "isolated"}}}}
        saved = getattr(state, "cfg", None)
        state.cfg = cfg
        try:
            m = _cost_model_for("BTC/USDC", "1h")
        finally:
            state.cfg = saved

        assert m, "la route doit exposer le contexte, pas un dict vide"
        assert m["market_type"] == "margin"
        assert m["borrows"] is True

    def test_the_route_helper_never_breaks_a_backtest(self):
        """Best-effort : un contexte indisponible masque la carte, il ne doit
        pas faire échouer le backtest lui-même."""
        from app.api import state
        from app.api.routes.backtest import _cost_model_for

        saved = getattr(state, "cfg", None)
        state.cfg = None            # état non initialisé
        try:
            assert _cost_model_for("BTC/USDC", "1h") == {}
        finally:
            state.cfg = saved
