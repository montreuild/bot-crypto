"""Tests unitaires — détecteurs ICT (app/core/ict.py)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.core.ict as ict


def test_consequent_encroachment():
    assert ict.consequent_encroachment(101.0, 99.0) == 100.0


def test_std_dev_projections():
    up = ict.std_dev_projections(100.0, 110.0, "up", mults=(1.0, 2.0))
    assert [p["level"] for p in up] == [120.0, 130.0]      # high + m×range
    dn = ict.std_dev_projections(100.0, 110.0, "down", mults=(1.0,))
    assert dn[0]["level"] == 90.0                          # low − m×range
    assert ict.std_dev_projections(100.0, 100.0, "up") == []   # range nul


def test_balanced_price_range():
    fvgs = [
        {"kind": "bullish", "index": 5, "top": 101.0, "bottom": 99.0, "filled_at": None},
        {"kind": "bearish", "index": 6, "top": 100.5, "bottom": 98.5, "filled_at": None},
    ]
    z = ict.balanced_price_ranges(fvgs, 10)
    assert len(z) == 1
    assert z[0]["bottom"] == 99.0 and z[0]["top"] == 100.5
    assert z[0]["ce"] == 99.75
    # causal : à i=6, le FVG baissier (index 6) n'est pas encore formé
    assert ict.balanced_price_ranges(fvgs, 6) == []
    # comblé avant i → exclu
    fvgs[1]["filled_at"] = 8
    assert ict.balanced_price_ranges(fvgs, 10) == []


def test_unicorn_zones():
    brk = [{"kind": "bullish", "top": 101.0, "bottom": 99.0,
            "created_at": 5, "invalidated_at": None}]
    fvgs = [{"kind": "bullish", "index": 6, "top": 100.5, "bottom": 99.5, "filled_at": None}]
    z = ict.unicorn_zones(brk, fvgs, 10)
    assert len(z) == 1 and z[0]["kind"] == "bullish"
    assert z[0]["bottom"] == 99.5 and z[0]["top"] == 100.5
    # polarité opposée → pas d'unicorn
    fvgs[0]["kind"] = "bearish"
    assert ict.unicorn_zones(brk, fvgs, 10) == []


def test_silver_bullet_flags():
    # epochs à h=15 UTC (fenêtre) et h=12 UTC (hors fenêtre)
    ep = np.array([15 * 3600, 12 * 3600, 8 * 3600, 19 * 3600], dtype=np.int64)
    f = ict.silver_bullet_flags(ep)
    assert f.tolist() == [True, False, True, True]


def test_judas_swing():
    n = 20
    ep = np.array([(8 if i >= 14 else 12) * 3600 + i * 86400 for i in range(n)], np.int64)
    h = np.full(n, 100.0); l = np.full(n, 99.0); c = np.full(n, 99.5)
    # barre 15 (heure 8, dans la fenêtre) balaie le plus-haut récent puis referme
    h[15] = 102.0; c[15] = 99.6            # high > max(100) mais clôture < 100
    out = ict.judas_swing(h, l, c, ep, open_hour=8, window=3, lookback=12)
    assert out[15] == -1
    # même mèche hors fenêtre (barre 5, heure 12) → ignorée
    h[5] = 102.0; c[5] = 99.6
    assert ict.judas_swing(h, l, c, ep, open_hour=8, window=3, lookback=12)[5] == 0


def test_smt_divergence_and_causality():
    n = 60
    rng = np.random.default_rng(1)
    a = 100 + rng.normal(0, 1, n).cumsum()
    b = a.copy()
    a[40] = a[:40].max() + 5      # A nouveau plus-haut
    b[40] = b[:40].max() - 5      # B non → SMT baissier
    out = ict.smt_divergence(a, b, 20)
    assert out[40] == -1
    # causal : préfixe == full sur barres intérieures
    pref = ict.smt_divergence(a[:50], b[:50], 20)
    assert np.array_equal(pref[25:50], out[25:50])
