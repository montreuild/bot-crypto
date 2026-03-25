"""
Trailing Stop Dynamique Multi-Phases (sans TP fixe).
5 phases irréversibles : GRACE → WIDE → BREAKEVEN → LOCK → TIGHT.
Le stop ne peut qu'améliorer (ratchet strict), la phase ne peut que monter.
"""
from __future__ import annotations


class DynamicTrailingStop:
    """Trailing stop multi-phases sans TP fixe. Une instance par trade."""

    def __init__(self, breakeven_r=1.2, lock_r=2.5, tight_r=4.0, lock_ratio=0.60,
                 trail_wide=2.5, trail_normal=2.0, trail_lock=1.5, trail_tight=1.0,
                 grace_bars=4, use_swing=True, swing_lookback=8):
        self.breakeven_r = breakeven_r
        self.lock_r = lock_r
        self.tight_r = tight_r
        self.lock_ratio = lock_ratio
        self.trail_wide = trail_wide
        self.trail_normal = trail_normal
        self.trail_lock = trail_lock
        self.trail_tight = trail_tight
        self.grace_bars = grace_bars
        self.use_swing = use_swing
        self.swing_lookback = swing_lookback
        self._phase = 0
        self._peak_price = 0.0
        self._initial_stop_dist = 0.0

    def init(self, entry, atr, side):
        """Initialise pour un nouveau trade. Retourne le stop initial."""
        dist = atr * self.trail_wide
        self._initial_stop_dist = max(dist, 1e-9)
        self._peak_price = entry
        self._phase = 0
        return entry - dist if side == "long" else entry + dist

    def update(self, current_price, current_stop, atr, side, entry, bars_held,
               recent_lows=None, recent_highs=None):
        """Retourne (nouveau_stop, phase_int, label_phase)."""
        if atr <= 0 or self._initial_stop_dist <= 0:
            return current_stop, self._phase, "atr_zero"
        if side == "long":
            self._peak_price = max(self._peak_price, current_price)
            peak_r = (self._peak_price - entry) / self._initial_stop_dist
        else:
            self._peak_price = min(self._peak_price, current_price)
            peak_r = (entry - self._peak_price) / self._initial_stop_dist
        if   peak_r >= self.tight_r:       self._phase = max(self._phase, 4)
        elif peak_r >= self.lock_r:        self._phase = max(self._phase, 3)
        elif peak_r >= self.breakeven_r:   self._phase = max(self._phase, 2)
        elif bars_held >= self.grace_bars: self._phase = max(self._phase, 1)
        phase = self._phase
        if phase == 0:
            return current_stop, 0, "grace"
        mults = {1: self.trail_wide, 2: self.trail_normal, 3: self.trail_lock, 4: self.trail_tight}
        names = {1: "wide", 2: "breakeven", 3: "lock", 4: "tight"}
        mult = mults[phase]
        new_stop = current_price - atr * mult if side == "long" else current_price + atr * mult
        if phase >= 2:
            be = entry + atr * 0.15 if side == "long" else entry - atr * 0.15
            new_stop = max(new_stop, be) if side == "long" else min(new_stop, be)
        if phase >= 3:
            peak_raw = abs(self._peak_price - entry)
            if peak_raw > 0:
                locked = (entry + peak_raw * self.lock_ratio if side == "long"
                          else entry - peak_raw * self.lock_ratio)
                new_stop = max(new_stop, locked) if side == "long" else min(new_stop, locked)
        if self.use_swing and phase >= 3:
            if side == "long" and recent_lows and len(recent_lows) >= 3:
                sw = min(recent_lows[-self.swing_lookback:]) - atr * 0.3
                if sw > new_stop and sw < current_price - atr * 0.8:
                    new_stop = sw
            elif side == "short" and recent_highs and len(recent_highs) >= 3:
                sw = max(recent_highs[-self.swing_lookback:]) + atr * 0.3
                if sw < new_stop and sw > current_price + atr * 0.8:
                    new_stop = sw
        if side == "long":
            return max(current_stop, new_stop), phase, names[phase]
        return min(current_stop, new_stop), phase, names[phase]

    def is_triggered(self, price, stop, side):
        return price <= stop if side == "long" else price >= stop

    @property
    def phase(self):
        return self._phase

    @property
    def phase_name(self):
        return ["grace", "wide", "breakeven", "lock", "tight"][min(self._phase, 4)]


class TrailingStopManager:
    """Interface simplifiée vers DynamicTrailingStop."""

    def __init__(self, mult=2.5, grace_bars=4, breakeven_r=1.2, trail_tight_mult=1.0,
                 lock_r=2.5, tight_r=4.0, lock_ratio=0.60, use_swing=True):
        self.mult = mult
        self._dts = None
        self._kwargs = dict(
            trail_wide=mult, trail_normal=max(mult - 0.5, 1.5),
            trail_lock=max(mult - 1.0, 1.2), trail_tight=trail_tight_mult,
            breakeven_r=breakeven_r, lock_r=lock_r, tight_r=tight_r,
            lock_ratio=lock_ratio, use_swing=use_swing,
        )

    def initial_stop(self, entry, atr, side):
        self._dts = DynamicTrailingStop(**self._kwargs)
        return self._dts.init(entry, atr, side)

    def init_from_stop(self, entry: float, saved_stop: float, side: str):
        """Réinitialise depuis un stop sauvegardé en BDD (reprise après crash)."""
        self._dts = DynamicTrailingStop(**self._kwargs)
        # Fallback si stop == entry : 1% du prix comme distance (évite peak_r infini)
        _FALLBACK_STOP_PCT = 0.01
        raw_dist = abs(entry - saved_stop)
        dist = raw_dist if raw_dist > 0 else max(entry * _FALLBACK_STOP_PCT, 1e-9)
        self._dts._initial_stop_dist = max(dist, 1e-9)
        self._dts._peak_price        = entry
        self._dts._phase             = 0

    def update_stop(self, current_price, current_stop, atr, side, entry=None,
                    bars_held=0, recent_lows=None, recent_highs=None):
        if atr <= 0:
            return current_stop
        if self._dts is None:
            self._dts = DynamicTrailingStop(**self._kwargs)
            dist = abs(entry - current_stop) if entry else atr * self.mult
            self._dts._initial_stop_dist = max(dist, 1e-9)
            self._dts._peak_price = current_price
        new_stop, _, _ = self._dts.update(
            current_price=current_price, current_stop=current_stop, atr=atr,
            side=side, entry=entry or current_price, bars_held=bars_held,
            recent_lows=recent_lows, recent_highs=recent_highs,
        )
        return new_stop

    def reset(self):
        self._dts = None

    def is_triggered(self, current_price, stop, side):
        return current_price <= stop if side == "long" else current_price >= stop

    @property
    def phase(self):
        return self._dts.phase if self._dts else 0

    @property
    def phase_name(self):
        return self._dts.phase_name if self._dts else "grace"
