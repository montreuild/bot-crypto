"""Tests unitaires — moteur Smart Money Concepts (app/core/smc.py)
et stratégie smart_money (app/strategies/smart_money.py)."""
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import smc
from app.strategies.smart_money import Strategy


# ── Helpers de construction OHLCV ─────────────────────────────────────────────

def _mk_df(o, h, l, c, v=None):
    n = len(c)
    times = [datetime(2024, 1, 1) + timedelta(hours=i) for i in range(n)]
    return pl.DataFrame({
        "time": times,
        "open": [float(x) for x in o], "high": [float(x) for x in h],
        "low": [float(x) for x in l], "close": [float(x) for x in c],
        "volume": [float(x) for x in (v if v is not None else [100.0] * n)],
    })


def _random_df(n=800, seed=7, jump_p=0.02):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    jumps = np.where(rng.random(n) < jump_p, rng.normal(0, 1.2, n), 0.0)
    base = 100 + 10 * np.sin(t / 60) + 0.004 * t \
        + np.cumsum(rng.normal(0, 0.3, n) + jumps)
    base = np.maximum(base, 10)
    o = base + rng.normal(0, 0.1, n)
    c = base + rng.normal(0, 0.1, n)
    h = np.maximum(o, c) + np.abs(rng.normal(0, 0.2, n))
    l = np.minimum(o, c) - np.abs(rng.normal(0, 0.2, n))
    v = np.abs(rng.normal(1000, 300, n))
    return _mk_df(o, h, l, c, v)


def _flat(n, price=100.0, amp=0.1):
    """Marché plat : bougies alternées ±amp autour de price."""
    o = [price + (amp if i % 2 else -amp) for i in range(n)]
    c = [price - (amp if i % 2 else -amp) for i in range(n)]
    h = [max(a, b) + amp / 2 for a, b in zip(o, c)]
    l = [min(a, b) - amp / 2 for a, b in zip(o, c)]
    return o, h, l, c


# ══════════════════════════════════════════════════════════════════════════════
#  Moteur SMC
# ══════════════════════════════════════════════════════════════════════════════

class TestSwingsStructure:
    def test_pivots_detected_and_confirmed_later(self):
        # V simple : montée, sommet net, descente, creux net, remontée.
        o, h, l, c = _flat(60)
        # sommet à l'indice 20, creux à l'indice 40
        for i, d in ((18, 2), (19, 4), (20, 6), (21, 4), (22, 2)):
            o[i] += d; c[i] += d; h[i] += d + 0.2; l[i] += d
        for i, d in ((38, -2), (39, -4), (40, -6), (41, -4), (42, -2)):
            o[i] += d; c[i] += d; h[i] += d; l[i] += d - 0.2
        df = _mk_df(o, h, l, c)
        r = smc.analyze(df, {"swing_left": 3, "swing_right": 3})
        highs = [s for s in r["_all_swings"] if s["kind"] == "high"]
        lows = [s for s in r["_all_swings"] if s["kind"] == "low"]
        assert any(s["index"] == 20 for s in highs), "pivot high 20 manquant"
        assert any(s["index"] == 40 for s in lows), "pivot low 40 manquant"
        for s in r["_all_swings"]:
            assert s["confirmed_at"] == s["index"] + 3, "confirmation ≠ pivot+right"

    def test_bos_up_detected_on_close_above_swing_high(self):
        # Sommet confirmé puis clôture au-dessus → BOS direction up, trend haussier.
        o, h, l, c = _flat(50)
        for i, d in ((18, 2), (19, 4), (20, 6), (21, 4), (22, 2)):
            o[i] += d; c[i] += d; h[i] += d + 0.2; l[i] += d
        # cassure franche à l'indice 35
        for i in range(35, 50):
            o[i] += 12; c[i] += 12; h[i] += 12.3; l[i] += 12
        df = _mk_df(o, h, l, c)
        r = smc.analyze(df, {"swing_left": 3, "swing_right": 3})
        ups = [e for e in r["_all_struct_events"] if e["direction"] == "up"]
        assert ups, "aucune cassure haussière détectée"
        assert ups[0]["index"] >= 24, "cassure avant confirmation du swing"
        assert r["bias"]["trend"] == 1

    def test_choch_after_trend_flip(self):
        # Tendance haussière installée puis cassure du swing low → CHoCH down.
        rng = np.random.default_rng(1)
        n = 300
        base = np.concatenate([
            100 + np.linspace(0, 40, 200),           # uptrend
            140 - np.linspace(0, 50, 100),           # retournement
        ]) + rng.normal(0, 0.4, n)
        o = base - 0.1; c = base + 0.1
        h = np.maximum(o, c) + 0.3; l = np.minimum(o, c) - 0.3
        df = _mk_df(o, h, l, c)
        r = smc.analyze(df)
        kinds = {(e["kind"], e["direction"]) for e in r["_all_struct_events"]}
        assert ("CHoCH", "down") in kinds, f"CHoCH down absent : {kinds}"
        assert r["bias"]["trend"] == -1


