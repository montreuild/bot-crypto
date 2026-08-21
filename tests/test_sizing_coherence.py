"""S12 — invariant central : ce que le sizer produit, le ledger l'accepte.

Le defaut d'origine (docs/CONCEPTION_ENVELOPPES_DE_RISQUE.md §1) etait un
notionnel calcule sur une base (l'equite globale) et confronte a un plafond
calcule sur une autre (le budget du slot) : 200 EUR contre un plafond de
95 EUR, refus systematique, jamais detecte. Ce fichier verrouille l'invariant
qui rend ce bug impossible -- sur des centaines de cas generes (§9).

Registre VIDE : c'est la seule situation ou le sizer peut garantir quoi que
ce soit. Sur un registre deja charge, un refus est le comportement voulu --
c'est le role du ledger d'arbitrer entre slots concurrents.
"""
import itertools

import pytest

from app.core.risk.envelope import Envelope
from app.core.risk.ledger import RiskLedger
from app.core.risk.sizer import RiskSizer, _floor_to


class _Sizer(RiskSizer):
    """Hote minimal du mixin — pas de drawdown, pas de frein de volatilite,
    pour isoler l'arithmetique d'enveloppe."""

    def __init__(self, equity=1000.0, peak=1000.0, brake=1.0):
        import threading
        self._lock = threading.RLock()
        self.equity = equity
        self.peak_equity = peak
        self.volatility_brake_factor = brake
        self.base_risk = 0.025
        self.open_positions = {}


def _env(*, slot_envelope, symbol_envelope, leverage, min_notional=0.0,
        risk_pct=0.025, symbol_risk_pct=0.02, venue_risk_pct=0.03) -> Envelope:
    return Envelope(
        venue="v", symbol="S", slot_key="k", currency="EUR",
        venue_envelope=symbol_envelope, venue_risk_budget=symbol_envelope * venue_risk_pct,
        symbol_envelope=symbol_envelope, symbol_risk_budget=symbol_envelope * symbol_risk_pct,
        slot_envelope=slot_envelope, slot_risk_amount=slot_envelope * risk_pct,
        max_leverage=leverage, min_notional=min_notional,
        trade_risk_pct=risk_pct, weight=slot_envelope / symbol_envelope,
    )


class TestStopDistanceIsMandatory:
    def test_zero_stop_distance_raises(self):
        """Le repli sur l'ATR brut est supprime : il faisait risquer
        risk% x (stop_dist/ATR), soit ~2,5x le risque annonce."""
        sizer = _Sizer()
        env = _env(slot_envelope=100.0, symbol_envelope=1000.0, leverage=1.0)
        with pytest.raises(ValueError, match="stop_dist"):
            sizer.compute_size(entry=100.0, stop_dist=0.0, env=env)

    def test_negative_stop_distance_raises(self):
        sizer = _Sizer()
        env = _env(slot_envelope=100.0, symbol_envelope=1000.0, leverage=1.0)
        with pytest.raises(ValueError, match="stop_dist"):
            sizer.compute_size(entry=100.0, stop_dist=-1.0, env=env)

    def test_zero_entry_raises(self):
        sizer = _Sizer()
        env = _env(slot_envelope=100.0, symbol_envelope=1000.0, leverage=1.0)
        with pytest.raises(ValueError, match="entry"):
            sizer.compute_size(entry=0.0, stop_dist=1.0, env=env)


