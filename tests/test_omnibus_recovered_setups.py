"""Setups récupérés de V9 : présents, désactivés, et neutres par défaut.

`SHORT_TD` et `LONG_PULLBACK_TU` n'existaient que dans `opus_omnibus_v8/v9`,
supprimés avec le pack V4 figé (docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md
§3.1). Les perdre aurait retiré deux setups du champ des possibles ; les
réintroduire ACTIVÉS aurait changé le comportement de V11. Ils sont donc
portés désactivés — activables par YAML, donc optimisables.

Ce fichier verrouille les deux propriétés qui rendent l'opération légitime :
la neutralité (rien ne change tant qu'ils sont désactivés) et l'activabilité
(ils fonctionnent réellement quand on les active — sinon ce serait du code
mort déguisé en option).
"""
import app.strategies.opus_omnibus_v11 as v11

RECOVERED = ("SHORT_TD", "LONG_PULLBACK_TU")
# Setups actifs de la lignée V10/V11 — ce que le bot trade aujourd'hui.
LIVE = ("SIGNAL_UP", "SHORT_TD_HIGH", "LONG_CHOPPY", "SHORT_CHOPPY",
        "LONG_TU", "LONG_EXIT_TD", "LONG_RANGE_STRICT", "LONG_RANGE_LIGHT")


def _by_name(setups):
    return {s["name"]: s for s in setups}


# ── Présence et désactivation ────────────────────────────────────────────────
def test_recovered_setups_are_present():
    names = {s["name"] for s in v11._DEFAULT_SETUPS}
    for n in RECOVERED:
        assert n in names, f"{n} a disparu — il n'existe plus nulle part ailleurs"


def test_recovered_setups_are_disabled_by_default():
    d = _by_name(v11._DEFAULT_SETUPS)
    for n in RECOVERED:
        assert d[n]["enabled"] is False, f"{n} activé par défaut : change le comportement V11"


def test_live_setups_untouched():
    d = _by_name(v11._DEFAULT_SETUPS)
    for n in LIVE:
        assert d[n]["enabled"] is True
    assert len(v11._DEFAULT_SETUPS) == len(LIVE) + len(RECOVERED)


def test_recovered_setups_carry_the_full_schema():
    """Un setup incomplet ferait planter ``_evaluate_setup`` à l'activation
    (accès direct à ``regime``/``amp_min``/``dir_min``/``dir_max``)."""
    ref = set(_by_name(v11._DEFAULT_SETUPS)["SHORT_TD_HIGH"].keys())
    d = _by_name(v11._DEFAULT_SETUPS)
    for n in RECOVERED:
        assert set(d[n].keys()) == ref, f"{n} n'a pas le même schéma que les autres setups"


# ── Neutralité ───────────────────────────────────────────────────────────────
def test_disabled_setups_never_selected():
    """Balayage du domaine de décision : aucun (régime, p_event, p_up) ne doit
    faire sortir un setup désactivé."""
    setups = v11._apply_setup_overrides({})
    seen = set()
    for regime in (0, 1, 2, 3):
        for p_event in (0.0, 0.35, 0.5, 0.75, 1.0):
            for p_up in (0.0, 0.3, 0.5, 0.65, 1.0):
                for exit_td in (False, True):
                    for rsi in (30.0, 50.0, 70.0):
                        for adx in (10.0, 30.0):
                            s = v11._select_setup(setups, regime, p_event, p_up,
                                                  exit_td, True, rsi, adx)
                            if s is not None:
                                seen.add(s["name"])
    for n in RECOVERED:
        assert n not in seen, f"{n} sélectionné alors qu'il est désactivé"
    assert seen, "le balayage n'a sélectionné aucun setup — test sans valeur"


def test_evaluate_setup_rejects_disabled():
    d = _by_name(v11._apply_setup_overrides({}))
    for n in RECOVERED:
        assert v11._evaluate_setup(d[n], d[n]["regime"], 1.0, 0.99, False,
                                   True, 10.0, 99.0) is False


# ── Activabilité ─────────────────────────────────────────────────────────────
def test_recovered_setups_can_be_enabled_by_params():
    setups = _by_name(v11._apply_setup_overrides({
        "setup_short_td_enabled": True,
        "setup_long_pullback_tu_enabled": True,
    }))
    for n in RECOVERED:
        assert setups[n]["enabled"] is True, f"{n} non activable par params"


def test_short_td_fires_when_enabled():
    """Conditions V9 : régime TD, amp >= 0.55, p_up < 0.35."""
    setups = v11._apply_setup_overrides({"setup_short_td_enabled": True,
                                         "setup_short_td_high_enabled": False})
    s = v11._select_setup(setups, v11.REGIME_TREND_DN, 0.56, 0.30, False)
    assert s is not None and s["name"] == "SHORT_TD"
    assert s["direction"] == -1
    # Hors conditions : p_up trop haut.
    assert v11._select_setup(setups, v11.REGIME_TREND_DN, 0.56, 0.50, False) is None