class TestLiquidity:
    def _equal_lows_df(self):
        """Deux creux quasi égaux (equal lows) puis sweep rejeté."""
        o, h, l, c = _flat(80, price=100.0)
        for i, d in ((18, -2), (19, -4), (20, -6), (21, -4), (22, -2)):
            o[i] += d; c[i] += d; h[i] += d; l[i] += d - 0.2
        for i, d in ((38, -2), (39, -4), (40, -6.05), (41, -4), (42, -2)):
            o[i] += d; c[i] += d; h[i] += d; l[i] += d - 0.2
        # sweep à l'indice 60 : mèche sous les equal lows, clôture au-dessus
        l[60] = 92.0          # sous le niveau ~93.6
        o[60] = 100.2; c[60] = 100.4; h[60] = 100.8
        return _mk_df(o, h, l, c)

    def test_equal_lows_form_sell_side_pool(self):
        r = smc.analyze(self._equal_lows_df(), {"eq_tol_atr": 0.6})
        pools = [p for p in r["_all_pools"] if p["kind"] == "sell_side"]
        assert pools, "pool sell-side (equal lows) non détecté"
        assert len(pools[0]["indices"]) >= 2

    def test_sweep_rejected_recorded(self):
        r = smc.analyze(self._equal_lows_df(), {"eq_tol_atr": 0.6})
        sw = [s for s in r["_all_sweeps"]
              if s["kind"] == "sell_side" and s["index"] == 60]
        assert sw, "sweep des equal lows non détecté à la barre 60"
        assert sw[0]["rejected"] is True, "le sweep aurait dû être rejeté (close > level)"

    def test_liquidity_targets_causal(self):
        df = _random_df(600, seed=3)
        r = smc.analyze(df)
        c = df["close"].to_numpy()
        for i in (300, 450, 599):
            above = smc.liquidity_targets_above(r, i, float(c[i]))
            below = smc.liquidity_targets_below(r, i, float(c[i]))
            assert all(lv > c[i] for lv in above)
            assert all(lv < c[i] for lv in below)
            assert above == sorted(above)
            assert below == sorted(below, reverse=True)


