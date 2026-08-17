"""Modes de sortie génériques — comportement distinct, défaut inerte.

Chaque stratégie déclarait sa gestion de sortie à la main. Comparer deux
stratégies revenait donc à comparer aussi deux gestions de position, sans
pouvoir dire laquelle expliquait l'écart : le dossier `smc_ml_edge` a buté
exactement là-dessus, le coupable étant un stop trop serré et non le signal.

Deux exigences, et la seconde compte autant que la première :

1. **chaque mode fait quelque chose de différent** — quatre noms qui produisent
   le même backtest ne serviraient à rien ;
2. **le défaut ne change rien** — `as_declared` doit laisser tous les backtests
   existants rigoureusement identiques, sinon on aurait modifié en silence
   toutes les mesures déjà publiées.
"""
import datetime as dt

import numpy as np
import polars as pl
import pytest

from app.core.execution import (
    EXIT_MODES,
    TP1_TP2_RUNNER_EXITS,
    apply_exit_mode,
    plan_partial_targets,
)
from app.engine.backtest import Backtester
from app.engine.engine import BaseStrategy, Engine


# ─────────────────────────────────────────────────────────────────────────────
#  Résolution du mode (unitaire)
# ─────────────────────────────────────────────────────────────────────────────
def _signal(**extra) -> dict:
    return {"name": "x", "side": "long", "score": 0.9, **extra}


def test_as_declared_rend_le_signal_intact():
    """Le défaut doit être une identité — pas une copie « équivalente »."""
    sig = _signal(exits=[{"r": 3.0, "fraction": 0.5}], disable_trailing=True)
    assert apply_exit_mode(sig, "as_declared") is sig


def test_sl_tp_coupe_partielles_et_suiveur():
    out = apply_exit_mode(_signal(exits=[{"r": 1.0, "fraction": 0.5}]), "sl_tp")
    assert out["exits"] == []
    assert out["disable_trailing"] is True


def test_trailing_active_le_suiveur_sans_partielles():
    out = apply_exit_mode(_signal(disable_trailing=True), "trailing")
    assert out["exits"] == []
    assert out["disable_trailing"] is False
    assert "_trail_activate_r" not in out


def test_trailing_after_profit_pose_un_seuil_d_armement():
    out = apply_exit_mode(_signal(), "trailing_after_profit")
    assert out["disable_trailing"] is False
    assert out["_trail_activate_r"] == pytest.approx(1.0)

    perso = apply_exit_mode(_signal(), "trailing_after_profit",
                            {"trail_activate_r": 2.5})
    assert perso["_trail_activate_r"] == pytest.approx(2.5)


def test_les_modes_suiveurs_retirent_la_cible_fixe():
    """Un mode qui laisse courir doit POSSÉDER la géométrie de sortie.

    Mesuré : `smc_ml_edge` fixe `sl_atr_mult = tp_atr_mult = 2.5`, donc sa
    cible tombe exactement à 1R. Le moteur testant la cible fixe AVANT les
    cibles partielles, elle soldait toujours la position en entier et
    `tp1_tp2_runner` rendait un backtest identique à `as_declared`, zéro jambe.
    """
    sig = _signal(tp_atr_mult=2.5, tp_hint=123.0)
    for mode in ("trailing", "trailing_after_profit", "tp1_tp2_runner"):
        out = apply_exit_mode(sig, mode)
        assert out["tp_atr_mult"] is None, f"{mode} garde tp_atr_mult"
        assert out["tp_hint"] is None, f"{mode} garde tp_hint"
    # `sl_tp` la garde, évidemment : c'est tout son objet.
    garde = apply_exit_mode(sig, "sl_tp")
    assert garde["tp_atr_mult"] == 2.5


def test_tp1_tp2_runner_laisse_un_reliquat():
    """Le runner EST le reliquat : des fractions qui somment à 1 ne laisseraient
    rien courir, ce qui viderait le mode de son sens."""
    out = apply_exit_mode(_signal(), "tp1_tp2_runner")
    total = sum(e["fraction"] for e in out["exits"])
    assert total < 1.0, f"aucun reliquat pour le runner (somme={total})"
    assert out["be_after_partial"] is True
    assert out["disable_trailing"] is False


def test_les_cibles_du_mode_sont_planifiables():
    """Le mode doit produire des cibles que `plan_partial_targets` accepte —
    sinon on aurait un mode qui ne déclenche jamais rien."""
    sig = apply_exit_mode(_signal(), "tp1_tp2_runner")
    cibles = plan_partial_targets(sig, entry=100.0, stop=98.0)
    assert len(cibles) == len(TP1_TP2_RUNNER_EXITS)
    # 1R = 100 + 1×2 = 102 ; 2R = 104.
    assert [round(c["price"], 6) for c in cibles] == [102.0, 104.0]


def test_un_mode_inconnu_est_refuse_bruyamment():
    """Une faute de frappe dans un YAML doit se voir. Ignorée en silence, elle
    produirait un backtest dont on croirait qu'il teste autre chose."""
    with pytest.raises(ValueError, match="mode de sortie inconnu"):
        apply_exit_mode(_signal(), "trailling")


def test_tous_les_modes_declares_sont_applicables():
    for mode in EXIT_MODES:
        assert apply_exit_mode(_signal(), mode) is not None


