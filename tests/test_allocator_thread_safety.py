"""OPS-10 — CapitalAllocator sous accès concurrent (RLock interne).

Acceptation : thread lifecycle en boucle serrée (allocation continue +
rebuilds) pendant que deux threads « trading » enchaînent ouvertures/
clôtures — aucune incohérence entre ``sum(used_notional)`` et le notionnel
réellement ouvert, aucun trade hebdo perdu.
"""
import threading

from app.live.capital_allocator import CapitalAllocator

_ACTIVE = {
    "1h": [{"name": "trend_rider", "symbol": "BTC/USDC"},
           {"name": "smart_money", "symbol": "ETH/USDC"}],
}
_KEY_A = "trend_rider::1h::BTC/USDC"
_KEY_B = "smart_money::1h::ETH/USDC"


def test_concurrent_open_close_vs_lifecycle():
    alloc = CapitalAllocator(capital=10_000.0, active_per_tf=_ACTIVE, cfg={})
    stop = threading.Event()
    errors: list = []

    def lifecycle():
        # Simule le thread lifecycle : réallocation continue + rebuilds.
        scores = {_KEY_A: 1.0, _KEY_B: 0.5}
        while not stop.is_set():
            try:
                alloc.apply_continuous_allocation(scores)
                alloc.rebuild_slots(_ACTIVE)
                alloc.get_status()
            except Exception as e:      # pragma: no cover - échec = bug
                errors.append(e)
                return

    def churn(key: str, n: int):
        # Simule le cycle principal : n paires ouverture/clôture.
        for _ in range(n):
            ok, _reason = alloc.can_allocate(key, 10.0)
            alloc.register_open(key, 10.0)
            alloc.register_close(key, 10.0, pnl=1.0)

    n = 2000
    threads = [
        threading.Thread(target=lifecycle),
        threading.Thread(target=churn, args=(_KEY_A, n)),
        threading.Thread(target=churn, args=(_KEY_B, n)),
    ]
    for t in threads[1:]:
        t.start()
    threads[0].start()
    threads[1].join()
    threads[2].join()
    stop.set()
    threads[0].join()

    assert not errors, f"exception dans le thread lifecycle : {errors[0]!r}"
    # Tout ce qui a été ouvert a été fermé : exposition résiduelle nulle.
    assert alloc._slots[_KEY_A].used_notional == 0.0
    assert alloc._slots[_KEY_B].used_notional == 0.0
    # Aucune mise à jour perdue sur les stats hebdo (read-modify-write).
    assert alloc._slots[_KEY_A].weekly_trades == n
    assert alloc._slots[_KEY_B].weekly_trades == n
    assert alloc._slots[_KEY_A].weekly_pnl == n * 1.0
    # Les budgets restent normalisés (slots actifs > 0).
    budgets = alloc.enabled_budgets()
    assert set(budgets) == {_KEY_A, _KEY_B}
    assert all(v >= 0.0 for v in budgets.values())


def test_locked_accessors():
    alloc = CapitalAllocator(capital=1_000.0, active_per_tf=_ACTIVE, cfg={})
    assert alloc.budget_pct(_KEY_A) == alloc._slots[_KEY_A].budget_pct
    assert alloc.budget_pct("inconnu::1h::X") == 0.0
    assert alloc.enabled_budgets() == {
        k: round(s.budget_pct, 4) for k, s in alloc._slots.items() if s.enabled
    }