class TestOrderBlocksFVG:
    def _displacement_df(self):
        """Bougie rouge puis displacement haussier massif → OB bullish + FVG."""
        o, h, l, c = _flat(80, price=100.0)
        # bougie rouge nette à 40
        o[40], c[40], h[40], l[40] = 100.5, 99.5, 100.7, 99.3
        # displacement à 41 : corps énorme qui engloutit le high précédent
        o[41], c[41], h[41], l[41] = 99.6, 106.0, 106.3, 99.5
        # bougie suivante laisse un gap (FVG) : low[42] > high[40]
        o[42], c[42], h[42], l[42] = 106.0, 107.0, 107.3, 105.5
        # retour dans la zone OB à 60
        o[60], c[60], h[60], l[60] = 101.0, 101.5, 101.8, 100.2
        for i in range(43, 60):
            o[i] += 6; c[i] += 6; h[i] += 6; l[i] += 6
        for i in range(61, 80):
            o[i] += 2; c[i] += 2; h[i] += 2; l[i] += 2
        return _mk_df(o, h, l, c)

    def test_bullish_ob_created_at_last_red_candle(self):
        r = smc.analyze(self._displacement_df(), {"disp_body_atr": 1.3})
        obs = [x for x in r["_all_obs"] if x["kind"] == "bullish"]
        assert any(x["index"] == 40 for x in obs), \
            f"OB bullish attendu à 40, trouvé : {[x['index'] for x in obs]}"
        ob = next(x for x in obs if x["index"] == 40)
        assert ob["created_at"] == 41
        assert ob["bottom"] == 99.3 and abs(ob["top"] - 100.5) < 1e-9

    def test_ob_touched_on_return(self):
        r = smc.analyze(self._displacement_df(), {"disp_body_atr": 1.3})
        ob = next(x for x in r["_all_obs"]
                  if x["kind"] == "bullish" and x["index"] == 40)
        assert ob["touched_at"] == 60, f"touch attendu à 60, obtenu {ob['touched_at']}"

    def test_bullish_fvg_detected(self):
        r = smc.analyze(self._displacement_df(), {"fvg_min_atr": 0.2})
        fvgs = [f for f in r["_all_fvgs"] if f["kind"] == "bullish"]
        assert any(f["index"] == 41 for f in fvgs), "FVG bullish (gap 40→42) manquant"


class TestPremiumDiscount:
    def test_zones_and_ote_fields(self):
        df = _random_df(500, seed=11)
        r = smc.analyze(df)
        pd_zone = r["premium_discount"]
        assert pd_zone is not None
        assert pd_zone["zone"] in ("premium", "discount", "equilibrium")
        assert pd_zone["range_low"] < pd_zone["equilibrium"] < pd_zone["range_high"]

    def test_causal_pd_at_bar(self):
        df = _random_df(500, seed=11)
        r = smc.analyze(df)
        h = df["high"].to_numpy(); l = df["low"].to_numpy()
        c = df["close"].to_numpy()
        pd_mid = smc.premium_discount_at(r, h, l, c, 250)
        assert pd_mid is None or pd_mid["zone"] in ("premium", "discount", "equilibrium")
        # à la dernière barre, identique au champ public
        pd_last = smc.premium_discount_at(r, h, l, c, len(df) - 1)
        assert pd_last == r["premium_discount"]


class TestTrendlinesChannel:
    def test_trendlines_project_to_last_bar(self):
        df = _random_df(400, seed=5)
        r = smc.analyze(df)
        for t in r["trendlines"]:
            assert t["kind"] in ("support", "resistance")
            assert t["x2"] == len(df) - 1
            assert t["x1"] < t["x2"]

    def test_regression_channel(self):
        df = _random_df(400, seed=5)
        r = smc.analyze(df, {"channel_lookback": 120})
        ch = r["channel"]
        assert ch is not None
        assert ch["end_index"] == len(df) - 1
        assert ch["half_width"] > 0


# ══════════════════════════════════════════════════════════════════════════════
#  Stratégie smart_money
# ══════════════════════════════════════════════════════════════════════════════

class TestStrategyContract:
    def test_score_returns_valid_dict(self):
        s = Strategy()
        r = s.score(_random_df(600, seed=2))
        assert isinstance(r, dict)
        assert r["side"] in ("long", "short", "none")
        assert 0 <= r["score"] <= 1
        assert r["name"] == "smart_money"

    def test_insufficient_data_none(self):
        s = Strategy()
        r = s.score(_random_df(80))
        assert r["side"] == "none"

    def test_registry_metadata(self):
        assert Strategy.param_space, "param_space vide — invisible pour l'optimiseur"
        assert "min_gain_pct" in Strategy.fixed_params
        assert Strategy.fixed_params["min_gain_pct"] == 0.4

    def test_signal_carries_bracket_and_gain(self):
        """Tout signal émis doit porter SL/TP fixes et gain potentiel > 0.4 %."""
        s = Strategy()
        found = 0
        for seed in range(12):
            df = _random_df(900, seed=seed, jump_p=0.04)
            s2 = Strategy()
            s2._bt_params = None
            s2.prepare_for_backtest(df)
            for i, sig in (s2._bt_signals or {}).items():
                found += 1
                assert sig["disable_trailing"] is True
                assert sig["stop_hint"] is not None and sig["tp_hint"] is not None
                gain = sig["indicators"]["gain_pct"]
                assert gain > 0.4, f"gain {gain} <= 0.4% (filtre violé)"
                if sig["side"] == "long":
                    assert sig["stop_hint"] < sig["tp_hint"]
                else:
                    assert sig["stop_hint"] > sig["tp_hint"]
        assert found > 0, "aucun signal généré sur 12 seeds — test non significatif"


