"""Portillon mémoire de l'optimiseur (admission control anti-OOM inter-jobs).

Régression : lancée depuis l'UI, ``start_async`` ouvre jusqu'à cpu-1 jobs
concurrents ; avec n_jobs=1 chaque job entraîne LightGBM en walk-forward
IN-PROCESS. Sur de gros jeux de données, cpu-1 backtests ML simultanés
épuisaient la RAM → std::bad_alloc / OOM → mort silencieuse du process (le cas
rapporté : 9 jobs ML, retour sec au prompt sans traceback). Le portillon borne
la mémoire CUMULÉE des jobs actifs.
"""
import threading
import time

import app.engine.auto_optimizer as ao


def _reset(budget):
    with ao._mem_cond:
        ao._mem_budget = budget
        ao._mem_committed = 0


def test_gate_disabled_when_ram_unknown(monkeypatch):
    """RAM inconnue (available_memory_bytes → None) → portillon transparent :
    pas de réservation, comportement historique (cap mémoire désactivé)."""
    monkeypatch.setattr(ao, "available_memory_bytes", lambda: None)
    _reset(None)                                  # force le re-snapshot paresseux
    assert ao._acquire_mem_slot(10**12) is False
    assert ao._mem_committed == 0


def test_admits_within_budget_and_blocks_over_budget():
    _reset(1000)
    assert ao._acquire_mem_slot(400) is True
    assert ao._acquire_mem_slot(400) is True          # committed = 800
    assert ao._mem_committed == 800

    admitted = threading.Event()
    def waiter():
        if ao._acquire_mem_slot(400):                 # 1200 > 1000 → attend
            admitted.set()
    t = threading.Thread(target=waiter, daemon=True)
    t.start()

    time.sleep(0.4)
    assert not admitted.is_set(), "le 3e job doit attendre tant que 1200 > 1000"

    ao._release_mem_slot(400)                          # committed = 400 → 800 ≤ 1000
    assert admitted.wait(timeout=3.0), "le 3e job doit être admis après libération"
    assert ao._mem_committed == 800


def test_oversized_job_alone_is_admitted_no_deadlock():
    """Un job seul est toujours admis même si son estimation dépasse le budget."""
    _reset(1000)
    assert ao._acquire_mem_slot(5000) is True
    assert ao._mem_committed == 5000
    ao._release_mem_slot(5000)
    assert ao._mem_committed == 0


def test_cancel_during_wait_returns_false_without_reserving():
    _reset(1000)
    assert ao._acquire_mem_slot(900) is True           # committed = 900
    ev = threading.Event()
    out = {}
    def waiter():
        out["ok"] = ao._acquire_mem_slot(900, cancel_event=ev)  # 1800 > 1000 → attend
    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.4)
    ev.set()
    t.join(timeout=3.0)
    assert out.get("ok") is False
    assert ao._mem_committed == 900, "une annulation ne doit jamais réserver"


def test_release_never_goes_negative():
    _reset(1000)
    ao._release_mem_slot(123)
    assert ao._mem_committed == 0


def test_estimate_scales_with_rows_and_ml_flag():
    class _DF:
        def __init__(self, n): self.n = n
        def __len__(self): return self.n

    small_ml = ao._estimate_job_bytes(_DF(1000), _DF(500), is_ml=True)
    big_ml   = ao._estimate_job_bytes(_DF(40000), _DF(20000), is_ml=True)
    big_noml = ao._estimate_job_bytes(_DF(40000), _DF(20000), is_ml=False)

    assert big_ml > small_ml                 # croît avec le nombre de barres
    assert big_ml > big_noml                 # ML plus lourd que non-ML
    assert small_ml >= 350 * 1024 * 1024     # plancher ML respecté
