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


class TestLiquidityVoidsBreakers:
    def _void_df(self):
        """4 bougies vertes consécutives massives → liquidity void bullish,
        puis retracement complet → filled."""
        o, h, l, c = _flat(80, price=100.0)
        px = 100.0
        for i in range(40, 44):           # run haussier : +3 par bougie
            o[i], c[i] = px, px + 3
            h[i], l[i] = px + 3.2, px - 0.2
            px += 3
        for i in range(44, 60):           # plateau haut
            o[i] += 12; c[i] += 12; h[i] += 12; l[i] += 12
        # retracement complet à 60 : retour sous l'origine du run
        o[60], c[60], h[60], l[60] = 112.0, 99.0, 112.2, 98.5
        for i in range(61, 80):
            o[i] -= 0; c[i] -= 0
        return _mk_df(o, h, l, c)

    def test_void_detected_and_filled(self):
        r = smc.analyze(self._void_df(), {"void_min_bars": 3, "void_min_atr": 2.0})
        vds = [v for v in r["_all_voids"] if v["kind"] == "bullish"]
        assert vds, "liquidity void bullish non détecté"
        vd = vds[0]
        assert vd["start_index"] == 40
        assert vd["top"] > vd["bottom"]
        assert vd["filled_at"] == 60, f"fill attendu à 60, obtenu {vd['filled_at']}"

    def test_void_targets_causal(self):
        r = smc.analyze(self._void_df(), {"void_min_bars": 3, "void_min_atr": 2.0})
        vd = [v for v in r["_all_voids"] if v["kind"] == "bullish"][0]
        # Avant le fill (barre 50) : les bords du void sont des cibles
        above = smc.void_targets_above(r, 50, 100.0)
        assert vd["top"] in above
        below = smc.void_targets_below(r, 50, 113.0)
        assert vd["bottom"] in below
        # Après le fill (barre 70) : le void comblé n'est plus une cible
        below_after = smc.void_targets_below(r, 70, 113.0)
        assert vd["bottom"] not in below_after

    def test_breaker_created_on_ob_invalidation(self):
        """OB bullish invalidé sur clôture → breaker bearish créé, retest suivi."""
        o, h, l, c = _flat(90, price=100.0)
        # bougie rouge (future OB) puis displacement haussier
        o[40], c[40], h[40], l[40] = 100.5, 99.5, 100.7, 99.3
        o[41], c[41], h[41], l[41] = 99.6, 106.0, 106.3, 99.5
        for i in range(42, 55):
            o[i] += 6; c[i] += 6; h[i] += 6; l[i] += 6
        # invalidation : clôture sous le bottom de l'OB (99.3) à 60
        o[60], c[60], h[60], l[60] = 100.0, 98.0, 100.2, 97.8
        for i in range(61, 90):
            o[i] -= 3; c[i] -= 3; h[i] -= 3; l[i] -= 3
        # retest de la zone (par le bas) à 75
        o[75], c[75], h[75], l[75] = 98.5, 99.0, 99.6, 98.3
        df = _mk_df(o, h, l, c)
        r = smc.analyze(df, {"disp_body_atr": 1.3})
        ob = next((x for x in r["_all_obs"]
                   if x["kind"] == "bullish" and x["index"] == 40), None)
        assert ob is not None and ob["invalidated_at"] == 60
        brks = [b for b in r["_all_breakers"]
                if b["kind"] == "bearish" and b["index"] == 40]
        assert brks, "breaker bearish non créé à l'invalidation de l'OB"
        assert brks[0]["created_at"] == 60
        assert brks[0]["touched_at"] == 75, \
            f"retest attendu à 75, obtenu {brks[0]['touched_at']}"