class TestMinGainFilter:
    def _res_stub(self, pool_level):
        """Résultat SMC minimal : un seul pool buy-side comme cible de TP."""
        return {
            "_all_pools": [{"kind": "buy_side", "level": pool_level,
                            "top": pool_level, "bottom": pool_level,
                            "indices": [10], "formed_at": 20, "swept_at": None}],
            "_all_swings": [],
        }

    def test_target_too_close_rejected(self):
        s = Strategy()
        p = dict(Strategy.fixed_params)
        # entrée 100, SL 99.9 (risque 0.1), cible 100.2 → gain 0.2% <= 0.4% ;
        # fallback 2R = 100.2 → 0.2% <= 0.4% → rejet.
        trade = s._build_trade(self._res_stub(100.2), 50, "long",
                               entry=100.0, sl=99.9, atr=0.0, p=p,
                               setup="TEST", score=0.9, detail="")
        assert trade is None, "position au gain < 0.4% acceptée — filtre cassé"

    def test_target_far_enough_accepted(self):
        s = Strategy()
        p = dict(Strategy.fixed_params)
        trade = s._build_trade(self._res_stub(101.5), 50, "long",
                               entry=100.0, sl=99.5, atr=0.0, p=p,
                               setup="TEST", score=0.9, detail="")
        assert trade is not None
        assert trade["indicators"]["gain_pct"] > 0.4
        assert trade["indicators"]["rr"] >= p["min_rr"]
        assert trade["tp_hint"] <= 101.5   # front-run : TP avant la poche

    def test_short_mirror(self):
        s = Strategy()
        p = dict(Strategy.fixed_params)
        res = {
            "_all_pools": [{"kind": "sell_side", "level": 98.5,
                            "top": 98.5, "bottom": 98.5,
                            "indices": [10], "formed_at": 20, "swept_at": None}],
            "_all_swings": [],
        }
        trade = s._build_trade(res, 50, "short",
                               entry=100.0, sl=100.5, atr=0.0, p=p,
                               setup="TEST", score=0.9, detail="")
        assert trade is not None
        assert trade["indicators"]["gain_pct"] > 0.4
        assert trade["tp_hint"] >= 98.5


class TestBacktestCacheCoherence:
    def test_cached_signals_match_live_windows(self):
        """Le cache prepare_for_backtest doit donner la même décision que
        score() sur la fenêtre tronquée (pas de fuite du futur)."""
        df = _random_df(1200, seed=42, jump_p=0.04)
        s = Strategy()
        s._bt_params = None
        s.prepare_for_backtest(df)
        assert s._bt_signals is not None
        for m in range(700, 1200, 41):
            cached = s._bt_signals.get(m - 1)
            live = Strategy().score(df[:m])
            c_side = cached["side"] if cached else "none"
            assert c_side == live["side"], \
                f"barre {m - 1} : cache={c_side} live={live['side']}"

    def test_cache_invalidated_on_other_data(self):
        df1 = _random_df(600, seed=1)
        df2 = _random_df(600, seed=2)
        s = Strategy()
        s._bt_params = None
        s.prepare_for_backtest(df1)
        assert not s._cache_valid(df2), "cache accepté pour un autre dataset"
