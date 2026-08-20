"""Sizing live : la distance au stop utilisée pour dimensionner est CELLE du
stop réellement posé.

Le maillon manquant de la couverture. `compute_size(stop_dist=…)` était déjà
verrouillé (tests/test_sprint0_critical_fixes.py) et le stop posé à l'ouverture
aussi, mais rien ne vérifiait que les deux parlent de la même distance —
c'est-à-dire que `_initial_stop_distance` réplique fidèlement la logique de
`_open_position`. Si les deux divergent, le risque réel vaut
``risk% × (stop_réel / stop_supposé)`` : une divergence silencieuse de facteur
2,5 est possible sans qu'aucun test ne tombe.

Les trois chemins ont la même priorité des deux côtés :
``sl_atr_mult`` → ``stop_hint`` → trailing initial (``trail_wide × ATR``).
"""

import pytest

from app.core.trailing import TrailingStopManager
from app.live.position_open_mixin import PositionOpenMixin

ATR = 2.0
PRICE = 100.0
TRAIL_WIDE = 2.5


class _Harness(PositionOpenMixin):
    """Porte juste ce dont `_initial_stop_distance` a besoin."""

    def __init__(self, trail_wide: float = TRAIL_WIDE):
        self._trailing_cfg = {
            "mult": trail_wide, "grace_bars": 4, "breakeven_r": 1.2,
            "trail_tight_mult": 1.0, "lock_r": 2.5, "tight_r": 4.0,
            "lock_ratio": 0.60, "use_swing": True,
        }


def _stop_posed_by_open_position(signal: dict, trail_cfg: dict) -> float:
    """Réplique la SEULE branche « trailing » de `_open_position` (celle qui
    n'est pas triviale) pour comparer la distance obtenue."""
    trailing = TrailingStopManager(**trail_cfg)
    return trailing.initial_stop(PRICE, ATR, signal["side"])


# ── Les trois chemins, dans l'ordre de priorité ─────────────────────────────

class TestPriorityOrder:
    def test_sl_atr_mult_wins(self):
        h = _Harness()
        d = h._initial_stop_distance("long", PRICE, ATR,
                                     {"side": "long", "sl_atr_mult": 1.5,
                                      "stop_hint": 80.0})
        assert d == pytest.approx(1.5 * ATR)

    def test_stop_hint_when_no_mult(self):
        h = _Harness()
        d = h._initial_stop_distance("long", PRICE, ATR,
                                     {"side": "long", "stop_hint": 96.0})
        assert d == pytest.approx(4.0)

    def test_stop_hint_works_for_shorts(self):
        h = _Harness()
        d = h._initial_stop_distance("short", PRICE, ATR,
                                     {"side": "short", "stop_hint": 104.0})
        assert d == pytest.approx(4.0)

    def test_falls_back_on_the_trailing_width(self):
        h = _Harness()
        d = h._initial_stop_distance("long", PRICE, ATR, {"side": "long"})
        assert d == pytest.approx(TRAIL_WIDE * ATR)


# ── Le point dur : la distance dimensionnée = la distance posée ─────────────

class TestSizingMatchesThePosedStop:
    @pytest.mark.parametrize("side", ["long", "short"])
    def test_fallback_path_matches_the_trailing_stop(self, side):
        """Sans stop fourni par la stratégie, le sizing doit utiliser exactement
        la distance que `DynamicTrailingStop.init` va poser."""
        h = _Harness()
        signal = {"side": side}
        d_sizing = h._initial_stop_distance(side, PRICE, ATR, signal)
        stop_posed = _stop_posed_by_open_position(signal, h._trailing_cfg)
        assert d_sizing == pytest.approx(abs(PRICE - stop_posed))

    @pytest.mark.parametrize("trail_wide", [1.0, 2.5, 4.0])
    def test_the_match_holds_when_trail_wide_changes(self, trail_wide):
        """Le repli lit `live.trailing.trail_wide` — il ne doit pas rester collé
        à un 2,5 en dur. Sinon, changer le trailing en config ferait diverger le
        sizing du stop réel sans le moindre signal."""
        h = _Harness(trail_wide=trail_wide)
        signal = {"side": "long"}
        d_sizing = h._initial_stop_distance("long", PRICE, ATR, signal)
        stop_posed = _stop_posed_by_open_position(signal, h._trailing_cfg)
        assert d_sizing == pytest.approx(trail_wide * ATR)
        assert d_sizing == pytest.approx(abs(PRICE - stop_posed))

    def test_trail_override_is_honoured_on_both_sides(self):
        """Une stratégie qui élargit son trailing par signal doit voir le sizing
        suivre : sinon elle est dimensionnée sur un stop qu'elle n'a pas."""
        h = _Harness()
        signal = {"side": "long", "trail_override": {"trail_wide": 4.0}}
        d_sizing = h._initial_stop_distance("long", PRICE, ATR, signal)
        assert d_sizing == pytest.approx(4.0 * ATR)

        from app.live.position_open_mixin import _apply_trail_override
        merged = _apply_trail_override(h._trailing_cfg, signal["trail_override"])
        stop_posed = _stop_posed_by_open_position(signal, merged)
        assert d_sizing == pytest.approx(abs(PRICE - stop_posed))


# ── Le seul chemin qui sur-risque encore ────────────────────────────────────

class TestDegenerateFallback:
    def test_unparsable_stop_returns_zero(self):
        """Un `sl_atr_mult` non numérique renvoie 0.0 → `compute_size` retombe
        sur l'ATR brut, donc sur-risque d'un facteur = multiple du stop. C'est
        le seul chemin résiduel ; ce test le rend visible plutôt que théorique.
        """
        h = _Harness()
        d = h._initial_stop_distance("long", PRICE, ATR,
                                     {"side": "long", "sl_atr_mult": "abc"})
        assert d == 0.0

    def test_zero_distance_is_now_refused_instead_of_over_risking(self):
        """S12 : ce chemin ne sur-risque plus — il n'existe plus.

        Avant, une distance nulle faisait retomber ``compute_size`` sur l'ATR
        brut et produisait une taille 2,5x trop grosse, en silence. Desormais
        elle leve, et l'appelant compte ``stop_invalide`` puis refuse le trade.
        """
        from app.core.risk.envelope import Envelope
        from app.core.risk.gate import RiskGate

        rm = RiskGate({
            "trading": {"max_trades_per_minute": 3,
                        "daily_drawdown_limit": 0.05, "max_drawdown_global": 0.20},
            "venues": {"default": "v", "defs": {"v": {"max_leverage": 1}}, "assign": {}},
            "risk": {"profile": "t", "profiles": {"t": 0.01},
                     "envelopes": {"v": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                                        "symbol_risk_pct": 0.02, "venue_risk_pct": 0.05}}},
        })
        env = Envelope(
            venue="v", symbol="BTC/USDC", slot_key="s::1h::BTC/USDC", currency="USDC",
            venue_envelope=1000.0, venue_risk_budget=50.0,
            symbol_envelope=1000.0, symbol_risk_budget=20.0,
            slot_envelope=1000.0, slot_risk_amount=10.0,
            max_leverage=1.0, min_notional=0.0, trade_risk_pct=0.01, weight=1.0,
        )
        size_ok, _ = rm.compute_size(entry=PRICE, stop_dist=TRAIL_WIDE * ATR, env=env)
        assert size_ok > 0
        with pytest.raises(ValueError, match="stop_dist"):
            rm.compute_size(entry=PRICE, stop_dist=0.0, env=env)