class TestRejectionBlocks:
    def test_rejection_block_on_wick_swing(self):
        """Swing high avec grande mèche haute → rejection block bearish."""
        o, h, l, c = _flat(60, price=100.0)
        # sommet à 30 : corps petit, mèche haute de 3 (>> 0.5×ATR)
        for i, d in ((28, 1), (29, 2), (31, 2), (32, 1)):
            o[i] += d; c[i] += d; h[i] += d + 0.1; l[i] += d
        o[30], c[30], h[30], l[30] = 102.4, 102.6, 106.0, 102.2
        # retest de la zone par le bas à 45
        h[45] = 103.5; o[45] = 100.0; c[45] = 100.2; l[45] = 99.8
        df = _mk_df(o, h, l, c)
        r = smc.analyze(df, {"rb_wick_atr": 0.5})
        rbs = [x for x in r["_all_rejections"]
               if x["kind"] == "bearish" and x["index"] == 30]
        assert rbs, "rejection block bearish non détecté au swing 30"
        rb = rbs[0]
        assert abs(rb["top"] - 106.0) < 1e-9 and abs(rb["bottom"] - 102.6) < 1e-9
        assert rb["touched_at"] == 45, f"retest attendu à 45, obtenu {rb['touched_at']}"


class TestVolumeProfileSessions:
    def test_volume_profile_fields(self):
        df = _random_df(500, seed=4)
        h = df["high"].to_numpy(); l = df["low"].to_numpy()
        c = df["close"].to_numpy(); v = df["volume"].to_numpy()
        vp = smc.volume_profile(h, l, c, v, len(df) - 1, lookback=240)
        assert vp is not None
        assert vp["range_low"] <= vp["poc"] <= vp["range_high"]
        for lv in vp["hvns"] + vp["lvns"]:
            assert vp["range_low"] <= lv <= vp["range_high"]
        # fenêtre trop courte → None
        assert smc.volume_profile(h, l, c, v, 10, lookback=240) is None

    def test_killzone_flags(self):
        import numpy as np
        # 08:00 UTC (hors KZ), 08:30… non : 7h,9h dans LDN ; 13h dans NY ; 3h hors
        epochs = np.array([7 * 3600, 9 * 3600, 13 * 3600, 3 * 3600, 11 * 3600],
                          dtype=np.int64)
        flags = smc.killzone_flags(epochs)
        assert list(flags) == [1, 1, 1, 0, 0]
        assert smc.session_label(3 * 3600) == "asia"
        assert smc.session_label(13 * 3600) == "newyork"


class TestHTFBias:
    def test_htf_trend_causal_and_mapped(self):
        df = _random_df(800, seed=13)
        arr, meta = smc.htf_trend_series(df, mult=4)
        assert len(arr) == len(df)
        assert set(map(int, set(arr))) <= {-1, 0, 1}
        assert meta["n_htf"] > 100
        # Causalité : la série tronquée doit être identique sur son support
        arr2, _ = smc.htf_trend_series(df[:500], mult=4)
        assert (arr[:500] == arr2).all(), "htf_trend_series non causale"

    def test_strategy_contract_with_htf(self):
        s = Strategy()
        r = s.score(_random_df(700, seed=6),
                    {"smart_money": {"htf_filter": "strict"}})
        assert r["side"] in ("long", "short", "none")


