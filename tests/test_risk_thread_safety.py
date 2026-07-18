"""S1-01 — RiskManager sous accès concurrent (RLock interne).

open_positions/equity/slot_states sont lus et mutés depuis le thread de
trading (can_trade/register_open/register_close/update_equity) ET depuis les
threads API (status_dict pour /api/status) — sans verrou, races possibles
(compteur de trades/jour perdu par un read-modify-write non atomique, lecture
torn d'un dict en cours de mutation). Miroir de test_allocator_thread_safety.py
(OPS-10) appliqué à RiskManager.
"""
import threading
import time

from app.core.risk import RiskManager

SLOT = "trend_rider::1h::BTC/USDC"


def _cfg():
    return {
        "trading": {"capital": 10_000.0, "paper_mode": True,
                    "max_positions": 1000, "max_longs": 1000, "max_shorts": 1000,
                    "max_trades_per_minute": 1_000_000},
        "risk": {},
    }


def test_register_slot_open_counter_survives_concurrent_access():
    """daily_trades est un read-modify-write (+= 1) — sans lock, des mises à
    jour concurrentes se perdent (le compteur final est < N)."""
    rm = RiskManager(_cfg())
    # Volume modéré : suffisant pour exercer le read-modify-write concurrent
    # sans créer de pression CPU/threads excessive une fois combiné au reste
    # de la suite (610+ tests dans le même process).
    n_per_thread = 400
    n_threads = 3

    def churn():
        for _ in range(n_per_thread):
            rm.register_slot_open(SLOT)

    threads = [threading.Thread(target=churn) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert rm.slot_states[SLOT].daily_trades == n_per_thread * n_threads


def test_concurrent_open_close_vs_status_reader():
    """Un thread ouvre/ferme des positions pendant qu'un autre lit status_dict
    /get_slot_states en boucle (simule le thread API) — aucune exception, et
    l'ensemble ouvert == fermé se retrouve à l'équilibre (aucun ID perdu)."""
    rm = RiskManager(_cfg())
    stop = threading.Event()
    errors: list = []

    def reader():
        while not stop.is_set():
            try:
                rm.status_dict()
                rm.get_slot_states()
                rm.get_circuit_breakers_status()
            except Exception as e:  # pragma: no cover - échec = bug
                errors.append(e)
                return
            time.sleep(0)  # cède le GIL — évite un busy-loop CPU-bound serré

    n = 800

    def churn():
        for i in range(n):
            pos_id = f"pos-{i}"
            rm.register_open({"id": pos_id, "symbol": "BTC/USDC", "side": "long",
                              "strategy": "trend_rider", "timeframe": "1h"})
            rm.register_close(pos_id)

    reader_thread = threading.Thread(target=reader)
    churn_thread = threading.Thread(target=churn)
    reader_thread.start()
    churn_thread.start()
    churn_thread.join()
    stop.set()
    reader_thread.join()

    assert not errors, f"exception dans le thread lecteur : {errors[0]!r}"
    assert rm.open_positions == {}
    assert rm.slot_states[SLOT].daily_trades == n
