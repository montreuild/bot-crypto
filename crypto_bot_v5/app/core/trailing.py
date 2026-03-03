"""
Trailing Stop dynamique amélioré — V2

Améliorations vs V1 :
  - Période de grâce (grace_bars) : le trailing ne s'active qu'après N barres
    → évite d'être sorti sur le 1er pullback inévitable
  - Breakeven stop : une fois en profit de `breakeven_r` × distance_stop,
    le stop est déplacé au prix d'entrée (risque 0)
  - Trailing resserré en profit : quand le trade est très profitable (>2×R),
    le trailing passe à trail_tight_mult pour capturer plus de gains
"""


class TrailingStopManager:
    def __init__(self,
                 mult: float = 2.0,
                 grace_bars: int = 3,
                 breakeven_r: float = 1.0,
                 trail_tight_mult: float | None = None):
        """
        mult             : ATR mult du trailing de base
        grace_bars       : N barres avant d'activer le trailing
        breakeven_r      : dès que profit >= breakeven_r × stop_dist → stop au BE
        trail_tight_mult : quand profit > 2×stop_dist → trailing plus serré (None=désactivé)
        """
        self.mult             = mult
        self.grace_bars       = grace_bars
        self.breakeven_r      = breakeven_r
        self.trail_tight_mult = trail_tight_mult

    def initial_stop(self, entry: float, atr: float, side: str) -> float:
        """Stop initial — utilise un mult légèrement plus large que le trailing."""
        if atr <= 0:
            raise ValueError("ATR doit être strictement positif")
        if side == "long":
            return entry - atr * self.mult
        if side == "short":
            return entry + atr * self.mult
        raise ValueError(f"Side inconnu : {side}")

    def update_stop(self, current_price: float, current_stop: float,
                    atr: float, side: str,
                    entry: float = None, bars_held: int = 0) -> float:
        """
        Met à jour le stop. Logique par étapes :
          1. Pendant grace_bars  : stop fixe (ne bouge pas)
          2. Breakeven           : si profit >= breakeven_r × stop_dist → stop au BE
          3. Trailing normal     : stop suit le prix à mult × ATR
          4. Trailing resserré   : si gros profit et trail_tight_mult configuré
        """
        if atr <= 0:
            return current_stop

        # ── ① Période de grâce : on ne touche pas le stop ──────────────────────
        if bars_held < self.grace_bars:
            return current_stop

        # ── ② Breakeven ─────────────────────────────────────────────────────────
        if entry is not None:
            stop_dist = abs(entry - current_stop)
            if stop_dist > 0:
                if side == "long":
                    profit_r = (current_price - entry) / stop_dist
                    if profit_r >= self.breakeven_r:
                        # Breakeven + petit buffer (0.1 ATR pour ne pas sortir sur noise)
                        be_stop = entry + atr * 0.1
                        current_stop = max(current_stop, be_stop)
                else:
                    profit_r = (entry - current_price) / stop_dist
                    if profit_r >= self.breakeven_r:
                        be_stop = entry - atr * 0.1
                        current_stop = min(current_stop, be_stop)

        # ── ③ Trailing avec mult adaptatif ──────────────────────────────────────
        # En gros profit, on resserre le trailing pour ne pas laisser partir le gain
        if entry is not None and self.trail_tight_mult is not None:
            stop_dist = abs(entry - current_stop)
            if stop_dist > 0:
                profit_r = abs(current_price - entry) / stop_dist
                effective_mult = self.trail_tight_mult if profit_r >= 2.0 else self.mult
            else:
                effective_mult = self.mult
        else:
            effective_mult = self.mult

        if side == "long":
            new_stop = current_price - atr * effective_mult
            return max(current_stop, new_stop)
        if side == "short":
            new_stop = current_price + atr * effective_mult
            return min(current_stop, new_stop)
        raise ValueError(f"Side inconnu : {side}")

    def is_triggered(self, current_price: float, stop: float, side: str) -> bool:
        if side == "long":
            return current_price <= stop
        return current_price >= stop

    def stop_moved(self, old_stop: float, new_stop: float, side: str,
                   min_move_pct: float = 0.001) -> bool:
        if old_stop == 0:
            return True
        move = abs(new_stop - old_stop) / abs(old_stop)
        return move >= min_move_pct
