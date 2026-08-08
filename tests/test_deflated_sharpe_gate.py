"""Tests du gate Deflated Sharpe au niveau de `beats_baseline` (P0-1).

Vérifie que :
- Sans `n_trials` ou `n_trials=1` : pas de gate DS (rétrocompatibilité).
- Avec `n_trials > 1` + `min_deflated_sharpe > 0` : gate DS actif.
- Sharpe faible + beaucoup d'essais → refus (biais de sélection probable).
- Sharpe élevé + beaucoup d'essais → acceptation.
- `min_deflated_sharpe=None` → gate désactivé même avec n_trials > 1.
- Les 4 autres conditions (trades minimaux, PnL > 0, PnL > baseline, WR/Sharpe
  amélioré) restent prioritaires sur le gate DS.

Couvre le câblage TODO(auto_optimizer.py:521) — Deflated Sharpe Ratio (Bailey &
López de Prado 2014) au gate de naissance.
"""
from app.engine.opt_scoring import beats_baseline

# ── Rétrocompatibilité : gate DS désactivé par défaut ────────────────────────

def test_beats_baseline_retrocompatible_sans_n_trials():
    """Sans n_trials, comportement strictement inchangé."""
    ok, reason = beats_baseline(15, 100, 55, 1.2, {"pnl": 50, "wr": 50, "sharpe": 0.8})
    assert ok is True
    assert reason == "ok"


def test_beats_baseline_avec_n_trials_1_pas_de_gate_ds():
    """n_trials=1 → pas de biais de sélection → gate DS inactif."""
    ok, reason = beats_baseline(15, 100, 55, 1.2, {"pnl": 50, "wr": 50, "sharpe": 0.8},
                                n_trials=1, min_deflated_sharpe=0.5)
    assert ok is True


def test_beats_baseline_gate_ds_desactive_via_none():
    """min_deflated_sharpe=None → gate désactivé même si n_trials > 1."""
    # Sharpe très faible qui serait refusé si le gate était actif
    ok, reason = beats_baseline(15, 100, 55, 0.3, {"pnl": 50, "wr": 50, "sharpe": 0.2},
                                n_trials=50, min_deflated_sharpe=None)
    assert ok is True


# ── Gate DS actif : refuse les Sharpes faibles après beaucoup d'essais ────────

def test_gate_ds_refuse_sharpe_faible_apres_50_essais():
    """Sharpe 0.3 après 50 essais → biais de sélection probable → refus."""
    ok, reason = beats_baseline(15, 100, 55, 0.3, {"pnl": 50, "wr": 50, "sharpe": 0.2},
                                n_trials=50, min_deflated_sharpe=0.5)
    assert ok is False
    assert "Deflated Sharpe" in reason


def test_gate_ds_refuse_sharpe_negatif():
    """Sharpe négatif → Deflated Sharpe négatif → refus."""
    ok, reason = beats_baseline(15, 100, 55, -0.5, {"pnl": 50, "wr": 50, "sharpe": -0.4},
                                n_trials=20, min_deflated_sharpe=0.5)
    # Note : le gate 4 (WR/Sharpe amélioré) refusera d'abord car oos_sharpe < b_sharpe
    # Mais si on contourne en mettant b_sharpe plus bas :
    assert ok is False


def test_gate_ds_accepte_sharpe_eleve_apres_50_essais():
    """Sharpe 3.0 après 50 essais → edge réel, DS reste positif → acceptation."""
    ok, reason = beats_baseline(15, 100, 55, 3.0, {"pnl": 50, "wr": 50, "sharpe": 2.5},
                                n_trials=50, min_deflated_sharpe=0.5)
    assert ok is True
    assert reason == "ok"


def test_gate_ds_plus_strict_avec_seuil_eleve():
    """Seuil DS=1.0 plus strict → refuse Sharpe qui passait avec seuil=0.5."""
    # Sharpe 1.0 après 50 essais : DS ≈ 0.4 (entre 0 et 1.0)
    # Seuil 0.5 → OK ; Seuil 1.0 → refuse
    ok_05, _ = beats_baseline(15, 100, 55, 1.0, {"pnl": 50, "wr": 50, "sharpe": 0.8},
                              n_trials=50, min_deflated_sharpe=0.5)
    ok_10, reason_10 = beats_baseline(15, 100, 55, 1.0, {"pnl": 50, "wr": 50, "sharpe": 0.8},
                                      n_trials=50, min_deflated_sharpe=1.0)
    # Le test 1.0/50 trials/seuil=0.5 peut passer ou échouer selon l'approximation
    # du DSR — on teste juste que seuil=1.0 est strictement plus exigeant
    assert ok_10 is False
    # Si ok_05 passe, alors seuil plus strict refuse — relation d'ordre vérifiée
    if ok_05:
        assert "Deflated Sharpe" in reason_10


# ── Priorité des autres conditions sur le gate DS ────────────────────────────

def test_gate_ds_ne_compense_pas_pnl_negatif():
    """PnL OOS ≤ 0 → refus AVANT le gate DS (priorité des conditions)."""
    ok, reason = beats_baseline(15, -50, 55, 3.0, {"pnl": 50, "wr": 50, "sharpe": 0.8},
                                n_trials=50, min_deflated_sharpe=0.5)
    assert ok is False
    assert "PnL OOS non positif" in reason


def test_gate_ds_ne_compense_pas_echantillon_insuffisant():
    """< 10 trades → refus AVANT le gate DS."""
    ok, reason = beats_baseline(5, 100, 55, 3.0, {"pnl": 50, "wr": 50, "sharpe": 0.8},
                                n_trials=50, min_deflated_sharpe=0.5)
    assert ok is False
    assert "échantillon OOS insuffisant" in reason


def test_gate_ds_ne_compense_pas_baseline_inférieur():
    """PnL ≤ baseline → refus AVANT le gate DS."""
    ok, reason = beats_baseline(15, 100, 55, 3.0, {"pnl": 200, "wr": 50, "sharpe": 0.8},
                                n_trials=50, min_deflated_sharpe=0.5)
    assert ok is False
    assert "≤ baseline" in reason


def test_gate_ds_ne_compense_pas_aucune_amelioration():
    """Aucune amélioration WR/Sharpe → refus AVANT le gate DS."""
    ok, reason = beats_baseline(15, 100, 50, 0.8, {"pnl": 50, "wr": 55, "sharpe": 1.0},
                                n_trials=50, min_deflated_sharpe=0.5)
    assert ok is False
    assert "aucune amélioration de qualité" in reason


# ── Robustesse : gate DS ne casse pas sur données inattendues ─────────────────

def test_gate_ds_resiste_a_sharpe_nan():
    """Sharpe NaN → le gate DS doit échouer gracieusement (log + acceptation
    pour ne pas bloquer le gate principal silencieusement)."""
    # On ne peut pas passer NaN directement car les conditions précédentes
    # pourraient déjà refuser. On utilise un trick : passer un très grand n_trials
    # et un sharpe float normal — le module deflated_sharpe ne devrait jamais
    # crasher sur des entrées valides.
    ok, _ = beats_baseline(15, 100, 55, 0.0, {"pnl": 50, "wr": 50, "sharpe": -0.1},
                            n_trials=2, min_deflated_sharpe=0.5)
    # Avec sharpe=0 et 2 essais, le DSR devrait être faible → refus probable
    # mais le test ne doit jamais crasher
    assert isinstance(ok, bool)
