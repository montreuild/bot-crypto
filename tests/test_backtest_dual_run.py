"""S12 — double passe `reference` / `live` : invariance d'echelle.

Deux questions differentes (§5.1) : la passe `live` repond a « ce bot est-il
promouvable ? » (son enveloppe reelle), la passe `reference` a « cette
strategie vaut-elle quelque chose ? » (une echelle fixe, comparable entre
tous les bots).

Le sizing etant lineaire en l'enveloppe, tout ecart de PnL % entre les deux
est imputable aux contraintes ABSOLUES -- notionnel minimum, lot indivisible,
frais fixes. Sur une venue fractionnable et sans minimum, l'ecart doit etre
NUL ; sur une venue actions, il existe ET les compteurs de refus disent
pourquoi. C'est cette derniere propriete qui rend la divergence explicable
plutot que constatee.
"""
from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

from app.core.risk_envelope import Envelope
from app.engine.backtest import Backtester, run_dual_pass
from app.engine.engine import BaseStrategy, Engine


def _make_df(n=400):
    np.random.seed(7)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(max(closes[-1] + np.random.randn() * 0.6 + 0.05, 1.0))
    closes = np.array(closes)
    highs = closes * (1 + np.abs(np.random.randn(n) * 0.004))
    lows = closes * (1 - np.abs(np.random.randn(n) * 0.004))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    start = datetime(2024, 1, 1)
    return pl.DataFrame({
        "time": [start + timedelta(hours=i) for i in range(n)],
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": np.random.uniform(1000, 5000, n),
    })


class _AlwaysLong(BaseStrategy):
    name = "dummy"

    def score(self, df, params=None, df_htf=None, **kw):
        if len(df) < 30:
            return {"side": "none", "score": 0}
        return {"side": "long", "score": 0.9, "name": self.name,
                "reason": "test", "sl_atr_mult": 2.0}


def _engine():
    eng = Engine()
    eng.register(_AlwaysLong())
    return eng


def _cfg(**over):
    cfg = {
        "trading": {"timeframe": "1h", "paper_mode": True, "taker_fee": 0.001,
                    "maker_fee": 0.0004, "score_threshold": 0.55,
                    "borrow_rate_daily": 0.0},
        "backtest": {"spread_pct": 0.0, "latency_ms": 0, "partial_fill_pct": 1.0,
                     "atr_stop_mult": 2.0, "trail_wide": 2.5, "trail_normal": 2.0,
                     "trail_lock": 1.5, "trail_tight": 1.0, "grace_bars": 4,
                     "breakeven_r": 1.2, "lock_r": 2.5, "tight_r": 4.0,
                     "lock_ratio": 0.60, "use_swing": False,
                     "reference_envelope": 1000},
        "venues": {"default": "v", "defs": {"v": {"max_leverage": 1}}, "assign": {}},
        "risk": {"profile": "p", "profiles": {"p": 0.025},
                 "envelopes": {"v": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                                    "symbol_risk_pct": 0.05, "venue_risk_pct": 0.10}}},
        "strategies": {"enabled": ["dummy"]},
        "strategy_params": {},
    }
    for k, v in over.items():
        cfg[k] = {**cfg.get(k, {}), **v} if isinstance(v, dict) else v
    return cfg


def _env(slot_envelope, *, min_notional=0.0, leverage=1.0):
    return Envelope(
        venue="v", symbol="BTC/USDC", slot_key="dummy::1h::BTC/USDC", currency="USDC",
        venue_envelope=1000.0, venue_risk_budget=100.0,
        symbol_envelope=1000.0, symbol_risk_budget=50.0,
        slot_envelope=slot_envelope, slot_risk_amount=slot_envelope * 0.025,
        max_leverage=leverage, min_notional=min_notional,
        trade_risk_pct=0.025, weight=slot_envelope / 1000.0,
    )


def _pnl_pct(result) -> float:
    return result.total_pnl / result.initial_capital * 100.0


# La taille est arrondie a la baisse au millionieme d'unite : c'est un quantum
# ABSOLU, qui ne se met donc pas a l'echelle avec l'enveloppe. Il subsiste a ce
# titre un ecart residuel entre les deux passes, de l'ordre de 1e-6 en relatif
# — mesure, pas suppose. On l'enonce plutot que de le masquer : l'invariance
# est exacte "aux contraintes absolues pres", et l'arrondi de taille en est une.
_SCALE_TOL = 1e-4


class TestScaleInvarianceOnAFractionalVenue:
    """Crypto : fractionnable, pas de notionnel minimum, frais proportionnels.
    Rien d'absolu ne mord (hors quantum d'arrondi) => passes homothetiques."""

    def test_same_trade_count_and_same_pnl_pct(self):
        df = _make_df()
        out = run_dual_pass(_engine(), _cfg(), df, _env(90.0), symbol="BTC/USDC")
        live, ref = out["live"], out["reference"]

        assert live.total_trades > 0, "le scenario doit produire des trades"
        assert live.total_trades == ref.total_trades
        assert _pnl_pct(live) == pytest.approx(_pnl_pct(ref), rel=_SCALE_TOL)

    def test_the_reference_pass_uses_the_configured_fixed_scale(self):
        df = _make_df()
        out = run_dual_pass(_engine(), _cfg(), df, _env(90.0), symbol="BTC/USDC")
        assert out["reference"].initial_capital == 1000
        assert out["live"].initial_capital == 90.0

    def test_a_tiny_envelope_still_tracks_the_reference(self):
        """Meme a 12 EUR d'enveloppe, sans contrainte absolue, le comportement
        relatif est identique -- c'est la definition de l'invariance."""
        df = _make_df()
        out = run_dual_pass(_engine(), _cfg(), df, _env(12.0), symbol="BTC/USDC")
        assert out["live"].total_trades == out["reference"].total_trades
        assert _pnl_pct(out["live"]) == pytest.approx(_pnl_pct(out["reference"]),
                                                      rel=_SCALE_TOL)


