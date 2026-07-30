"""Phase 4 — hiérarchie de notifications 3 niveaux, throttling, flux UI, réconciliation."""
from app.core.notifications import LEVELS, Notifier


def _notifier():
    # Aucun canal actif → _dispatch est un no-op réseau ; on teste le flux mémoire.
    return Notifier({"notifications": {"throttle_secs": 100, "feed_size": 50}})


def test_three_levels_recorded_in_feed():
    n = _notifier()
    n.send("info msg", async_=False, level="info")
    n.send("warn msg", async_=False, level="warning")
    n.send("crit msg", async_=False, level="critical")
    feed = n.recent(limit=10)
    assert [e["level"] for e in feed] == ["critical", "warning", "info"]  # plus récent d'abord
    assert LEVELS == ("info", "warning", "critical")


def test_recent_filters_by_min_level():
    n = _notifier()
    n.send("i", async_=False, level="info")
    n.send("w", async_=False, level="warning")
    n.send("c", async_=False, level="critical")
    only_warn_plus = n.recent(min_level="warning")
    assert {e["level"] for e in only_warn_plus} == {"warning", "critical"}
    only_crit = n.recent(min_level="critical")
    assert [e["level"] for e in only_crit] == ["critical"]


def test_throttling_merges_same_key_but_criticals_always_pass():
    n = _notifier()
    n.send("repeat", async_=False, level="info", key="K")
    n.send("repeat", async_=False, level="info", key="K")   # throttlé
    assert len(n.recent()) == 1
    # Les critiques ne sont jamais throttlés
    n.send("boom", async_=False, level="critical", key="K")
    n.send("boom", async_=False, level="critical", key="K")
    crits = [e for e in n.recent() if e["level"] == "critical"]
    assert len(crits) == 2


def test_feed_strips_markdown():
    n = _notifier()
    n.send("*bold* `code` _it_", async_=False)
    assert n.recent()[0]["message"] == "bold code it"


def test_reconciliation_mismatch_is_critical():
    n = _notifier()
    n.notify_reconciliation_mismatch("BTC/USDC", "frais", 0.01, 0.02, 100.0)
    e = n.recent()[0]
    assert e["level"] == "critical"
    assert "réconciliation" in e["message"].lower() or "reconcil" in e["message"].lower()