class TestSingleBase:
    def test_risk_engaged_equals_the_slot_risk_amount(self):
        """Hors plafond, le risque engage vaut EXACTEMENT slot_risk_amount --
        quelle que soit la distance au stop. C'est la definition d'un sizing
        par le risque.

        Stops >= 2,5 % du prix : en deca, le plafond notionnel du slot mord
        avant le budget de risque et le risque engage devient MOINDRE que
        l'entitlement (cf. diagnostic plafond_notionnel_mordant)."""
        sizer = _Sizer()
        env = _env(slot_envelope=1000.0, symbol_envelope=10_000.0, leverage=1.0)
        for stop_dist in (2.5, 5.0, 7.3, 12.0):
            size, _ = sizer.compute_size(entry=100.0, stop_dist=stop_dist, env=env)
            assert size * stop_dist == pytest.approx(env.slot_risk_amount, rel=1e-4)

    def test_equity_does_not_influence_sizing(self):
        """La base est l'enveloppe du slot, PAS l'equite : deux moteurs
        d'equites tres differentes produisent la meme taille."""
        env = _env(slot_envelope=100.0, symbol_envelope=1000.0, leverage=1.0)
        a = _Sizer(equity=1_000.0, peak=1_000.0).compute_size(100.0, 2.0, env)
        b = _Sizer(equity=50_000.0, peak=50_000.0).compute_size(100.0, 2.0, env)
        assert a == b

    def test_score_no_longer_modulates_size(self):
        """Le facteur de score interne est supprime (il cassait la parite
        backtest) : compute_size n'accepte plus ni score ni threshold."""
        import inspect
        params = set(inspect.signature(RiskSizer.compute_size).parameters)
        assert "score" not in params
        assert "threshold" not in params
        assert "budget" not in params
        assert "atr" not in params


class TestSizeFactor:
    def test_size_factor_scales_the_risk(self):
        sizer = _Sizer()
        env = _env(slot_envelope=1000.0, symbol_envelope=10_000.0, leverage=1.0)
        full, _ = sizer.compute_size(100.0, 10.0, env, size_factor=1.0)
        half, _ = sizer.compute_size(100.0, 10.0, env, size_factor=0.5)
        assert half == pytest.approx(full / 2, rel=1e-4)

    def test_size_factor_is_clamped_to_two(self):
        sizer = _Sizer()
        env = _env(slot_envelope=1000.0, symbol_envelope=10_000.0, leverage=1.0)
        at_two, _ = sizer.compute_size(100.0, 10.0, env, size_factor=2.0)
        beyond, _ = sizer.compute_size(100.0, 10.0, env, size_factor=99.0)
        assert beyond == at_two

    def test_negative_size_factor_yields_no_position(self):
        sizer = _Sizer()
        env = _env(slot_envelope=1000.0, symbol_envelope=10_000.0, leverage=1.0)
        size, notional = sizer.compute_size(100.0, 10.0, env, size_factor=-1.0)
        assert size == 0.0
        assert notional == 0.0


class TestConjuncturalReductions:
    def test_volatility_brake_halves_the_size(self):
        env = _env(slot_envelope=1000.0, symbol_envelope=10_000.0, leverage=1.0)
        normal, _ = _Sizer(brake=1.0).compute_size(100.0, 10.0, env)
        braked, _ = _Sizer(brake=0.5).compute_size(100.0, 10.0, env)
        assert braked == pytest.approx(normal / 2, rel=1e-4)

    def test_drawdown_curve_still_applies(self):
        """BT-09 : au-dela de 10% de drawdown, le risque engage est divise par
        deux -- la courbe partagee backtest/live survit a la refonte."""
        env = _env(slot_envelope=1000.0, symbol_envelope=10_000.0, leverage=1.0)
        flat, _ = _Sizer(equity=1000.0, peak=1000.0).compute_size(100.0, 10.0, env)
        deep, _ = _Sizer(equity=850.0, peak=1000.0).compute_size(100.0, 10.0, env)
        assert deep == pytest.approx(flat * 0.5, rel=1e-4)


class TestNotionalCap:
    def test_notional_never_exceeds_the_slot_cap(self):
        """Stop tres serre -> taille enorme -> le plafond notionnel mord."""
        sizer = _Sizer()
        env = _env(slot_envelope=100.0, symbol_envelope=1000.0, leverage=1.0)
        _, notional = sizer.compute_size(entry=100.0, stop_dist=0.001, env=env)
        assert notional <= env.max_notional

    def test_leverage_raises_the_cap(self):
        sizer = _Sizer()
        lev1 = _env(slot_envelope=100.0, symbol_envelope=1000.0, leverage=1.0)
        lev3 = _env(slot_envelope=100.0, symbol_envelope=1000.0, leverage=3.0)
        _, n1 = sizer.compute_size(100.0, 0.001, lev1)
        _, n3 = sizer.compute_size(100.0, 0.001, lev3)
        assert n1 == pytest.approx(100.0)
        assert n3 == pytest.approx(300.0)

    def test_rounding_is_downward_never_upward(self):
        """Les plafonds du ledger sont stricts : un arrondi au plus proche
        pourrait franchir le plafond d'un demi-ulp et faire refuser un trade
        que le sizer croyait conforme."""
        assert _floor_to(1.99999999, 4) == 1.9999
        assert _floor_to(100.00005, 4) == 100.0