class TestStructureLineCycle:
    def test_zigzag_alternates_and_strictly_increasing(self):
        df = _random_df(600, seed=9)
        r = smc.analyze(df)
        line = r["structure_line"]
        assert len(line) >= 4
        for a, b in zip(line, line[1:]):
            assert a["kind"] != b["kind"], "zigzag non alterné"
            assert a["index"] < b["index"], "indices non strictement croissants"

    def test_cycle_projection_fields(self):
        df = _random_df(600, seed=9)
        r = smc.analyze(df)
        cy = r["cycle"]
        assert cy is not None
        assert cy["phase"] in ("advance", "decline")
        assert cy["boundary"] in ("upper", "lower")
        assert cy["target"] > 0

    def test_trendline_value_causal(self):
        df = _random_df(600, seed=9)
        r = smc.analyze(df)
        for i in (300, 599):
            v = smc.trendline_value_at(r, i, "support")
            w = smc.trendline_value_at(r, i, "resistance")
            assert v is None or v > 0
            assert w is None or w > 0
        # à la dernière barre, cohérent avec la trendline publique
        for t in r["trendlines"]:
            v = smc.trendline_value_at(r, len(df) - 1, t["kind"])
            assert v is not None and abs(v - t["y2"]) < 1e-6


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
            "_all_voids": [],
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
            "_all_voids": [],
        }
        trade = s._build_trade(res, 50, "short",
                               entry=100.0, sl=100.5, atr=0.0, p=p,
                               setup="TEST", score=0.9, detail="")
        assert trade is not None
        assert trade["indicators"]["gain_pct"] > 0.4
        assert trade["tp_hint"] >= 98.5

    def test_confluence_sizing(self):
        """size_by_confluence → size_factor croît avec le score (borné
        [0.4, 1.7]), vaut 1.0 au centre ; désactivé (défaut) → 1.0 constant."""
        s = Strategy()

        def sf(score, enabled, slope=3.0, center=0.83):
            p = dict(Strategy.fixed_params)
            p["size_by_confluence"] = enabled
            p["size_conf_slope"] = slope
            p["size_conf_center"] = center
            t = s._build_trade(self._res_stub(101.5), 50, "long",
                               entry=100.0, sl=99.5, atr=0.0, p=p,
                               setup="TEST", score=score, detail="")
            return t["size_factor"]

        assert Strategy.fixed_params["size_by_confluence"] is False
        # désactivé (défaut) : facteur neutre, quel que soit le score
        assert sf(1.0, False) == 1.0 and sf(0.70, False) == 1.0
        # activé : monotone croissant, = 1.0 au centre
        assert sf(0.70, True) < sf(0.83, True) < sf(1.0, True)
        assert abs(sf(0.83, True) - 1.0) < 1e-9
        # bornes : slope élevée → clampé à [0.4, 1.7]
        assert sf(1.0, True, slope=10) == 1.7
        assert sf(0.70, True, slope=10) == 0.4

    def test_optional_pistes_default_off(self):
        """Pistes SMC / croisements optionnels OFF par défaut (byte-identique)."""
        for k in ("ext_structure_filter", "tp_measured_move", "inv_fvg_bonus",
                  "candle_bonus"):
            assert Strategy.fixed_params[k] is False
        assert Strategy.fixed_params["chop_filter_max"] == 0.0

    def test_chop_and_candle_aux_wiring(self):
        """Filtre choppiness + confirmation bougie : séries aux None par défaut,
        présentes (bonnes formes) quand activés."""
        df = _random_df(900, seed=5, jump_p=0.04)
        s = Strategy()
        p_off = s._p({"smart_money": {}})
        res = smc.analyze(df, s._smc_params(p_off))
        aux_off = s._build_aux(df, p_off, res)
        assert aux_off["chop"] is None
        assert aux_off["pin"] is None and aux_off["eng"] is None
        p_on = s._p({"smart_money": {"chop_filter_max": 61.8, "candle_bonus": True}})
        aux_on = s._build_aux(df, p_on, res)
        assert aux_on["chop"] is not None and len(aux_on["chop"]) == df.height
        assert aux_on["pin"] is not None
        assert set(np.unique(aux_on["pin"]).tolist()) <= {-1, 0, 1}


    def test_ext_structure_filter_wiring(self):
        """1d : aux['ext_trend'] None par défaut, array causal quand activé."""
        df = _random_df(900, seed=3, jump_p=0.04)
        s = Strategy()
        p_off = s._p({"smart_money": {"ext_structure_filter": False}})
        res = smc.analyze(df, s._smc_params(p_off))
        assert s._build_aux(df, p_off, res)["ext_trend"] is None
        p_on = s._p({"smart_money": {"ext_structure_filter": True,
                                     "ext_swing_len": 8}})
        ext = s._build_aux(df, p_on, res)["ext_trend"]
        assert ext is not None and len(ext) == df.height


