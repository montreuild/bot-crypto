"""Deux défauts rencontrés sur une optimisation réelle (2026-08-22).

1. `Sharpe=None` faisait lever le log de refus, ce qui emportait tout le job —
   y compris l'écriture de la trace d'audit posée juste après.
2. La barre de progression comptait les essais RÉUSSIS et la boucle les essais
   RENDUS : un lot en échec la faisait geler avant la fin (« 200/400 ») alors
   que la recherche allait bien à son terme.
"""

import pytest

from app.engine.opt_scoring import beats_baseline, fmt_metric

# ── 1. Une métrique non mesurable ne fait pas tomber un log ──────────────────

def test_fmt_metric_rend_un_tiret_pour_none():
    assert fmt_metric(None) == "—"
    assert fmt_metric(1.234) == "1.23"
    assert fmt_metric(0.0) == "0.00"
    assert fmt_metric(None, "+.1f") == "—"


def test_le_refus_se_formate_avec_un_sharpe_absent():
    """0 trade → `_run_baseline` rend `sharpe: None`, délibérément (F-02)."""
    ok, raison = beats_baseline(
        20, 100.0, 55.0, None,
        {"pnl": 50.0, "wr": 60.0, "sharpe": None},
    )
    assert ok is False
    assert "—" in raison          # formaté, pas planté


@pytest.mark.parametrize("sharpe,baseline_sharpe", [
    (None, None), (None, 1.2), (1.2, None), (1.2, 0.8),
])
def test_beats_baseline_ne_leve_jamais_sur_un_sharpe_absent(sharpe, baseline_sharpe):
    ok, raison = beats_baseline(
        20, 100.0, 55.0, sharpe,
        {"pnl": 50.0, "wr": 60.0, "sharpe": baseline_sharpe},
    )
    assert isinstance(ok, bool) and isinstance(raison, str)


def test_un_sharpe_absent_ne_bat_pas_le_baseline():
    """Non mesurable n'est pas zéro : il ne peut pas valoir amélioration."""
    ok, _ = beats_baseline(
        20, 100.0, 55.0, None,
        {"pnl": 50.0, "wr": 50.0, "sharpe": -1.0},
    )
    assert ok is False


# ── 2. La progression suit le budget consommé ────────────────────────────────

class _Chercheur:
    """Le strict nécessaire pour exercer `_tell_progress`."""

    from app.engine.opt_bayesian import OptimizerBayesianMixin as _M
    _tell_progress = _M._tell_progress

    def __init__(self, cb):
        self.progress_callback = cb


def test_la_progression_avance_meme_sur_un_essai_en_echec():
    vus = []
    c = _Chercheur(lambda done, total, best, latest: vus.append((done, total)))
    c._tell_progress(1, 4, 0.5, {"oos_pnl": 1.0, "oos_sharpe": 0.2})
    c._tell_progress(2, 4, 0.5, None)      # worker KO
    c._tell_progress(3, 4, 0.5, None)      # worker KO
    c._tell_progress(4, 4, 0.7, {"oos_pnl": 2.0, "oos_sharpe": 0.4})
    assert vus == [(1, 4), (2, 4), (3, 4), (4, 4)]


def test_la_charge_utile_reste_consommable_sur_un_echec():
    """`on_progress` fait `latest.get(..., 0)` — un dict vide doit passer."""
    recu = {}
    c = _Chercheur(lambda done, total, best, latest: recu.update({"latest": latest}))
    c._tell_progress(1, 2, -999.0, None)
    assert recu["latest"] == {}
    assert recu["latest"].get("oos_pnl", 0) == 0


def test_sans_callback_rien_ne_leve():
    _Chercheur(None)._tell_progress(1, 2, 0.0, None)


def test_le_compteur_ne_melange_plus_deux_quantites():
    """`len(self.results)` cumule d'autres passes : la boucle bornait sur
    `done`, la barre affichait autre chose."""
    from pathlib import Path
    texte = Path("app/engine/opt_bayesian.py").read_text(encoding="utf-8")
    assert "self.progress_callback(len(self.results)" not in texte