# ── L'invariant, sur cas generes ────────────────────────────────────────────

_ENTRIES = (0.05, 1.0, 37.42, 1000.0, 68_420.0)
_STOPS_PCT = (0.001, 0.005, 0.025, 0.08, 0.30)
_WEIGHTS = (0.05, 0.1, 0.34, 0.5, 1.0)
_LEVERAGES = (1.0, 3.0, 5.0)
_MIN_NOTIONALS = (0.0, 10.0, 200.0)


class TestSizerLedgerCoherence:
    """Le test qui aurait attrape le bug d'origine."""

    def test_every_generated_size_is_accepted_by_an_empty_ledger(self):
        sizer = _Sizer()
        cases = list(itertools.product(_ENTRIES, _STOPS_PCT, _WEIGHTS,
                                       _LEVERAGES, _MIN_NOTIONALS))
        assert len(cases) >= 500, f"couverture insuffisante : {len(cases)} cas"

        refused = []
        for entry, stop_pct, weight, leverage, min_notional in cases:
            symbol_envelope = 10_000.0
            env = _env(slot_envelope=symbol_envelope * weight,
                      symbol_envelope=symbol_envelope, leverage=leverage,
                      min_notional=min_notional,
                      # risk_pct <= symbol_risk_pct : sans quoi un slot a poids
                      # 1.0 depasse a lui seul le budget du symbole (cf.
                      # diagnostic budget_symbole_insuffisant).
                      risk_pct=0.02, symbol_risk_pct=0.02, venue_risk_pct=0.03)
            size, notional = sizer.compute_size(entry, entry * stop_pct, env)
            if size <= 0:
                continue
            risk = sizer.engaged_risk(entry, entry * (1 - stop_pct), size)
            decision = RiskLedger().reserve(env, risk=risk, notional=notional,
                                            pos_key="p")
            if not decision.allowed and decision.reason_code != "notionnel_min":
                refused.append((entry, stop_pct, weight, leverage, min_notional,
                                decision.reason_code, decision.detail))

        assert refused == [], (
            f"{len(refused)} cas ou le sizer produit un trade que le ledger "
            f"refuse — les deux ne partagent pas la meme base. "
            f"Premiers cas : {refused[:3]}"
        )

    def test_a_notional_below_the_venue_minimum_is_the_only_legitimate_refusal(self):
        """`notionnel_min` est structurel : l'enveloppe du slot est trop
        petite pour la venue. Le sizer ne peut PAS le corriger (gonfler la
        taille depasserait le budget de risque) -- c'est un probleme de
        configuration, signale par risk_diagnostics, pas un defaut de base."""
        sizer = _Sizer()
        env = _env(slot_envelope=100.0, symbol_envelope=1000.0, leverage=1.0,
                  min_notional=200.0)
        size, notional = sizer.compute_size(entry=50.0, stop_dist=5.0, env=env)
        decision = RiskLedger().reserve(env, risk=sizer.engaged_risk(50.0, 45.0, size),
                                        notional=notional, pos_key="p")
        assert decision.allowed is False
        assert decision.reason_code == "notionnel_min"

    def test_symbol_cap_carries_leverage_like_the_slot_cap(self):
        """Regression : avec un plafond symbole sans levier, un slot a poids
        1.0 et levier 3 produisait un notionnel conforme a la regle 1 mais
        refuse par la regle 2 -- deux bases pour la meme grandeur."""
        sizer = _Sizer()
        env = _env(slot_envelope=1000.0, symbol_envelope=1000.0, leverage=3.0,
                  risk_pct=0.02, symbol_risk_pct=0.02)
        size, notional = sizer.compute_size(entry=100.0, stop_dist=0.01, env=env)
        assert notional == pytest.approx(3000.0)
        decision = RiskLedger().reserve(env, risk=sizer.engaged_risk(100.0, 99.99, size),
                                        notional=notional, pos_key="p")
        assert decision.allowed is True