# ─────────────────────────────────────────────────────────────────────────────
#  Effet réel en backtest
# ─────────────────────────────────────────────────────────────────────────────
def _ohlcv(n: int = 500, seed: int = 11) -> pl.DataFrame:
    """Marché en tendance haussière franche : un runner a de quoi courir, et
    un stop trop serré a de quoi se faire toucher."""
    rng = np.random.RandomState(seed)
    start = dt.datetime(2022, 1, 1)
    rets = rng.normal(0.0025, 0.012, n)
    close = 100 * np.cumprod(1 + rets)
    open_ = np.concatenate([[100.0], close[:-1]])
    return pl.DataFrame({
        "time": [start + dt.timedelta(hours=i) for i in range(n)],
        "open": open_,
        "high": np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.005, n))),
        "low": np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.005, n))),
        "close": close,
        "volume": rng.uniform(100, 1000, n),
    })


class _EntreeReguliere(BaseStrategy):
    """Ouvre un long tous les 60 barres, sans déclarer de gestion de sortie :
    c'est le mode qui doit décider."""

    name = "entree_reguliere"

    def min_bars_required(self, params: dict = None) -> int:
        return 50

    def score(self, df, params=None, df_htf=None, symbol: str = "") -> dict:
        n = len(df)
        if n < 50 or n % 60 != 0:
            return {"name": self.name, "side": "none", "score": 0.0}
        return {"name": self.name, "side": "long", "score": 0.9,
                "sl_atr_mult": 2.0, "tp_atr_mult": 6.0}


def _cfg(mode: str, params: dict = None) -> dict:
    return {
        "trading": {"capital": 10000, "risk_per_trade": 0.01, "timeframe": "1h",
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.5, "borrow_rate_daily": 0.0002},
        "backtest": {"spread_pct": 0.0005, "atr_stop_mult": 2.0, "trail_wide": 2.5,
                     "trail_normal": 2.0, "trail_lock": 1.5, "trail_tight": 1.0,
                     "grace_bars": 4, "breakeven_r": 1.2, "lock_r": 2.5,
                     "tight_r": 4.0, "lock_ratio": 0.6, "use_swing": False,
                     "max_notional_pct": 0.2,
                     "exit_mode": mode, "exit_mode_params": params or {}},
        "strategies": {"enabled": ["entree_reguliere"]},
        "strategy_params": {"entree_reguliere": {}},
    }


def _run(mode: str, params: dict = None):
    eng = Engine()
    eng.register(_EntreeReguliere(), silent=True)
    return Backtester(eng, _cfg(mode, params), ml_mode="inline").run(
        _ohlcv(), "BTC/USDC", "1h")


@pytest.fixture(scope="module")
def resultats():
    return {m: _run(m) for m in EXIT_MODES}


def test_le_defaut_ne_change_rien(resultats):
    """`as_declared` doit produire EXACTEMENT le backtest d'avant. C'est ce qui
    autorise à brancher ce mécanisme sans invalider les mesures publiées."""
    eng = Engine()
    eng.register(_EntreeReguliere(), silent=True)
    cfg = _cfg("as_declared")
    cfg["backtest"].pop("exit_mode")          # config d'avant, sans la clé
    cfg["backtest"].pop("exit_mode_params")
    avant = Backtester(eng, cfg, ml_mode="inline").run(_ohlcv(), "BTC/USDC", "1h")

    apres = resultats["as_declared"]
    assert len(avant.trades) == len(apres.trades)
    assert avant.total_pnl == pytest.approx(apres.total_pnl, abs=1e-9)


def test_chaque_mode_produit_un_resultat_distinct(resultats):
    """Quatre noms qui donneraient le même backtest ne serviraient à rien."""
    empreintes = {m: (len(r.trades), round(r.total_pnl, 4))
                  for m, r in resultats.items()}
    actifs = {m: e for m, e in empreintes.items() if m != "as_declared"}
    assert len(set(actifs.values())) > 1, (
        f"tous les modes donnent le même résultat : {empreintes}")


def test_sl_tp_ne_produit_aucune_sortie_partielle(resultats):
    for t in resultats["sl_tp"].trades:
        assert "tp1" not in str(t.get("exit_reason", ""))


def test_tp1_tp2_runner_declenche_bien_des_partielles(resultats):
    """Le mode doit réellement sortir en plusieurs fois — sinon il n'est qu'un
    trailing déguisé."""
    res = resultats["tp1_tp2_runner"]
    if not res.trades:
        pytest.skip("aucun trade sur ce jeu")
    # Les jambes sont journalisées SUR LE TRADE (clé `exits`), le trade n'étant
    # enregistré qu'à sa clôture complète.
    motifs = {str(j.get("reason", ""))
              for t in res.trades for j in (t.get("exits") or [])}
    assert motifs & {"tp1", "tp2"}, (
        f"aucune jambe partielle observée (motifs={motifs})")

    # Et le mode ne doit pas tout solder d'un coup : le runner doit exister.
    partiels = [t for t in res.trades if t.get("exits")]
    assert partiels, "aucun trade n'a de jambe partielle"


def test_les_autres_modes_ne_produisent_aucune_jambe(resultats):
    """Contre-épreuve : sans elle, un `exits` laissé traîner par la stratégie
    ferait passer le test précédent pour tous les modes."""
    for mode in ("sl_tp", "trailing", "trailing_after_profit"):
        jambes = [j for t in resultats[mode].trades for j in (t.get("exits") or [])]
        assert not jambes, f"{mode} produit des jambes partielles : {jambes[:2]}"


def test_trailing_after_profit_garde_le_stop_initial_au_debut(resultats):
    """Le mode n'a d'intérêt que s'il DIFFÈRE du trailing simple : un trade
    stoppé avant d'être en profit doit l'être à son stop d'origine."""
    a = resultats["trailing"]
    b = resultats["trailing_after_profit"]
    assert (len(a.trades), round(a.total_pnl, 4)) != \
           (len(b.trades), round(b.total_pnl, 4)), (
        "trailing et trailing_after_profit donnent le même résultat — "
        "l'armement différé n'a aucun effet")