class TestTradePlans:
    def test_plans_contract(self):
        """Chaque plan porte le bracket complet, le déclencheur, le motif et
        respecte le filtre de gain > min_gain_pct et le RR minimal."""
        found = 0
        for seed in range(10):
            plans = Strategy().trade_plans(_random_df(900, seed=seed, jump_p=0.04))
            for p in plans:
                found += 1
                assert p["status"] in ("immediate", "pending")
                assert p["side"] in ("long", "short")
                assert p["setup"] in ("SWEEP_REVERSAL", "OB_RETEST",
                                      "BREAKER_RETEST")
                assert p["entry"] is not None and p["stop"] is not None \
                    and p["tp"] is not None
                assert p["gain_pct"] > Strategy.fixed_params["min_gain_pct"]
                assert p["rr"] >= Strategy.fixed_params["min_rr"]
                assert p["trigger"] and p["reason"]
                if p["side"] == "long":
                    assert p["stop"] < p["entry"] < p["tp"]
                else:
                    assert p["tp"] < p["entry"] < p["stop"]
        assert found > 0, "aucun plan généré sur 10 seeds — test non significatif"

    def test_plans_insufficient_data(self):
        assert Strategy().trade_plans(_random_df(80)) == []

    def test_immediate_plan_matches_score(self):
        """Un plan 'immediate' doit correspondre EXACTEMENT au signal que
        score() renvoie (même côté/setup/entrée/SL/TP) — garantit que le
        tableau « Trades à ouvrir » ne montre pas de trade que le moteur ne
        prendrait pas. Enforce la cohérence trade_plans ↔ _signal_at."""
        checked = 0
        for seed in range(20):
            df = _random_df(900, seed=seed, jump_p=0.04)
            plans = Strategy().trade_plans(df)
            sig = Strategy().score(df)
            imm = [p for p in plans if p["status"] == "immediate"]
            if sig["side"] != "none":
                assert imm, f"score={sig['side']} mais aucun plan immediate (seed {seed})"
                p = imm[0]
                assert p["side"] == sig["side"] and p["setup"] == sig["setup"]
                assert abs(p["stop"] - sig["stop_hint"]) < 1e-6
                assert abs(p["tp"] - sig["tp_hint"]) < 1e-6
                checked += 1
            else:
                assert not imm, f"aucun signal mais plan immediate présent (seed {seed})"
        assert checked > 0, "aucun signal immédiat testé — échantillon non significatif"

    def test_time_stop_sets_exit_after_bars(self):
        """time_stop_bars > 0 → les signaux portent exit_after_bars (mécanisme
        natif du Backtester) ; 0 (défaut) → pas de time-stop (None)."""
        assert Strategy.fixed_params["time_stop_bars"] == 0
        found_off = found_on = 0
        for seed in range(12):
            df = _random_df(900, seed=seed, jump_p=0.04)
            # défaut : exit_after_bars None
            s_off = Strategy(); s_off._bt_params = None
            s_off.prepare_for_backtest(df)
            for sig in (s_off._bt_signals or {}).values():
                assert sig.get("exit_after_bars") is None
                found_off += 1
            # time_stop 12 : exit_after_bars == 12
            s_on = Strategy()
            s_on._bt_params = {"smart_money": {"time_stop_bars": 12}}
            s_on.prepare_for_backtest(df)
            for sig in (s_on._bt_signals or {}).values():
                assert sig.get("exit_after_bars") == 12
                found_on += 1
        assert found_off > 0 and found_on > 0, "aucun signal généré — test non significatif"

    def test_trailing_wiring(self):
        """use_trailing=True → signaux sans TP fixe (tp_hint None), trailing
        activé (disable_trailing False + trail_override), et time-stop porté
        par check_early_exit (exit_after_bars None). Défaut (off) inchangé."""
        assert Strategy.fixed_params["use_trailing"] is False
        found = 0
        for seed in range(12):
            df = _random_df(900, seed=seed, jump_p=0.04)
            s = Strategy()
            s._bt_params = {"smart_money": {"use_trailing": True,
                                            "trail_mult": 3.5,
                                            "time_stop_bars": 12}}
            s.prepare_for_backtest(df)
            for sig in (s._bt_signals or {}).values():
                found += 1
                assert sig["tp_hint"] is None
                assert sig["exit_after_bars"] is None
                assert sig["disable_trailing"] is False
                assert sig["trail_override"] == {"trail_wide": 3.5, "mode": "dynamic"}
                # la cible reste exposée pour l'affichage, + risque pour le time-stop
                assert sig["indicators"]["tp_target"] is not None
                assert sig["indicators"]["_risk_pct"] > 0
        assert found > 0, "aucun signal trailing généré — test non significatif"

    def test_conditional_time_stop_cuts_only_stallers(self):
        """En mode trailing, le time-stop conditionnel (check_early_exit) coupe
        UNIQUEMENT les trades stagnants (MFE < ts_profit_r×R après N barres) —
        jamais un gagnant qui court, ni un trade encore jeune. Off si !trailing."""
        s = Strategy()
        df = _random_df(30, seed=1)          # height 30 → bars_held = 29 - bar
        on = {"smart_money": {"use_trailing": True, "time_stop_bars": 12,
                              "choch_exit": False}}
        # stagnant : détenu 19 barres, MFE 1.0 % < 1×R (risque 2.0 %) → coupé
        stall = {"bar": 10, "mfe": 1.0, "indicators": {"_risk_pct": 2.0}}
        assert s.check_early_exit(df, stall, on) == "time_stop_stall"
        # gagnant qui court : MFE 3.0 % ≥ 1×R → PAS coupé (le trailing gère)
        winner = {"bar": 10, "mfe": 3.0, "indicators": {"_risk_pct": 2.0}}
        assert s.check_early_exit(df, winner, on) is None
        # trop jeune : détenu 4 barres < 12 → PAS coupé
        fresh = {"bar": 25, "mfe": 0.0, "indicators": {"_risk_pct": 2.0}}
        assert s.check_early_exit(df, fresh, on) is None
        # fallback risque via _stop_trail quand _risk_pct absent
        fb = {"bar": 10, "mfe": 0.5, "entry": 100.0,
              "_stop_trail": [{"bar": 11, "stop": 98.0}], "indicators": {}}
        assert s.check_early_exit(df, fb, on) == "time_stop_stall"
        # sans trailing : le time-stop natif (exit_after_bars) gère, pas ici
        off = {"smart_money": {"use_trailing": False, "time_stop_bars": 12,
                               "choch_exit": False}}
        assert s.check_early_exit(df, stall, off) is None

    def test_dir_gate_and_htf_ok_helpers(self):
        # _htf_ok : sémantique off/soft/strict
        assert Strategy._htf_ok("off", -1) == (True, True)
        assert Strategy._htf_ok("soft", 1) == (True, False)
        assert Strategy._htf_ok("soft", -1) == (False, True)
        assert Strategy._htf_ok("strict", 0) == (False, False)
        # _dir_gate : structure + momentum + ema + htf
        assert Strategy._dir_gate("long", 1, "premium", True, True) is True
        assert Strategy._dir_gate("long", 1, "discount", True, True) is False
        assert Strategy._dir_gate("long", -1, "premium", True, True) is False
        assert Strategy._dir_gate("short", -1, "discount", True, True) is True
        assert Strategy._dir_gate("short", -1, "premium", True, True) is False


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