def test_long_pullback_tu_fires_when_enabled():
    """Conditions V9 : régime TU, amp >= 0.40, p_up > 0.55, RSI < 50."""
    setups = v11._apply_setup_overrides({"setup_long_pullback_tu_enabled": True,
                                         "setup_long_tu_enabled": False})
    s = v11._select_setup(setups, v11.REGIME_TREND_UP, 0.45, 0.60, False,
                          False, 45.0, 30.0)
    assert s is not None and s["name"] == "LONG_PULLBACK_TU"
    # RSI >= 50 : le filtre de pullback rejette.
    assert v11._select_setup(setups, v11.REGIME_TREND_UP, 0.45, 0.60, False,
                             False, 55.0, 30.0) is None


def test_recovered_setups_have_early_exit_rules():
    """Sans règle de sortie, un setup activé resterait ouvert jusqu'au timeout —
    comportement différent de V9, où les deux en avaient une."""
    assert v11._check_early_exit("SHORT_TD", v11.REGIME_RANGE, 0.5) == "regime_exit_TD"
    assert v11._check_early_exit("SHORT_TD", v11.REGIME_TREND_DN, 0.99) == "p_dir_inversion"
    assert v11._check_early_exit("LONG_PULLBACK_TU", v11.REGIME_TREND_DN, 0.5) == "to_TD"
    assert v11._check_early_exit("LONG_PULLBACK_TU", v11.REGIME_TREND_UP, 0.01) == "p_dir_drop"


# ─────────────────────────────────────────────────────────────────────────────
#  Sorties anticipées sur un preset SANS artefact
# ─────────────────────────────────────────────────────────────────────────────
def _proxy_window(n: int = 600):
    """Fenêtre OHLCV avec les colonnes ``_pre_*`` que lit ``ProxyPredictor``."""
    import numpy as np
    import polars as pl

    from app.core.indicators_precompute import precompute_df
    rng = np.random.RandomState(11)
    close = 100.0 * np.cumprod(1 + rng.normal(0, 0.01, n))
    t0 = __import__("datetime").datetime(2025, 1, 1)
    td = __import__("datetime").timedelta(hours=1)
    return precompute_df(pl.DataFrame({
        "time":   [t0 + i * td for i in range(n)],
        "open":   np.concatenate([[100.0], close[:-1]]),
        "high":   close * 1.004,
        "low":    close * 0.996,
        "close":  close,
        "volume": rng.uniform(1e3, 5e3, n),
    }))


def test_early_exit_works_without_any_artifact():
    """Un preset proxy doit pouvoir sortir en anticipé.

    ``check_early_exit`` gardait sur ``tf in trained_tfs`` et lisait ``p_up``
    via ``predict_direction`` (donc l'artefact) : une variante sans ML ne
    sortait JAMAIS — 0 déclenchement sur 320 appels contre 164 pour le fork
    autonome qu'elle remplace. Les positions couraient jusqu'au TP/SL/timeout
    au lieu d'être coupées sur inversion.
    """
    import app.strategies.opus_omnibus_v11_no_ml as noml
    strat = noml.Strategy()
    assert strat.uses_artifact is False
    assert not strat.ml.state.trained_tfs      # rien n'est jamais « entraîné »

    df = _proxy_window()
    fired = [strat.check_early_exit(df, {"setup": s}, params={strat.name: {}})
             for s in ("SIGNAL_UP", "SHORT_TD_HIGH", "LONG_CHOPPY",
                       "SHORT_CHOPPY", "LONG_TU", "LONG_RANGE_STRICT")]
    assert any(r is not None for r in fired), (
        "aucune sortie anticipée déclenchée — la garde d'artefact est de retour"
    )


def test_early_exit_thresholds_come_from_defaults_not_literals():
    """Un preset qui déclare un seuil de sortie doit être entendu.

    ``check_early_exit`` repliait sur des littéraux (0.55/0.42/0.40) au lieu
    de ``_DEFAULTS`` : V8 déclare ``early_exit_dir_inv_long: 0.45`` et cette
    déclaration n'avait aucun effet.
    """
    import app.strategies.opus_omnibus_v8_no_ml as v8
    assert v8.Strategy._DEFAULTS["early_exit_dir_inv_long"] == 0.45
    assert v11.Strategy._DEFAULTS["early_exit_dir_inv_long"] == 0.42

    # p_up = 0.43 : au-dessus du seuil V11 (0.42), en dessous du seuil V8 (0.45).
    assert v11._check_early_exit("SIGNAL_UP", v11.REGIME_RANGE, 0.43,
                                 dir_inv_long=0.42) is None
    assert v11._check_early_exit("SIGNAL_UP", v11.REGIME_RANGE, 0.43,
                                 dir_inv_long=0.45) == "p_dir_drop"