class TestDivergenceOnAnEquityVenueIsExplained:
    """Actions : titres entiers, notionnel minimum 200, plancher de courtage.
    L'ecart existe -- et les compteurs disent lequel de ces trois seuils mord."""

    def test_a_small_envelope_is_blocked_by_the_minimum_notional(self):
        df = _make_df()
        cfg = _cfg()
        # Enveloppe de 100 EUR contre un notionnel minimum de 200 : aucun trade
        # ne peut passer, quelle que soit la volatilite.
        bt = Backtester(_engine(), cfg, envelope=_env(100.0, min_notional=200.0))
        result = bt.run(df, symbol="AIR.PA")

        assert result.total_trades == 0
        motifs = result.rejections["par_motif"]
        assert motifs.get("notionnel_min", 0) > 0, (
            f"la divergence doit etre expliquee par un motif, pas subie : {motifs}"
        )

    def test_the_reference_pass_trades_where_the_live_one_cannot(self):
        """La passe d'etude (1 000 EUR) franchit le minimum, la passe live
        (100 EUR) non. C'est exactement l'ecart que la double passe existe pour
        rendre visible : la strategie n'est pas en cause, l'allocation l'est."""
        df = _make_df()
        out = run_dual_pass(_engine(), _cfg(), df,
                            _env(100.0, min_notional=200.0), symbol="AIR.PA")

        assert out["live"].total_trades == 0
        assert out["reference"].total_trades > 0
        assert out["live"].rejections["par_motif"].get("notionnel_min", 0) > 0
        assert out["reference"].rejections["par_motif"].get("notionnel_min", 0) == 0

    def test_rejection_codes_are_the_shared_vocabulary(self):
        from app.core.rejections import REASONS
        df = _make_df()
        bt = Backtester(_engine(), _cfg(), envelope=_env(100.0, min_notional=200.0))
        result = bt.run(df, symbol="AIR.PA")
        assert set(result.rejections["par_motif"]) <= set(REASONS)


class TestResultPayload:
    def test_rejections_are_exposed_in_to_dict(self):
        """Le payload doit porter les compteurs : c'est par la que l'UI et
        l'optimiseur expliquent un backtest qui ne trade pas."""
        df = _make_df()
        bt = Backtester(_engine(), _cfg(), envelope=_env(90.0))
        d = bt.run(df, symbol="BTC/USDC").to_dict()
        assert "rejections" in d
        assert set(d["rejections"]) >= {"total", "par_motif"}

    def test_initial_capital_reports_the_slot_envelope(self):
        df = _make_df()
        bt = Backtester(_engine(), _cfg(), envelope=_env(90.0))
        assert bt.run(df, symbol="BTC/USDC").to_dict()["initial_capital"] == 90.0

    def test_without_an_envelope_the_default_venue_capital_is_used(self):
        """Etudes libres (walk-forward historique) : pas d'enveloppe, on
        retombe sur le capital de la venue par defaut."""
        df = _make_df()
        bt = Backtester(_engine(), _cfg())
        assert bt.run(df, symbol="BTC/USDC").initial_capital == 1000


# ── La double passe est OPTIONNELLE, pilotée par l'UI ───────────────────────

class TestRouteWiring:
    """`POST /api/backtest?dual_pass=true` — la passe d'étude coûte un run
    complet de plus. Elle est donc explicite : par défaut le comportement est
    strictement celui d'avant, et l'optimiseur ne la déclenche jamais (§5.1)."""

    def test_the_route_exposes_dual_pass_as_an_opt_in_flag(self):
        import inspect

        from app.api.routes.backtest import run_backtest
        sig = inspect.signature(run_backtest)
        assert "dual_pass" in sig.parameters
        assert sig.parameters["dual_pass"].default is False, (
            "la double passe doit être opt-in : activée par défaut, elle "
            "doublerait le temps de chaque backtest"
        )

    def test_the_pass_summary_expresses_pnl_on_its_own_base(self):
        """Chaque passe rapporte son PnL en % de SA base — sinon les deux ne se
        comparent pas, ce qui est tout l'objet de l'exercice."""
        from app.api.routes.backtest import _pass_summary
        bt = Backtester(_engine(), _cfg(), envelope=_env(90.0))
        summary = _pass_summary(bt.run(_make_df(), symbol="BTC/USDC"))
        assert summary["initial_capital"] == 90.0
        # Les deux champs sont arrondis séparément à 4 décimales : la tolérance
        # couvre cet arrondi, pas une approximation du calcul.
        assert summary["pnl_pct"] == pytest.approx(
            summary["total_pnl"] / 90.0 * 100, rel=1e-4)
        assert "rejections" in summary

    def test_a_zero_base_does_not_divide_by_zero(self):
        from app.api.routes.backtest import _pass_summary

        class _Res:
            initial_capital = 0.0
            total_trades = 0
            total_pnl = 0.0
            max_drawdown = 0.0
            rejections = {}
        assert _pass_summary(_Res())["pnl_pct"] == 0.0

    def test_the_envelope_payload_carries_the_base_alongside_the_risk(self):
        """Règle d'affichage du §6 : jamais un montant de risque sans sa base."""
        from app.api.routes.backtest import _envelope_payload
        payload = _envelope_payload(_env(90.0))
        assert payload["slot_envelope"] == 90.0
        assert payload["risk_amount"] == pytest.approx(2.25)
        assert payload["trade_risk_pct"] == 0.025
        assert _envelope_payload(None) is None