# ── SMT divergence générique (smc.smt_series) ─────────────────────────────────

class TestSmtSeries:
    def _mk_corr(self, tmp_path, times, closes):
        p = tmp_path / "corr.parquet"
        pl.DataFrame({
            "time": times, "open": closes, "high": closes,
            "low": closes, "close": closes,
            "volume": [1.0] * len(closes),
        }).write_parquet(p)
        return str(p)

    def test_none_when_path_missing(self):
        df = _random_df(300)
        assert smc.smt_series(df, "", 20) is None
        assert smc.smt_series(df, "/does/not/exist.parquet", 20) is None

    def test_shape_and_domain(self, tmp_path):
        df = _random_df(400, seed=3)
        corr = self._mk_corr(tmp_path, df["time"].to_list(),
                             df["close"].to_numpy() * 1.5)
        s = smc.smt_series(df, corr, 20)
        assert s is not None and len(s) == df.height
        assert set(np.unique(s)).issubset({-1, 0, 1})

    def test_bullish_divergence_detected(self, tmp_path):
        # A fait un nouveau plus-bas à la dernière barre, B (corrélé) NON → +1
        n = 40
        times = [datetime(2024, 1, 1) + timedelta(hours=i) for i in range(n)]
        a = [100.0] * n
        a[-1] = 80.0                       # A : nouveau plus-bas franc
        b = [100.0] * n                    # B : plat, pas de nouveau plus-bas
        b[-1] = 105.0
        df = _mk_df(a, a, a, a)
        corr = self._mk_corr(tmp_path, times, b)
        s = smc.smt_series(df, corr, 20)
        assert int(s[-1]) == 1
